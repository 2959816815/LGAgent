from __future__ import annotations

import json
import shutil
import unittest
import uuid
from pathlib import Path

from mcp_manager import (
    McpManager,
    McpManagerError,
    _header_is_secret,
    _sanitize,
    _transport,
)
from secret_store import is_protected


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent.parent / f".test_mcp_config_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.manager = McpManager(self.root, startup_timeout=1.0, tool_timeout=1.0)

    def _write(self, servers):
        (self.root / "mcp_servers.json").write_text(
            json.dumps({"servers": servers}, ensure_ascii=False), encoding="utf-8"
        )

    def test_legacy_entry_defaults_to_stdio(self):
        self.assertEqual(_transport({"command": ["python"]}), "stdio")

    def test_remote_requires_url_and_accepts_headers(self):
        self._write(
            [
                {
                    "id": "remote",
                    "name": "Remote",
                    "enabled": True,
                    "transport": "streamable-http",
                    "url": "https://example.com/mcp",
                    "headers": {"Authorization": "Bearer plain"},
                    "permission": "ask",
                }
            ]
        )

        manager = McpManager(self.root, startup_timeout=1.0, tool_timeout=1.0)
        item = manager._find("remote")

        self.assertEqual(_transport(item), "streamable-http")
        self.assertTrue(is_protected(item["headers"]["Authorization"]))

    def test_remote_rejects_non_http_url(self):
        self._write(
            [
                {
                    "id": "remote",
                    "name": "Remote",
                    "enabled": True,
                    "transport": "sse",
                    "url": "file:///tmp/x",
                    "permission": "ask",
                }
            ]
        )

        with self.assertRaises(McpManagerError):
            McpManager(self.root, startup_timeout=1.0, tool_timeout=1.0)

    def test_secret_header_detection(self):
        self.assertTrue(_header_is_secret("Authorization", set()))
        self.assertTrue(_header_is_secret("X-Api-Token", set()))
        self.assertTrue(_header_is_secret("X-Custom", {"x-custom"}))
        self.assertFalse(_header_is_secret("X-Trace", set()))

    def test_sanitize_removes_authorization_and_tokens(self):
        text = "failed Authorization: Bearer abc token=xyz"

        cleaned = _sanitize(text)

        self.assertNotIn("abc", cleaned)
        self.assertNotIn("xyz", cleaned)
