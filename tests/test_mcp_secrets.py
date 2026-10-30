"""MCP 密钥加密存储测试：mcp_servers.json 里不应出现明文 Token。"""

from __future__ import annotations

import json
import shutil
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import secret_store
from mcp_manager import McpManager

WORKSPACE = Path(__file__).resolve().parent.parent


class FakeClient:
    captured_env: dict[str, str] = {}

    def __init__(self, command, cwd=None, env=None, **kwargs):
        FakeClient.captured_env = dict(env or {})
        self.tools = []
        self.alive = True
        self.stderr_tail = []

    def start(self) -> None:
        pass

    def close(self) -> None:
        self.alive = False


class SecretStoreTests(unittest.TestCase):
    def test_plaintext_passes_through(self) -> None:
        self.assertEqual(secret_store.unprotect("plain-value"), "plain-value")
        self.assertEqual(secret_store.protect(""), "")

    @unittest.skipUnless(sys.platform == "win32", "DPAPI 仅 Windows")
    def test_roundtrip_hides_plaintext(self) -> None:
        blob = secret_store.protect("ghp_secret_value")

        self.assertTrue(blob.startswith("dpapi:"))
        self.assertNotIn("ghp_secret_value", blob)
        self.assertEqual(secret_store.unprotect(blob), "ghp_secret_value")


class McpSecretStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = WORKSPACE / f".test_mcp_secret_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _config_text(self) -> str:
        return (self.root / "mcp_servers.json").read_text(encoding="utf-8")

    @unittest.skipUnless(sys.platform == "win32", "DPAPI 仅 Windows")
    def test_secret_is_encrypted_at_rest_and_decrypted_for_launch(self) -> None:
        with patch("mcp_manager.StdioMcpClient", FakeClient):
            manager = McpManager(self.root, startup_timeout=1)
            manager.add_server(
                "github",
                env_values={"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_secret_value"},
            )

        raw = self._config_text()
        self.assertNotIn("ghp_secret_value", raw)
        self.assertIn("dpapi:", raw)
        self.assertEqual(
            FakeClient.captured_env.get("GITHUB_PERSONAL_ACCESS_TOKEN"),
            "ghp_secret_value",
        )

    @unittest.skipUnless(sys.platform == "win32", "DPAPI 仅 Windows")
    def test_plaintext_config_is_migrated_on_load(self) -> None:
        config = {
            "servers": [
                {
                    "id": "demo",
                    "name": "Demo",
                    "enabled": False,
                    "command": [sys.executable, "-c", "pass"],
                    "cwd": ".",
                    "env": {"DEMO_TOKEN": "legacy-token"},
                    "permission": "ask",
                }
            ]
        }
        (self.root / "mcp_servers.json").write_text(
            json.dumps(config, ensure_ascii=False), encoding="utf-8"
        )

        with patch("mcp_manager.StdioMcpClient", FakeClient):
            manager = McpManager(self.root, startup_timeout=1)
            manager.set_enabled("demo", True)

        self.assertNotIn("legacy-token", self._config_text())
        self.assertEqual(FakeClient.captured_env.get("DEMO_TOKEN"), "legacy-token")

    def test_non_secret_values_stay_readable(self) -> None:
        with patch("mcp_manager.StdioMcpClient", FakeClient):
            manager = McpManager(self.root, startup_timeout=1)
            manager.add_server(
                "sqlite", env_values={"MCP_SQLITE_DB": "D:/data/demo.db"}
            )

        self.assertIn("D:/data/demo.db", self._config_text())


if __name__ == "__main__":
    unittest.main()
