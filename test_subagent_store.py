from __future__ import annotations

import json
import shutil
import unittest
import uuid
from pathlib import Path

from langchain_core.messages import HumanMessage

from subagent_store import (
    MAX_TASK_CHARS,
    AgentRecord,
    SubagentStore,
    SubagentStoreError,
    now_iso,
)


class SubagentStoreTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent / f".test_store_{uuid.uuid4().hex[:8]}"
        self.store = SubagentStore(self.root)
        self.record = self.store.create("整理周报", agent_id="agent-20261023-201500-a1b2")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_create_and_load(self):
        loaded = self.store.load(self.record.id)
        self.assertEqual(loaded.id, self.record.id)
        self.assertEqual(loaded.task, "整理周报")
        self.assertEqual(loaded.status, "running")
        self.assertTrue((self.root / f"{self.record.id}.json").exists())

    def test_save_round_trips_messages(self):
        self.record.messages = [HumanMessage(content="你好")]
        self.record.result = "完成"
        self.record.status = "succeeded"
        self.record.finished_at = now_iso()
        self.store.save(self.record)

        loaded = self.store.load(self.record.id)
        self.assertEqual(loaded.result, "完成")
        self.assertEqual(loaded.messages[0].content, "你好")
        self.assertEqual(loaded.status, "succeeded")

    def test_list_sorted_by_created_desc(self):
        second = self.store.create("B", agent_id="agent-20261023-201501-b2c3")
        second.created_at = "2026-10-23T20:15:01+08:00"
        self.record.created_at = "2026-10-23T20:15:00+08:00"
        self.store.save(second)
        self.store.save(self.record)

        records = self.store.list_records()
        self.assertEqual([r.id for r in records], [second.id, self.record.id])

    def test_corrupt_file_is_skipped_with_warning(self):
        bad = self.root / "agent-20261023-201502-c3d4.json"
        bad.write_text("{not json", encoding="utf-8")
        records = self.store.list_records()
        self.assertEqual(len(records), 1)
        self.assertTrue(any("损坏" in warning for warning in self.store.warnings))

    def test_rejects_invalid_status(self):
        self.record.status = "unknown"
        with self.assertRaises(SubagentStoreError):
            self.store.save(self.record)

    def test_rejects_too_long_task(self):
        with self.assertRaises(SubagentStoreError):
            self.store.create("x" * (MAX_TASK_CHARS + 1))

    def test_load_missing_raises(self):
        with self.assertRaises(SubagentStoreError):
            self.store.load("agent-20261023-201599-ffff")


if __name__ == "__main__":
    unittest.main()
