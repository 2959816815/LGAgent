# Remote MCP with Resources and Prompts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add remote MCP servers over Streamable HTTP and SSE with tools, resources, and prompts, while leaving local stdio servers unchanged.

**Architecture:** A new `RemoteMcpClient` runs the official `mcp` SDK `ClientSession` in a dedicated asyncio loop thread and exposes a synchronous facade. `McpManager` becomes transport-aware, keeps `StdioMcpClient` for local servers, and adds remote configuration, status, test, import, resource, and prompt APIs. The MCP panel gains a remote form, JSON import, and per-server resource/prompt sections.

**Tech Stack:** Python 3.12 (`D:/Anaconda3/envs/rag/python.exe`), `mcp` 1.24.0, FastAPI, React 18, TypeScript, Vitest, FastMCP test servers.

**Spec:** `docs/superpowers/specs/2026-11-02-remote-mcp-design.md`

**Scope:** Development tree only. Do not package or install the desktop application, and do not modify the installed copy.

---

### Task 1: Resource and prompt data types

**Files:**
- Create: `Langgraph/mcp_resources.py`
- Test: `Langgraph/tests/test_mcp_resources.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mcp_resources.py`:

```python
from __future__ import annotations

import unittest

from mcp import types

from mcp_resources import (
    MAX_PROMPT_CHARS,
    MAX_RESOURCE_CHARS,
    McpPromptArgument,
    format_call_tool_result,
    format_prompt_for_composer,
    format_read_resource,
    parse_prompts,
    parse_resources,
    parse_templates,
    parse_tools,
)


class ParseTests(unittest.TestCase):
    def test_parses_tools_from_sdk_types(self):
        result = types.ListToolsResult(
            tools=[
                types.Tool(
                    name="echo",
                    description="回显",
                    inputSchema={"type": "object", "properties": {}},
                    annotations=types.ToolAnnotations(readOnlyHint=True),
                )
            ]
        )

        specs = parse_tools(result)

        self.assertEqual(specs[0].name, "echo")
        self.assertTrue(specs[0].read_only)

    def test_parses_resources_and_templates(self):
        resources = types.ListResourcesResult(
            resources=[
                types.Resource(
                    uri="demo://readme",
                    name="README",
                    description="说明",
                    mimeType="text/plain",
                )
            ]
        )
        templates = types.ListResourceTemplatesResult(
            resourceTemplates=[
                types.ResourceTemplate(
                    uriTemplate="demo://items/{id}",
                    name="Item",
                    description="",
                    mimeType="",
                )
            ]
        )

        parsed = parse_resources(resources)
        parsed_templates = parse_templates(templates)

        self.assertEqual(parsed[0].uri, "demo://readme")
        self.assertEqual(parsed_templates[0].uri_template, "demo://items/{id}")

    def test_parses_prompts_and_arguments(self):
        result = types.ListPromptsResult(
            prompts=[
                types.Prompt(
                    name="summarize",
                    description="总结",
                    arguments=[
                        types.PromptArgument(name="text", description="内容", required=True)
                    ],
                )
            ]
        )

        prompts = parse_prompts(result)

        self.assertEqual(prompts[0].name, "summarize")
        self.assertEqual(
            prompts[0].arguments[0],
            McpPromptArgument(name="text", description="内容", required=True),
        )


class FormatTests(unittest.TestCase):
    def test_text_resource_is_returned(self):
        result = types.ReadResourceResult(
            contents=[
                types.TextResourceContents(
                    uri="demo://readme", mimeType="text/plain", text="hello"
                )
            ]
        )

        formatted = format_read_resource(result)

        self.assertEqual(formatted["text"], "hello")
        self.assertFalse(formatted["binary"])

    def test_binary_resource_is_summarized(self):
        result = types.ReadResourceResult(
            contents=[
                types.BlobResourceContents(
                    uri="demo://image", mimeType="image/png", blob="AAAA"
                )
            ]
        )

        formatted = format_read_resource(result)

        self.assertTrue(formatted["binary"])
        self.assertIn("[二进制资源]", formatted["text"])
        self.assertIn("image/png", formatted["text"])

    def test_resource_text_is_truncated(self):
        result = types.ReadResourceResult(
            contents=[
                types.TextResourceContents(
                    uri="demo://long", mimeType="text/plain", text="x" * (MAX_RESOURCE_CHARS + 10)
                )
            ]
        )

        formatted = format_read_resource(result)

        self.assertLessEqual(len(formatted["text"]), MAX_RESOURCE_CHARS + 20)
        self.assertIn("资源内容已截断", formatted["text"])

    def test_call_tool_result_extracts_text(self):
        result = types.CallToolResult(
            content=[types.TextContent(type="text", text="完成")]
        )

        self.assertEqual(format_call_tool_result(result), "完成")

    def test_prompt_composer_text_has_roles_and_cap(self):
        messages = [
            types.PromptMessage(
                role="user", content=types.TextContent(type="text", text="问题")
            ),
            types.PromptMessage(
                role="assistant", content=types.TextContent(type="text", text="回答")
            ),
        ]

        from mcp_resources import parse_prompt_messages

        text = format_prompt_for_composer("demo", parse_prompt_messages(types.GetPromptResult(messages=messages)))

        self.assertIn("[Prompt: demo]", text)
        self.assertIn("[user]", text)
        self.assertIn("[assistant]", text)
        self.assertLessEqual(len(text), MAX_PROMPT_CHARS + 30)
```

- [ ] **Step 2: Run the tests and verify they fail**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest -v tests.test_mcp_resources
```

Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_resources'`.

- [ ] **Step 3: Implement `mcp_resources.py`**

```python
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
```

- [ ] **Step 4: Run the tests and verify they pass**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest -v tests.test_mcp_resources
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd D:/Vibecoding/Langgraph
git add mcp_resources.py tests/test_mcp_resources.py
git commit -m "feat: add MCP resource and prompt data types"
```

---

### Task 2: Auth provider seam

**Files:**
- Create: `Langgraph/mcp_auth.py`
- Test: `Langgraph/tests/test_mcp_auth.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_mcp_auth.py`:

```python
from __future__ import annotations

import unittest

from mcp_auth import StaticHeaderAuthProvider


class StaticHeaderAuthProviderTests(unittest.TestCase):
    def test_prepare_returns_a_copy_of_headers(self):
        provider = StaticHeaderAuthProvider()
        config = {"headers": {"Authorization": "Bearer t", "X-Trace": "1"}}

        headers = provider.prepare(config)

        self.assertEqual(headers, {"Authorization": "Bearer t", "X-Trace": "1"})
        headers["Authorization"] = "changed"
        self.assertEqual(config["headers"]["Authorization"], "Bearer t")

    def test_on_auth_error_is_a_no_op(self):
        self.assertIsNone(StaticHeaderAuthProvider().on_auth_error(401, "denied"))
```

- [ ] **Step 2: Run the test and verify it fails**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest -v tests.test_mcp_auth
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `mcp_auth.py`**

```python
"""Auth provider seam for remote MCP servers."""

from __future__ import annotations

from typing import Protocol


class RemoteAuthProvider(Protocol):
    def prepare(self, config: dict) -> dict[str, str]:
        ...

    def on_auth_error(self, status_code: int, body: str) -> None:
        ...


class StaticHeaderAuthProvider:
    """Uses the headers already resolved by the manager."""

    def prepare(self, config: dict) -> dict[str, str]:
        return {
            str(key): str(value)
            for key, value in (config.get("headers") or {}).items()
        }

    def on_auth_error(self, status_code: int, body: str) -> None:
        return None
```

- [ ] **Step 4: Run the test and verify it passes**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest -v tests.test_mcp_auth
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd D:/Vibecoding/Langgraph
git add mcp_auth.py tests/test_mcp_auth.py
git commit -m "feat: add a remote MCP auth provider seam"
```

---

### Task 3: Transport-aware config, secrets, and redaction

**Files:**
- Modify: `Langgraph/mcp_manager.py` (config loading, secrets, helpers)
- Test: `Langgraph/tests/test_mcp_config.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mcp_config.py`:

```python
from __future__ import annotations

import json
import shutil
import unittest
import uuid
from pathlib import Path

from mcp_manager import (
    McpManager,
    McpManagerError,
    _header_is_secret,
    _sanitize,
    _transport,
)
from secret_store import is_protected


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent.parent / f".test_mcp_config_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.manager = McpManager(self.root, startup_timeout=1.0, tool_timeout=1.0)

    def _write(self, servers):
        (self.root / "mcp_servers.json").write_text(
            json.dumps({"servers": servers}, ensure_ascii=False), encoding="utf-8"
        )

    def test_legacy_entry_defaults_to_stdio(self):
        self.assertEqual(_transport({"command": ["python"]}), "stdio")

    def test_remote_requires_url_and_accepts_headers(self):
        self._write(
            [
                {
                    "id": "remote",
                    "name": "Remote",
                    "enabled": True,
                    "transport": "streamable-http",
                    "url": "https://example.com/mcp",
                    "headers": {"Authorization": "Bearer plain"},
                    "permission": "ask",
                }
            ]
        )

        manager = McpManager(self.root, startup_timeout=1.0, tool_timeout=1.0)
        item = manager._find("remote")

        self.assertEqual(_transport(item), "streamable-http")
        self.assertTrue(is_protected(item["headers"]["Authorization"]))

    def test_remote_rejects_non_http_url(self):
        self._write(
            [
                {
                    "id": "remote",
                    "name": "Remote",
                    "enabled": True,
                    "transport": "sse",
                    "url": "file:///tmp/x",
                    "permission": "ask",
                }
            ]
        )

        with self.assertRaises(McpManagerError):
            McpManager(self.root, startup_timeout=1.0, tool_timeout=1.0)

    def test_secret_header_detection(self):
        self.assertTrue(_header_is_secret("Authorization", set()))
        self.assertTrue(_header_is_secret("X-Api-Token", set()))
        self.assertTrue(_header_is_secret("X-Custom", {"x-custom"}))
        self.assertFalse(_header_is_secret("X-Trace", set()))

    def test_sanitize_removes_authorization_and_tokens(self):
        text = "failed Authorization: Bearer abc token=xyz"

        cleaned = _sanitize(text)

        self.assertNotIn("abc", cleaned)
        self.assertNotIn("xyz", cleaned)
```

- [ ] **Step 2: Run the tests and verify they fail**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest -v tests.test_mcp_config
```

Expected: FAIL because the helpers do not exist.

- [ ] **Step 3: Add transport-aware helpers**

In `mcp_manager.py`, add near the top:

```python
_REMOTE_TRANSPORTS = {"streamable-http", "sse"}


def _transport(item: dict) -> str:
    return str(item.get("transport") or "stdio").strip().lower()


def _header_is_secret(name: str, secret_headers: set[str]) -> bool:
    lowered = str(name or "").strip().lower()
    if lowered in {"authorization", "proxy-authorization"}:
        return True
    if lowered in secret_headers:
        return True
    return _looks_secret(name)


_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*:\s*)([^\s,;]+(?:\s+[^\s,;]+)?)"),
    re.compile(r"(?i)((?:token|key|secret|password|passwd|credential)\s*[=:]\s*)([^\s,;]+)"),
)


