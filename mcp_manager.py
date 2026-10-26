"""MCP server configuration and process lifecycle management."""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp_catalog import catalog_items, get_preset
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
_ENV_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_SERVER_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class McpManager:
    def __init__(
        self,
        root: Path,
        max_tools: int = 64,
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
            raw = {
                "servers": [
                    {
                        "id": "playwright",
                        "name": "浏览器（Playwright）",
                        "enabled": True,
                        "command": [
                            "npx",
                            "-y",
                            "@playwright/mcp@latest",
                            "--headless",
                        ],
                        "cwd": ".",
                        "env": {},
                        "permission": "ask",
                    }
                ]
            }
            return raw
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
        # 配置可能在应用运行期间被编辑；重启时重新读取，避免继续使用旧 env/command。
        self._raw = self._load_config()
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

    def catalog(self) -> list[dict]:
        """返回内置服务目录，并标记当前配置中是否已存在。"""
        installed = {str(item.get("id")) for item in self._servers()}
        result: list[dict] = []
        for item in catalog_items():
            entry = dict(item)
            entry["installed"] = item["id"] in installed
            result.append(entry)
        return result

    def add_server(
        self,
        preset_id: str,
        env_values: dict[str, str] | None = None,
        permission: str | None = None,
        server_id: str | None = None,
        name: str | None = None,
    ) -> McpServerStatus:
        """按目录模板新增一个 server，并立即尝试启动。"""
        try:
            preset = get_preset(preset_id)
        except KeyError as exc:
            raise McpManagerError(f"未知的 MCP 模板: {preset_id}") from exc

        target_id = str(server_id or preset["id"]).strip().lower()
        if not _SERVER_ID.fullmatch(target_id):
            raise McpManagerError("MCP server id 只能包含小写字母、数字、下划线和短横线")
        if any(item.get("id") == target_id for item in self._servers()):
            raise McpManagerError(f"MCP server 已存在: {target_id}")

        values = {str(k): str(v) for k, v in (env_values or {}).items()}
        fields = preset.get("fields") or []
        env: dict[str, str] = {str(k): str(v) for k, v in (preset.get("env") or {}).items()}
        for field in fields:
            key = str(field["key"])
            value = values.get(key, str(field.get("default") or "")).strip()
            if field.get("required") and not value:
                raise McpManagerError(f"缺少必填配置: {field.get('label') or key}")
            env[key] = value

        # 允许覆盖模板 env 中非表单字段（例如临时目录）。
        for key, value in values.items():
            if key not in env:
                env[key] = value

        for key, value in list(env.items()):
            env[key] = self._expand_env(value, f"{target_id}.{key}")

        chosen_permission = str(permission or preset.get("permission") or "ask")
        if chosen_permission not in _VALID_PERMISSIONS:
            raise McpManagerError("permission 只能是 auto、ask 或 deny")

        item = {
            "id": target_id,
            "name": str(name or preset.get("name") or target_id),
            "enabled": True,
            "command": [self._expand_env(str(part), f"{target_id}.command") for part in preset["command"]],
            "cwd": str(preset.get("cwd") or "."),
            "env": env,
            "permission": chosen_permission,
        }
        self._raw.setdefault("servers", []).append(item)
        self._save_config()
        try:
            self._launch(target_id)
        except (McpError, McpManagerError) as exc:
            # 配置已保留，让用户能在面板里看到失败原因并修改配置后重试。
            self._set_failure(target_id, f"MCP 启动失败: {exc}")
        return self._status(item)

    def remove_server(self, server_id: str) -> None:
        self._find(server_id)
        self._stop(server_id)
        self._raw["servers"] = [
            item for item in self._servers() if item.get("id") != server_id
        ]
        self._save_config()

    def set_permission(self, server_id: str, permission: str) -> McpServerStatus:
        if permission not in _VALID_PERMISSIONS:
            raise McpManagerError("permission 只能是 auto、ask 或 deny")
        item = self._find(server_id)
        item["permission"] = permission
        self._save_config()
        return self._status(item)

    @staticmethod
    def _expand_env(value: str, context: str) -> str:
        missing: list[str] = []

        def replace(match: re.Match[str]) -> str:
            key = match.group(1)
            resolved = os.environ.get(key)
            if resolved is None:
                missing.append(key)
                return match.group(0)
            return resolved

        expanded = _ENV_PLACEHOLDER.sub(replace, value)
        if missing:
            names = "、".join(dict.fromkeys(missing))
            raise McpManagerError(f"{context} 缺少环境变量: {names}")
        return expanded

    def enabled_ids(self) -> list[str]:
        """返回当前启用且可参与 Agent 的工具服务器 id。"""
        return [status.id for status in self.list_servers() if status.enabled]

    def get_status(self, server_id: str) -> dict[str, Any]:
        """兼容 mcp_tools 的状态查询接口。"""
        item = self._find(server_id)
        return {"id": server_id, "status": self._status(item).status}

    def tool_specs(self, server_id: str) -> list[McpToolSpec]:
        """返回某服务器已发现的工具规格。"""
        return list(self.client_for(server_id).tools)

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

        command = [
            self._expand_env(str(part), f"{server_id}.command")
            for part in item["command"]
        ]
        cwd = (self.root / self._expand_env(str(item.get("cwd") or "."), f"{server_id}.cwd")).resolve()
        if command and ("/" in command[0] or "\\" in command[0]):
            command[0] = str((cwd / command[0]).resolve())
        env = {
            str(key): self._expand_env(str(value), f"{server_id}.env.{key}")
            for key, value in (item.get("env") or {}).items()
        }
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
