from __future__ import annotations

import shutil
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from subagent_manager import SubagentError, SubagentManager, _pid_alive
from subagent_store import SubagentStore, now_iso


class PidProbeTests(unittest.TestCase):
    def test_probe_does_not_terminate_running_process(self):
        import subprocess
        import sys

        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            time.sleep(0.5)
            self.assertIsNone(child.poll(), "子进程应仍在运行")
            self.assertTrue(_pid_alive(child.pid))
            time.sleep(0.3)
            self.assertIsNone(child.poll(), "检查存活不应终止目标进程")
        finally:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)


class SubagentManagerTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent.parent / f".test_manager_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()
        self.worker = Path(__file__).resolve().parent.parent / "subagent_fake_worker.py"
        self.manager = SubagentManager(self.root, worker_path=self.worker)

    def tearDown(self):
        self.manager.shutdown()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_spawn_records_session_id(self):
        record = self.manager.spawn("归属会话的任务", session_id="s-42")
        self.assertEqual(self.manager.store.load(record.id).session_id, "s-42")

    def test_cancel_stops_running_agent(self):
        record = self.manager.spawn("slow 任务")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and self.manager.store.load(record.id).pid is None:
            time.sleep(0.05)

        cancelled = self.manager.cancel(record.id)

        self.assertEqual(cancelled.status, "cancelled")
        time.sleep(0.3)
        self.assertEqual(self.manager.store.load(record.id).status, "cancelled")

    def test_cancel_unknown_agent_raises(self):
        with self.assertRaises(SubagentError):
            self.manager.cancel("agent-20260101-000000-abcd")

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

    def test_startup_does_not_replay_existing_terminal_record(self):
        old = self.manager.store.create(
            "旧任务", agent_id="agent-20261023-201500-beef"
        )
        old.status = "succeeded"
        old.result = "旧结果"
        self.manager.store.save(old)

        fresh = SubagentManager(self.root, worker_path=self.worker)
        fresh.refresh()
        self.assertEqual(fresh.pop_notifications(), [])
        fresh.shutdown()

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

    def test_frozen_spawn_uses_embedded_worker(self):
        with patch("subagent_manager.subprocess.Popen") as popen:
            popen.return_value.pid = 1234
            popen.return_value.poll.return_value = None
            with patch.object(__import__("subagent_manager").sys, "frozen", True, create=True):
                self.manager.spawn("打包模式")
            command = popen.call_args.args[0]
        self.assertIn("--subagent-worker", command)

    def test_spawn_passes_model_key(self):
        self.manager.set_model_key("qwen")
        with patch("subagent_manager.subprocess.Popen") as popen:
            popen.return_value.pid = 1234
            popen.return_value.poll.return_value = None
            self.manager.spawn("检查模型")
            command = popen.call_args.args[0]
        self.assertIn("--model-key", command)
        self.assertEqual(command[command.index("--model-key") + 1], "qwen")

    def test_spawn_passes_allow_writes_only_when_requested(self):
        with patch("subagent_manager.subprocess.Popen") as popen:
            popen.return_value.pid = 1234
            popen.return_value.poll.return_value = None
            self.manager.spawn("只读任务")
            default_command = popen.call_args.args[0]
            self.manager.spawn("写文件任务", allow_writes=True)
            write_command = popen.call_args.args[0]
        self.assertNotIn("--allow-writes", default_command)
        self.assertIn("--allow-writes", write_command)


if __name__ == "__main__":
    unittest.main()
