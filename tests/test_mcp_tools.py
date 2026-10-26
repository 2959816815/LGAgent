from __future__ import annotations

import unittest

import approval
from mcp_client import McpToolSpec
from mcp_tools import build_mcp_tools


class FakeClient:
    def __init__(self) -> None:
        self.calls = []

    def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return "mcp-result"


class FakeManager:
    def __init__(self, permission: str = "auto") -> None:
        self.permission = permission
        self.client = FakeClient()

    def get_status(self, server_id):
        return {"id": server_id, "status": "running"}

    def tool_specs(self, server_id):
        return [
            McpToolSpec("echo", "echo", {"type": "object", "properties": {"text": {"type": "string"}}}, False),
            McpToolSpec("read", "read only", {"type": "object", "properties": {}}, True),
        ]

    def client_for(self, server_id):
        return self.client

    def permission_for(self, server_id, tool=None):
        return self.permission


class FakeBroker:
    def __init__(self) -> None:
        self.called = None

    def request(self, name, args):
        self.called = (name, args)


class McpToolTests(unittest.TestCase):
    def tearDown(self) -> None:
        approval.set_current(None)

    def test_tool_name_and_schema(self) -> None:
        tools = build_mcp_tools(FakeManager(), ["demo"])
        by_name = {tool.name: tool for tool in tools}
        self.assertEqual(set(by_name), {"mcp__demo__echo", "mcp__demo__read"})
        tool = by_name["mcp__demo__echo"]
        self.assertIn("demo", tool.description)
        self.assertIn("text", tool.args_schema.model_fields)

    def test_read_only_is_exposed_as_auto(self) -> None:
        manager = FakeManager()
        manager.tool_specs = lambda server_id: [
            McpToolSpec("read", "read only", {"type": "object"}, True)
        ]
        tools = build_mcp_tools(manager, ["demo"])
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0].name, "mcp__demo__read")
        self.assertEqual(tools[0].metadata["permission"], "auto")

    def test_call_forwards_to_client(self) -> None:
        manager = FakeManager()
        tool = next(t for t in build_mcp_tools(manager, ["demo"]) if t.name.endswith("echo"))
        result = tool.invoke({"text": "hi"})
        self.assertEqual(result, "mcp-result")
        self.assertEqual(manager.client.calls, [("echo", {"text": "hi"})])

    def test_ask_permission(self) -> None:
        manager = FakeManager(permission="ask")
        broker = FakeBroker()
        approval.set_current(broker)
        tool = next(t for t in build_mcp_tools(manager, ["demo"]) if t.name.endswith("echo"))
        wrapped = approval.wrap_mcp_tools([tool])[0]
        result = wrapped.invoke({"text": "hi"})
        self.assertEqual(result, "mcp-result")
        self.assertEqual(broker.called[0], "mcp__demo__echo")

    def test_wrap_mcp_keeps_native_tools(self) -> None:
        class Native:
            name = "web_fetch"

        native = Native()
        manager = FakeManager()
        tools = build_mcp_tools(manager, ["demo"])
        echo = next(t for t in tools if t.name.endswith("echo"))
        wrapped = approval.wrap_mcp_tools([native, echo])
        self.assertIs(wrapped[0], native)
        self.assertEqual(wrapped[1].name, "mcp__demo__echo")


if __name__ == "__main__":
    unittest.main()