def _sanitize(text: str) -> str:
    cleaned = str(text or "")
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub(lambda match: match.group(1) + "***", cleaned)
    return cleaned
```

- [ ] **Step 4: Make `_load_config` transport-aware**

Replace the validation loop in `_load_config` with:

```python
        for item in raw["servers"]:
            if not isinstance(item, dict) or not item.get("id"):
                raise McpManagerError("MCP server 缺少 id")
            transport = _transport(item)
            if transport not in {"stdio", *_REMOTE_TRANSPORTS}:
                raise McpManagerError(f"MCP server {item.get('id')} transport 无效")
            permission = str(item.get("permission") or "ask")
            if permission not in _VALID_PERMISSIONS:
                raise McpManagerError(f"MCP server {item.get('id')} permission 无效")
            if transport == "stdio":
                if not isinstance(item.get("command"), list) or not item["command"]:
                    raise McpManagerError(f"MCP server {item.get('id')} 缺少 command")
            else:
                url = str(item.get("url") or "").strip()
                if not url:
                    raise McpManagerError(f"MCP server {item.get('id')} 缺少 url")
                if "${" not in url and not url.startswith(("http://", "https://")):
                    raise McpManagerError(f"MCP server {item.get('id')} url 必须是 http/https")
                headers = item.get("headers") or {}
                if not isinstance(headers, dict):
                    raise McpManagerError(f"MCP server {item.get('id')} headers 必须是对象")
                if any(not isinstance(value, (str, int, float, bool)) for value in headers.values()):
                    raise McpManagerError(f"MCP server {item.get('id')} header 值必须是字符串")
                item["headers"] = {str(k): str(v) for k, v in headers.items()}
                item.setdefault("verify_tls", True)
                item.setdefault("timeout", 60)
```

Also add this guard at the top of the loop in `_heal_sidecar_commands`, so
remote entries are skipped:

```python
        if _transport(item) != "stdio":
            continue
```

- [ ] **Step 5: Extend `_migrate_secrets` for headers**

After the existing env loop inside `_migrate_secrets`, add:

```python
        for item in self._servers():
            secret_names = {
                str(name).strip().lower()
                for name in (item.get("secret_headers") or [])
            }
            headers = item.get("headers")
            if not isinstance(headers, dict):
                continue
            for key, value in list(headers.items()):
                if not isinstance(value, str):
                    value = str(value)
                if _header_is_secret(key, secret_names) and not is_protected(value):
                    headers[key] = protect(value)
                else:
                    headers[key] = value
```

- [ ] **Step 6: Run the tests and the existing manager tests**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest -v tests.test_mcp_config tests.test_mcp_manager tests.test_mcp_secrets
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
cd D:/Vibecoding/Langgraph
git add mcp_manager.py tests/test_mcp_config.py
git commit -m "feat: make MCP config transport-aware"
```

---

### Task 4: Remote client core (Streamable HTTP and SSE)

**Files:**
- Create: `Langgraph/remote_mcp_client.py`
- Modify: `Langgraph/mcp_client.py`
- Create: `Langgraph/tests/fastmcp_test_server.py`
- Test: `Langgraph/tests/test_remote_mcp_client.py`

- [ ] **Step 1: Create the FastMCP test server helper**

Create `tests/fastmcp_test_server.py`:

```python
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
```

- [ ] **Step 2: Write the failing client tests**

Create `tests/test_remote_mcp_client.py`:

```python
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
```

- [ ] **Step 3: Run the tests and verify they fail**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest -v tests.test_remote_mcp_client
```

Expected: FAIL with `ModuleNotFoundError: No module named 'remote_mcp_client'`.

- [ ] **Step 4: Add the `McpClient` protocol and implement `remote_mcp_client.py`**

In `mcp_client.py`, change the typing import and add the protocol after
`McpToolSpec`:

```python
from typing import Any, Protocol
```

```python
class McpClient(Protocol):
    def start(self) -> None: ...
    def close(self) -> None: ...

    @property
    def alive(self) -> bool: ...

    @property
    def tools(self) -> list[McpToolSpec]: ...

    @property
    def tools_dirty(self) -> bool: ...

    def refresh_tools(self) -> list[McpToolSpec]: ...

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str: ...
```

Then create `remote_mcp_client.py`:

```python
"""Synchronous facade over the official MCP SDK for remote servers."""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx
from mcp import ClientSession, types
from pydantic import AnyUrl

from mcp_client import McpError, McpTimeout, McpToolSpec
from mcp_resources import (
    McpPrompt,
    McpPromptMessage,
    McpResource,
    McpResourceTemplate,
    format_call_tool_result,
    parse_prompt_messages,
    parse_prompts,
    parse_resources,
    parse_templates,
    parse_tools,
)


@dataclass(frozen=True)
class RemoteMcpConfig:
    url: str
    transport: str = "streamable-http"
    headers: dict[str, str] | None = None
    verify_tls: bool = True
    timeout: float = 60.0
    connect_timeout: float = 30.0
    sse_read_timeout: float = 300.0
    ping_interval: float = 15.0


