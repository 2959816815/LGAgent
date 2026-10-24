"""approval.py 的单元测试：diff、批准/拒绝、撤销。"""

from __future__ import annotations

import unittest
import uuid
from pathlib import Path

from approval import (
    ApprovalBroker,
    ApprovalDenied,
    UndoEmpty,
    build_preview,
    set_current,
    undo_last,
    wrap_tools,
)
from graph_agent import write_file


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.root = Path("tests") / f".approval_{uuid.uuid4().hex[:8]}"
        self.root.mkdir(parents=True)
        self.target = self.root / "note.txt"
        self.entries = []
        self.broker = ApprovalBroker(
            "run-test",
            "session-test",
            lambda event, data: None,
            self.entries,
        )
        set_current(self.broker)

    def tearDown(self):
        set_current(None)
        if self.root.exists():
            for path in self.root.iterdir():
                path.unlink()
            self.root.rmdir()

    def test_preview_shows_diff(self):
        self.target.write_text("旧内容\n", encoding="utf-8")
        preview = build_preview(
            "edit_file",
            {"path": str(self.target), "old_text": "旧内容", "new_text": "新内容"},
        )
        self.assertIn("-旧内容", preview["diff"])
        self.assertIn("+新内容", preview["diff"])

    def test_reject_blocks_write(self):
        self.target.write_text("before", encoding="utf-8")
        self.broker.emit = lambda event, data: self.broker.resolve(data["approval_id"], False)
        wrapped = wrap_tools([write_file])[0]
        result = wrapped.invoke({"path": str(self.target), "content": "after"})
        self.assertTrue(str(result).startswith("[错误]"))
        self.assertEqual(self.target.read_text(encoding="utf-8"), "before")
        self.assertEqual(self.entries, [])

    def test_approve_writes_and_undo_restores(self):
        self.target.write_text("before", encoding="utf-8")
        def approve(event, data):
            if event == "approval_request":
                self.broker.resolve(data["approval_id"], True)
        self.broker.emit = approve
        wrapped = wrap_tools([write_file])[0]
        wrapped.invoke({"path": str(self.target), "content": "after"})
        self.assertEqual(self.target.read_text(encoding="utf-8"), "after")
        self.assertEqual(len(self.entries), 1)
        restored = self.broker.undo_last()
        self.assertEqual(restored, self.target)
        self.assertEqual(self.target.read_text(encoding="utf-8"), "before")

    def test_undo_new_file_deletes_it(self):
        def approve(event, data):
            if event == "approval_request":
                self.broker.resolve(data["approval_id"], True)
        self.broker.emit = approve
        wrapped = wrap_tools([write_file])[0]
        wrapped.invoke({"path": str(self.target), "content": "new"})
        self.assertTrue(self.target.exists())
        self.broker.undo_last()
        self.assertFalse(self.target.exists())

    def test_undo_empty_raises(self):
        with self.assertRaises(UndoEmpty):
            undo_last([])


if __name__ == "__main__":
    unittest.main()
