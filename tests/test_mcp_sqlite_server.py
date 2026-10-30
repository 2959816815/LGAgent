"""SQLite MCP server 的查询守卫测试。"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from mcp_sqlite_server import describe_table, list_tables, query


class SqliteMcpServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp()
        self.db = os.path.join(self.dir, "demo.db")
        connection = sqlite3.connect(self.db)
        connection.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
        connection.execute("INSERT INTO notes(body) VALUES('x;y')")
        connection.commit()
        connection.close()
        self.env = patch.dict(os.environ, {"MCP_SQLITE_DB": self.db})
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_semicolon_inside_string_literal_is_allowed(self) -> None:
        result = query("SELECT 'a;b' AS v")

        self.assertIn("a;b", result)
        self.assertNotIn("[错误]", result)

    def test_multiple_statements_are_rejected(self) -> None:
        result = query("SELECT 1; SELECT 2")

        self.assertIn("一次只能执行一条语句", result)

    def test_leading_comment_is_allowed(self) -> None:
        result = query("-- 备注\nSELECT body FROM notes")

        self.assertIn("x;y", result)
        self.assertNotIn("[错误]", result)

    def test_write_statement_is_rejected(self) -> None:
        result = query("DELETE FROM notes")

        self.assertIn("只允许只读查询", result)

    def test_table_helpers_work(self) -> None:
        self.assertIn("notes", list_tables())
        self.assertIn("body", describe_table("notes"))


if __name__ == "__main__":
    unittest.main()
