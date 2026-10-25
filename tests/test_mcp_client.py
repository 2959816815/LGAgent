from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

from mcp_client import McpError, McpTimeout, StdioMcpClient


ROOT = Path(__file__).resolve().parents[1]
FAKE = ROOT / "tests" / "fake_mcp_server.py"


def make_client(**kwargs) -> StdioMcpClient:
    options = {
        "startup_timeout": 5.0,
        "tool_timeout": 5.0,
        "server_name": "fake",
    }
    options.update(kwargs)
    return StdioMcpClient(
        [sys.executable, str(FAKE)],
        cwd=ROOT,
        **options,
    )


class McpClientTests(unittest.TestCase):
    def test_start_handshake_and_list_tools(self) -> None:
        client = make_client()
        try:
            client.start()
            self.assertTrue(client.alive)
            names = [tool.name for tool in client.tools]
            self.assertEqual(names, ["echo", "slow", "crash"])
            echo = client.tools[0]
            self.assertTrue(echo.read_only)
            self.assertEqual(echo.input_schema["type"], "object")
        finally:
            client.close()

    def test_call_echo(self) -> None:
        client = make_client()
        try:
            client.start()
            text = client.call_tool("echo", {"text": "hi"})
            self.assertIn("参数", text)
            self.assertIn("hi", text)
        finally:
            client.close()

    def test_unknown_tool_returns_error(self) -> None:
        client = make_client()
        try:
            client.start()
            with self.assertRaises(McpError) as ctx:
                client.call_tool("unknown-tool", {})
            self.assertIn("unknown tool", str(ctx.exception))
        finally:
            client.close()

    def test_call_timeout(self) -> None:
        client = make_client(tool_timeout=0.5)
        try:
            client.start()
            started = time.monotonic()
            with self.assertRaises(McpTimeout):
                client.call_tool("slow", {"seconds": 1.2})
            self.assertLess(time.monotonic() - started, 2.5)
        finally:
            client.close()

    def test_crash_and_stderr(self) -> None:
        client = make_client()
        try:
            client.start()
            with self.assertRaises(McpError):
                client.call_tool("crash", {})
            time.sleep(0.3)
            self.assertFalse(client.alive)
            self.assertTrue(any("fake stderr" in line for line in client.stderr_tail))
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main()
