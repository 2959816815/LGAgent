from __future__ import annotations

import json
import shutil
import unittest
import uuid
from pathlib import Path

from mcp_import import parse_import_payload
from mcp_manager import McpManager


class ImportParseTests(unittest.TestCase):
    def test_parses_stdio_and_remote_entries(self):
        payload = {
            "mcpServers": {
                "local": {"command": "npx", "args": ["-y", "pkg"], "env": {"A": "1"}},
                "remote": {
                    "url": "https://example.com/mcp",
                    "type": "sse",
                    "headers": {"Authorization": "Bearer x"},
                },
            }
        }

        entries, errors = parse_import_payload(payload, existing_ids=set())

        self.assertEqual(errors, [])
        self.assertEqual(entries[0]["transport"], "stdio")
        self.assertEqual(entries[0]["command"], ["npx", "-y", "pkg"])
        self.assertEqual(entries[1]["transport"], "sse")
        self.assertEqual(entries[1]["id"], "remote")

    def test_conflicting_ids_get_suffixes(self):
        entries, errors = parse_import_payload(
            {"mcpServers": {"demo": {"url": "https://example.com/mcp"}}},
            existing_ids={"demo"},
        )

        self.assertEqual(errors, [])
        self.assertEqual(entries[0]["id"], "demo-2")
        self.assertTrue(entries[0]["conflict"])

    def test_invalid_entry_fails_all(self):
        entries, errors = parse_import_payload(
            {
                "mcpServers": {
                    "ok": {"url": "https://example.com/mcp"},
                    "bad": {"url": "file:///tmp/x"},
                }
            },
            existing_ids=set(),
        )

        self.assertTrue(errors)
        self.assertEqual(entries, [])

    def test_manager_import_adds_servers(self):
        root = Path(__file__).resolve().parent.parent / f".test_mcp_import_{uuid.uuid4().hex[:8]}"
        root.mkdir()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        manager = McpManager(root, startup_timeout=1.0, tool_timeout=1.0)
        try:
            entries, errors = parse_import_payload(
                {"mcpServers": {"demo": {"command": ["definitely-not-real"]}}},
                existing_ids=set(),
            )
            self.assertEqual(errors, [])
            statuses = manager.import_servers(entries)
            self.assertEqual(statuses[0].id, "demo")
            self.assertTrue(any(item.id == "demo" for item in manager.list_servers()))
        finally:
            manager.stop_all()
