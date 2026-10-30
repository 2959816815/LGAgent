"""MCP 预设目录测试：打包/开发两种形态下的默认值。"""

from __future__ import annotations

import sys
import unittest
from unittest.mock import patch

from mcp_catalog import get_preset


class McpCatalogTests(unittest.TestCase):
    def test_sqlite_requires_explicit_path_when_packaged(self) -> None:
        with patch.object(sys, "frozen", True, create=True):
            preset = get_preset("sqlite")

        self.assertEqual(preset["env"].get("MCP_SQLITE_DB"), "")
        self.assertEqual(preset["fields"][0]["default"], "")
        self.assertTrue(preset["fields"][0]["required"])

    def test_sqlite_default_never_points_at_session_database(self) -> None:
        with patch.object(sys, "frozen", True, create=True):
            preset = get_preset("sqlite")

        joined = " ".join(str(value) for value in preset["env"].values())
        self.assertNotIn("langgraph-agent", joined)

    def test_sqlite_dev_default_is_project_demo_db(self) -> None:
        with patch.object(sys, "frozen", False, create=True):
            preset = get_preset("sqlite")

        self.assertEqual(preset["fields"][0]["default"], "data/agent.db")

    def test_playwright_preset_is_usable_when_packaged(self) -> None:
        with patch.object(sys, "frozen", True, create=True):
            preset = get_preset("playwright")

        self.assertEqual(preset["command"][0], "npx")
        joined = " ".join(preset["command"]) + " " + " ".join(str(v) for v in preset["env"].values())
        self.assertNotIn("node_modules", joined)
        self.assertNotIn(".playwright-cache", joined)
        self.assertNotIn(".playwright-temp", joined)


if __name__ == "__main__":
    unittest.main()
