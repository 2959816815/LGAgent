"""A local FastMCP server used by remote MCP tests."""

from __future__ import annotations

import socket
import sys
import threading
import time

import uvicorn
from mcp.server.fastmcp import FastMCP


class _AuthMiddleware:
    """Pure ASGI middleware so SSE responses are not buffered."""

    def __init__(self, app, token: str):
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = {
                key.decode("latin-1").lower(): value.decode("latin-1")
                for key, value in scope.get("headers", [])
            }
            if headers.get("authorization") != f"Bearer {self.token}":
                body = b'{"error":"unauthorized"}'
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode()),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class FastMcpServer:
    def __init__(
        self,
        transport: str = "streamable-http",
        token: str = "test-token",
        port: int | None = None,
    ):
        self.transport = transport
        self.token = token
        self.port = port
        self.mcp = FastMCP("demo")
        self._register()

    def _register(self) -> None:
        @self.mcp.tool()
        def echo(text: str) -> str:
            """回显输入。"""
            return text

        @self.mcp.resource("demo://readme")
        def readme() -> str:
            """演示资源。"""
            return "hello resource"

        @self.mcp.prompt()
        def summarize(text: str) -> str:
            """总结输入。"""
            return f"请总结：{text}"

    def _app(self):
        app = (
            self.mcp.streamable_http_app()
            if self.transport == "streamable-http"
            else self.mcp.sse_app()
        )
        app.add_middleware(_AuthMiddleware, token=self.token)
        return app

    def __enter__(self) -> "FastMcpServer":
        self.port = self.port or _free_port()
        config = uvicorn.Config(
            self._app(), host="127.0.0.1", port=self.port, log_level="error"
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.time() + 10
        while not self.server.started and time.time() < deadline:
            time.sleep(0.05)
        if not self.server.started:
            raise RuntimeError("FastMCP test server did not start")
        return self

    def __exit__(self, *args) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5)

    @property
    def path(self) -> str:
        return "/mcp" if self.transport == "streamable-http" else "/sse"

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}{self.path}"


if __name__ == "__main__":
    requested_port = int(sys.argv[1]) if len(sys.argv) > 1 else None
    with FastMcpServer(
        "streamable-http", token="test-token", port=requested_port
    ) as demo:
        print(demo.url)
        input("Press Enter to stop...\n")
