from __future__ import annotations

import unittest

from graph_agent import CORE_TOOL_NAMES, TOOLS, collect_tools


class CoreToolTests(unittest.TestCase):
    def test_local_model_gets_only_core_tools(self):
        tools = collect_tools(model_key="local")
        names = {tool.name for tool in tools}

        self.assertEqual(names, set(CORE_TOOL_NAMES))
        self.assertIn("read_file", names)
        self.assertNotIn("read_docx", names)

    def test_cloud_model_gets_all_builtin_tools(self):
        tools = collect_tools(model_key="deepseek")
        names = {tool.name for tool in tools}

        self.assertEqual(names, {tool.name for tool in TOOLS})
        self.assertIn("read_docx", names)
