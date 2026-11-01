"""Context view and rolling summary API tests."""

from __future__ import annotations

import os
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage

from desktop_api import create_app
from session_store import SessionStore


def make_messages(count: int, size: int = 400) -> list:
    messages = []
    for index in range(count):
        factory = HumanMessage if index % 2 == 0 else AIMessage
        messages.append(factory(content="内容" * size))
    return messages


class FakeModel:
    def __init__(self, text: str = "摘要内容", error: Exception | None = None):
        self.text = text
        self.error = error

    def invoke(self, messages):
        if self.error:
            raise self.error
        return AIMessage(content=self.text)


class EchoGraph:
    def stream(self, payload, *args, **kwargs):
        yield ("values", {"messages": [*payload["messages"], AIMessage(content="回答")]})


class ContextApiTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent.parent / f".test_context_api_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.env = patch.dict(os.environ, {"OLLAMA_CONTEXT_WINDOW": "16384"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.store = SessionStore(self.root / ".sessions")
        self.headers = {"X-Desktop-Token": "t"}

    def _app(self, model=None):
        return create_app(
            root=self.root,
            token="t",
            graph_builder=lambda manager, model_key, extra_tools=None: EchoGraph(),
            model_factory=lambda key: model or FakeModel(),
        )

    def test_run_keeps_full_history_when_view_is_trimmed(self):
        session = self.store.create("长会话", model_key="local")
        session.messages = make_messages(30)
        self.store.save(session)
        client = TestClient(self._app())

        response = client.post(
            f"/api/sessions/{session.id}/messages",
            json={"text": "继续"},
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200)
        detail = client.get(f"/api/sessions/{session.id}", headers=self.headers).json()
        self.assertEqual(detail["message_count"], 32)
        self.assertGreater(detail["context"]["dropped_turns"], 0)
        self.assertTrue(detail["context"]["fits"])

    def test_compact_updates_summary_without_deleting_messages(self):
        session = self.store.create("长会话", model_key="local")
        session.messages = make_messages(30)
        self.store.save(session)
        client = TestClient(self._app())

        response = client.post(
            f"/api/sessions/{session.id}/compact", headers=self.headers
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["message_count"], 30)
        self.assertGreater(body["summary_upto"], 0)
        self.assertTrue(body["summary"])
        self.assertEqual(body["summary_model"], "local")

    def test_compact_rejects_short_sessions(self):
        session = self.store.create("短会话", model_key="local")
        session.messages = make_messages(4)
        self.store.save(session)
        client = TestClient(self._app())

        response = client.post(
            f"/api/sessions/{session.id}/compact", headers=self.headers
        )

        self.assertEqual(response.status_code, 400)

    def test_compact_reports_summary_failure(self):
        session = self.store.create("长会话", model_key="local")
        session.messages = make_messages(30)
        self.store.save(session)
        client = TestClient(
            self._app(FakeModel(error=RuntimeError("模型不可用")))
        )

        response = client.post(
            f"/api/sessions/{session.id}/compact", headers=self.headers
        )

        self.assertEqual(response.status_code, 502)

    def test_session_detail_exposes_context_and_summary_coverage(self):
        session = self.store.create("详情", model_key="local")
        session.messages = make_messages(4)
        self.store.save(session)
        client = TestClient(self._app())

        body = client.get(f"/api/sessions/{session.id}", headers=self.headers).json()

        self.assertEqual(body["summary_upto"], 0)
        self.assertIn("context", body)
        self.assertEqual(body["context"]["window"], 16384)
