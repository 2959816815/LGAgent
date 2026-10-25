"""Tiny stdio MCP server used by the unit tests.

Usage: python fake_mcp_server.py [--stderr]
"""

from __future__ import annotations

import json
import sys
import time


TOOLS = [
    {
        "name": "echo",
        "description": "回显参数",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "slow",
        "description": "慢工具，用于超时测试",
        "inputSchema": {
            "type": "object",
            "properties": {"seconds": {"type": "number"}},
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "crash",
        "description": "让服务退出",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": False},
    },
]


def send(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def error(request_id, code: int, message: str) -> None:
    send({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})


def result(request_id, data: dict) -> None:
    send({"jsonrpc": "2.0", "id": request_id, "result": data})


def main() -> None:
    sys.stderr.write("fake stderr ready\n")
    sys.stderr.flush()

    for line in sys.stdin:
        text = line.strip()
        if not text:
            continue
        try:
            request = json.loads(text)
        except json.JSONDecodeError:
            continue
        method = request.get("method")
        request_id = request.get("id")

        if method == "initialize":
            result(
                request_id,
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fake-mcp", "version": "1.0"},
                },
            )
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            result(request_id, {"tools": TOOLS})
        elif method == "tools/call":
            params = request.get("params") or {}
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if name == "echo":
                result(
                    request_id,
                    {
                        "content": [
                            {
                                "type": "text",
                                "text": f"参数: {arguments.get('text', '')}",
                            }
                        ],
                        "isError": False,
                    },
                )
            elif name == "slow":
                time.sleep(float(arguments.get("seconds", 1.0)))
                result(
                    request_id,
                    {"content": [{"type": "text", "text": "慢工具完成"}]},
                )
            elif name == "crash":
                sys.exit(42)
            else:
                error(request_id, -32601, f"unknown tool: {name}")
        else:
            if request_id is not None:
                error(request_id, -32601, f"unknown method: {method}")


if __name__ == "__main__":
    main()
