from __future__ import annotations

import json
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from mcp_manager import McpManager
from remote_mcp_client import RemoteMcpClient
from tests.fastmcp_test_server import FastMcpServer


class RemoteManagerTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent.parent / f".test_mcp_remote_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        # 空配置避免落回内置的 Playwright 默认项，测试只关注远程服务。
        (self.root / "mcp_servers.json").write_text(
            json.dumps({"servers": []}, ensure_ascii=False), encoding="utf-8"
        )

    def test_add_and_launch_remote_server(self):
        with FastMcpServer("streamable-http") as server:
            manager = McpManager(self.root, startup_timeout=10.0, tool_timeout=10.0)
            try:
                status = manager.add_remote_server(
                    {
                        "id": "demo",
                        "name": "Demo",
                        "url": server.url,
                        "transport": "streamable-http",
                        "headers": {"Authorization": "Bearer test-token"},
                        "permission": "ask",
                    }
                )
                self.assertEqual(status.transport, "streamable-http")
                self.assertEqual(status.status, "running")
                self.assertGreaterEqual(status.tool_count, 1)
                self.assertGreaterEqual(status.resource_count, 1)
                self.assertGreaterEqual(status.prompt_count, 1)
                self.assertTrue(status.capabilities["resources"])
            finally:
                manager.stop_all()

    def test_test_remote_server_does_not_save(self):
        with FastMcpServer("streamable-http") as server:
            manager = McpManager(self.root, startup_timeout=10.0, tool_timeout=10.0)
            try:
                result = manager.test_remote_server(
                    {
                        "url": server.url,
                        "transport": "streamable-http",
                        "headers": {"Authorization": "Bearer test-token"},
                    }
                )
                self.assertTrue(result["ok"])
                self.assertEqual(manager.list_servers(), [])
            finally:
                manager.stop_all()

    def test_resource_and_prompt_delegation(self):
        with FastMcpServer("streamable-http") as server:
            manager = McpManager(self.root, startup_timeout=10.0, tool_timeout=10.0)
            try:
                manager.add_remote_server(
                    {
                        "id": "demo",
                        "name": "Demo",
                        "url": server.url,
                        "transport": "streamable-http",
                        "headers": {"Authorization": "Bearer test-token"},
                    }
                )
                resources = manager.list_resources("demo")
                self.assertTrue(any(item.uri == "demo://readme" for item in resources))
                content = manager.read_resource("demo", "demo://readme")
                self.assertEqual(content["text"], "hello resource")
                prompts = manager.list_prompts("demo")
                self.assertTrue(any(item.name == "summarize" for item in prompts))
                result = manager.get_prompt("demo", "summarize", {"text": "内容"})
                self.assertIn("请总结", result["messages"][0].text)
            finally:
                manager.stop_all()
