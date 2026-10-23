from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from graph_agent import active_model, build_graph


class FakeLLM:
    def __init__(self):
        self.tools = None

    def bind_tools(self, tools):
        self.tools = tools
        return self


class GraphModelTests(unittest.TestCase):
    def test_build_graph_forwards_model_key(self):
        with patch("graph_agent.build_llm", return_value=FakeLLM()) as build:
            graph = build_graph(
                manager=None,
                include_subagent_tools=False,
                model_key="qwen",
            )
        build.assert_called_once_with("qwen")
        self.assertIsNotNone(graph)

    def test_active_model_uses_requested_key(self):
        with patch.dict(os.environ, {"DEEPSEEK_MODEL": "deepseek-flash"}, clear=False):
            provider, model, base_url = active_model("deepseek")
        self.assertEqual(provider, "deepseek")
        self.assertEqual(model, "deepseek-flash")
        self.assertTrue(base_url.startswith("https://api.deepseek.com"))


if __name__ == "__main__":
    unittest.main()
