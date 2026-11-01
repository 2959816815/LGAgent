r"""SessionStore 持久化层的单元测试（标准库 unittest）。

运行（本机建议用含 LangChain 的 rag 环境）：
    D:\Anaconda3\envs\rag\python.exe -m unittest -v tests.test_session_store
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from session_store import (
    DEFAULT_TITLE,
    SessionStore,
    SessionStoreError,
    clean_title,
)
from session_store_sqlite import SqliteSessionStore

SESSION_ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")


class SessionStoreTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent.parent / f".test_sessions_{uuid.uuid4().hex[:8]}"
        self.now = datetime(2026, 10, 23, 19, 0, 0, tzinfo=timezone(timedelta(hours=8)))
        self.store = SessionStore(self.root, clock=self._clock)

    def _clock(self):
        current = self.now
        self.now += timedelta(seconds=1)
        return current

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_create_session_file_and_defaults(self):
        session = self.store.create()
        self.assertRegex(session.id, SESSION_ID_RE)
        self.assertEqual(session.title, DEFAULT_TITLE)
        self.assertEqual(session.messages, [])
        path = self.root / f"{session.id}.json"
        self.assertTrue(path.exists())
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["id"], session.id)

    def test_title_is_cleaned_and_truncated(self):
        session = self.store.create("  a\n b \x00 " + "x" * 80)
        self.assertTrue(session.title.startswith("a b"))
        self.assertLessEqual(len(session.title), 60)
        self.assertNotIn("\n", session.title)

    def test_save_and_load_message_roundtrip(self):
        session = self.store.create("工作")
        session.messages = [
            HumanMessage(content="读文件"),
            AIMessage(
                content="",
                tool_calls=[{"name": "read_file", "args": {"path": "a.txt"}, "id": "call-1"}],
            ),
            ToolMessage(content="[文件] a.txt", tool_call_id="call-1"),
            AIMessage(content="读完了"),
        ]
        self.store.save(session)
        loaded = self.store.load(session.id)
        self.assertEqual([m.type for m in loaded.messages], ["human", "ai", "tool", "ai"])
        self.assertEqual(loaded.messages[0].content, "读文件")
        self.assertEqual(loaded.messages[1].tool_calls[0]["name"], "read_file")
        self.assertEqual(loaded.messages[2].tool_call_id, "call-1")
        self.assertEqual(loaded.updated_at, session.updated_at)

    def test_dangling_tool_call_is_removed_on_save_and_load(self):
        session = self.store.create("中断")
        session.messages = [
            HumanMessage(content="浏览网页"),
            AIMessage(
                content="我来试试。",
                tool_calls=[{"name": "web_fetch", "args": {"url": "https://example.com"}, "id": "call-x"}],
            ),
            HumanMessage(content="继续"),
        ]
        self.store.save(session)
        loaded = self.store.load(session.id)
        self.assertEqual([m.type for m in loaded.messages], ["human", "ai", "human"])
        self.assertEqual(loaded.messages[1].content, "我来试试。")
        self.assertEqual(loaded.messages[1].tool_calls, [])

    def test_orphan_tool_message_is_removed(self):
        session = self.store.create("孤立工具")
        session.messages = [
            HumanMessage(content="你好"),
            ToolMessage(content="工具结果", tool_call_id="call-none"),
        ]
        self.store.save(session)
        loaded = self.store.load(session.id)
        self.assertEqual([m.type for m in loaded.messages], ["human"])

    def test_incomplete_multi_tool_call_is_removed(self):
        session = self.store.create("多工具")
        session.messages = [
            HumanMessage(content="做两件事"),
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "a", "args": {}, "id": "call-a"},
                    {"name": "b", "args": {}, "id": "call-b"},
                ],
            ),
            ToolMessage(content="a ok", tool_call_id="call-a"),
            HumanMessage(content="算了"),
        ]
        self.store.save(session)
        loaded = self.store.load(session.id)
        self.assertEqual([m.type for m in loaded.messages], ["human", "human"])

    def test_system_message_not_persisted(self):
        session = self.store.create()
        session.messages = [SystemMessage(content="旧系统提示"), HumanMessage(content="你好")]
        self.store.save(session)
        raw = json.loads((self.root / f"{session.id}.json").read_text(encoding="utf-8"))
        self.assertNotIn("system", [item["type"] for item in raw["messages"]])
        loaded = self.store.load(session.id)
        self.assertEqual([m.type for m in loaded.messages], ["human"])

    def test_list_sessions_sorted_by_updated_at(self):
        first = self.store.create("A")
        first.messages = [HumanMessage(content="a")]
        self.store.save(first)
        second = self.store.create("B")
        second.messages = [HumanMessage(content="b1"), HumanMessage(content="b2")]
        self.store.save(second)
        metas = self.store.list_sessions()
        self.assertEqual([m.id for m in metas], [second.id, first.id])
        self.assertEqual(metas[0].message_count, 2)
        self.assertEqual(metas[1].title, "A")

    def test_active_pointer_set_get_clear(self):
        session = self.store.create()
        self.store.set_active_id(session.id)
        self.assertEqual(self.store.get_active_id(), session.id)
        self.store.set_active_id(None)
        self.assertIsNone(self.store.get_active_id())
        with self.assertRaises(SessionStoreError):
            self.store.set_active_id("../bad")

    def test_delete_session_and_clear_active(self):
        session = self.store.create()
        self.store.set_active_id(session.id)
        self.store.delete(session.id)
        self.assertFalse((self.root / f"{session.id}.json").exists())
        self.assertIsNone(self.store.get_active_id())
        with self.assertRaises(SessionStoreError):
            self.store.delete(session.id)

    def test_invalid_session_id_rejected(self):
        with self.assertRaises(SessionStoreError):
            self.store.load("../outside")
        with self.assertRaises(SessionStoreError):
            self.store.load("20261023-190000-zzzz")

    def test_corrupt_file_skipped_by_list(self):
        bad = self.root / "20261023-190000-dead.json"
        bad.write_text("{not json", encoding="utf-8")
        self.assertEqual(self.store.list_sessions(), [])
        with self.assertRaises(SessionStoreError):
            self.store.load("20261023-190000-dead")

    def test_load_active_or_create_falls_back_to_newest(self):
        first = self.store.create("A")
        second = self.store.create("B")
        self.store.set_active_id(first.id)
        self.store.delete(first.id)
        session = self.store.load_active_or_create()
        self.assertEqual(session.id, second.id)
        self.assertEqual(self.store.get_active_id(), second.id)

    def test_empty_store_creates_active_session(self):
        session = self.store.load_active_or_create()
        self.assertTrue((self.root / f"{session.id}.json").exists())
        self.assertEqual(self.store.get_active_id(), session.id)

    def test_atomic_write_failure_preserves_original(self):
        session = self.store.create("A")
        path = self.root / f"{session.id}.json"
        original = path.read_text(encoding="utf-8")
        session.messages = [HumanMessage(content="new")]
        with patch("session_store.os.replace", side_effect=OSError("boom")):
            with self.assertRaises(SessionStoreError):
                self.store.save(session)
        self.assertEqual(path.read_text(encoding="utf-8"), original)
        self.assertEqual(list(self.root.glob(".*.tmp")), [])

    def test_clean_title_handles_control_characters(self):
        self.assertEqual(clean_title("  a\n\tb \x00 "), "a b")


class SummaryCoverageStoreTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent.parent / f".test_summary_cover_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def test_json_store_roundtrips_summary_coverage(self):
        store = SessionStore(self.root / ".sessions")
        session = store.create("覆盖")
        session.summary = "旧摘要"
        session.summary_upto = 3
        session.summary_updated_at = "2026-11-01T00:00:00+08:00"
        store.save(session)

        loaded = store.load(session.id)

        self.assertEqual(loaded.summary_upto, 3)
        self.assertEqual(loaded.summary_updated_at, "2026-11-01T00:00:00+08:00")
        self.assertEqual(loaded.summary_count, 0)

    def test_old_json_session_defaults_coverage_to_zero(self):
        store = SessionStore(self.root / ".sessions")
        session = store.create("旧会话")
        path = self.root / ".sessions" / f"{session.id}.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw.pop("summary_upto", None)
        raw.pop("summary_updated_at", None)
        path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

        loaded = store.load(session.id)

        self.assertEqual(loaded.summary_upto, 0)
        self.assertEqual(loaded.summary_updated_at, "")

    def test_sqlite_store_roundtrips_summary_coverage(self):
        store = SqliteSessionStore(self.root / "agent.db")
        session = store.create("覆盖")
        session.summary = "旧摘要"
        session.summary_upto = 5
        session.summary_updated_at = "2026-11-01T00:00:00+08:00"
        store.save(session)

        loaded = store.load(session.id)

        self.assertEqual(loaded.summary_upto, 5)
        self.assertEqual(loaded.summary_updated_at, "2026-11-01T00:00:00+08:00")

    def test_sqlite_adds_columns_to_old_database(self):
        db = self.root / "old.db"
        connection = sqlite3.connect(db)
        connection.execute(
            "CREATE TABLE sessions ("
            "id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL, "
            "updated_at TEXT NOT NULL, model_key TEXT, "
            "message_count INTEGER NOT NULL DEFAULT 0, "
            "summary TEXT NOT NULL DEFAULT '', "
            "summary_count INTEGER NOT NULL DEFAULT 0)"
        )
        connection.execute(
            "INSERT INTO sessions VALUES('20260101-000000-abcd', '旧', "
            "'2026-01-01T00:00:00+08:00', '2026-01-01T00:00:00+08:00', "
            "'local', 0, '', 0)"
        )
        connection.commit()
        connection.close()

        store = SqliteSessionStore(db)
        loaded = store.load("20260101-000000-abcd")

        self.assertEqual(loaded.summary_upto, 0)
        self.assertEqual(loaded.summary_updated_at, "")


if __name__ == "__main__":
    unittest.main()