class RemoteMcpClient:
    def __init__(self, config: RemoteMcpConfig, server_name: str = "mcp"):
        self.config = config
        self.server_name = server_name
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._stopped = threading.Event()
        self._tools_dirty = threading.Event()
        self._resources_dirty = threading.Event()
        self._prompts_dirty = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._supervisor_task: asyncio.Task | None = None
        self._thread: threading.Thread | None = None
        self._session: ClientSession | None = None
        self._state = "stopped"
        self._startup_error = ""
        self._started = False
        self._tools: list[McpToolSpec] = []
        self._resources: list[McpResource] = []
        self._templates: list[McpResourceTemplate] = []
        self._prompts: list[McpPrompt] = []
        self._capabilities: dict[str, bool] = {}
        self._server_info: dict[str, str] = {}
        self._warnings: list[str] = []

    # ------------------------------------------------------------- properties
    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def alive(self) -> bool:
        with self._lock:
            return (
                self._session is not None
                and self._state == "running"
                and not self._stopped.is_set()
            )

    @property
    def tools(self) -> list[McpToolSpec]:
        with self._lock:
            return list(self._tools)

    @property
    def tools_dirty(self) -> bool:
        return self._tools_dirty.is_set()

    @property
    def resources(self) -> list[McpResource]:
        with self._lock:
            return list(self._resources)

    @property
    def resource_templates(self) -> list[McpResourceTemplate]:
        with self._lock:
            return list(self._templates)

    @property
    def resources_dirty(self) -> bool:
        return self._resources_dirty.is_set()

    @property
    def prompts(self) -> list[McpPrompt]:
        with self._lock:
            return list(self._prompts)

    @property
    def prompts_dirty(self) -> bool:
        return self._prompts_dirty.is_set()

    @property
    def capabilities(self) -> dict[str, bool]:
        with self._lock:
            return dict(self._capabilities)

    @property
    def server_info(self) -> dict[str, str]:
        with self._lock:
            return dict(self._server_info)

    @property
    def warnings(self) -> list[str]:
        with self._lock:
            return list(self._warnings)

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stopped.clear()
            self._ready.clear()
            self._startup_error = ""
            self._started = False
            self._state = "starting"
            self._thread = threading.Thread(
                target=self._thread_main,
                daemon=True,
                name=f"mcp-remote-{self.server_name}",
            )
            self._thread.start()
        if not self._ready.wait(self.config.connect_timeout):
            self.close()
            raise McpTimeout("远程 MCP 连接超时")
        if self._startup_error:
            self.close()
            raise McpError(self._startup_error)

    def close(self) -> None:
        self._stopped.set()
        loop = self._loop
        if loop is not None and not loop.is_closed() and self._stop_event is not None:
            try:
                loop.call_soon_threadsafe(self._stop_event.set)
            except RuntimeError:
                pass
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5)
        if thread is not None and thread.is_alive() and loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(self._cancel_supervisor)
            except RuntimeError:
                pass
            thread.join(timeout=2)
        with self._lock:
            self._thread = None
            self._session = None
            self._state = "stopped"

    def _cancel_supervisor(self) -> None:
        task = self._supervisor_task
        if task is not None and not task.done():
            task.cancel()

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._supervise())
        except Exception as exc:  # noqa: BLE001
            self._startup_error = _sanitize(str(exc))
            self._ready.set()
        finally:
            with self._lock:
                self._session = None
                if self._state != "stopped":
                    self._state = "stopped"

    async def _supervise(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        self._supervisor_task = asyncio.current_task()
        if self._stopped.is_set():
            self._set_state("stopped")
            self._ready.set()
            return
        delays = (1, 2, 5, 10, 30, 60)
        attempt = 0
        while not self._stop_event.is_set():
            self._set_state("starting")
            try:
                await self._connect_once()
            except Exception as exc:  # noqa: BLE001
                message = _sanitize(str(exc))
                self._set_error(message)
                self._set_state("failed")
                if not self._started:
                    self._startup_error = message
                    self._ready.set()
                    return
            if self._stop_event.is_set():
                break
            with self._lock:
                self._session = None
            delay = delays[min(attempt, len(delays) - 1)]
            attempt += 1
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
                break
            except asyncio.TimeoutError:
                pass
        self._set_state("stopped")

    async def _connect_once(self) -> None:
        async with self._transport() as streams:
            if self.config.transport == "sse":
                read, write = streams
            else:
                read, write, _ = streams
            async with ClientSession(
                read,
                write,
                read_timeout_seconds=timedelta(seconds=self.config.timeout),
                message_handler=self._on_message,
            ) as session:
                init = await session.initialize()
                capabilities = init.capabilities
                with self._lock:
                    self._session = session
                    self._capabilities = {
                        "tools": capabilities.tools is not None,
                        "resources": capabilities.resources is not None,
                        "prompts": capabilities.prompts is not None,
                    }
                    self._server_info = {
                        "name": str(getattr(init.serverInfo, "name", "") or ""),
                        "version": str(getattr(init.serverInfo, "version", "") or ""),
                    }
                    self._warnings = []
                await self._load_tools(session)
                await self._load_resources(session)
                await self._load_prompts(session)
                self._started = True
                self._set_state("running")
                self._ready.set()
                while not self._stop_event.is_set():
                    await asyncio.sleep(self.config.ping_interval)
                    await session.send_ping()

    def _transport(self):
        factory = self._http_factory()
        if self.config.transport == "sse":
            from mcp.client.sse import sse_client

            return sse_client(
                self.config.url,
                headers=dict(self.config.headers or {}),
                timeout=self.config.connect_timeout,
                sse_read_timeout=self.config.sse_read_timeout,
                httpx_client_factory=factory,
            )
        from mcp.client.streamable_http import streamablehttp_client

        return streamablehttp_client(
            self.config.url,
            headers=dict(self.config.headers or {}),
            timeout=self.config.connect_timeout,
            sse_read_timeout=self.config.sse_read_timeout,
            terminate_on_close=True,
            httpx_client_factory=factory,
        )

    def _http_factory(self):
        verify = self.config.verify_tls

        def factory(**kwargs):
            kwargs["verify"] = verify
            kwargs["trust_env"] = True
            kwargs["follow_redirects"] = False
            return httpx.AsyncClient(**kwargs)

        return factory

    async def _on_message(self, message) -> None:
        if isinstance(message, types.ServerNotification):
            root = message.root
            if isinstance(root, types.ToolListChangedNotification):
                self._tools_dirty.set()
            elif isinstance(root, types.ResourceListChangedNotification):
                self._resources_dirty.set()
            elif isinstance(root, types.PromptListChangedNotification):
                self._prompts_dirty.set()

    async def _load_tools(self, session: ClientSession) -> None:
        with self._lock:
            capabilities = dict(self._capabilities)
        if not capabilities.get("tools"):
            with self._lock:
                self._tools = []
            self._tools_dirty.clear()
            return
        specs = parse_tools(await session.list_tools())
        with self._lock:
            self._tools = specs
        self._tools_dirty.clear()

    async def _load_resources(self, session: ClientSession) -> None:
        with self._lock:
            capabilities = dict(self._capabilities)
        if not capabilities.get("resources"):
            with self._lock:
                self._resources = []
                self._templates = []
            self._resources_dirty.clear()
            return
        try:
            resources = parse_resources(await session.list_resources())
            templates = parse_templates(await session.list_resource_templates())
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._warnings.append(f"资源列表加载失败: {_sanitize(str(exc))}")
            return
        with self._lock:
            self._resources = resources
            self._templates = templates
        self._resources_dirty.clear()

    async def _load_prompts(self, session: ClientSession) -> None:
        with self._lock:
            capabilities = dict(self._capabilities)
        if not capabilities.get("prompts"):
            with self._lock:
                self._prompts = []
            self._prompts_dirty.clear()
            return
        try:
            prompts = parse_prompts(await session.list_prompts())
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._warnings.append(f"Prompt 列表加载失败: {_sanitize(str(exc))}")
            return
        with self._lock:
            self._prompts = prompts
        self._prompts_dirty.clear()

    # ------------------------------------------------------------- sync bridge
    def _session_or_raise(self) -> ClientSession:
        with self._lock:
            session = self._session
            state = self._state
        if session is None or state != "running":
            raise McpError("MCP 远程连接未就绪")
        return session

    def _run(self, coroutine, timeout: float | None = None):
        loop = self._loop
        if loop is None or loop.is_closed():
            raise McpError("MCP 远程连接未就绪")
        future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        try:
            return future.result(timeout or self.config.timeout)
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise McpTimeout("MCP 请求超时") from exc

    def list_tools(self) -> list[McpToolSpec]:
        session = self._session_or_raise()
        specs = parse_tools(self._run(session.list_tools()))
        with self._lock:
            self._tools = specs
        self._tools_dirty.clear()
        return specs

    def refresh_tools(self) -> list[McpToolSpec]:
        return self.list_tools()

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        session = self._session_or_raise()
        result = self._run(
            session.call_tool(
                name,
                dict(arguments),
                read_timeout_seconds=timedelta(seconds=self.config.timeout),
            ),
            timeout=self.config.timeout + 5,
        )
        return format_call_tool_result(result)

    def list_resources(self) -> list[McpResource]:
        session = self._session_or_raise()
        resources = parse_resources(self._run(session.list_resources()))
        templates = parse_templates(self._run(session.list_resource_templates()))
        with self._lock:
            self._resources = resources
            self._templates = templates
        self._resources_dirty.clear()
        return resources

    def refresh_resources(self) -> list[McpResource]:
        try:
            return self.list_resources()
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._warnings.append(f"资源列表刷新失败: {_sanitize(str(exc))}")
            self._resources_dirty.clear()
            return self.resources

    def list_resource_templates(self) -> list[McpResourceTemplate]:
        with self._lock:
            return list(self._templates)

    def read_resource(self, uri: str) -> dict:
        session = self._session_or_raise()
        from mcp_resources import format_read_resource

        return format_read_resource(self._run(session.read_resource(AnyUrl(uri))))

    def list_prompts(self) -> list[McpPrompt]:
        session = self._session_or_raise()
        prompts = parse_prompts(self._run(session.list_prompts()))
        with self._lock:
            self._prompts = prompts
        self._prompts_dirty.clear()
        return prompts

    def refresh_prompts(self) -> list[McpPrompt]:
        try:
            return self.list_prompts()
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._warnings.append(f"Prompt 列表刷新失败: {_sanitize(str(exc))}")
            self._prompts_dirty.clear()
            return self.prompts

    def get_prompt(self, name: str, arguments: dict[str, str] | None = None) -> dict:
        session = self._session_or_raise()
        result = self._run(session.get_prompt(name, dict(arguments or {})))
        messages = parse_prompt_messages(result)
        return {
            "name": name,
            "description": str(getattr(result, "description", "") or ""),
            "messages": messages,
        }

    # ------------------------------------------------------------- state
    def _set_state(self, state: str) -> None:
        with self._lock:
            self._state = state

    def _set_error(self, message: str) -> None:
        with self._lock:
            self._warnings = [message]


def _sanitize(text: str) -> str:
    from mcp_manager import _sanitize as sanitize

    return sanitize(text)
```

- [ ] **Step 5: Run the tests and fix SDK details**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest -v tests.test_remote_mcp_client
```

Expected: PASS for Streamable HTTP and SSE, auth failure, and reconnect. If FastMCP's SSE path differs, inspect `mcp.settings.sse_path` and update `FastMcpServer.path`. If `ToolAnnotations` construction differs, adjust the test fixture.

- [ ] **Step 6: Commit**

```bash
cd D:/Vibecoding/Langgraph
git add mcp_client.py remote_mcp_client.py tests/fastmcp_test_server.py tests/test_remote_mcp_client.py
git commit -m "feat: add a remote MCP client over Streamable HTTP and SSE"
```

---

### Task 5: Remote resources, prompts, and notifications

**Files:**
- Modify: `Langgraph/tests/test_remote_mcp_client.py`

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_remote_mcp_client.py`:

```python
class RemoteResourcePromptTests(unittest.TestCase):
    def _config(self, server, token="test-token"):
        return RemoteMcpConfig(
            url=server.url,
            transport=server.transport,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10.0,
            connect_timeout=5.0,
            ping_interval=0.5,
        )

    def test_lists_and_reads_resources(self):
        with FastMcpServer("streamable-http") as server:
            client = RemoteMcpClient(self._config(server))
            try:
                client.start()
                resources = client.list_resources()
                self.assertTrue(any(item.uri == "demo://readme" for item in resources))
                content = client.read_resource("demo://readme")
                self.assertEqual(content["text"], "hello resource")
            finally:
                client.close()

    def test_lists_and_gets_prompts(self):
        with FastMcpServer("streamable-http") as server:
            client = RemoteMcpClient(self._config(server))
            try:
                client.start()
                prompts = client.list_prompts()
                self.assertTrue(any(item.name == "summarize" for item in prompts))
                result = client.get_prompt("summarize", {"text": "内容"})
                self.assertEqual(result["messages"][0].role, "user")
                self.assertIn("请总结", result["messages"][0].text)
            finally:
                client.close()

    def test_notifications_set_dirty_flags(self):
        from mcp import types

        client = RemoteMcpClient(
            RemoteMcpConfig(url="http://127.0.0.1:1", headers={}),
        )
        import asyncio

        asyncio.run(
            client._on_message(
                types.ServerNotification(
                    root=types.ToolListChangedNotification()
                )
            )
        )
        asyncio.run(
            client._on_message(
                types.ServerNotification(
                    root=types.ResourceListChangedNotification()
                )
            )
        )
        asyncio.run(
            client._on_message(
                types.ServerNotification(
                    root=types.PromptListChangedNotification()
                )
            )
        )

        self.assertTrue(client.tools_dirty)
        self.assertTrue(client.resources_dirty)
        self.assertTrue(client.prompts_dirty)
```

- [ ] **Step 2: Run the tests**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest -v tests.test_remote_mcp_client.RemoteResourcePromptTests
```

Expected: PASS if Task 4's implementation included the resource/prompt methods. If a test fails, fix the SDK conversion or the FastMCP fixture; do not change the intended behavior.

- [ ] **Step 3: Commit**

```bash
cd D:/Vibecoding/Langgraph
git add tests/test_remote_mcp_client.py
git commit -m "test: cover remote MCP resources, prompts, and notifications"
```

---

### Task 6: Manager launch, status, and resource/prompt delegation

**Files:**
- Modify: `Langgraph/mcp_manager.py`
- Test: `Langgraph/tests/test_mcp_manager_remote.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mcp_manager_remote.py`:

```python
from __future__ import annotations

import json
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from mcp_manager import McpManager
from remote_mcp_client import RemoteMcpClient
from tests.fastmcp_test_server import FastMcpServer


class RemoteManagerTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent.parent / f".test_mcp_remote_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def test_add_and_launch_remote_server(self):
        with FastMcpServer("streamable-http") as server:
            manager = McpManager(self.root, startup_timeout=10.0, tool_timeout=10.0)
            try:
                status = manager.add_remote_server(
                    {
                        "id": "demo",
                        "name": "Demo",
                        "url": server.url,
                        "transport": "streamable-http",
                        "headers": {"Authorization": "Bearer test-token"},
                        "permission": "ask",
                    }
                )
                self.assertEqual(status.transport, "streamable-http")
                self.assertEqual(status.status, "running")
                self.assertGreaterEqual(status.tool_count, 1)
                self.assertGreaterEqual(status.resource_count, 1)
                self.assertGreaterEqual(status.prompt_count, 1)
                self.assertTrue(status.capabilities["resources"])
            finally:
                manager.stop_all()

    def test_test_remote_server_does_not_save(self):
        with FastMcpServer("streamable-http") as server:
            manager = McpManager(self.root, startup_timeout=10.0, tool_timeout=10.0)
            try:
                result = manager.test_remote_server(
                    {
                        "url": server.url,
                        "transport": "streamable-http",
                        "headers": {"Authorization": "Bearer test-token"},
                    }
                )
                self.assertTrue(result["ok"])
                self.assertEqual(manager.list_servers(), [])
            finally:
                manager.stop_all()

    def test_resource_and_prompt_delegation(self):
        with FastMcpServer("streamable-http") as server:
            manager = McpManager(self.root, startup_timeout=10.0, tool_timeout=10.0)
            try:
                manager.add_remote_server(
                    {
                        "id": "demo",
                        "name": "Demo",
                        "url": server.url,
                        "transport": "streamable-http",
                        "headers": {"Authorization": "Bearer test-token"},
                    }
                )
                resources = manager.list_resources("demo")
                self.assertTrue(any(item.uri == "demo://readme" for item in resources))
                content = manager.read_resource("demo", "demo://readme")
                self.assertEqual(content["text"], "hello resource")
                prompts = manager.list_prompts("demo")
                self.assertTrue(any(item.name == "summarize" for item in prompts))
                result = manager.get_prompt("demo", "summarize", {"text": "内容"})
                self.assertIn("请总结", result["messages"][0].text)
            finally:
                manager.stop_all()
```

- [ ] **Step 2: Run the tests and verify they fail**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest -v tests.test_mcp_manager_remote
```

Expected: FAIL with `AttributeError: 'McpManager' object has no attribute 'add_remote_server'`.

- [ ] **Step 3: Update `mcp_manager.py`**

Add imports:

```python
from mcp_auth import StaticHeaderAuthProvider
from mcp_client import McpClient, McpError, McpToolSpec, StdioMcpClient
from mcp_resources import McpPrompt, McpResource, McpResourceTemplate
from remote_mcp_client import RemoteMcpClient, RemoteMcpConfig
```

Keep the existing `_processes` dict name, but change its dataclass:

```python
@dataclass
class ManagedServer:
    client: McpClient
    transport: str
    started_at: float
```

Replace `ManagedProcess` usages with `ManagedServer`. Update `McpServerStatus`:

```python
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
    transport: str = "stdio"
    resource_count: int = 0
    prompt_count: int = 0
    capabilities: dict[str, bool] = field(default_factory=dict)
    server_info: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
```

Add helpers:

```python
    def _remote_config(self, item: dict, server_id: str | None = None) -> RemoteMcpConfig:
        target = server_id or str(item["id"])
        headers = {
            str(key): self._expand_env(
                unprotect(str(value)), f"{target}.headers.{key}"
            )
            for key, value in (item.get("headers") or {}).items()
        }
        provider = StaticHeaderAuthProvider()
        url = self._expand_env(str(item.get("url") or ""), f"{target}.url")
        if not url.startswith(("http://", "https://")):
            raise McpManagerError("远程 MCP 的 url 必须是 http/https")
        return RemoteMcpConfig(
            url=url,
            transport=_transport(item),
            headers=provider.prepare({"headers": headers}),
            verify_tls=bool(item.get("verify_tls", True)),
            timeout=float(item.get("timeout") or self.tool_timeout),
            connect_timeout=self.startup_timeout,
            sse_read_timeout=300.0,
        )

    def _normalize_remote_item(self, config: dict, target_id: str) -> dict:
        url = str(config.get("url") or "").strip()
        if not url:
            raise McpManagerError("远程 MCP 缺少 url")
        if "${" not in url and not url.startswith(("http://", "https://")):
            raise McpManagerError("远程 MCP 的 url 必须是 http/https")
        transport = str(config.get("transport") or "streamable-http").lower()
        if transport not in _REMOTE_TRANSPORTS:
            raise McpManagerError("远程 MCP transport 只能是 streamable-http 或 sse")
        headers = {str(k): str(v) for k, v in (config.get("headers") or {}).items()}
        secret_names = {
            str(name).strip().lower()
            for name in (config.get("secret_headers") or [])
        }
        stored_headers = {
            key: protect(value) if _header_is_secret(key, secret_names) else value
            for key, value in headers.items()
        }
        return {
            "id": target_id,
            "name": str(config.get("name") or target_id),
            "enabled": True,
            "transport": transport,
            "url": url,
            "headers": stored_headers,
            "secret_headers": sorted(secret_names),
            "verify_tls": bool(config.get("verify_tls", True)),
            "timeout": float(config.get("timeout") or self.tool_timeout),
            "permission": str(config.get("permission") or "ask"),
        }

    def add_remote_server(self, config: dict, background: bool = False) -> McpServerStatus:
        target_id = str(config.get("id") or "").strip().lower()
        if not _SERVER_ID.fullmatch(target_id):
            raise McpManagerError("MCP server id 只能包含小写字母、数字、下划线和短横线")
        if any(item.get("id") == target_id for item in self._servers()):
            raise McpManagerError(f"MCP server 已存在: {target_id}")
        item = self._normalize_remote_item(config, target_id)
        if item["permission"] not in _VALID_PERMISSIONS:
            raise McpManagerError("permission 只能是 auto、ask 或 deny")
        self._raw.setdefault("servers", []).append(item)
        self._save_config()
        if background:
            self._launch_in_background(target_id)
        else:
            try:
                self._launch(target_id)
            except (McpError, McpManagerError) as exc:
                self._set_failure(target_id, f"MCP 启动失败: {exc}")
        return self._status(item)

    def test_remote_server(self, config: dict) -> dict:
        item = self._normalize_remote_item(config, "test")
        client = RemoteMcpClient(self._remote_config(item, "test"), server_name="test")
        try:
            client.start()
            return {
                "ok": True,
                "transport": item["transport"],
                "server_info": client.server_info,
                "capabilities": client.capabilities,
                "tools": len(client.tools),
                "resources": len(client.resources),
                "prompts": len(client.prompts),
                "error": "",
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "transport": item["transport"],
                "server_info": {},
                "capabilities": {},
                "tools": 0,
                "resources": 0,
                "prompts": 0,
                "error": _sanitize(str(exc)),
            }
        finally:
            client.close()

    def _remote_client(self, server_id: str) -> RemoteMcpClient:
        managed = self._processes.get(server_id)
        if managed is None or not isinstance(managed.client, RemoteMcpClient):
            raise McpManagerError(f"MCP server 不是远程服务: {server_id}")
        if not managed.client.alive:
            raise McpError(f"MCP server 未就绪: {server_id}")
        return managed.client

    def list_resources(self, server_id: str):
        return self._remote_client(server_id).list_resources()

    def list_resource_templates(self, server_id: str):
        return self._remote_client(server_id).list_resource_templates()

    def read_resource(self, server_id: str, uri: str) -> dict:
        return self._remote_client(server_id).read_resource(uri)

    def list_prompts(self, server_id: str):
        return self._remote_client(server_id).list_prompts()

    def get_prompt(self, server_id: str, name: str, arguments: dict[str, str]):
        return self._remote_client(server_id).get_prompt(name, arguments)
```

Replace `_launch` with a transport-aware version. Keep the stdio body, and add the remote branch:

```python
    def _launch(self, server_id: str) -> McpServerStatus:
        item = self._find(server_id)
        if not item.get("enabled", True):
            return self._status(item)
        existing = self._processes.get(server_id)
        if existing is not None and existing.client.alive:
            return self._status(item)
        if existing is not None:
            self._stop(server_id)

        transport = _transport(item)
        if transport == "stdio":
            command = [
                self._expand_env(str(part), f"{server_id}.command")
                for part in item["command"]
            ]
            cwd = (self.root / self._expand_env(str(item.get("cwd") or "."), f"{server_id}.cwd")).resolve()
            if command and ("/" in command[0] or "\\" in command[0]):
                command[0] = str((cwd / command[0]).resolve())
            env = {
                str(key): self._expand_env(
                    unprotect(str(value)), f"{server_id}.env.{key}"
                )
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
        else:
            client = RemoteMcpClient(
                self._remote_config(item), server_name=server_id
            )
        try:
            client.start()
            if len(client.tools) > self.max_tools:
                raise McpError(
                    f"工具数 {len(client.tools)} 超过上限 {self.max_tools}"
                )
        except Exception as exc:
            tail = "；".join(getattr(client, "stderr_tail", [])[-3:])
            client.close()
            self._set_failure(
                server_id,
                f"MCP 启动失败: {exc}{(': ' + tail) if tail else ''}",
            )
            raise McpManagerError(f"MCP server 启动失败: {server_id}") from exc

        self._processes[server_id] = ManagedServer(
            client=client,
            transport=transport,
            started_at=time.time(),
        )
        item.pop("last_error", None)
        return self._status(item)
```

Replace `_status` with:

```python
    def _status(self, item: dict) -> McpServerStatus:
        server_id = str(item["id"])
        enabled = bool(item.get("enabled", True))
        transport = _transport(item)
        managed = self._processes.get(server_id)
        client = managed.client if managed is not None else None
        tool_names = [tool.name for tool in client.tools] if client is not None else []
        capabilities = getattr(client, "capabilities", {}) if client is not None else {}
        resources = getattr(client, "resources", []) if client is not None else []
        prompts = getattr(client, "prompts", []) if client is not None else []
        warnings = getattr(client, "warnings", []) if client is not None else []
        server_info = getattr(client, "server_info", {}) if client is not None else {}
        remote_state = getattr(client, "state", None) if client is not None else None
        last_error = item.get("last_error")
        if not enabled:
            status = "disabled"
        elif server_id in self._starting:
            status = "starting"
        elif client is not None and client.alive:
            status = "running"
        elif transport in _REMOTE_TRANSPORTS and remote_state in {"starting", "failed"}:
            status = remote_state
        elif item.get("last_error"):
            status = "failed"
        else:
            status = "stopped"
        if status == "failed" and last_error is None and warnings:
            last_error = warnings[0]
        return McpServerStatus(
            id=server_id,
            name=str(item.get("name") or server_id),
            enabled=enabled,
            status=status,
            permission=str(item.get("permission") or "ask"),
            tool_count=len(tool_names),
            tools=tool_names,
            last_error=last_error,
            transport=transport,
            resource_count=len(resources),
            prompt_count=len(prompts),
            capabilities=dict(capabilities),
            server_info=dict(server_info),
            warnings=list(warnings),
        )
```

Update the watcher:

```python
    def _watch_tool_changes(self) -> None:
        while not self._watch_stop.wait(2.0):
            for managed in list(self._processes.values()):
                client = managed.client
                if getattr(client, "tools_dirty", False):
                    try:
                        client.refresh_tools()
                    except Exception:  # noqa: BLE001
                        pass
                if getattr(client, "resources_dirty", False):
                    try:
                        client.refresh_resources()
                    except Exception:  # noqa: BLE001
                        pass
                if getattr(client, "prompts_dirty", False):
                    try:
                        client.refresh_prompts()
                    except Exception:  # noqa: BLE001
                        pass
```

Update `client_for` return type to `McpClient` and `tool_specs` to use the managed client.

- [ ] **Step 4: Run the tests and the existing manager suite**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest -v tests.test_mcp_manager_remote tests.test_mcp_manager tests.test_mcp_secrets
```

Expected: PASS. If the FastMCP resource/prompt counts differ, inspect `client.resources`/`client.prompts`; do not weaken the manager behavior.

- [ ] **Step 5: Commit**

```bash
cd D:/Vibecoding/Langgraph
git add mcp_manager.py tests/test_mcp_manager_remote.py
git commit -m "feat: manage remote MCP servers with resources and prompts"
```

---

### Task 7: JSON import

**Files:**
- Create: `Langgraph/mcp_import.py`
- Modify: `Langgraph/mcp_manager.py`
- Test: `Langgraph/tests/test_mcp_import.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mcp_import.py`:

```python
from __future__ import annotations

import json
import shutil
import unittest
import uuid
from pathlib import Path

from mcp_import import parse_import_payload
from mcp_manager import McpManager


class ImportParseTests(unittest.TestCase):
    def test_parses_stdio_and_remote_entries(self):
        payload = {
            "mcpServers": {
                "local": {"command": "npx", "args": ["-y", "pkg"], "env": {"A": "1"}},
                "remote": {
                    "url": "https://example.com/mcp",
                    "type": "sse",
                    "headers": {"Authorization": "Bearer x"},
                },
            }
        }

        entries, errors = parse_import_payload(payload, existing_ids=set())

        self.assertEqual(errors, [])
        self.assertEqual(entries[0]["transport"], "stdio")
        self.assertEqual(entries[0]["command"], ["npx", "-y", "pkg"])
        self.assertEqual(entries[1]["transport"], "sse")
        self.assertEqual(entries[1]["id"], "remote")

    def test_conflicting_ids_get_suffixes(self):
        entries, errors = parse_import_payload(
            {"mcpServers": {"demo": {"url": "https://example.com/mcp"}}},
            existing_ids={"demo"},
        )

        self.assertEqual(errors, [])
        self.assertEqual(entries[0]["id"], "demo-2")
        self.assertTrue(entries[0]["conflict"])

    def test_invalid_entry_fails_all(self):
        entries, errors = parse_import_payload(
            {
                "mcpServers": {
                    "ok": {"url": "https://example.com/mcp"},
                    "bad": {"url": "file:///tmp/x"},
                }
            },
            existing_ids=set(),
        )

        self.assertTrue(errors)
        self.assertEqual(entries, [])

    def test_manager_import_adds_servers(self):
        root = Path(__file__).resolve().parent.parent / f".test_mcp_import_{uuid.uuid4().hex[:8]}"
        root.mkdir()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        manager = McpManager(root, startup_timeout=1.0, tool_timeout=1.0)
        try:
            entries, errors = parse_import_payload(
                {"mcpServers": {"demo": {"command": ["definitely-not-real"]}}},
                existing_ids=set(),
            )
            self.assertEqual(errors, [])
            statuses = manager.import_servers(entries)
            self.assertEqual(statuses[0].id, "demo")
            self.assertTrue(any(item.id == "demo" for item in manager.list_servers()))
        finally:
            manager.stop_all()
```

- [ ] **Step 2: Run the tests and verify they fail**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest -v tests.test_mcp_import
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `mcp_import.py`**

```python
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
```

- [ ] **Step 4: Add `import_servers` to `McpManager`**

```python
    def import_servers(self, entries: list[dict]) -> list[McpServerStatus]:
        existing = {str(item.get("id")) for item in self._servers()}
        items: list[dict] = []
        for entry in entries:
            target_id = str(entry.get("id") or "").strip().lower()
            if not _SERVER_ID.fullmatch(target_id):
                raise McpManagerError("MCP server id 不合法")
            if target_id in existing:
                raise McpManagerError(f"MCP server 已存在: {target_id}")
            if entry.get("transport") == "stdio":
                item = {
                    "id": target_id,
                    "name": str(entry.get("name") or target_id),
                    "enabled": True,
                    "transport": "stdio",
                    "command": [str(part) for part in entry.get("command") or []],
                    "cwd": str(entry.get("cwd") or "."),
                    "env": {
                        str(key): protect(str(value))
                        if _looks_secret(str(key))
                        else str(value)
                        for key, value in (entry.get("env") or {}).items()
                    },
                    "permission": str(entry.get("permission") or "ask"),
                }
            else:
                item = self._normalize_remote_item(entry, target_id)
            if item["permission"] not in _VALID_PERMISSIONS:
                raise McpManagerError("permission 只能是 auto、ask 或 deny")
            existing.add(target_id)
            items.append(item)
        self._raw.setdefault("servers", []).extend(items)
        self._save_config()
        statuses = []
        for item in items:
            self._launch_in_background(item["id"])
            statuses.append(self._status(item))
        return statuses
```

- [ ] **Step 5: Run the tests and verify they pass**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest -v tests.test_mcp_import
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd D:/Vibecoding/Langgraph
git add mcp_import.py mcp_manager.py tests/test_mcp_import.py
git commit -m "feat: import mcpServers JSON"
```

---

### Task 8: Agent resource tools

**Files:**
- Modify: `Langgraph/mcp_tools.py`
- Test: `Langgraph/tests/test_mcp_resource_tools.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mcp_resource_tools.py`:

```python
from __future__ import annotations

import unittest

from mcp_resources import McpResource
from mcp_tools import build_mcp_tools


class FakeRemoteClient:
    def __init__(self):
        self._resources = [McpResource("demo://readme", "README", "说明", "text/plain")]

    @property
    def capabilities(self):
        return {"tools": True, "resources": True, "prompts": False}

    @property
    def resources(self):
        return list(self._resources)

    def read_resource(self, uri):
        if uri != "demo://readme":
            raise RuntimeError("not found")
        return {"text": "hello resource", "binary": False, "size": 14}

    def call_tool(self, name, arguments):
        return "tool-result"


class FakeManager:
    def get_status(self, server_id):
        return {"status": "running"}

    def tool_specs(self, server_id):
        return []

    def client_for(self, server_id):
        return FakeRemoteClient()

    def permission_for(self, server_id, tool=None):
        return "auto"


class ResourceToolTests(unittest.TestCase):
    def test_lists_resources(self):
        tools = {tool.name: tool for tool in build_mcp_tools(FakeManager(), ["demo"])}

        output = tools["mcp__demo__list_resources"].invoke({})

        self.assertIn("demo://readme", output)
        self.assertIn("README", output)

    def test_reads_a_listed_resource(self):
        tools = {tool.name: tool for tool in build_mcp_tools(FakeManager(), ["demo"])}

        output = tools["mcp__demo__read_resource"].invoke({"uri": "demo://readme"})

        self.assertIn("hello resource", output)

    def test_rejects_an_unlisted_uri(self):
        tools = {tool.name: tool for tool in build_mcp_tools(FakeManager(), ["demo"])}

        output = tools["mcp__demo__read_resource"].invoke({"uri": "demo://secret"})

        self.assertIn("不在资源列表中", output)
```

- [ ] **Step 2: Run the tests and verify they fail**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest -v tests.test_mcp_resource_tools
```

Expected: FAIL because the tools are missing.

- [ ] **Step 3: Implement the resource tools**

First change the pydantic import in `mcp_tools.py` to:

```python
from pydantic import BaseModel, Field, create_model
```

Then add:

```python
class _ReadResourceInput(BaseModel):
    uri: str = Field(description="资源 URI，必须是 list_resources 返回的 URI")
```

Add a module-level helper after `_args_model`:

```python
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
```

Inside the `for server_id in server_ids or []:` loop in `build_mcp_tools`,
after that server's tool loop, add:

```python
        client = manager.client_for(server_id)
        if getattr(client, "capabilities", {}).get("resources"):
            tools.extend(_resource_tools(server_id, client))
```

- [ ] **Step 4: Run the tests and verify they pass**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest -v tests.test_mcp_resource_tools tests.test_mcp_tools
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd D:/Vibecoding/Langgraph
git add mcp_tools.py tests/test_mcp_resource_tools.py
git commit -m "feat: expose remote MCP resources to the agent"
```

---

### Task 9: API endpoints

**Files:**
- Modify: `Langgraph/desktop_api.py`
- Test: `Langgraph/tests/test_mcp_remote_api.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mcp_remote_api.py`:

```python
from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from desktop_api import create_app
from mcp_import import parse_import_payload
from mcp_manager import McpManager


class RemoteApiTests(unittest.TestCase):
    def setUp(self):
        from tests.fastmcp_test_server import FastMcpServer

        self.server = FastMcpServer("streamable-http")
        self.server.__enter__()
        self.addCleanup(self._stop_server)
        self.root = Path(__file__).resolve().parent.parent / f".test_mcp_remote_api_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.manager = McpManager(self.root, startup_timeout=10.0, tool_timeout=10.0)
        self.addCleanup(self.manager.stop_all)
        self.app = create_app(root=self.root, token="t", mcp_manager=self.manager)
        self.client = TestClient(self.app)
        self.headers = {"X-Desktop-Token": "t"}

    def _stop_server(self):
        self.server.__exit__()

    def test_add_remote_and_read_resources_and_prompts(self):
        response = self.client.post(
            "/api/mcp/servers",
            json={
                "kind": "remote",
                "id": "demo",
                "name": "Demo",
                "url": self.server.url,
                "transport": "streamable-http",
                "headers": {"Authorization": "Bearer test-token"},
                "permission": "ask",
            },
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        status = response.json()["server"]
        self.assertEqual(status["transport"], "streamable-http")
        self.assertEqual(status["status"], "running")

        resources = self.client.get(
            "/api/mcp/servers/demo/resources", headers=self.headers
        ).json()
        self.assertTrue(any(item["uri"] == "demo://readme" for item in resources["resources"]))

        content = self.client.get(
            "/api/mcp/servers/demo/resources/read",
            params={"uri": "demo://readme"},
            headers=self.headers,
        ).json()
        self.assertEqual(content["text"], "hello resource")

        prompts = self.client.get(
            "/api/mcp/servers/demo/prompts", headers=self.headers
        ).json()
        self.assertTrue(any(item["name"] == "summarize" for item in prompts["prompts"]))

        prompt = self.client.post(
            "/api/mcp/servers/demo/prompts/get",
            json={"name": "summarize", "arguments": {"text": "内容"}},
            headers=self.headers,
        ).json()
        self.assertIn("请总结", prompt["messages"][0]["text"])

    def test_test_connection_endpoint(self):
        response = self.client.post(
            "/api/mcp/servers/test",
            json={
                "url": self.server.url,
                "transport": "streamable-http",
                "headers": {"Authorization": "Bearer test-token"},
            },
            headers=self.headers,
        )
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertGreaterEqual(body["tools"], 1)

    def test_import_preview_and_confirm(self):
        payload = {
            "mcpServers": {
                "demo-remote": {
                    "url": self.server.url,
                    "type": "streamable-http",
                    "headers": {"Authorization": "Bearer test-token"},
                }
            }
        }
        preview = self.client.post(
            "/api/mcp/import",
            json={"payload": payload, "confirm": False},
            headers=self.headers,
        ).json()
        self.assertEqual(preview["errors"], [])
        self.assertEqual(preview["entries"][0]["id"], "demo-remote")

        imported = self.client.post(
            "/api/mcp/import",
            json={"payload": payload, "confirm": True},
            headers=self.headers,
        ).json()
        self.assertEqual(imported["imported"][0]["id"], "demo-remote")
```

- [ ] **Step 2: Run the tests and verify they fail**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest -v tests.test_mcp_remote_api
```

Expected: FAIL with 400 or missing routes.

- [ ] **Step 3: Update `desktop_api.py`**

Add `_sanitize` to the `mcp_manager` import and use it for every new
endpoint's error detail. In addition to the explicit `McpError` /
`McpManagerError` / `AttributeError` cases, wrap each remote call with a broad
`except Exception as exc` that returns 502 with `_sanitize(str(exc))`.

Extend the status serializer:

```python
def _mcp_status_json(status: Any) -> dict:
    return {
        "id": status.id,
        "name": status.name,
        "enabled": status.enabled,
        "status": status.status,
        "permission": status.permission,
        "tool_count": status.tool_count,
        "tools": status.tools,
        "last_error": status.last_error,
        "transport": getattr(status, "transport", "stdio"),
        "resource_count": getattr(status, "resource_count", 0),
        "prompt_count": getattr(status, "prompt_count", 0),
        "capabilities": getattr(status, "capabilities", {}),
        "server_info": getattr(status, "server_info", {}),
        "warnings": getattr(status, "warnings", []),
    }
```

Extend `add_mcp_server`:

```python
    @app.post("/api/mcp/servers", dependencies=[Depends(require_token)])
    def add_mcp_server(payload: dict) -> dict:
        if payload.get("kind") == "remote":
            try:
                status = mcp.add_remote_server(payload)
            except (McpError, McpManagerError, ValueError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return {"server": _mcp_status_json(status)}
        preset_id = str(payload.get("preset_id") or "").strip()
        if not preset_id:
            raise HTTPException(status_code=400, detail="缺少 preset_id")
        ...
```

Add endpoints after `/api/mcp/catalog`:

```python
    @app.post("/api/mcp/servers/test", dependencies=[Depends(require_token)])
    def test_mcp_server(payload: dict) -> dict:
        try:
            return mcp.test_remote_server(payload)
        except (McpManagerError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/mcp/import", dependencies=[Depends(require_token)])
    def import_mcp_servers(payload: dict) -> dict:
        from mcp_import import parse_import_payload

        confirm = _as_bool(payload.get("confirm"), default=False)
        existing = {status.id for status in mcp.list_servers()}
        entries, errors = parse_import_payload(payload.get("payload"), existing)
        if not confirm:
            return {
                "entries": [
                    {
                        "id": entry["id"],
                        "name": entry.get("name", entry["id"]),
                        "transport": entry.get("transport", "stdio"),
                        "conflict": bool(entry.get("conflict")),
                    }
                    for entry in entries
                ],
                "errors": errors,
            }
        if errors:
            raise HTTPException(status_code=400, detail={"errors": errors})
        try:
            statuses = mcp.import_servers(entries)
        except (McpError, McpManagerError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "imported": [
                {"id": status.id, "transport": status.transport}
                for status in statuses
            ]
        }

    @app.get("/api/mcp/servers/{server_id}/resources", dependencies=[Depends(require_token)])
    def mcp_resources(server_id: str) -> dict:
        try:
            resources = mcp.list_resources(server_id)
            templates = mcp.list_resource_templates(server_id)
        except (McpError, McpManagerError, AttributeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "resources": [
                {
                    "uri": item.uri,
                    "name": item.name,
                    "description": item.description,
                    "mime_type": item.mime_type,
                }
                for item in resources
            ],
            "templates": [
                {
                    "uri_template": item.uri_template,
                    "name": item.name,
                    "description": item.description,
                    "mime_type": item.mime_type,
                }
                for item in templates
            ],
        }

    @app.get("/api/mcp/servers/{server_id}/resources/read", dependencies=[Depends(require_token)])
    def mcp_read_resource(server_id: str, uri: str = "") -> dict:
        if not uri.strip():
            raise HTTPException(status_code=400, detail="uri 不能为空")
        try:
            return {"uri": uri, **mcp.read_resource(server_id, uri)}
        except (McpError, McpManagerError, AttributeError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/api/mcp/servers/{server_id}/prompts", dependencies=[Depends(require_token)])
    def mcp_prompts(server_id: str) -> dict:
        try:
            prompts = mcp.list_prompts(server_id)
        except (McpError, McpManagerError, AttributeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "prompts": [
                {
                    "name": item.name,
                    "description": item.description,
                    "arguments": [
                        {
                            "name": arg.name,
                            "description": arg.description,
                            "required": arg.required,
                        }
                        for arg in item.arguments
                    ],
                }
                for item in prompts
            ]
        }

    @app.post("/api/mcp/servers/{server_id}/prompts/get", dependencies=[Depends(require_token)])
    def mcp_get_prompt(server_id: str, payload: dict) -> dict:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="name 不能为空")
        arguments = payload.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise HTTPException(status_code=400, detail="arguments 必须是对象")
        try:
            result = mcp.get_prompt(
                server_id, name, {str(k): str(v) for k, v in arguments.items()}
            )
        except (McpError, McpManagerError, AttributeError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {
            "name": result["name"],
            "description": result["description"],
            "messages": [
                {"role": message.role, "text": message.text}
                for message in result["messages"]
            ],
        }
```

Note: `client_for` must return the remote client; `list_resource_templates` must exist on `RemoteMcpClient`. Add:

```python
    def list_resource_templates(self):
        with self._lock:
            return list(self._templates)

    def list_prompts(self)...
```

already exists; `read_resource`, `get_prompt` exist.

- [ ] **Step 4: Run the tests and the existing MCP API tests**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest -v tests.test_mcp_remote_api tests.test_mcp_api
```

Expected: PASS. If the test-connection endpoint returns 400 for a normalized item without `id`, ensure `_normalize_remote_item` receives a generated id in `test_remote_server`.

- [ ] **Step 5: Commit**

```bash
cd D:/Vibecoding/Langgraph
git add desktop_api.py tests/test_mcp_remote_api.py
git commit -m "feat: expose remote MCP resources and prompts over the API"
```

---

### Task 10: Frontend types, API, and MCP panel

**Files:**
- Modify: `Langgraph/desktop/src/types.ts`
- Modify: `Langgraph/desktop/src/api.ts`
- Modify: `Langgraph/desktop/src/App.tsx`
- Modify: `Langgraph/desktop/src/components/McpPanel.tsx`
- Create: `Langgraph/desktop/src/components/McpRemoteForm.tsx`
- Create: `Langgraph/desktop/src/components/McpServerDetail.tsx`
- Create: `Langgraph/desktop/src/components/McpImportForm.tsx`
- Test: `Langgraph/desktop/src/components/McpRemoteForm.test.tsx`
- Test: `Langgraph/desktop/src/components/McpServerDetail.test.tsx`
- Test: `Langgraph/desktop/src/components/McpImportForm.test.tsx`

- [ ] **Step 1: Add types and API functions**

In `desktop/src/types.ts`, extend `McpServerStatus`:

```ts
  transport?: "stdio" | "streamable-http" | "sse";
  resource_count?: number;
  prompt_count?: number;
  capabilities?: { tools?: boolean; resources?: boolean; prompts?: boolean };
  server_info?: { name?: string; version?: string };
  warnings?: string[];
```

Add:

```ts
export interface McpRemoteConfig {
  id: string;
  name: string;
  url: string;
  transport: "streamable-http" | "sse";
  headers: Record<string, string>;
  secret_headers?: string[];
  verify_tls: boolean;
  timeout: number;
  permission: string;
}

export interface McpTestResult {
  ok: boolean;
  transport: string;
  server_info: Record<string, string>;
  capabilities: Record<string, boolean>;
  tools: number;
  resources: number;
  prompts: number;
  error: string;
}

export interface McpResourceInfo {
  uri: string;
  name: string;
  description: string;
  mime_type: string;
}

export interface McpResourceTemplateInfo {
  uri_template: string;
  name: string;
  description: string;
  mime_type: string;
}

export interface McpResourceContent {
  uri: string;
  text: string;
  binary: boolean;
  size: number;
}

export interface McpPromptArgumentInfo {
  name: string;
  description: string;
  required: boolean;
}

export interface McpPromptInfo {
  name: string;
  description: string;
  arguments: McpPromptArgumentInfo[];
}

export interface McpPromptMessageInfo {
  role: string;
  text: string;
}

export interface McpPromptResult {
  name: string;
  description: string;
  messages: McpPromptMessageInfo[];
}

export interface McpImportPreview {
  entries: { id: string; name: string; transport: string; conflict: boolean }[];
  errors: string[];
}
```

In `desktop/src/api.ts`, add:

```ts
export async function addRemoteMcpServer(config: McpRemoteConfig): Promise<void> {
  await request("/api/mcp/servers", {
    method: "POST",
    body: JSON.stringify({ kind: "remote", ...config }),
  });
}

export async function testMcpServer(
  config: Omit<McpRemoteConfig, "id" | "name"> & { id?: string; name?: string },
): Promise<McpTestResult> {
  return request<McpTestResult>("/api/mcp/servers/test", {
    method: "POST",
    body: JSON.stringify(config),
  });
}

export async function importMcpServers(
  payload: string | Record<string, unknown>,
  confirm: boolean,
): Promise<McpImportPreview | { imported: { id: string; transport: string }[] }> {
  return request("/api/mcp/import", {
    method: "POST",
    body: JSON.stringify({ payload, confirm }),
  });
}

export async function getMcpResources(id: string): Promise<{
  resources: McpResourceInfo[];
  templates: McpResourceTemplateInfo[];
}> {
  return request(`/api/mcp/servers/${encodeURIComponent(id)}/resources`);
}

export async function readMcpResource(
  id: string,
  uri: string,
): Promise<McpResourceContent> {
  return request(
    `/api/mcp/servers/${encodeURIComponent(id)}/resources/read?uri=${encodeURIComponent(uri)}`,
  );
}

export async function getMcpPrompts(id: string): Promise<{ prompts: McpPromptInfo[] }> {
  return request(`/api/mcp/servers/${encodeURIComponent(id)}/prompts`);
}

export async function getMcpPrompt(
  id: string,
  name: string,
  args: Record<string, string>,
): Promise<McpPromptResult> {
  return request(`/api/mcp/servers/${encodeURIComponent(id)}/prompts/get`, {
    method: "POST",
    body: JSON.stringify({ name, arguments: args }),
  });
}
```

Import the new types in `api.ts`.

- [ ] **Step 2: Add the remote form component**

Create `desktop/src/components/McpRemoteForm.tsx`:

```tsx
import { useState } from "react";
import type { McpRemoteConfig, McpTestResult } from "../types";

interface HeaderRow {
  name: string;
  value: string;
  secret: boolean;
}

export function McpRemoteForm(props: {
  onSave: (config: McpRemoteConfig) => Promise<void>;
  onTest: (config: McpRemoteConfig) => Promise<McpTestResult>;
  onCancel: () => void;
}) {
  const [id, setId] = useState("");
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [transport, setTransport] = useState<"streamable-http" | "sse">("streamable-http");
  const [headers, setHeaders] = useState<HeaderRow[]>([{ name: "", value: "", secret: true }]);
  const [verifyTls, setVerifyTls] = useState(true);
  const [timeout, setTimeout] = useState(60);
  const [permission, setPermission] = useState("ask");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [test, setTest] = useState<McpTestResult | null>(null);

  function build(): McpRemoteConfig {
    const filled = headers.filter((row) => row.name.trim());
    const values: Record<string, string> = {};
    const secretHeaders: string[] = [];
    for (const row of filled) {
      values[row.name.trim()] = row.value;
      if (row.secret) secretHeaders.push(row.name.trim());
    }
    return {
      id: id.trim(),
      name: name.trim() || id.trim(),
      url: url.trim(),
      transport,
      headers: values,
      secret_headers: secretHeaders,
      verify_tls: verifyTls,
      timeout,
      permission,
    };
  }

  async function runTest(): Promise<void> {
    setBusy(true);
    setMessage("");
    try {
      setTest(await props.onTest(build()));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function save(): Promise<void> {
    setBusy(true);
    setMessage("");
    try {
      await props.onSave(build());
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mcp-remote-form">
      <label>ID<input value={id} onChange={(event) => setId(event.target.value)} placeholder="remote_demo" /></label>
      <label>名称<input value={name} onChange={(event) => setName(event.target.value)} /></label>
      <label>URL<input value={url} onChange={(event) => setUrl(event.target.value)} placeholder="https://example.com/mcp" /></label>
      <label>
        传输
        <select value={transport} onChange={(event) => setTransport(event.target.value as "streamable-http" | "sse")}>
          <option value="streamable-http">Streamable HTTP</option>
          <option value="sse">SSE</option>
        </select>
      </label>
      <label className="mcp-checkbox">
        <input type="checkbox" checked={verifyTls} onChange={(event) => setVerifyTls(event.target.checked)} />
        校验 TLS 证书
      </label>
      <label>超时（秒）<input type="number" value={timeout} onChange={(event) => setTimeout(Number(event.target.value))} /></label>
      <label>
        权限
        <select value={permission} onChange={(event) => setPermission(event.target.value)}>
          <option value="ask">每次询问</option>
          <option value="auto">自动执行</option>
          <option value="deny">禁止执行</option>
        </select>
      </label>
      <div className="mcp-headers">
        <strong>Headers</strong>
        {headers.map((row, index) => (
          <div className="mcp-header-row" key={index}>
            <input placeholder="Header 名" value={row.name} onChange={(event) => setHeaders((current) => current.map((item, i) => i === index ? { ...item, name: event.target.value } : item))} />
            <input placeholder="值，可用 ${ENV}" value={row.value} onChange={(event) => setHeaders((current) => current.map((item, i) => i === index ? { ...item, value: event.target.value } : item))} />
            <label className="mcp-checkbox"><input type="checkbox" checked={row.secret} onChange={(event) => setHeaders((current) => current.map((item, i) => i === index ? { ...item, secret: event.target.checked } : item))} />密钥</label>
          </div>
        ))}
        <button type="button" onClick={() => setHeaders((current) => [...current, { name: "", value: "", secret: true }])}>＋ 添加 Header</button>
      </div>
      {url.startsWith("http://") && <div className="mcp-warning">未使用 TLS，Header 和资源内容可能被窃听。</div>}
      {!verifyTls && <div className="mcp-warning">已关闭 TLS 证书校验。</div>}
      {test && (
        <div className={test.ok ? "mcp-test-ok" : "mcp-error"}>
          {test.ok
            ? `连接成功：工具 ${test.tools}，资源 ${test.resources}，Prompt ${test.prompts}`
            : `连接失败：${test.error}`}
        </div>
      )}
      <div className="mcp-actions">
        <button type="button" onClick={() => void runTest()} disabled={busy}>测试连接</button>
        <button type="button" className="primary" onClick={() => void save()} disabled={busy}>保存</button>
        <button type="button" onClick={props.onCancel} disabled={busy}>取消</button>
      </div>
      {message && <div className="mcp-error">{message}</div>}
    </div>
  );
}
```

- [ ] **Step 3: Add the import form component**

Create `desktop/src/components/McpImportForm.tsx`:

```tsx
import { useState } from "react";
import type { McpImportPreview } from "../types";

export function McpImportForm(props: {
  onPreview: (payload: string) => Promise<McpImportPreview>;
  onConfirm: (payload: string) => Promise<void>;
  onCancel: () => void;
}) {
  const [text, setText] = useState("");
  const [preview, setPreview] = useState<McpImportPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  async function runPreview(): Promise<void> {
    setBusy(true);
    setMessage("");
    try {
      setPreview(await props.onPreview(text));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function confirm(): Promise<void> {
    setBusy(true);
    setMessage("");
    try {
      await props.onConfirm(text);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mcp-import-form">
      <textarea
        value={text}
        onChange={(event) => setText(event.target.value)}
        placeholder='粘贴 {"mcpServers": {...}}'
        rows={8}
      />
      {preview && (
        <div className="mcp-import-preview">
          {preview.entries.map((entry) => (
            <div key={entry.id}>
              {entry.id} · {entry.transport}
              {entry.conflict ? " · 将自动改名" : ""}
            </div>
          ))}
          {preview.errors.map((error) => <div className="mcp-error" key={error}>{error}</div>)}
        </div>
      )}
      <div className="mcp-actions">
        <button type="button" onClick={() => void runPreview()} disabled={busy || !text.trim()}>预览</button>
        <button type="button" className="primary" onClick={() => void confirm()} disabled={busy || !preview || preview.errors.length > 0}>确认导入</button>
        <button type="button" onClick={props.onCancel} disabled={busy}>取消</button>
      </div>
      {message && <div className="mcp-error">{message}</div>}
    </div>
  );
}
```

- [ ] **Step 4: Add the server detail component**

Create `desktop/src/components/McpServerDetail.tsx`:

```tsx
import { useEffect, useState } from "react";
import {
  getMcpPrompt,
  getMcpPrompts,
  getMcpResources,
  readMcpResource,
} from "../api";
import type {
  McpPromptInfo,
  McpPromptResult,
  McpResourceInfo,
  McpResourceTemplateInfo,
  McpServerStatus,
} from "../types";

export function McpServerDetail(props: {
  server: McpServerStatus;
  onInsert: (text: string) => void;
}) {
  const [resources, setResources] = useState<McpResourceInfo[]>([]);
  const [templates, setTemplates] = useState<McpResourceTemplateInfo[]>([]);
  const [prompts, setPrompts] = useState<McpPromptInfo[]>([]);
  const [content, setContent] = useState("");
  const [promptArgs, setPromptArgs] = useState<Record<string, string>>({});
  const [promptResult, setPromptResult] = useState<McpPromptResult | null>(null);
  const [error, setError] = useState("");
  const remote =
    props.server.transport === "streamable-http" || props.server.transport === "sse";

  useEffect(() => {
    if (!remote) return;
    let cancelled = false;
    void (async () => {
      try {
        const resourceData = await getMcpResources(props.server.id);
        if (cancelled) return;
        setResources(resourceData.resources);
        setTemplates(resourceData.templates);
        const promptData = await getMcpPrompts(props.server.id);
        if (!cancelled) setPrompts(promptData.prompts);
      } catch (cause) {
        if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause));
      }
    })();
    return () => { cancelled = true; };
  }, [props.server.id, remote]);

  async function read(uri: string): Promise<void> {
    try {
      const data = await readMcpResource(props.server.id, uri);
      setContent(data.text);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  async function insertResource(resource: McpResourceInfo): Promise<void> {
    try {
      const data = await readMcpResource(props.server.id, resource.uri);
      props.onInsert(`[资源: ${resource.name} (${resource.uri})]\n${data.text}`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  async function loadPrompt(prompt: McpPromptInfo): Promise<void> {
    try {
      const args = Object.fromEntries(
        prompt.arguments.map((arg) => [arg.name, promptArgs[arg.name] ?? ""]),
      );
      const result = await getMcpPrompt(props.server.id, prompt.name, args);
      setPromptResult(result);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  function insertPrompt(): void {
    if (!promptResult) return;
    const lines = [`[Prompt: ${promptResult.name}]`];
    for (const message of promptResult.messages) {
      lines.push(`[${message.role}]`, message.text);
    }
    props.onInsert(lines.join("\n"));
  }

  return (
    <div className="mcp-detail">
      <div className="mcp-detail-section">
        <strong>工具</strong>
        <div>{props.server.tools.join("、") || "无"}</div>
      </div>
      {remote && (
        <>
          <div className="mcp-detail-section">
            <strong>资源（{resources.length}）</strong>
            {resources.map((resource) => (
              <div className="mcp-resource" key={resource.uri}>
                <span>{resource.name} · {resource.uri}</span>
                <button type="button" onClick={() => void read(resource.uri)}>读取</button>
                <button type="button" onClick={() => void insertResource(resource)}>插入</button>
              </div>
            ))}
            {resources.length === 0 && <div className="empty-hint">没有资源</div>}
            {content && <pre className="mcp-resource-content">{content}</pre>}
            {templates.length > 0 && (
              <div className="mcp-templates">
                {templates.map((template) => (
                  <div key={template.uri_template}>{template.name} · {template.uri_template}</div>
                ))}
              </div>
            )}
          </div>
          <div className="mcp-detail-section">
            <strong>Prompts（{prompts.length}）</strong>
            {prompts.map((prompt) => (
              <div className="mcp-prompt" key={prompt.name}>
                <div>{prompt.name} {prompt.description}</div>
                {prompt.arguments.map((arg) => (
                  <input
                    key={arg.name}
                    placeholder={`${arg.name}${arg.required ? "（必填）" : ""}`}
                    value={promptArgs[arg.name] ?? ""}
                    onChange={(event) => setPromptArgs((current) => ({ ...current, [arg.name]: event.target.value }))}
                  />
                ))}
                <button type="button" onClick={() => void loadPrompt(prompt)}>获取</button>
              </div>
            ))}
            {promptResult && (
              <div className="mcp-prompt-preview">
                <pre>{promptResult.messages.map((message) => `[${message.role}] ${message.text}`).join("\n")}</pre>
                <button type="button" onClick={insertPrompt}>插入输入框</button>
              </div>
            )}
          </div>
        </>
      )}
      {error && <div className="mcp-error">{error}</div>}
    </div>
  );
}
```

- [ ] **Step 5: Integrate into `McpPanel`**

Add props (optional so existing tests still compile):

```tsx
  onAddRemote?: (config: McpRemoteConfig) => Promise<void>;
  onTestRemote?: (config: McpRemoteConfig) => Promise<McpTestResult>;
  onImportPreview?: (payload: string) => Promise<McpImportPreview>;
  onImportConfirm?: (payload: string) => Promise<void>;
  onInsertContext?: (text: string) => void;
```

Add state:

```tsx
  const [mode, setMode] = useState<"catalog" | "remote" | "import">("catalog");
  const [detailId, setDetailId] = useState<string | null>(null);
```

Render the mode buttons inside the add panel and the new components:

```tsx
        <div className="mcp-modes">
          <button type="button" className={mode === "catalog" ? "active" : ""} onClick={() => setMode("catalog")}>目录</button>
          <button type="button" className={mode === "remote" ? "active" : ""} onClick={() => setMode("remote")}>手动远程</button>
          <button type="button" className={mode === "import" ? "active" : ""} onClick={() => setMode("import")}>JSON 导入</button>
        </div>
        {mode === "catalog" && (
          <div className="mcp-catalog">...existing catalog...</div>
        )}
        {mode === "remote" && props.onAddRemote && props.onTestRemote && (
          <McpRemoteForm
            onSave={async (config) => { await props.onAddRemote!(config); setAdding(false); }}
            onTest={props.onTestRemote}
            onCancel={() => setAdding(false)}
          />
        )}
        {mode === "import" && props.onImportPreview && props.onImportConfirm && (
          <McpImportForm
            onPreview={props.onImportPreview}
            onConfirm={async (payload) => { await props.onImportConfirm!(payload); setAdding(false); }}
            onCancel={() => setAdding(false)}
          />
        )}
```

Wrap the existing catalog JSX in `mode === "catalog"`; keep the rest.

Extend the server card:

```tsx
          <div className="mcp-meta">
            {server.transport === "sse" ? "远程 SSE" : server.transport === "streamable-http" ? "远程 HTTP" : "本地 stdio"}
            {" · "}{server.tool_count} 工具 · {server.resource_count ?? 0} 资源 · {server.prompt_count ?? 0} Prompt
          </div>
          {(server.warnings ?? []).map((warning) => (
            <div className="mcp-warning" key={warning}>{warning}</div>
          ))}
          <button type="button" onClick={() => setDetailId(detailId === server.id ? null : server.id)}>
            {detailId === server.id ? "收起详情" : "详情"}
          </button>
          {detailId === server.id && props.onInsertContext && (
            <McpServerDetail server={server} onInsert={props.onInsertContext} />
          )}
```

Import the new components and types.

Append these styles to `desktop/src/styles.css`:

```css
.mcp-modes { display: flex; gap: 6px; margin-bottom: 8px; }
.mcp-modes button { padding: 4px 10px; border: 1px solid var(--border); border-radius: 6px; background: var(--panel-2); color: var(--muted); cursor: pointer; }
.mcp-modes button.active { color: var(--text); border-color: var(--accent); }
.mcp-remote-form, .mcp-import-form { display: flex; flex-direction: column; gap: 8px; padding: 10px; border: 1px solid var(--border); border-radius: 8px; }
.mcp-remote-form label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; }
.mcp-remote-form input, .mcp-remote-form select, .mcp-import-form textarea { padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; background: var(--panel-2); color: var(--text); }
.mcp-header-row { display: flex; gap: 6px; align-items: center; }
.mcp-header-row input { flex: 1; min-width: 0; }
.mcp-warning { padding: 7px 9px; border: 1px solid var(--border); border-radius: 6px; color: #c47a00; font-size: 12px; }
.mcp-test-ok { color: var(--accent); font-size: 12px; }
.mcp-detail { display: flex; flex-direction: column; gap: 10px; margin-top: 8px; padding-top: 8px; border-top: 1px solid var(--border); }
.mcp-detail-section { display: flex; flex-direction: column; gap: 6px; font-size: 12px; }
.mcp-resource { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.mcp-resource-content { max-height: 220px; overflow: auto; white-space: pre-wrap; word-break: break-word; padding: 8px; border: 1px solid var(--border); border-radius: 6px; background: var(--panel-2); }
.mcp-prompt { display: flex; flex-direction: column; gap: 4px; padding: 6px; border: 1px solid var(--border); border-radius: 6px; }
.mcp-prompt input { padding: 5px 7px; }
.mcp-prompt-preview pre { max-height: 220px; overflow: auto; white-space: pre-wrap; }
```

- [ ] **Step 6: Wire `App.tsx`**

Add API imports:

```tsx
  addRemoteMcpServer,
  getMcpPrompt,
  getMcpPrompts,
  getMcpResources,
  importMcpServers,
  readMcpResource,
  testMcpServer,
```

Add `McpRemoteConfig` and `McpImportPreview` to the type import from
`./types`.

Add handlers:

```tsx
  async function addRemoteMcp(config: McpRemoteConfig): Promise<void> {
    await addRemoteMcpServer(config);
    await refreshMcp();
  }

  async function previewMcp(payload: string): Promise<McpImportPreview> {
    return (await importMcpServers(payload, false)) as McpImportPreview;
  }

  async function confirmMcp(payload: string): Promise<void> {
    await importMcpServers(payload, true);
    await refreshMcp();
  }
```

Pass to `McpPanel`:

```tsx
        onAddRemote={addRemoteMcp}
        onTestRemote={(config) => testMcpServer(config)}
        onImportPreview={previewMcp}
        onImportConfirm={confirmMcp}
        onInsertContext={(text) => setInput((current) => current ? `${current}\n\n${text}` : text)}
```

The existing 3-second `refreshMcp` polling in `App.tsx` already satisfies the polling requirement; do not add another interval.

Update the `vi.mock("./api", ...)` object in `desktop/src/App.test.tsx` with
the new named exports, otherwise the module mock fails:

```tsx
    addRemoteMcpServer: vi.fn(async () => undefined),
    testMcpServer: vi.fn(async () => ({
      ok: true,
      transport: "streamable-http",
      server_info: {},
      capabilities: {},
      tools: 0,
      resources: 0,
      prompts: 0,
      error: "",
    })),
    importMcpServers: vi.fn(async () => ({ entries: [], errors: [] })),
    getMcpResources: vi.fn(async () => ({ resources: [], templates: [] })),
    readMcpResource: vi.fn(async () => ({
      uri: "",
      text: "",
      binary: false,
      size: 0,
    })),
    getMcpPrompts: vi.fn(async () => ({ prompts: [] })),
    getMcpPrompt: vi.fn(async () => ({ name: "", description: "", messages: [] })),
```

- [ ] **Step 7: Add tests**

Create `McpRemoteForm.test.tsx`:

```tsx
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { McpRemoteForm } from "./McpRemoteForm";

afterEach(cleanup);

describe("McpRemoteForm", () => {
  it("tests a connection with headers", async () => {
    const onTest = vi.fn(async () => ({
      ok: true,
      transport: "streamable-http",
      server_info: {},
      capabilities: {},
      tools: 1,
      resources: 1,
      prompts: 1,
      error: "",
    }));
    render(<McpRemoteForm onSave={vi.fn()} onTest={onTest} onCancel={vi.fn()} />);

    fireEvent.change(screen.getByPlaceholderText("remote_demo"), { target: { value: "demo" } });
    fireEvent.change(screen.getByPlaceholderText("https://example.com/mcp"), { target: { value: "https://example.com/mcp" } });
    fireEvent.click(screen.getByText("测试连接"));

    await waitFor(() => expect(onTest).toHaveBeenCalled());
    expect(await screen.findByText(/连接成功/)).toBeTruthy();
  });
});
```

Create `McpImportForm.test.tsx`:

```tsx
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { McpImportForm } from "./McpImportForm";

afterEach(cleanup);

describe("McpImportForm", () => {
  it("previews before confirming", async () => {
    const onPreview = vi.fn(async () => ({
      entries: [{ id: "demo", name: "demo", transport: "stdio", conflict: false }],
      errors: [],
    }));
    const onConfirm = vi.fn(async () => undefined);
    render(<McpImportForm onPreview={onPreview} onConfirm={onConfirm} onCancel={vi.fn()} />);

    fireEvent.change(screen.getByPlaceholderText(/粘贴/), { target: { value: '{"mcpServers":{}}' } });
    fireEvent.click(screen.getByText("预览"));
    await waitFor(() => expect(onPreview).toHaveBeenCalled());
    expect(await screen.findByText(/demo · stdio/)).toBeTruthy();
    fireEvent.click(screen.getByText("确认导入"));
    await waitFor(() => expect(onConfirm).toHaveBeenCalled());
  });
});
```

Create `McpServerDetail.test.tsx`:

```tsx
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../api", () => ({
  getMcpResources: vi.fn(async () => ({
    resources: [{ uri: "demo://readme", name: "README", description: "", mime_type: "text/plain" }],
    templates: [],
  })),
  getMcpPrompts: vi.fn(async () => ({
    prompts: [{ name: "summarize", description: "", arguments: [{ name: "text", description: "", required: true }] }],
  })),
  readMcpResource: vi.fn(async () => ({ uri: "demo://readme", text: "hello", binary: false, size: 5 })),
  getMcpPrompt: vi.fn(async () => ({ name: "summarize", description: "", messages: [{ role: "user", text: "请总结" }] })),
}));

import { McpServerDetail } from "./McpServerDetail";

afterEach(cleanup);

describe("McpServerDetail", () => {
  it("loads and reads a remote resource", async () => {
    render(
      <McpServerDetail
        server={{ id: "demo", name: "Demo", enabled: true, status: "running", permission: "ask", tool_count: 1, tools: ["echo"], transport: "streamable-http" }}
        onInsert={vi.fn()}
      />,
    );

    expect(await screen.findByText(/README/)).toBeTruthy();
    fireEvent.click(screen.getByText("读取"));
    expect(await screen.findByText("hello")).toBeTruthy();
  });
});
```

- [ ] **Step 8: Run frontend tests and type check**

```bash
cd D:/Vibecoding/Langgraph/desktop
npx vitest run src/components/McpRemoteForm.test.tsx src/components/McpImportForm.test.tsx src/components/McpServerDetail.test.tsx src/components/McpPanel.test.tsx
npx tsc --noEmit
```

Expected: PASS. Fix any missing prop types in existing tests by making the new panel props optional.

- [ ] **Step 9: Commit**

```bash
cd D:/Vibecoding/Langgraph
git add desktop/src/types.ts desktop/src/api.ts desktop/src/App.tsx \
  desktop/src/components/McpPanel.tsx desktop/src/components/McpRemoteForm.tsx \
  desktop/src/components/McpServerDetail.tsx desktop/src/components/McpImportForm.tsx \
  desktop/src/components/McpRemoteForm.test.tsx desktop/src/components/McpImportForm.test.tsx \
  desktop/src/components/McpServerDetail.test.tsx desktop/src/styles.css
git commit -m "feat: add remote MCP settings and resource panels"
```

---

### Task 11: Full suites and manual verification

**Files:**
- No new files.

- [ ] **Step 1: Run the full backend suite**

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m unittest discover -s tests -v
```

Expected: PASS, including the existing stdio MCP tests.

- [ ] **Step 2: Run the full frontend suite**

```bash
cd D:/Vibecoding/Langgraph/desktop
npm test
npx tsc --noEmit
```

Expected: PASS.

- [ ] **Step 3: Manual verification with the development app**

1. Start the dev app (do not package):

```bash
cd D:/Vibecoding/Langgraph/desktop
LANGGRAPH_DATA_DIR="D:/Vibecoding/Langgraph/.desktop-data" npm run dev
```

2. Open the MCP tab → 添加服务 → 手动远程.
3. Start a local demo server:

```bash
cd D:/Vibecoding/Langgraph
D:/Anaconda3/envs/rag/python.exe -m tests.fastmcp_test_server
```

The helper prints the URL and waits for Enter; it uses the token
`test-token`.

4. Add `http://127.0.0.1:<port>/mcp` with `Authorization: Bearer test-token`, transport Streamable HTTP, and test the connection.
5. Confirm tool/resource/prompt counts, call `echo` from chat, read `demo://readme` in the detail panel and insert it into the composer.
6. Fetch the `summarize` prompt, fill `text`, and confirm it pre-fills the composer without sending.
7. Stop the demo server; confirm the card moves to failed; restart it on the
   same port (`python -m tests.fastmcp_test_server <port>`); confirm automatic
   reconnection.
8. Paste an `mcpServers` JSON with one stdio and one remote entry; confirm preview, then import.
9. Confirm the existing local Playwright/GitHub/SQLite stdio servers still work.

No packaging, installer, or installed-application changes.

---

## Self-Review Checklist

- Spec coverage:
  - Streamable HTTP and SSE: Tasks 4, 6.
  - Static headers, DPAPI, `${ENV}`, OAuth seam: Tasks 2, 3, 6.
  - Transport-aware config and backward compatibility: Task 3.
  - JSON import with preview and all-or-nothing: Task 7.
  - Remote tools/resources/prompts: Tasks 4, 5, 6.
  - Agent resource tools with URI allowlist and binary summary: Task 8.
  - API endpoints and status fields: Task 9.
  - Remote form, JSON import, detail panel, prompt insertion: Task 10.
  - Reconnect/backoff and status transitions: Tasks 4, 6.
  - Existing stdio behavior unchanged: Tasks 3, 6, 11.
- No placeholders: every step has concrete code or an exact command.
- Type consistency: `RemoteMcpConfig`, `RemoteMcpClient`, `McpResource`, `McpPrompt`, `McpServerStatus`, `McpRemoteConfig`, and the API functions keep the same names across tasks.
- Known limitation: OAuth, local stdio resources/prompts, subscriptions, and server-initiated sampling/elicitation/roots remain out of scope.
