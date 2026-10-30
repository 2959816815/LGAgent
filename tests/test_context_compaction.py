"""上下文压缩：阈值判断、摘要落库与 API 暴露。"""

from __future__ import annotations

import shutil
import types
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from desktop_api import compact_session, create_app, estimate_tokens
from session_store import Session, SessionStore
from session_store_sqlite import SqliteSessionStore

WORKSPACE = Path(__file__).resolve().parent.parent


def make_messages(count: int, size: int = 400) -> list:
    messages = []
    for index in range(count):
        factory = HumanMessage if index % 2 == 0 else AIMessage
        messages.append(factory(content="内容" * size))
    return messages


class EstimateTokenTests(unittest.TestCase):
    def test_proportional_to_characters(self):
        tokens = estimate_tokens([HumanMessage(content="字" * 400)])

        self.assertGreater(tokens, 50)
        self.assertLess(tokens, 200)


class CompactTests(unittest.TestCase):
    def _session(self, count: int) -> Session:
        return Session("20260101-000000-abcd", "会话", "", "", make_messages(count))

    def test_compacts_when_over_threshold(self):
        session = self._session(30)
        before = len(session.messages)

        changed = compact_session(
            session, "local", 1000, lambda messages, key: "摘要内容"
        )

        self.assertTrue(changed)
        self.assertEqual(len(session.messages), 10)
        self.assertEqual(session.summary, "摘要内容")
        self.assertEqual(session.summary_count, before - 10)

    def test_keeps_everything_when_under_threshold(self):
        session = self._session(30)

        changed = compact_session(
            session, "local", 1_000_000, lambda messages, key: "摘要"
        )

        self.assertFalse(changed)
        self.assertEqual(len(session.messages), 30)
        self.assertEqual(session.summary, "")

    def test_short_sessions_are_never_compacted(self):
        session = self._session(8)

        changed = compact_session(session, "local", 10, lambda messages, key: "摘要")

        self.assertFalse(changed)

    def test_summarizer_failure_keeps_history(self):
        session = self._session(30)

        def boom(messages, key):
            raise RuntimeError("模型不可用")

        changed = compact_session(session, "local", 1000, boom)

        self.assertFalse(changed)
        self.assertEqual(len(session.messages), 30)
        self.assertEqual(session.summary, "")

    def test_summary_accumulates_across_rounds(self):
        session = self._session(30)
        compact_session(session, "local", 1000, lambda messages, key: "第一段")
        session.messages = make_messages(30)

        compact_session(session, "local", 1000, lambda messages, key: "第二段")

        self.assertIn("第一段", session.summary)
        self.assertIn("第二段", session.summary)


class SummaryStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = WORKSPACE / f".test_summary_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _fill(self, store) -> Session:
        session = store.create("摘要会话")
        session.messages = make_messages(4)
        session.summary = "旧对话摘要"
        session.summary_count = 12
        store.save(session)
        return session

    def test_json_store_roundtrip(self):
        store = SessionStore(self.root / ".sessions")
        session = self._fill(store)

        loaded = store.load(session.id)

        self.assertEqual(loaded.summary, "旧对话摘要")
        self.assertEqual(loaded.summary_count, 12)

    def test_sqlite_store_roundtrip(self):
        store = SqliteSessionStore(self.root / "agent.db")
        session = self._fill(store)

        loaded = store.load(session.id)

        self.assertEqual(loaded.summary, "旧对话摘要")
        self.assertEqual(loaded.summary_count, 12)


class CapturingGraph:
    def __init__(self) -> None:
        self.seen: list = []

    def stream(self, payload, *args, **kwargs):
        self.seen = list(payload["messages"])
        yield ("values", {"messages": [*payload["messages"], AIMessage(content="好的")]})


class CompactionApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = WORKSPACE / f".test_compact_api_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.store = SessionStore(self.root / ".sessions")

    def _seed(self) -> str:
        session = self.store.create("长会话")
        session.messages = make_messages(20)
        self.store.save(session)
        return session.id

    def test_session_json_exposes_summary(self):
        session = self.store.create("摘要")
        session.summary = "早先的摘要"
        session.summary_count = 7
        self.store.save(session)
        app = create_app(root=self.root, token="t")
        client = TestClient(app)

        payload = client.get(
            f"/api/sessions/{session.id}", headers={"X-Desktop-Token": "t"}
        ).json()

        self.assertEqual(payload["summary"], "早先的摘要")
        self.assertEqual(payload["summary_count"], 7)

    def test_run_sends_summary_to_the_model(self):
        session_id = self._seed()
        graph = CapturingGraph()
        app = create_app(
            root=self.root,
            token="t",
            graph_builder=lambda manager, model_key, extra_tools=None: graph,
            summarizer=lambda messages, key: "压缩后的摘要",
        )
        client = TestClient(app)

        with patch(
            "desktop_api.get_profile",
            return_value=types.SimpleNamespace(context_window=500),
        ):
            response = client.post(
                f"/api/sessions/{session_id}/messages",
                json={"text": "继续"},
                headers={"X-Desktop-Token": "t"},
            )

        self.assertEqual(response.status_code, 200)
        summaries = [
            message
            for message in graph.seen
            if isinstance(message, SystemMessage) and "压缩后的摘要" in str(message.content)
        ]
        self.assertEqual(len(summaries), 1, "摘要应作为系统消息进入模型输入")

        detail = client.get(
            f"/api/sessions/{session_id}", headers={"X-Desktop-Token": "t"}
        ).json()
        self.assertEqual(detail["summary"], "压缩后的摘要")
        self.assertGreater(detail["summary_count"], 0)


class ManualCompactApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = WORKSPACE / f".test_manual_compact_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.store = SessionStore(self.root / ".sessions")
        self.headers = {"X-Desktop-Token": "t"}

    def _seed(self, count: int) -> str:
        session = self.store.create("手动压缩")
        session.messages = make_messages(count)
        self.store.save(session)
        return session.id

    def test_compact_endpoint_forces_compaction(self):
        session_id = self._seed(20)
        app = create_app(
            root=self.root, token="t", summarizer=lambda messages, key: "手动摘要"
        )
        client = TestClient(app)

        response = client.post(
            f"/api/sessions/{session_id}/compact", headers=self.headers
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["summary"], "手动摘要")
        self.assertEqual(len(body["messages"]), 10)
        self.assertEqual(body["summary_count"], 10)

    def test_compact_endpoint_rejects_short_sessions(self):
        session_id = self._seed(4)
        app = create_app(
            root=self.root, token="t", summarizer=lambda messages, key: "摘要"
        )
        client = TestClient(app)

        response = client.post(
            f"/api/sessions/{session_id}/compact", headers=self.headers
        )

        self.assertEqual(response.status_code, 400)

    def test_compact_endpoint_reports_failure(self):
        session_id = self._seed(20)

        def boom(messages, key):
            raise RuntimeError("模型不可用")

        app = create_app(root=self.root, token="t", summarizer=boom)
        client = TestClient(app)

        response = client.post(
            f"/api/sessions/{session_id}/compact", headers=self.headers
        )

        self.assertEqual(response.status_code, 502)


if __name__ == "__main__":
    unittest.main()
