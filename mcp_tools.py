"""把 MCP server 的 tools 适配成 LangChain StructuredTool。"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, create_model

from mcp_client import McpToolSpec


class _ReadResourceInput(BaseModel):
    uri: str = Field(description="资源 URI，必须是 list_resources 返回的 URI")


def _args_model(spec: McpToolSpec):
    properties = spec.input_schema.get("properties")
    if not isinstance(properties, dict):
        properties = {}
    required = spec.input_schema.get("required")
    required_names = set(required) if isinstance(required, list) else set()
    fields: dict[str, tuple[Any, Any]] = {}
    for name, raw in properties.items():
        if not isinstance(name, str) or not name:
            continue
        if not isinstance(raw, dict):
            raw = {}
        type_name = str(raw.get("type") or "string")
        annotation: Any = str
        if type_name == "integer":
            annotation = int
        elif type_name == "number":
            annotation = float
        elif type_name == "boolean":
            annotation = bool
        elif type_name == "array":
            annotation = list
        elif type_name == "object":
            annotation = dict
        default = ... if name in required_names else None
        description = str(raw.get("description") or "") or None
        fields[name] = (annotation, Field(default, description=description))
    return create_model(f"Mcp_{spec.name}_Args", **fields)


def _resource_tools(server_id: str, client) -> list[StructuredTool]:
    def list_resources() -> str:
        resources = list(getattr(client, "resources", []))
        if not resources:
            return "[MCP 资源] 该服务没有可列出的资源。"
        lines = [f"[MCP 资源] {server_id}"]
        for index, item in enumerate(resources, 1):
            detail = f" ({item.mime_type})" if item.mime_type else ""
            lines.append(f"{index}. {item.name} — {item.uri}{detail}")
            if item.description:
                lines.append(f"   {item.description}")
        return "\n".join(lines)

    def read_resource(uri: str) -> str:
        allowed = {item.uri for item in getattr(client, "resources", [])}
        if uri not in allowed:
            return (
                "[错误] 资源不在资源列表中，可用的 URI："
                + ", ".join(sorted(allowed) or ["（无）"])
            )
        try:
            result = client.read_resource(uri)
            return f"[MCP 资源] {uri}\n{result.get('text', '')}"
        except Exception as exc:  # noqa: BLE001
            return f"[错误] 读取 MCP 资源失败: {exc}"

    return [
        StructuredTool.from_function(
            func=list_resources,
            name=f"mcp__{server_id}__list_resources",
            description=f"[{server_id}] 列出该 MCP 服务的资源 URI。",
        ),
        StructuredTool.from_function(
            func=read_resource,
            name=f"mcp__{server_id}__read_resource",
            description=f"[{server_id}] 读取一个已列出的 MCP 资源。",
            args_schema=_ReadResourceInput,
        ),
    ]


def build_mcp_tools(manager, server_ids: list[str] | None = None) -> list[StructuredTool]:
    """按当前启用的 server 构建 MCP 工具。"""
    tools: list[StructuredTool] = []
    for server_id in server_ids or []:
        try:
            status = manager.get_status(server_id) if hasattr(manager, "get_status") else None
            if status is not None and status.get("status") not in {"running", "starting"}:
                continue
            specs = manager.tool_specs(server_id)
            client = manager.client_for(server_id)
        except Exception:
            continue
        for spec in specs:
            name = f"mcp__{server_id}__{spec.name}"
            args_schema = _args_model(spec)
            description = f"[{server_id}] {spec.description or spec.name}"
            try:
                permission = manager.permission_for(server_id, spec)
            except Exception:
                permission = "ask"

            def call_mcp(_spec=spec, _client=client, **kwargs):
                try:
                    return _client.call_tool(_spec.name, kwargs)
                except Exception as exc:
                    return f"[错误] MCP 工具失败: {exc}"

            tools.append(
                StructuredTool.from_function(
                    func=call_mcp,
                    name=name,
                    description=description,
                    args_schema=args_schema,
                    metadata={"mcp_server": server_id, "mcp_tool": spec.name, "permission": permission},
                )
            )
        client = manager.client_for(server_id)
        if getattr(client, "capabilities", {}).get("resources"):
            tools.extend(_resource_tools(server_id, client))
    return tools
