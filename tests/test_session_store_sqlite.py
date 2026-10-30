r"""SQLite 会话后端测试。

运行：
    D:\Anaconda3\envs\rag\python.exe -m unittest -v tests.test_session_store_sqlite
"""

from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from session_store import SessionStore
from session_store_sqlite import SqliteSessionStore

_WORKSPACE = Path(__file__).resolve().parent.parent


class SqliteSessionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = _WORKSPACE / f".test_sqlite_sessions_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()
        self.store = SqliteSessionStore(self.root / "agent.db")

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_search_finds_message_text(self) -> None:
        session = self.store.create("搜索测试")
        session.messages.append(HumanMessage(content="请帮我整理季度报告"))
        session.messages.append(AIMessage(content="好的，我来整理"))
        self.store.save(session)

        hits = self.store.search("季度报告")

        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["session_id"], session.id)
        self.assertEqual(hits[0]["message_index"], 0)
        self.assertEqual(hits[0]["role"], "human")
        self.assertIn("季度报告", hits[0]["snippet"])

    def test_search_follows_edits_and_deletes(self) -> None:
        session = self.store.create("编辑测试")
        session.messages.append(HumanMessage(content="旧的季度报告内容"))
        self.store.save(session)
        self.assertEqual(len(self.store.search("旧的季度报告")), 1)

        session.messages[0] = HumanMessage(content="全新的年度总结")
        self.store.save(session)
        self.assertEqual(self.store.search("旧的季度报告"), [])
        self.assertEqual(len(self.store.search("年度总结")), 1)

        self.store.delete(session.id)
        self.assertEqual(self.store.search("年度总结"), [])

    def test_create_save_load_roundtrip(self) -> None:
        session = self.store.create("  我的 会话  ", model_key="deepseek")
        session.messages.append(HumanMessage(content="你好"))
        session.messages.append(AIMessage(content="你好，有什么可以帮你？"))
        self.store.save(session)

        loaded = self.store.load(session.id)
        self.assertEqual(loaded.title, "我的 会话")
        self.assertEqual(loaded.model_key, "deepseek")
        self.assertEqual(len(loaded.messages), 2)
        self.assertEqual(loaded.messages[0].content, "你好")

    def test_list_sessions_orders_by_updated_at_and_counts(self) -> None:
        first = self.store.create("第一个")
        first.messages.append(HumanMessage(content="one"))
        self.store.save(first)
        second = self.store.create("第二个")
        second.messages.extend([HumanMessage(content="a"), AIMessage(content="b")])
        self.store.save(second)

        metas = self.store.list_sessions()
        self.assertEqual({meta.id for meta in metas}, {first.id, second.id})
        counts = {meta.id: meta.message_count for meta in metas}
        self.assertEqual(counts[second.id], 2)
        self.assertEqual(counts[first.id], 1)

    def test_delete_clears_active(self) -> None:
        session = self.store.create("待删除")
        self.store.set_active_id(session.id)
        self.store.delete(session.id)
        self.assertIsNone(self.store.get_active_id())
        self.assertFalse(any("active 会话 ID 无效" in warning for warning in self.store.pop_warnings()))
        with self.assertRaises(Exception):
            self.store.load(session.id)

    def test_active_session_roundtrip_and_fallback(self) -> None:
        session = self.store.create("活动会话")
        self.store.set_active_id(session.id)
        self.assertEqual(self.store.get_active_id(), session.id)
        self.assertEqual(self.store.load_active_or_create().id, session.id)

    def test_migrates_legacy_json_once(self) -> None:
        legacy_dir = self.root / ".sessions"
        legacy = SessionStore(legacy_dir)
        old = legacy.create("旧会话", model_key="local")
        old.messages.append(HumanMessage(content="旧消息"))
        legacy.save(old)
        legacy.set_active_id(old.id)

        db_path = self.root / "agent.db"
        migrated = SqliteSessionStore(db_path, legacy_dir=legacy_dir)
        loaded = migrated.load(old.id)
        self.assertEqual(loaded.title, "旧会话")
        self.assertEqual(loaded.model_key, "local")
        self.assertEqual(loaded.messages[0].content, "旧消息")
        self.assertEqual(migrated.get_active_id(), old.id)

        # 第二次打开不应重复导入，也不应重复 warning。
        again = SqliteSessionStore(db_path, legacy_dir=legacy_dir)
        self.assertEqual(len(again.list_sessions()), 1)
        self.assertFalse(any("已从旧 JSON" in warning for warning in again.pop_warnings()))

    def test_dangling_tool_call_is_removed(self) -> None:
        session = self.store.create("工具")
        session.messages.append(
            AIMessage(
                content="",
                tool_calls=[{"id": "call_1", "name": "get_current_time", "args": {}}],
            )
        )
        self.store.save(session)
        loaded = self.store.load(session.id)
        self.assertEqual(loaded.messages, [])

    def test_complete_tool_call_is_kept(self) -> None:
        session = self.store.create("工具")
        session.messages.extend(
            [
                AIMessage(
                    content="",
                    tool_calls=[{"id": "call_1", "name": "get_current_time", "args": {}}],
                ),
                ToolMessage(content="2026-01-01", tool_call_id="call_1"),
            ]
        )
        self.store.save(session)
        loaded = self.store.load(session.id)
        self.assertEqual(len(loaded.messages), 2)
        self.assertEqual(loaded.messages[0].tool_calls[0]["id"], "call_1")

    def test_invalid_id_is_rejected(self) -> None:
        with self.assertRaises(Exception):
            self.store.load("../../etc/passwd")

    def test_desktop_factory_uses_sqlite_when_enabled(self) -> None:
        from desktop_api import _create_session_store

        with patch.dict("os.environ", {"DSH_SESSION_STORE": "sqlite"}):
            store = _create_session_store(self.root)
        self.assertIsInstance(store, SqliteSessionStore)
        self.assertEqual(store.db_path, self.root / "agent.db")


if __name__ == "__main__":
    unittest.main()
