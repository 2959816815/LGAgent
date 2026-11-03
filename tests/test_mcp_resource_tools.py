from __future__ import annotations

import unittest

from mcp_resources import McpResource
from mcp_tools import build_mcp_tools


class FakeRemoteClient:
    def __init__(self):
        self._resources = [McpResource("demo://readme", "README", "说明", "text/plain")]

    @property
    def capabilities(self):
        return {"tools": True, "resources": True, "prompts": False}

    @property
    def resources(self):
        return list(self._resources)

    def read_resource(self, uri):
        if uri != "demo://readme":
            raise RuntimeError("not found")
        return {"text": "hello resource", "binary": False, "size": 14}

    def call_tool(self, name, arguments):
        return "tool-result"


class FakeManager:
    def get_status(self, server_id):
        return {"status": "running"}

    def tool_specs(self, server_id):
        return []

    def client_for(self, server_id):
        return FakeRemoteClient()

    def permission_for(self, server_id, tool=None):
        return "auto"


class ResourceToolTests(unittest.TestCase):
    def test_lists_resources(self):
        tools = {tool.name: tool for tool in build_mcp_tools(FakeManager(), ["demo"])}

        output = tools["mcp__demo__list_resources"].invoke({})

        self.assertIn("demo://readme", output)
        self.assertIn("README", output)

    def test_reads_a_listed_resource(self):
        tools = {tool.name: tool for tool in build_mcp_tools(FakeManager(), ["demo"])}

        output = tools["mcp__demo__read_resource"].invoke({"uri": "demo://readme"})

        self.assertIn("hello resource", output)

    def test_rejects_an_unlisted_uri(self):
        tools = {tool.name: tool for tool in build_mcp_tools(FakeManager(), ["demo"])}

        output = tools["mcp__demo__read_resource"].invoke({"uri": "demo://secret"})

        self.assertIn("不在资源列表中", output)


if __name__ == "__main__":
    unittest.main()
