"""Parse standard mcpServers JSON for the remote MCP import UI."""

from __future__ import annotations

import json
import re

_SERVER_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_TRANSPORTS = {
    "sse": "sse",
    "http": "streamable-http",
    "streamable-http": "streamable-http",
    "streamablehttp": "streamable-http",
}


class McpImportError(ValueError):
    pass


def _clean_id(key: str, used: set[str]) -> tuple[str, bool]:
    base = re.sub(r"[^a-z0-9_-]+", "-", key.lower()).strip("-")[:60] or "mcp"
    if not (base[0].isalpha() or base[0].isdigit()):
        base = f"mcp-{base}"
    if not _SERVER_ID.fullmatch(base):
        base = "mcp"
    target = base
    suffix = 2
    while target in used:
        target = f"{base}-{suffix}"
        suffix += 1
    return target, target != base


def _parse_entry(key: str, item, used: set[str]) -> dict:
    if not isinstance(item, dict):
        raise McpImportError("配置必须是对象")
    target, conflict = _clean_id(key, used)
    name = str(item.get("name") or key)
    permission = str(item.get("permission") or "ask")
    if "command" in item:
        command = item.get("command")
        args = item.get("args") or []
        if isinstance(command, str):
            command = [command] + [str(arg) for arg in args]
        elif isinstance(command, list):
            command = [str(part) for part in command]
        else:
            raise McpImportError("command 必须是字符串或数组")
        if not command:
            raise McpImportError("command 不能为空")
        env = item.get("env") or {}
        if not isinstance(env, dict):
            raise McpImportError("env 必须是对象")
        return {
            "id": target,
            "name": name,
            "transport": "stdio",
            "command": command,
            "cwd": str(item.get("cwd") or "."),
            "env": {str(k): str(v) for k, v in env.items()},
            "permission": permission,
            "conflict": conflict,
        }
    if "url" in item:
        url = str(item.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            raise McpImportError("url 必须是 http/https")
        raw_type = str(item.get("type") or item.get("transport") or "streamable-http").lower()
        transport = _TRANSPORTS.get(raw_type)
        if transport is None:
            raise McpImportError(f"未知传输类型: {raw_type}")
        headers = item.get("headers") or {}
        if not isinstance(headers, dict):
            raise McpImportError("headers 必须是对象")
        return {
            "id": target,
            "name": name,
            "transport": transport,
            "url": url,
            "headers": {str(k): str(v) for k, v in headers.items()},
            "verify_tls": bool(item.get("verify_tls", True)),
            "timeout": float(item.get("timeout") or 60),
            "permission": permission,
            "conflict": conflict,
        }
    raise McpImportError("缺少 command 或 url")


def parse_import_payload(payload, existing_ids: set[str]) -> tuple[list[dict], list[str]]:
    raw = payload
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            return [], [f"JSON 解析失败: {exc}"]
    if not isinstance(raw, dict):
        return [], ["JSON 顶层必须是对象"]
    servers = raw.get("mcpServers", raw)
    if not isinstance(servers, dict):
        return [], ["mcpServers 必须是对象"]
    entries: list[dict] = []
    errors: list[str] = []
    used = set(existing_ids)
    for key, item in servers.items():
        try:
            entry = _parse_entry(str(key), item, used)
            entries.append(entry)
            used.add(entry["id"])
        except McpImportError as exc:
            errors.append(f"{key}: {exc}")
    if errors:
        return [], errors
    return entries, []
