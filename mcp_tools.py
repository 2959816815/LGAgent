"""把 MCP server 的 tools 适配成 LangChain StructuredTool。"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import Field, create_model

from mcp_client import McpToolSpec


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
            permission = manager.permission_for(server_id)
        except Exception:
            continue
        for spec in specs:
            if spec.read_only:
                continue
            name = f"mcp__{server_id}__{spec.name}"
            args_schema = _args_model(spec)
            description = f"[{server_id}] {spec.description or spec.name}"

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
    return tools
