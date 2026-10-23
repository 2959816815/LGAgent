from __future__ import annotations

import shutil
import time
import unittest
import uuid
from pathlib import Path

from subagent_manager import SubagentError, SubagentManager
from subagent_store import SubagentStore, now_iso


class SubagentManagerTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent.parent / f".test_manager_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()
        self.worker = Path(__file__).resolve().parent.parent / "subagent_fake_worker.py"
        self.manager = SubagentManager(self.root, worker_path=self.worker)

    def tearDown(self):
        self.manager.shutdown()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_spawn_and_wait_success(self):
        record = self.manager.spawn("整理周报")
        finished = self.manager.wait(record.id, timeout=5)
        self.assertEqual(finished.status, "succeeded")
        self.assertEqual(finished.result, "done:整理周报")
        self.assertTrue((self.root / ".agents" / f"{record.id}.json").exists())

    def test_failed_task(self):
        record = self.manager.spawn("fail 任务")
        finished = self.manager.wait(record.id, timeout=5)
        self.assertEqual(finished.status, "failed")
        self.assertIn("fake failure", finished.error or "")

    def test_crash_without_record_is_marked_failed(self):
        record = self.manager.spawn("crash 任务")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.manager.refresh()
            loaded = self.manager.store.load(record.id)
            if loaded.status == "failed":
                break
            time.sleep(0.05)
        self.assertEqual(loaded.status, "failed")
        self.assertIn("进程异常退出", loaded.error or "")

    def test_concurrency_limit(self):
        for i in range(3):
            self.manager.spawn(f"slow {i}")
        with self.assertRaises(SubagentError):
            self.manager.spawn("slow 4")

    def test_wait_timeout(self):
        record = self.manager.spawn("slow timeout")
        finished = self.manager.wait(record.id, timeout=1)
        self.assertEqual(finished.status, "running")

    def test_unknown_agent(self):
        with self.assertRaises(SubagentError):
            self.manager.wait("agent-20261023-201599-ffff")

    def test_notifications_only_once(self):
        record = self.manager.spawn("整理周报")
        self.manager.wait(record.id, timeout=5)
        first = self.manager.pop_notifications()
        self.assertTrue(any(record.id in item for item in first))
        self.assertEqual(self.manager.pop_notifications(), [])

    def test_startup_orphan_is_failed(self):
        store = SubagentStore(self.root / ".agents")
        orphan = store.create("遗留任务", agent_id="agent-20261023-201500-dead")
        orphan.status = "running"
        store.save(orphan)
        manager = SubagentManager(self.root, worker_path=self.worker)
        loaded = manager.store.load(orphan.id)
        self.assertEqual(loaded.status, "failed")
        self.assertIn("重启", loaded.error or "")
        manager.shutdown()


if __name__ == "__main__":
    unittest.main()
