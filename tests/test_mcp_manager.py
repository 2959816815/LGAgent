from __future__ import annotations

import json
import shutil
import sys
import time
import unittest
import uuid
from pathlib import Path

from mcp_client import McpError, McpToolSpec
from mcp_manager import McpManager, McpManagerError


ROOT = Path(__file__).resolve().parents[1]
FAKE = ROOT / "tests" / "fake_mcp_server.py"


class McpManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = ROOT / "tests" / f".mcp-manager-{uuid.uuid4().hex[:8]}"
        self.root.mkdir()
        self.managers: list[McpManager] = []

    def tearDown(self) -> None:
        for manager in self.managers:
            manager.stop_all()
        shutil.rmtree(self.root, ignore_errors=True)

    def _config(self, servers: list[dict]) -> Path:
        path = self.root / "mcp_servers.json"
        path.write_text(
            json.dumps({"servers": servers}, ensure_ascii=False), encoding="utf-8"
        )
        return path

    def _fake(self, server_id: str = "demo", enabled: bool = True, **extra) -> dict:
        server = {
            "id": server_id,
            "name": server_id.title(),
            "enabled": enabled,
            "command": [sys.executable, str(FAKE)],
            "cwd": str(ROOT),
            "permission": "ask",
        }
        server.update(extra)
        return server

    def _manager(self, max_tools: int = 32) -> McpManager:
        manager = McpManager(self.root, max_tools=max_tools, startup_timeout=5.0, tool_timeout=5.0)
        self.managers.append(manager)
        return manager

    def test_disabled_server_does_not_start(self) -> None:
        self._config([self._fake("demo", True), self._fake("off", False)])
        manager = self._manager()
        manager.start_enabled()
        statuses = {item.id: item for item in manager.list_servers()}
        self.assertEqual(statuses["demo"].status, "running")
        self.assertEqual(statuses["off"].status, "disabled")

    def test_enable_starts_server(self) -> None:
        self._config([self._fake("demo", False)])
        manager = self._manager()
        manager.start_enabled()
        self.assertEqual({s.id: s for s in manager.list_servers()}["demo"].status, "disabled")
        status = manager.set_enabled("demo", True)
        self.assertEqual(status.status, "running")
        self.assertIn("echo", manager.client_for("demo").tools[0].name)

    def test_restart_replaces_client(self) -> None:
        self._config([self._fake()])
        manager = self._manager()
        manager.start_enabled()
        first = manager.client_for("demo")
        manager.restart("demo")
        second = manager.client_for("demo")
        self.assertIsNot(first, second)
        self.assertTrue(second.alive)

    def test_crash_marks_failed(self) -> None:
        self._config([self._fake()])
        manager = self._manager()
        manager.start_enabled()
        client = manager.client_for("demo")
        with self.assertRaises(McpError):
            client.call_tool("crash", {})
        time.sleep(0.3)
        manager.refresh()
        status = {s.id: s for s in manager.list_servers()}["demo"]
        self.assertEqual(status.status, "failed")
        self.assertTrue(status.last_error)

    def test_startup_failure_is_visible(self) -> None:
        command = [sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"]
        self._config(
            [
                {
                    "id": "bad",
                    "name": "Bad",
                    "enabled": True,
                    "command": command,
                    "cwd": str(ROOT),
                }
            ]
        )
        manager = self._manager()
        manager.start_enabled()
        status = manager.list_servers()[0]
        self.assertEqual(status.status, "failed")
        self.assertIn("MCP 启动失败", status.last_error or "")

    def test_max_tools_enforced(self) -> None:
        self._config([self._fake()])
        manager = self._manager(max_tools=1)
        manager.start_enabled()
        status = manager.list_servers()[0]
        self.assertEqual(status.status, "failed")
        self.assertIn("工具数", status.last_error or "")

    def test_permission_rule(self) -> None:
        self._config([self._fake(permission="ask")])
        manager = self._manager()
        read_only = McpToolSpec("read", "read", {}, read_only=True)
        write = McpToolSpec("write", "write", {}, read_only=False)
        self.assertEqual(manager.permission_for("demo", read_only), "allow")
        self.assertEqual(manager.permission_for("demo", write), "ask")

    def test_disable_stops_server(self) -> None:
        self._config([self._fake()])
        manager = self._manager()
        manager.start_enabled()
        status = manager.set_enabled("demo", False)
        self.assertEqual(status.status, "disabled")
        with self.assertRaises(McpManagerError):
            manager.client_for("demo")


if __name__ == "__main__":
    unittest.main()
