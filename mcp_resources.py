"""Data types and formatting for remote MCP resources and prompts."""

from __future__ import annotations

from dataclasses import dataclass, field

from mcp import types

from mcp_client import McpError, McpToolSpec

MAX_RESOURCE_CHARS = 100_000
MAX_PROMPT_CHARS = 20_000
RESOURCE_TRUNCATE = "\n…[资源内容已截断]"
PROMPT_TRUNCATE = "\n…[Prompt 内容已截断]"


@dataclass(frozen=True)
class McpResource:
    uri: str
    name: str
    description: str = ""
    mime_type: str = ""


@dataclass(frozen=True)
class McpResourceTemplate:
    uri_template: str
    name: str
    description: str = ""
    mime_type: str = ""


@dataclass(frozen=True)
class McpPromptArgument:
    name: str
    description: str = ""
    required: bool = False


@dataclass(frozen=True)
class McpPrompt:
    name: str
    description: str = ""
    arguments: list[McpPromptArgument] = field(default_factory=list)


@dataclass(frozen=True)
class McpPromptMessage:
    role: str
    text: str


def parse_tools(result) -> list[McpToolSpec]:
    specs: list[McpToolSpec] = []
    for item in getattr(result, "tools", None) or []:
        name = str(getattr(item, "name", "") or "").strip()
        if not name:
            continue
        schema = getattr(item, "inputSchema", None) or getattr(item, "input_schema", None) or {}
        if not isinstance(schema, dict):
            schema = {}
        annotations = getattr(item, "annotations", None)
        read_only = bool(getattr(annotations, "readOnlyHint", False)) if annotations else False
        specs.append(
            McpToolSpec(
                name=name,
                description=str(getattr(item, "description", "") or ""),
                input_schema=schema,
                read_only=read_only,
            )
        )
    return specs


def parse_resources(result) -> list[McpResource]:
    resources: list[McpResource] = []
    for item in getattr(result, "resources", None) or []:
        resources.append(
            McpResource(
                uri=str(getattr(item, "uri", "") or ""),
                name=str(getattr(item, "name", "") or ""),
                description=str(getattr(item, "description", "") or ""),
                mime_type=str(getattr(item, "mimeType", "") or ""),
            )
        )
    return resources


def parse_templates(result) -> list[McpResourceTemplate]:
    items = getattr(result, "resourceTemplates", None) or getattr(
        result, "resource_templates", None
    ) or []
    templates: list[McpResourceTemplate] = []
    for item in items:
        templates.append(
            McpResourceTemplate(
                uri_template=str(getattr(item, "uriTemplate", "") or ""),
                name=str(getattr(item, "name", "") or ""),
                description=str(getattr(item, "description", "") or ""),
                mime_type=str(getattr(item, "mimeType", "") or ""),
            )
        )
    return templates


def parse_prompts(result) -> list[McpPrompt]:
    prompts: list[McpPrompt] = []
    for item in getattr(result, "prompts", None) or []:
        arguments = []
        for arg in getattr(item, "arguments", None) or []:
            arguments.append(
                McpPromptArgument(
                    name=str(getattr(arg, "name", "") or ""),
                    description=str(getattr(arg, "description", "") or ""),
                    required=bool(getattr(arg, "required", False)),
                )
            )
        prompts.append(
            McpPrompt(
                name=str(getattr(item, "name", "") or ""),
                description=str(getattr(item, "description", "") or ""),
                arguments=arguments,
            )
        )
    return prompts


def _summarize_resource(resource) -> str:
    if isinstance(resource, types.TextResourceContents):
        return resource.text or ""
    if isinstance(resource, types.BlobResourceContents):
        blob = resource.blob or ""
        return (
            f"[二进制资源] mime={getattr(resource, 'mimeType', '') or ''} "
            f"size={(len(blob) * 3) // 4}"
        )
    return "[资源]"


def _prompt_content_text(content) -> str:
    if isinstance(content, types.TextContent):
        return content.text or ""
    if isinstance(content, types.ImageContent):
        return "[图片]"
    if isinstance(content, types.EmbeddedResource):
        return _summarize_resource(getattr(content, "resource", None))
    return f"[{getattr(content, 'type', '内容')}]"


def parse_prompt_messages(result) -> list[McpPromptMessage]:
    messages: list[McpPromptMessage] = []
    for item in getattr(result, "messages", None) or []:
        messages.append(
            McpPromptMessage(
                role=str(getattr(item, "role", "user") or "user"),
                text=_prompt_content_text(getattr(item, "content", None)),
            )
        )
    return messages


def format_read_resource(result) -> dict:
    parts: list[str] = []
    binary = False
    size = 0
    for item in getattr(result, "contents", None) or []:
        if isinstance(item, types.TextResourceContents):
            parts.append(item.text or "")
        elif isinstance(item, types.BlobResourceContents):
            blob = item.blob or ""
            size += (len(blob) * 3) // 4
            binary = True
            parts.append(
                f"[二进制资源] mime={getattr(item, 'mimeType', '') or ''} size={size}"
            )
        elif isinstance(item, types.EmbeddedResource):
            resource = getattr(item, "resource", None)
            if isinstance(resource, types.TextResourceContents):
                parts.append(resource.text or "")
            else:
                binary = True
                parts.append(_summarize_resource(resource))
        else:
            parts.append(f"[{getattr(item, 'type', '内容')}]")
    text = "\n".join(parts)
    if len(text) > MAX_RESOURCE_CHARS:
        text = text[:MAX_RESOURCE_CHARS] + RESOURCE_TRUNCATE
    return {"text": text, "binary": binary, "size": size}


def format_call_tool_result(result) -> str:
    if getattr(result, "isError", False):
        raise McpError(_call_content_text(result))
    return _call_content_text(result)


def _call_content_text(result) -> str:
    parts: list[str] = []
    for item in getattr(result, "content", None) or []:
        if isinstance(item, types.TextContent):
            parts.append(item.text or "")
        elif isinstance(item, types.ImageContent):
            parts.append("[图片]")
        elif isinstance(item, types.EmbeddedResource):
            parts.append(_summarize_resource(getattr(item, "resource", None)))
        else:
            parts.append(f"[{getattr(item, 'type', '内容')}]")
    return "\n".join(parts) if parts else str(result)


def format_prompt_for_composer(name: str, messages: list[McpPromptMessage]) -> str:
    lines = [f"[Prompt: {name}]"]
    for message in messages:
        lines.append(f"[{message.role}]")
        lines.append(message.text)
    text = "\n".join(lines)
    if len(text) > MAX_PROMPT_CHARS:
        text = text[:MAX_PROMPT_CHARS] + PROMPT_TRUNCATE
    return text
