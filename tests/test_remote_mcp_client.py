from __future__ import annotations

import threading
import time
import unittest

from mcp_client import McpError
from remote_mcp_client import RemoteMcpClient, RemoteMcpConfig
from tests.fastmcp_test_server import FastMcpServer


def wait_for(predicate, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


class RemoteClientTests(unittest.TestCase):
    def _config(self, server: FastMcpServer, token: str = "test-token") -> RemoteMcpConfig:
        return RemoteMcpConfig(
            url=server.url,
            transport=server.transport,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10.0,
            connect_timeout=5.0,
            ping_interval=0.5,
        )

    def test_streamable_http_tools(self):
        with FastMcpServer("streamable-http") as server:
            client = RemoteMcpClient(self._config(server), server_name="demo")
            try:
                client.start()
                self.assertTrue(client.alive)
                names = [tool.name for tool in client.tools]
                self.assertIn("echo", names)
                self.assertEqual(client.call_tool("echo", {"text": "hello"}), "hello")
            finally:
                client.close()

    def test_sse_tools(self):
        with FastMcpServer("sse") as server:
            client = RemoteMcpClient(self._config(server), server_name="demo")
            try:
                client.start()
                self.assertIn("echo", [tool.name for tool in client.tools])
            finally:
                client.close()

    def test_bad_token_fails_startup(self):
        with FastMcpServer("streamable-http") as server:
            client = RemoteMcpClient(self._config(server, token="wrong"), server_name="demo")
            with self.assertRaises(McpError):
                client.start()
            client.close()

    def test_reconnects_after_server_restart(self):
        server = FastMcpServer("streamable-http")
        with server:
            client = RemoteMcpClient(self._config(server), server_name="demo")
            try:
                client.start()
                port = server.port
                server.__exit__()
                self.assertTrue(wait_for(lambda: client.state == "failed", 30))
                replacement = FastMcpServer("streamable-http")
                replacement.port = port
                # Start a replacement on the same port.
                import uvicorn
                replacement.server = uvicorn.Server(
                    uvicorn.Config(
                        replacement._app(), host="127.0.0.1", port=port, log_level="error"
                    )
                )
                replacement.thread = threading.Thread(
                    target=replacement.server.run, daemon=True
                )
                replacement.thread.start()
                try:
                    self.assertTrue(wait_for(lambda: client.alive, 30))
                finally:
                    replacement.server.should_exit = True
                    replacement.thread.join(timeout=5)
            finally:
                client.close()
