"""MCP server configuration and process lifecycle management."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp_client import McpError, McpToolSpec, StdioMcpClient


class McpManagerError(RuntimeError):
    """MCP 配置或生命周期错误。"""


@dataclass
class McpServerStatus:
    id: str
    name: str
    enabled: bool
    status: str
    permission: str = "ask"
    tool_count: int = 0
    tools: list[str] = field(default_factory=list)
    last_error: str | None = None


@dataclass
class ManagedProcess:
    client: StdioMcpClient
    process: Any
    started_at: float


_VALID_PERMISSIONS = {"auto", "ask", "deny"}


class McpManager:
    def __init__(
        self,
        root: Path,
        max_tools: int = 32,
        startup_timeout: float = 30.0,
        tool_timeout: float = 60.0,
    ) -> None:
        self.root = Path(root).resolve()
        self.config_path = self.root / "mcp_servers.json"
        self.max_tools = max(1, int(max_tools))
        self.startup_timeout = float(startup_timeout)
        self.tool_timeout = float(tool_timeout)
        self._processes: dict[str, ManagedProcess] = {}
        self._raw = self._load_config()

    # ---------------------------------------------------------------- config
    def _load_config(self) -> dict:
        if not self.config_path.is_file():
            raise McpManagerError(f"缺少 MCP 配置文件: {self.config_path.name}")
        try:
            raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise McpManagerError(f"MCP 配置读取失败: {exc}") from exc
        if not isinstance(raw, dict) or not isinstance(raw.get("servers"), list):
            raise McpManagerError("MCP 配置格式错误：servers 必须是列表")
        for item in raw["servers"]:
            if not isinstance(item, dict) or not item.get("id"):
                raise McpManagerError("MCP server 缺少 id")
            if not isinstance(item.get("command"), list) or not item["command"]:
                raise McpManagerError(f"MCP server {item.get('id')} 缺少 command")
            permission = str(item.get("permission") or "ask")
            if permission not in _VALID_PERMISSIONS:
                raise McpManagerError(f"MCP server {item.get('id')} permission 无效")
        return raw

    def _servers(self) -> list[dict]:
        return list(self._raw.get("servers", []))

    def _find(self, server_id: str) -> dict:
        for item in self._servers():
            if item.get("id") == server_id:
                return item
        raise McpManagerError(f"MCP server 不存在: {server_id}")

    def _save_config(self) -> None:
        tmp = self.config_path.with_suffix(".json.tmp")
        clean = json.loads(json.dumps(self._raw))
        for item in clean.get("servers", []):
            item.pop("last_error", None)
        tmp.write_text(
            json.dumps(clean, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, self.config_path)

    # ---------------------------------------------------------------- lifecycle
    def start_enabled(self) -> None:
        for item in self._servers():
            if item.get("enabled", True):
                try:
                    self._launch(str(item["id"]))
                except McpError as exc:
                    if not item.get("last_error"):
                        self._set_failure(str(item["id"]), f"MCP 启动失败: {exc}")
                except Exception as exc:
                    if not item.get("last_error"):
                        self._set_failure(str(item["id"]), f"MCP 启动失败: {exc}")

    def stop_all(self) -> None:
        for server_id in list(self._processes):
            self._stop(server_id)

    def refresh(self) -> None:
        for server_id in list(self._processes):
            managed = self._processes.get(server_id)
            if managed is None:
                continue
            if not managed.client.alive:
                tail = "；".join(managed.client.stderr_tail[-3:])
                self._set_failure(
                    server_id,
                    f"MCP 服务已退出{(': ' + tail) if tail else ''}",
                    keep_client=True,
                )

    def restart(self, server_id: str) -> McpServerStatus:
        item = self._find(server_id)
        self._stop(server_id)
        if item.get("enabled", True):
            self._launch(server_id)
        return self._status(item)

    def set_enabled(self, server_id: str, enabled: bool) -> McpServerStatus:
        item = self._find(server_id)
        item["enabled"] = bool(enabled)
        self._save_config()
        if enabled:
            self._launch(server_id)
        else:
            self._stop(server_id)
        return self._status(item)

    def list_servers(self) -> list[McpServerStatus]:
        self.refresh()
        return [self._status(item) for item in self._servers()]

    def client_for(self, server_id: str) -> StdioMcpClient:
        item = self._find(server_id)
        if not item.get("enabled", True):
            raise McpManagerError(f"MCP server 已禁用: {server_id}")
        self.refresh()
        managed = self._processes.get(server_id)
        if managed is None or not managed.client.alive:
            raise McpManagerError(f"MCP server 未运行: {server_id}")
        return managed.client

    def permission_for(self, server_id: str, tool: McpToolSpec) -> str:
        item = self._find(server_id)
        if tool.read_only:
            return "allow"
        value = str(item.get("permission") or "ask")
        return value if value in _VALID_PERMISSIONS else "ask"

    # ---------------------------------------------------------------- internals
    def _launch(self, server_id: str) -> McpServerStatus:
        item = self._find(server_id)
        if not item.get("enabled", True):
            return self._status(item)
        existing = self._processes.get(server_id)
        if existing is not None and existing.client.alive:
            return self._status(item)
        if existing is not None:
            self._stop(server_id)

        command = [str(part) for part in item["command"]]
        cwd = (self.root / str(item.get("cwd") or ".")).resolve()
        if command and ("/" in command[0] or "\\" in command[0]):
            command[0] = str((cwd / command[0]).resolve())
        env = item.get("env") or {}
        client = StdioMcpClient(
            command,
            cwd=cwd,
            env={str(k): str(v) for k, v in env.items()},
            startup_timeout=self.startup_timeout,
            tool_timeout=self.tool_timeout,
            server_name=server_id,
        )
        try:
            client.start()
            if len(client.tools) > self.max_tools:
                raise McpError(
                    f"工具数 {len(client.tools)} 超过上限 {self.max_tools}"
                )
        except Exception as exc:
            tail = "；".join(client.stderr_tail[-3:])
            client.close()
            self._set_failure(
                server_id,
                f"MCP 启动失败: {exc}{(': ' + tail) if tail else ''}",
            )
            raise McpManagerError(f"MCP server 启动失败: {server_id}") from exc

        self._processes[server_id] = ManagedProcess(
            client=client,
            process=getattr(client, "_proc", None),
            started_at=time.time(),
        )
        item.pop("last_error", None)
        return self._status(item)

    def _stop(self, server_id: str) -> None:
        managed = self._processes.pop(server_id, None)
        if managed is not None:
            managed.client.close()

    def _set_failure(
        self, server_id: str, message: str, keep_client: bool = False
    ) -> None:
        item = self._find(server_id)
        item["last_error"] = message
        if not keep_client:
            managed = self._processes.pop(server_id, None)
            if managed is not None:
                managed.client.close()

    def _status(self, item: dict) -> McpServerStatus:
        server_id = str(item["id"])
        enabled = bool(item.get("enabled", True))
        managed = self._processes.get(server_id)
        tool_names: list[str] = []
        if managed is not None:
            tool_names = [tool.name for tool in managed.client.tools]
        if not enabled:
            status = "disabled"
        elif managed is not None and managed.client.alive:
            status = "running"
        elif item.get("last_error"):
            status = "failed"
        else:
            status = "stopped"
        return McpServerStatus(
            id=server_id,
            name=str(item.get("name") or server_id),
            enabled=enabled,
            status=status,
            permission=str(item.get("permission") or "ask"),
            tool_count=len(tool_names),
            tools=tool_names,
            last_error=item.get("last_error"),
        )


def write_default_config(root: Path) -> Path:
    """Create the default config used by a fresh checkout."""
    path = Path(root).resolve() / "mcp_servers.json"
    if not path.exists():
        path.write_text(
            json.dumps(
                {
                    "servers": [
                        {
                            "id": "playwright",
                            "name": "Playwright",
                            "enabled": True,
                            "command": ["npx", "-y", "@playwright/mcp@latest"],
                            "cwd": ".",
                            "permission": "ask",
                        },
                        {
                            "id": "fetch",
                            "name": "Fetch",
                            "enabled": False,
                            "command": ["uvx", "mcp-server-fetch"],
                            "cwd": ".",
                            "permission": "ask",
                        },
                    ]
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return path
