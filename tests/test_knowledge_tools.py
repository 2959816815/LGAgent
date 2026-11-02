from __future__ import annotations

import unittest

from knowledge_tools import build_knowledge_tools


class FakeSearcher:
    def __init__(self, result):
        self.result = result

    def search(self, query, top_k=5):
        return self.result


class KnowledgeToolTests(unittest.TestCase):
    def _tool(self, result):
        return build_knowledge_tools(FakeSearcher(result))[0]

    def test_formats_results_with_sources(self):
        tool = self._tool(
            {
                "enabled": True,
                "embedding_used": True,
                "warning": "",
                "results": [
                    {
                        "path": "项目A/方案.md",
                        "absolute_path": "D:/资料/项目A/方案.md",
                        "heading": "架构",
                        "page": None,
                        "sheet": "",
                        "slide": None,
                        "score": 0.031,
                        "text": "蓝鲸计划。",
                    }
                ],
                "reason": "",
            }
        )

        output = tool.invoke({"query": "蓝鲸", "top_k": 1})

        self.assertIn("项目A/方案.md", output)
        self.assertIn("D:/资料/项目A/方案.md", output)
        self.assertIn("蓝鲸计划。", output)

    def test_disabled_message(self):
        tool = self._tool(
            {"enabled": False, "embedding_used": False, "warning": "", "results": [], "reason": "disabled"}
        )
        self.assertIn("未启用", tool.invoke({"query": "x"}))

    def test_empty_message(self):
        tool = self._tool(
            {"enabled": True, "embedding_used": True, "warning": "", "results": [], "reason": "empty"}
        )
        self.assertIn("还没有索引内容", tool.invoke({"query": "x"}))
