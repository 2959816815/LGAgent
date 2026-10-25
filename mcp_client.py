"""Minimal stdio MCP (Model Context Protocol) client.

The client talks JSON-RPC 2.0 over a local subprocess.  It intentionally
supports only the subset needed by the desktop Agent:

    initialize -> notifications/initialized -> tools/list -> tools/call

No network transport is implemented.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class McpError(RuntimeError):
    """MCP 握手、协议或子进程错误。"""


class McpTimeout(McpError):
    """MCP 请求超时。"""


@dataclass(frozen=True)
class McpToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    read_only: bool = False


def _strip_nul(text: str) -> str:
    return text.replace("\x00", "")


class _LineReader:
    """Read stdout lines in a daemon thread and hand them to callers."""

    _SENTINEL = object()

    def __init__(self, stream) -> None:
        self._queue: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._read, args=(stream,), daemon=True)
        self._thread.start()

    def _read(self, stream) -> None:
        try:
            for line in stream:
                self._queue.put(line)
        except Exception:
            pass
        finally:
            self._queue.put(self._SENTINEL)

    def get(self, timeout: float):
        try:
            item = self._queue.get(timeout=max(0.0, timeout))
        except queue.Empty:
            return queue.Empty
        if item is self._SENTINEL:
            # Keep the sentinel available for subsequent reads.
            self._queue.put(self._SENTINEL)
            return None
        return item


class _StderrDrain:
    def __init__(self, stream) -> None:
        self._lines: list[str] = []
        self._error: str | None = None
        self._thread = threading.Thread(target=self._read, args=(stream,), daemon=True)
        self._thread.start()

    def _read(self, stream) -> None:
        try:
            while True:
                line = stream.readline()
                if not line:
                    break
                self._lines.append(line.rstrip(b"\r\n").decode("utf-8", errors="replace"))
                if len(self._lines) > 100:
                    del self._lines[:-100]
        except Exception as exc:
            self._error = repr(exc)

    def tail(self) -> list[str]:
        return list(self._lines)


class StdioMcpClient:
    """A synchronous MCP client for one local stdio server."""

    def __init__(
        self,
        command: list[str],
        cwd: Path,
        env: dict[str, str] | None = None,
        startup_timeout: float = 30.0,
        tool_timeout: float = 60.0,
        server_name: str = "mcp",
    ) -> None:
        if not command:
            raise ValueError("command 不能为空")
        self._command = list(command)
        self._cwd = Path(cwd).resolve()
        self._env = dict(env or {})
        self._startup_timeout = float(startup_timeout)
        self._tool_timeout = float(tool_timeout)
        self._server_name = server_name
        self._proc: subprocess.Popen | None = None
        self._lines: _LineReader | None = None
        self._stderr: _StderrDrain | None = None
        self._next_id = 0
        self._lock = threading.Lock()
        self._tools: list[McpToolSpec] = []

    # ---------------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        child_env = os.environ.copy()
        child_env.update(
            {
                "PYTHONUNBUFFERED": "1",
                "PYTHONIOENCODING": "utf-8",
                "MCP_SERVER_NAME": self._server_name,
            }
        )
        child_env.update(self._env)

        self._proc = subprocess.Popen(
            self._command,
            cwd=str(self._cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=child_env,
            text=False,
            bufsize=0,
        )
        self._lines = _LineReader(self._proc.stdout)
        self._stderr = _StderrDrain(self._proc.stderr)

        self._request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "langgraph-desktop", "version": "1.0"},
            },
            timeout=self._startup_timeout,
        )
        self._notify("notifications/initialized")
        self._tools = self._parse_tools(
            self._request("tools/list", {}, timeout=self._startup_timeout)
        )

    def close(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
        except Exception:
            pass
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass

    # ---------------------------------------------------------------- properties
    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def tools(self) -> list[McpToolSpec]:
        return list(self._tools)

    @property
    def stderr_tail(self) -> list[str]:
        return self._stderr.tail() if self._stderr is not None else []

    # ---------------------------------------------------------------- protocol
    def list_tools(self) -> list[McpToolSpec]:
        if not self.alive:
            raise McpError("MCP 服务未启动")
        try:
            result = self._request(
                "tools/list", {}, timeout=self._startup_timeout
            )
            self._tools = self._parse_tools(result)
            return list(self._tools)
        except McpTimeout:
            raise

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        if not self.alive:
            raise McpError("MCP 服务未启动")
        result = self._request(
            "tools/call",
            {"name": name, "arguments": dict(arguments)},
            timeout=self._tool_timeout,
        )
        if not isinstance(result, dict):
            raise McpError("MCP 工具返回格式错误")
        if result.get("isError"):
            raise McpError(self._text_from_content(result))
        return self._text_from_content(result)

    # ---------------------------------------------------------------- internals
    def _request(self, method: str, params: dict[str, Any], timeout: float):
        with self._lock:
            proc = self._proc
            if proc is None or proc.poll() is not None or proc.stdin is None:
                raise McpError("MCP 子进程已退出")
            self._next_id += 1
            request_id = f"r{self._next_id}"
            payload = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
            try:
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"
                proc.stdin.write(data)
                proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise McpError(f"MCP 写入失败: {exc}") from exc

            if self._lines is None:
                raise McpError("MCP 输出流未初始化")

            deadline = time.monotonic() + max(0.0, timeout)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise McpTimeout(f"MCP 请求超时: {method}")
                line = self._lines.get(remaining)
                if line is queue.Empty:
                    raise McpTimeout(f"MCP 请求超时: {method}")
                if line is None:
                    raise McpError("MCP 子进程已关闭输出")
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    message = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if not isinstance(message, dict):
                    continue
                if message.get("id") != request_id:
                    continue
                error = message.get("error")
                if error:
                    if isinstance(error, dict):
                        detail = error.get("message") or json.dumps(
                            error, ensure_ascii=False
                        )
                    else:
                        detail = str(error)
                    raise McpError(str(detail))
                return message.get("result")

    def _notify(self, method: str) -> None:
        proc = self._proc
        if proc is None or proc.poll() is not None or proc.stdin is None:
            raise McpError("MCP 子进程已退出")
        payload = {"jsonrpc": "2.0", "method": method, "params": {}}
        try:
            proc.stdin.write(
                json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"
            )
            proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise McpError(f"MCP 写入失败: {exc}") from exc

    def _parse_tools(self, result: Any) -> list[McpToolSpec]:
        if not isinstance(result, dict):
            raise McpError("MCP tools/list 返回格式错误")
        raw_tools = result.get("tools")
        if not isinstance(raw_tools, list):
            raise McpError("MCP tools/list 缺少 tools")
        parsed: list[McpToolSpec] = []
        for item in raw_tools:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            schema = item.get("inputSchema") or item.get("input_schema") or {}
            if not isinstance(schema, dict):
                schema = {}
            annotations = item.get("annotations")
            read_only = False
            if isinstance(annotations, dict):
                read_only = bool(annotations.get("readOnlyHint"))
            parsed.append(
                McpToolSpec(
                    name=name,
                    description=str(item.get("description") or ""),
                    input_schema=schema,
                    read_only=read_only,
                )
            )
        return parsed

    @staticmethod
    def _text_from_content(result: dict[str, Any]) -> str:
        content = result.get("content")
        if not isinstance(content, list):
            return str(result)
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
        if not parts:
            return str(result)
        return "\n".join(parts)
