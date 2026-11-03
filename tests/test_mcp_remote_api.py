from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from desktop_api import create_app
from mcp_import import parse_import_payload
from mcp_manager import McpManager


class RemoteApiTests(unittest.TestCase):
    def setUp(self):
        from tests.fastmcp_test_server import FastMcpServer

        self.server = FastMcpServer("streamable-http")
        self.server.__enter__()
        self.addCleanup(self._stop_server)
        self.root = Path(__file__).resolve().parent.parent / f".test_mcp_remote_api_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.manager = McpManager(self.root, startup_timeout=10.0, tool_timeout=10.0)
        self.addCleanup(self.manager.stop_all)
        self.app = create_app(root=self.root, token="t", mcp_manager=self.manager)
        self.client = TestClient(self.app)
        self.headers = {"X-Desktop-Token": "t"}

    def _stop_server(self):
        self.server.__exit__()

    def test_add_remote_and_read_resources_and_prompts(self):
        response = self.client.post(
            "/api/mcp/servers",
            json={
                "kind": "remote",
                "id": "demo",
                "name": "Demo",
                "url": self.server.url,
                "transport": "streamable-http",
                "headers": {"Authorization": "Bearer test-token"},
                "permission": "ask",
            },
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        status = response.json()["server"]
        self.assertEqual(status["transport"], "streamable-http")
        self.assertEqual(status["status"], "running")

        resources = self.client.get(
            "/api/mcp/servers/demo/resources", headers=self.headers
        ).json()
        self.assertTrue(any(item["uri"] == "demo://readme" for item in resources["resources"]))

        content = self.client.get(
            "/api/mcp/servers/demo/resources/read",
            params={"uri": "demo://readme"},
            headers=self.headers,
        ).json()
        self.assertEqual(content["text"], "hello resource")

        prompts = self.client.get(
            "/api/mcp/servers/demo/prompts", headers=self.headers
        ).json()
        self.assertTrue(any(item["name"] == "summarize" for item in prompts["prompts"]))

        prompt = self.client.post(
            "/api/mcp/servers/demo/prompts/get",
            json={"name": "summarize", "arguments": {"text": "内容"}},
            headers=self.headers,
        ).json()
        self.assertIn("请总结", prompt["messages"][0]["text"])

    def test_test_connection_endpoint(self):
        response = self.client.post(
            "/api/mcp/servers/test",
            json={
                "url": self.server.url,
                "transport": "streamable-http",
                "headers": {"Authorization": "Bearer test-token"},
            },
            headers=self.headers,
        )
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertGreaterEqual(body["tools"], 1)

    def test_import_preview_and_confirm(self):
        payload = {
            "mcpServers": {
                "demo-remote": {
                    "url": self.server.url,
                    "type": "streamable-http",
                    "headers": {"Authorization": "Bearer test-token"},
                }
            }
        }
        preview = self.client.post(
            "/api/mcp/import",
            json={"payload": payload, "confirm": False},
            headers=self.headers,
        ).json()
        self.assertEqual(preview["errors"], [])
        self.assertEqual(preview["entries"][0]["id"], "demo-remote")

        imported = self.client.post(
            "/api/mcp/import",
            json={"payload": payload, "confirm": True},
            headers=self.headers,
        ).json()
        self.assertEqual(imported["imported"][0]["id"], "demo-remote")


if __name__ == "__main__":
    unittest.main()
