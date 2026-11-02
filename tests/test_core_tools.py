from __future__ import annotations

import unittest

from langchain_core.tools import StructuredTool

from graph_agent import CORE_TOOL_NAMES, TOOLS, collect_tools


class CoreToolTests(unittest.TestCase):
    def test_local_model_gets_only_core_tools(self):
        tools = collect_tools(model_key="local")
        names = {tool.name for tool in tools}

        self.assertEqual(
            names, set(CORE_TOOL_NAMES) - {"search_knowledge_base"}
        )
        self.assertIn("read_file", names)
        self.assertNotIn("read_docx", names)

    def test_cloud_model_gets_all_builtin_tools(self):
        tools = collect_tools(model_key="deepseek")
        names = {tool.name for tool in tools}

        self.assertEqual(names, {tool.name for tool in TOOLS})
        self.assertIn("read_docx", names)


class CoreExtraToolTests(unittest.TestCase):
    def test_local_keeps_core_extra_tools_only(self):
        def echo():
            return "ok"

        kb = StructuredTool.from_function(
            func=echo,
            name="search_knowledge_base",
            description="知识库",
        )
        mcp = StructuredTool.from_function(
            func=echo,
            name="mcp__demo__echo",
            description="MCP",
        )

        tools = collect_tools(
            model_key="local", extra_tools=[kb, mcp]
        )
        names = {tool.name for tool in tools}

        self.assertIn("search_knowledge_base", names)
        self.assertNotIn("mcp__demo__echo", names)
