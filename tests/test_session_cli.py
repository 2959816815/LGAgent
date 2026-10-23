# test_session_cli.py
from __future__ import annotations

import unittest

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from session_cli import (
    SessionCommandError,
    confirm,
    format_history,
    format_session_list,
    parse_command,
    resolve_session_target,
)
from session_store import Session, SessionMeta


class SessionCliTests(unittest.TestCase):
    def setUp(self):
        self.metas = [
            SessionMeta("20261023-190000-bbbb", "第二个", "2026-10-23T19:00:00+08:00", "2026-10-23T19:10:00+08:00", 4),
            SessionMeta("20261023-180000-aaaa", "第一个", "2026-10-23T18:00:00+08:00", "2026-10-23T18:10:00+08:00", 2),
        ]

    def test_parse_command(self):
        self.assertIsNone(parse_command("你好"))
        self.assertEqual(parse_command("/new 工作 记录"), ("new", "工作 记录"))
        self.assertEqual(parse_command("/sessions"), ("list", ""))
        self.assertEqual(parse_command("/HISTORY 2"), ("history", "2"))
        self.assertEqual(parse_command("/wat"), ("unknown", "/wat"))

    def test_resolve_session_target(self):
        self.assertEqual(resolve_session_target("1", self.metas), self.metas[0].id)
        self.assertEqual(resolve_session_target(self.metas[1].id, self.metas), self.metas[1].id)
        self.assertEqual(resolve_session_target("20261023-1800", self.metas), self.metas[1].id)
        with self.assertRaises(SessionCommandError):
            resolve_session_target("9", self.metas)
        with self.assertRaises(SessionCommandError):
            resolve_session_target("20261023", self.metas)

    def test_format_session_list_marks_active(self):
        text = format_session_list(self.metas, self.metas[0].id)
        self.assertIn("*", text)
        self.assertIn("1.", text)
        self.assertIn("第二个", text)
        self.assertIn("4 条", text)

    def test_format_empty_history(self):
        session = Session("20261023-190000-aaaa", "空会话", "", "", [])
        text = format_history(session)
        self.assertIn("[历史] 空会话", text)
        self.assertIn("还没有消息", text)

    def test_format_history_pages(self):
        messages = [HumanMessage(content=f"m{i}") for i in range(21)]
        session = Session("20261023-190000-aaaa", "分页", "", "", messages)
        first = format_history(session, page=1)
        second = format_history(session, page=2)
        self.assertIn("第 1/2 页（最新）", first)
        self.assertIn("m20", first)
        self.assertNotIn("m0", first)
        self.assertIn("第 2/2 页（较早）", second)
        self.assertIn("m0", second)

    def test_format_history_redacts_file_tool_content(self):
        messages = [
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "write_file",
                    "args": {"path": "D:\\secret.txt", "content": "TOP_SECRET"},
                    "id": "call-1",
                }],
            ),
            ToolMessage(content="[新建] D:\\secret.txt", tool_call_id="call-1"),
        ]
        session = Session("20261023-190000-aaaa", "脱敏", "", "", messages)
        text = format_history(session)
        self.assertIn("D:\\secret.txt", text)
        self.assertNotIn("TOP_SECRET", text)

    def test_format_history_truncates(self):
        messages = [HumanMessage(content="x" * 900)]
        session = Session("20261023-190000-aaaa", "截断", "", "", messages)
        text = format_history(session)
        self.assertIn("已截断", text)

    def test_confirm(self):
        self.assertTrue(confirm("?", lambda _: "y"))
        self.assertFalse(confirm("?", lambda _: ""))
        self.assertFalse(confirm("?", lambda _: (_ for _ in ()).throw(EOFError())))


if __name__ == "__main__":
    unittest.main()
