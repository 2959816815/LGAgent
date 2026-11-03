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
