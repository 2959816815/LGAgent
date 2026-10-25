from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage

from desktop_api import create_app
from mcp_client import McpToolSpec
from mcp_manager import McpServerStatus


class FakeClient:
    def call_tool(self, name, arguments):
        return "mcp-result"


class FakeMcp:
    def __init__(self, fail_startup: bool = False) -> None:
        self.fail_startup = fail_startup
        self.restart_calls = []
        self.enabled_calls = []
        self.extra_tools_seen = []
        self.status = McpServerStatus(
            id="playwright",
            name="浏览器（Playwright）",
            enabled=True,
            status="running",
            permission="ask",
            tool_count=1,
            tools=["browser_navigate"],
        )

    def start_enabled(self):
        if self.fail_startup:
            raise RuntimeError("boom")
        self.start_called = True

    def stop_all(self):
        self.stopped = True

    def list_servers(self):
        return [self.status]

    def restart(self, server_id):
        self.restart_calls.append(server_id)
        return self.status

    def set_enabled(self, server_id, enabled):
        self.enabled_calls.append((server_id, enabled))
        return self.status

    def enabled_ids(self):
        return ["playwright"]

    def get_status(self, server_id):
        return {"id": server_id, "status": "running"}

    def tool_specs(self, server_id):
        return [
            McpToolSpec("browser_navigate", "打开网页", {"type": "object", "properties": {"url": {"type": "string"}}}, False)
        ]

    def client_for(self, server_id):
        return FakeClient()

    def permission_for(self, server_id, tool=None):
        return "ask"


class CapturingGraph:
    def __init__(self, extra_tools):
        self.extra_tools = extra_tools

    def stream(self, *args, **kwargs):
        yield ("values", {"messages": [HumanMessage(content="hi"), AIMessage(content="ok")]})


class McpApiTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent.parent / f".test_mcp_api_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.headers = {"X-Desktop-Token": "t"}

    def _app(self, mcp=None, graph_builder=None):
        return create_app(
            root=self.root,
            token="t",
            mcp_manager=mcp or FakeMcp(),
            graph_builder=graph_builder or (lambda manager, model_key, extra_tools=None: CapturingGraph(extra_tools)),
        )

    def test_list_servers(self):
        client = TestClient(self._app())
        response = client.get("/api/mcp/servers", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["servers"][0]["id"], "playwright")

    def test_restart(self):
        mcp = FakeMcp()
        client = TestClient(self._app(mcp=mcp))
        response = client.post("/api/mcp/servers/playwright/restart", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mcp.restart_calls, ["playwright"])

    def test_enable(self):
        mcp = FakeMcp()
        client = TestClient(self._app(mcp=mcp))
        response = client.post(
            "/api/mcp/servers/playwright/enable",
            json={"enabled": False},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mcp.enabled_calls, [("playwright", False)])

    def test_graph_receives_mcp_tools(self):
        captured = []

        def builder(manager, model_key, extra_tools=None):
            captured.append(extra_tools)
            return CapturingGraph(extra_tools)

        client = TestClient(self._app(graph_builder=builder))
        session = client.post(
            "/api/sessions", json={"title": "MCP"}, headers=self.headers
        ).json()
        response = client.post(
            f"/api/sessions/{session['id']}/messages",
            json={"text": "hi"},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(captured)
        self.assertEqual(captured[0][0].name, "mcp__playwright__browser_navigate")

    def test_startup_failure_is_isolated(self):
        mcp = FakeMcp(fail_startup=True)
        with TestClient(self._app(mcp=mcp)) as client:
            self.assertEqual(client.get("/api/health").status_code, 200)
        self.assertTrue(mcp.stopped)


if __name__ == "__main__":
    unittest.main()
