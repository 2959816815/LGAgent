from __future__ import annotations

import argparse
import json
import os
import queue
import socket
import sys
import threading
import time
import uuid

import httpx
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from graph_agent import SYSTEM_PROMPT, build_graph, build_llm, collect_tools
from context_builder import (
    ContextBudgetError,
    SummaryError,
    build_view,
    ensure_summary,
    measure_tool_tokens,
)
from approval import ApprovalBroker, UndoEmpty, UndoEntry, set_current as set_approval_broker, undo_last
from mcp_client import McpError
from mcp_manager import McpManager, McpManagerError
from mcp_tools import build_mcp_tools
from model_registry import (
    ModelConfigError,
    get_profile,
    initial_model_key,
    list_profiles,
    resolve_selector,
)
from session_store import SessionStore, SessionStoreError
from session_store_sqlite import SqliteSessionStore
from subagent_manager import SubagentError, SubagentManager
from attachments import AttachmentError, AttachmentStore

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_TOKEN = os.getenv("DSH_DESKTOP_TOKEN", "")


def _text(content: Any) -> str:
    if isinstance(content, list):
        return "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    return str(content)


def _image_parts(text: str, attachments: Any, store: AttachmentStore) -> str | list[dict]:
    if not attachments:
        return text
    if not isinstance(attachments, list) or len(attachments) > 4:
        raise HTTPException(400, "一次最多附加 4 张图片")
    parts: list[dict] = []
    if text:
        parts.append({"type": "text", "text": text})
    for item in attachments:
        data_url = str((item or {}).get("data_url", ""))
        if not data_url.startswith("data:image/"):
            raise HTTPException(400, "图片格式无效")
        try:
            ref = store.save_data_url(data_url, name=str((item or {}).get("name") or ""))
        except AttachmentError as exc:
            raise HTTPException(400, f"图片保存失败: {exc}") from exc
        parts.append(ref)
    return parts


def _replace_content(message: Any, parts: list) -> Any:
    """返回一个内容被替换的消息副本（兼容 pydantic v1/v2）。"""
    if hasattr(message, "model_copy"):
        return message.model_copy(update={"content": parts})
    return message.copy(update={"content": parts})


def hydrate_messages(messages: list, store: AttachmentStore) -> list:
    """把 image_ref 还原成 data URL，供模型调用使用。"""
    hydrated: list = []
    for message in messages:
        content = getattr(message, "content", "")
        if not isinstance(content, list):
            hydrated.append(message)
            continue
        parts: list = []
        changed = False
        for part in content:
            if isinstance(part, dict) and part.get("type") == "image_ref":
                attachment_id = str(part.get("attachment_id") or "")
                try:
                    url = store.data_url(attachment_id)
                except AttachmentError:
                    # 文件丢失时跳过这张图，不影响文字对话
                    changed = True
                    continue
                parts.append({"type": "image_url", "image_url": {"url": url}})
                changed = True
            else:
                parts.append(part)
        hydrated.append(_replace_content(message, parts) if changed else message)
    return hydrated


def dehydrate_messages(messages: list, store: AttachmentStore) -> list:
    """把 data URL 图片写盘并替换成 image_ref，避免 base64 落库。"""
    dehydrated: list = []
    for message in messages:
        content = getattr(message, "content", "")
        if not isinstance(content, list):
            dehydrated.append(message)
            continue
        parts: list = []
        changed = False
        for part in content:
            if isinstance(part, dict) and part.get("type") == "image_url":
                url = (part.get("image_url") or {}).get("url") if isinstance(part.get("image_url"), dict) else None
                if isinstance(url, str) and url.startswith("data:"):
                    try:
                        parts.append(store.save_data_url(url, name=str(part.get("name") or "")))
                        changed = True
                        continue
                    except AttachmentError:
                        pass
            parts.append(part)
        dehydrated.append(_replace_content(message, parts) if changed else message)
    return dehydrated


def _message_json(message: Any, attachment_url=None) -> dict:
    role = {
        "human": "user",
        "ai": "assistant",
        "tool": "tool",
        "system": "system",
    }.get(getattr(message, "type", ""), getattr(message, "type", "unknown"))
    data = {"role": role, "content": _text(message.content)}
    if isinstance(message.content, list):
        attachments: list[dict] = []
        for part in message.content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "image_url" and isinstance(part.get("image_url"), dict):
                url = part["image_url"].get("url")
                if url:
                    attachments.append({"type": "image", "data_url": url})
            elif part.get("type") == "image_ref" and part.get("attachment_id"):
                attachment_id = str(part["attachment_id"])
                link = (
                    attachment_url(attachment_id)
                    if attachment_url
                    else f"/api/attachments/{attachment_id}"
                )
                attachments.append(
                    {
                        "type": "image",
                        "data_url": link,
                        "name": part.get("name") or "",
                    }
                )
        if attachments:
            data["attachments"] = attachments
    if isinstance(message, AIMessage):
        data["tool_calls"] = [
            {"id": call.get("id"), "name": call.get("name"), "args": call.get("args", {})}
            for call in (message.tool_calls or [])
        ]
    if isinstance(message, ToolMessage):
        data["tool_call_id"] = message.tool_call_id
        data["name"] = message.name
    return data


def _create_session_store(data_dir: Path):
    """按环境变量选择会话后端；默认保持旧 JSON，安装包显式启用 SQLite。"""
    backend = os.getenv("DSH_SESSION_STORE", "json").strip().lower()
    if backend == "sqlite":
        return SqliteSessionStore(
            data_dir / "agent.db",
            legacy_dir=data_dir / ".sessions",
        )
    return SessionStore(data_dir / ".sessions")


_LOCAL_PROBE_TTL = 10.0
_local_probe_cache: dict[str, tuple[float, bool, str]] = {}


def _probe_local(base_url: str) -> tuple[bool, str]:
    """探测本地 OpenAI 兼容服务（Ollama）是否可达，结果缓存 10 秒。"""
    now = time.monotonic()
    cached = _local_probe_cache.get(base_url)
    if cached is not None and now - cached[0] < _LOCAL_PROBE_TTL:
        return cached[1], cached[2]
    url = base_url.rstrip("/") + "/models"
    try:
        response = httpx.get(url, timeout=2.5)
        ok = response.status_code == 200
        reason = "" if ok else f"本地模型服务返回 {response.status_code}"
    except Exception:  # noqa: BLE001
        ok = False
        reason = "Ollama 未启动或地址不可达"
    _local_probe_cache[base_url] = (now, ok, reason)
    return ok, reason


def _as_bool(value: Any, default: bool = False) -> bool:
    """严格解析布尔值：字符串 "false"/"0" 不能被当成 True。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if not text:
            return default
        return text in {"1", "true", "yes", "on"}
    return default


def _mcp_status_json(status: Any) -> dict:
    """MCP server 的统一序列化（多个端点共用，避免字段各写一份）。"""
    return {
        "id": status.id,
        "name": status.name,
        "enabled": status.enabled,
        "status": status.status,
        "permission": status.permission,
        "tool_count": status.tool_count,
        "tools": status.tools,
        "last_error": status.last_error,
    }


_NETWORK_EXC_NAMES = {
    "ConnectError",
    "ConnectTimeout",
    "ReadTimeout",
    "RemoteProtocolError",
    "APIConnectionError",
    "APITimeoutError",
    "gaierror",
    "ConnectionError",
    "ConnectionRefusedError",
    "TimeoutError",
}
_NETWORK_HINTS = (
    "connection refused",
    "timed out",
    "getaddrinfo failed",
    "network is unreachable",
    "temporary failure in name resolution",
    "connection reset",
)


def is_network_failure(exc: BaseException) -> bool:
    """Return True for connection/timeout failures, not HTTP status errors."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        names = {base.__name__ for base in type(current).__mro__}
        if names & _NETWORK_EXC_NAMES:
            return True
        text = str(current).lower()
        if any(hint in text for hint in _NETWORK_HINTS):
            return True
        current = current.__cause__ or current.__context__
    return False


def create_app(
    root: Path,
    token: str,
    graph_builder=build_graph,
    mcp_manager=None,
    data_root: Path | None = None,
    workspace: Path | None = None,
    model_factory=None,
) -> FastAPI:
    data_dir = Path(data_root or os.getenv("DSH_DATA_DIR") or root)
    store = _create_session_store(data_dir)
    attachment_store = AttachmentStore(data_dir)
    manager = SubagentManager(
        project_root=data_dir,
        worker_path=Path(__file__).resolve().parent / "subagent_worker.py",
    )
    mcp = mcp_manager if mcp_manager is not None else McpManager(data_dir)

    class RunState:
        def __init__(self, run_id: str, session_id: str):
            self.run_id = run_id
            self.session_id = session_id
            self.cancel = threading.Event()
            self.events: queue.Queue = queue.Queue()
            self.broker: ApprovalBroker | None = None

    runs: dict[str, RunState] = {}
    undo_entries: dict[str, list[UndoEntry]] = {}
    active_runs: dict[str, str] = {}
    runs_lock = threading.Lock()

    def emit(run: RunState, event: str, data: dict) -> None:
        run.events.put((event, data))

    model_factory = model_factory or build_llm

    def _extra_tools():
        try:
            return build_mcp_tools(mcp, mcp.enabled_ids())
        except Exception:  # noqa: BLE001
            return []

    def _tool_tokens_for(key: str, extra_tools=None) -> int:
        try:
            return measure_tool_tokens(
                collect_tools(manager, model_key=key, extra_tools=extra_tools or [])
            )
        except Exception:  # noqa: BLE001
            return 2000

    def _fallback_profile(exclude_key: str):
        candidates = [
            profile
            for profile in list_profiles()
            if profile.provider == "ollama" and profile.key != exclude_key
        ]
        candidates.sort(key=lambda profile: (profile.key != "local", profile.key))
        return candidates[0] if candidates else None

    def _context_json(session, key: str):
        try:
            profile = get_profile(key)
            _, stats = build_view(
                session,
                profile,
                SYSTEM_PROMPT,
                _tool_tokens_for(key, _extra_tools()),
            )
            return stats.as_dict()
        except Exception:  # noqa: BLE001
            return None

    def run_graph(run: RunState, session, text: str) -> None:
        from langchain_core.messages import AIMessageChunk

        tool_started: dict[str, float] = {}
        emitted_tool_end: set[str] = set()
        final_key = effective_model_key(session)

        def emit_tool_end(message: ToolMessage) -> None:
            call_id = message.tool_call_id
            if call_id in emitted_tool_end:
                return
            emitted_tool_end.add(call_id)
            emit(
                run,
                "tool_end",
                {
                    "run_id": run.run_id,
                    "tool": message.name or "tool",
                    "call_id": call_id,
                    "status": "error" if str(message.content).startswith("[错误]") else "ok",
                    "duration_ms": int(
                        (time.monotonic() - tool_started.get(call_id, time.monotonic())) * 1000
                    ),
                },
            )

        def attempt(key: str, profile, allow_summary: bool):
            tool_started.clear()
            emitted_tool_end.clear()
            extra_tools = [] if profile.provider == "ollama" else _extra_tools()
            tool_tokens = _tool_tokens_for(key, extra_tools)
            if allow_summary and profile.provider != "ollama":
                fallback = _fallback_profile(key)
                if fallback is not None:
                    try:
                        ensure_summary(
                            session,
                            profile,
                            fallback,
                            SYSTEM_PROMPT,
                            _tool_tokens_for(fallback.key),
                            model_factory,
                            store.save,
                        )
                    except Exception:  # noqa: BLE001
                        # 摘要失败不阻塞本次对话
                        pass
            view, stats = build_view(
                session,
                profile,
                SYSTEM_PROMPT,
                tool_tokens,
                hydrate=lambda items: hydrate_messages(items, attachment_store),
            )
            if not stats.fits:
                raise ContextBudgetError(
                    f"{profile.label} 的上下文窗口放不下系统提示和工具："
                    f"视图 {stats.view_tokens} tokens，窗口 {profile.context_window}。"
                )
            graph = graph_builder(manager, model_key=key, extra_tools=extra_tools)
            initial_count = len(view)
            final_messages = view
            for mode, payload in graph.stream(
                {"messages": view},
                config={"recursion_limit": 25},
                stream_mode=["messages", "updates", "values"],
            ):
                if run.cancel.is_set():
                    break
                if mode == "messages":
                    message, _meta = payload
                    if isinstance(message, AIMessageChunk):
                        text_chunk = _text(message.content)
                        if text_chunk:
                            emit(run, "token", {"run_id": run.run_id, "text": text_chunk})
                    elif isinstance(message, ToolMessage):
                        emit_tool_end(message)
                elif mode == "updates":
                    for _node, update in payload.items():
                        for message in update.get("messages", []):
                            if isinstance(message, AIMessage) and message.tool_calls:
                                for call in message.tool_calls:
                                    call_id = call.get("id") or "unknown"
                                    tool_started[call_id] = time.monotonic()
                                    emit(
                                        run,
                                        "tool_start",
                                        {
                                            "run_id": run.run_id,
                                            "tool": call.get("name", "tool"),
                                            "call_id": call_id,
                                            "args": call.get("args", {}),
                                        },
                                    )
                            elif isinstance(message, ToolMessage):
                                emit_tool_end(message)
                elif mode == "values":
                    final_messages = payload["messages"]
            return final_messages[initial_count:]

        try:
            key = effective_model_key(session)
            profile = get_profile(key)
            session.model_key = key
            manager.set_model_key(key)
            manager.set_current_session(session.id)
            broker = ApprovalBroker(
                run.run_id,
                session.id,
                lambda event, data: emit(run, event, data),
                undo_entries.setdefault(session.id, []),
            )
            run.broker = broker
            set_approval_broker(broker)
            try:
                new_messages = attempt(key, profile, allow_summary=True)
                final_key = key
            except Exception as exc:
                fallback = _fallback_profile(key)
                can_fallback = (
                    profile.provider != "ollama"
                    and fallback is not None
                    and is_network_failure(exc)
                    and _probe_local(fallback.base_url)[0]
                )
                if not can_fallback:
                    raise
                reason = str(exc).strip()[:200] or "网络不可用"
                emit(
                    run,
                    "model_fallback",
                    {
                        "run_id": run.run_id,
                        "from": key,
                        "to": fallback.key,
                        "label": fallback.label,
                        "reason": reason,
                        "restart": True,
                    },
                )
                session.model_key = fallback.key
                store.save(session)
                manager.set_model_key(fallback.key)
                try:
                    new_messages = attempt(fallback.key, fallback, allow_summary=False)
                    final_key = fallback.key
                except Exception as local_exc:
                    raise RuntimeError(
                        f"云端连接失败（{exc}）；本地模型重试也失败（{local_exc}）"
                    ) from local_exc
            session.messages.extend(dehydrate_messages(new_messages, attachment_store))
            store.save(session)
        except Exception as exc:
            emit(run, "error", {"run_id": run.run_id, "code": "run_error", "message": str(exc)})
        finally:
            set_approval_broker(None)
            with runs_lock:
                if active_runs.get(session.id) == run.run_id:
                    active_runs.pop(session.id, None)
            emit(
                run,
                "done",
                {
                    "run_id": run.run_id,
                    "interrupted": run.cancel.is_set(),
                    "model_key": final_key,
                },
            )
            run.events.put(None)

    app = FastAPI(title="LangGraph Agent Desktop API")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def require_token(x_desktop_token: str | None = Header(default=None)) -> None:
        if token and x_desktop_token != token:
            raise HTTPException(status_code=401, detail="invalid token")

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    # 文件面板与 @ 引用的根目录。开发期默认是项目根，打包后由 Electron
    # 通过 DSH_WORKSPACE 传入真实工作区，避免把 sidecar 的 _internal 当成工作区。
    workspace_root = workspace or os.getenv("DSH_WORKSPACE") or root
    file_root = Path(workspace_root).resolve()
    ignored_dirs = {"node_modules", "dist", "build", "__pycache__", ".venv", "venv"}

    def file_entry(path: Path) -> dict:
        relative = path.relative_to(file_root).as_posix()
        entry = {"name": path.name, "path": relative, "type": "dir" if path.is_dir() else "file"}
        if path.is_file():
            try:
                entry["size"] = path.stat().st_size
            except OSError:
                entry["size"] = 0
        return entry

    def resolve_file_path(raw: str) -> Path:
        target = (file_root / (raw or ".")).resolve()
        if target != file_root and file_root not in target.parents:
            raise HTTPException(status_code=400, detail="路径不在工作区内")
        return target

    @app.get("/api/files", dependencies=[Depends(require_token)])
    def list_files(path: str = ".") -> dict:
        target = resolve_file_path(path)
        if not target.is_dir():
            raise HTTPException(status_code=404, detail="目录不存在")
        entries: list[dict] = []
        try:
            children = list(target.iterdir())
        except OSError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        children.sort(key=lambda item: (not item.is_dir(), item.name.lower()))
        for child in children:
            if child.name.startswith(".") or child.name in ignored_dirs:
                continue
            entries.append(file_entry(child))
            if len(entries) >= 300:
                break
        relative = target.relative_to(file_root).as_posix()
        return {"path": "" if relative == "." else relative, "entries": entries}

    @app.get("/api/files/search", dependencies=[Depends(require_token)])
    def search_files(q: str = "") -> dict:
        query = q.strip().lower()
        results: list[dict] = []
        if not query:
            return {"results": results}
        for dirpath, dirnames, filenames in os.walk(file_root):
            dirnames[:] = sorted(
                name
                for name in dirnames
                if not name.startswith(".") and name not in ignored_dirs
            )
            for name in sorted(filenames):
                if query not in name.lower() or name.startswith("."):
                    continue
                results.append(file_entry(Path(dirpath) / name))
                if len(results) >= 50:
                    return {"results": results}
        return {"results": results}

    def effective_model_key(session) -> str:
        """会话记录的模型可能已被删除，回退到当前可用的默认模型。"""
        keys = {profile.key for profile in list_profiles()}
        if session.model_key in keys:
            return session.model_key
        return initial_model_key()

    def session_json(session, attachment_url=None) -> dict:
        key = effective_model_key(session)
        return {
            "id": session.id,
            "title": session.title,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "model_key": key,
            "message_count": len(session.messages),
            "summary": session.summary,
            "summary_count": session.summary_count,
            "summary_upto": session.summary_upto,
            "context": _context_json(session, key),
            "messages": [
                _message_json(m, attachment_url) for m in session.messages
            ],
        }

    def attachment_link(request: Request):
        """为 <img> 提供可直接访问的绝对地址（带 token 查询参数）。"""
        base = str(request.base_url).rstrip("/")
        suffix = f"?token={token}" if token else ""
        return lambda attachment_id: f"{base}/api/attachments/{attachment_id}{suffix}"

    @app.get("/api/sessions", dependencies=[Depends(require_token)])
    def list_sessions() -> dict:
        return {
            "sessions": [
                {
                    "id": meta.id,
                    "title": meta.title,
                    "created_at": meta.created_at,
                    "updated_at": meta.updated_at,
                    "message_count": meta.message_count,
                    "model_key": meta.model_key or initial_model_key(),
                }
                for meta in store.list_sessions()
            ]
        }

    @app.post("/api/sessions", dependencies=[Depends(require_token)])
    def create_session(request: Request, payload: dict | None = None) -> dict:
        payload = payload or {}
        title = str(payload.get("title") or "新会话")
        model_key = payload.get("model_key") or initial_model_key()
        try:
            session = store.create(title, model_key=resolve_selector(model_key))
        except (SessionStoreError, ModelConfigError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        store.set_active_id(session.id)
        return session_json(session, attachment_link(request))

    @app.get("/api/sessions/search", dependencies=[Depends(require_token)])
    def search_sessions(q: str = "") -> dict:
        query = q.strip()
        if not query:
            return {"results": []}
        needle = query.casefold()
        metas = store.list_sessions()
        titles = {meta.id: (meta.title or "") for meta in metas}
        results: list[dict] = []
        for meta in metas:
            title = meta.title or ""
            if needle in title.casefold():
                results.append(
                    {
                        "session_id": meta.id,
                        "title": title,
                        "role": "title",
                        "message_index": -1,
                        "snippet": title,
                    }
                )
        # 消息命中交给后端：SQLite 用 FTS5 全文索引，JSON 后端退回扫描。
        remaining = max(0, 20 - len(results))
        if remaining:
            for hit in store.search(query, limit=remaining):
                results.append(
                    {
                        "session_id": hit["session_id"],
                        "title": titles.get(hit["session_id"], ""),
                        "role": hit["role"],
                        "message_index": hit["message_index"],
                        "snippet": hit["snippet"],
                    }
                )
        return {"results": results[:20]}

    @app.get("/api/sessions/{session_id}", dependencies=[Depends(require_token)])
    def get_session(
        request: Request,
        session_id: str,
        limit: int | None = None,
        before: int | None = None,
    ) -> dict:
        try:
            session = store.load(session_id)
        except SessionStoreError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        payload = session_json(session, attachment_link(request))
        if limit is None:
            return payload
        # 分页：返回 before 之前（不含）最近的 limit 条；before 缺省表示取最新一页。
        total = len(session.messages)
        upper = total if before is None else max(0, min(before, total))
        lower = max(0, upper - max(0, limit))
        payload["messages"] = payload["messages"][lower:upper]
        payload["has_more"] = lower > 0
        payload["next_before"] = lower
        return payload

    @app.delete("/api/sessions/{session_id}", dependencies=[Depends(require_token)])
    def delete_session(session_id: str) -> dict:
        try:
            store.delete(session_id)
        except SessionStoreError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"deleted": session_id}

    @app.patch("/api/sessions/{session_id}", dependencies=[Depends(require_token)])
    def rename_session(session_id: str, payload: dict) -> dict:
        title = str(payload.get("title", "")).strip()
        if not title:
            raise HTTPException(status_code=400, detail="标题不能为空")
        if len(title) > 80:
            raise HTTPException(status_code=400, detail="标题最多 80 个字符")
        try:
            session = store.load(session_id)
        except SessionStoreError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        session.title = title
        store.save(session)
        return {"id": session.id, "title": session.title}

    @app.patch("/api/sessions/{session_id}/model", dependencies=[Depends(require_token)])
    def switch_model(session_id: str, payload: dict) -> dict:
        try:
            session = store.load(session_id)
            key = resolve_selector(str(payload.get("model_key", "")))
            profile = get_profile(key)
        except (SessionStoreError, ModelConfigError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if profile.key != "local" and not profile.api_key:
            raise HTTPException(
                status_code=400,
                detail=f"未找到 {profile.key_env}，无法切换到 {profile.label}",
            )
        session.model_key = key
        store.save(session)
        manager.set_model_key(key)
        return {
            "id": session_id,
            "model_key": key,
            "label": profile.label,
            "context": _context_json(session, key),
        }

    @app.post("/api/sessions/{session_id}/messages", dependencies=[Depends(require_token)])
    def post_message(session_id: str, payload: dict):
        text = str(payload.get("text", "")).strip()
        attachments = payload.get("attachments") or []
        mode = str(payload.get("mode") or "send").strip().lower()
        edit_index = payload.get("edit_index")

        if mode not in {"send", "regenerate"}:
            raise HTTPException(status_code=400, detail="未知的消息模式")
        if mode == "send" and edit_index is None and not text and not attachments:
            raise HTTPException(status_code=400, detail="消息不能为空")

        with runs_lock:
            if session_id in active_runs:
                raise HTTPException(status_code=409, detail="已有回复正在生成")
            try:
                session = store.load(session_id)
            except SessionStoreError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            key = effective_model_key(session)
            if session.model_key != key:
                session.model_key = key
            try:
                profile = get_profile(key)
            except ModelConfigError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if attachments and not profile.supports_vision:
                raise HTTPException(status_code=400, detail="当前模型不支持图片，请切换到千问 VL")

            if mode == "regenerate":
                # 重新生成：丢掉最后一条用户消息之后的所有内容，再用同样的输入跑一次。
                last_human = next(
                    (
                        index
                        for index in range(len(session.messages) - 1, -1, -1)
                        if isinstance(session.messages[index], HumanMessage)
                    ),
                    None,
                )
                if last_human is None:
                    raise HTTPException(status_code=400, detail="没有可重新生成的消息")
                del session.messages[last_human + 1 :]
                text = _text(session.messages[last_human].content)
            elif edit_index is not None:
                # 编辑后重发：改写指定用户消息，并丢弃它之后的全部轮次。
                if isinstance(edit_index, bool) or not isinstance(edit_index, int):
                    raise HTTPException(status_code=400, detail="edit_index 不合法")
                if (
                    edit_index < 0
                    or edit_index >= len(session.messages)
                    or not isinstance(session.messages[edit_index], HumanMessage)
                ):
                    raise HTTPException(status_code=400, detail="只能编辑用户消息")
                if not text and not attachments:
                    raise HTTPException(status_code=400, detail="消息不能为空")
                content = _image_parts(text, attachments, attachment_store)
                del session.messages[edit_index + 1 :]
                session.messages[edit_index] = HumanMessage(content=content)
            else:
                content = _image_parts(text, attachments, attachment_store)
                session.messages.append(HumanMessage(content=content))
                if session.title == "新会话":
                    session.title = (text or "图片对话")[:30]
            store.save(session)
            run = RunState(f"run-{uuid.uuid4().hex[:8]}", session_id)
            runs[run.run_id] = run
            active_runs[session_id] = run.run_id
        thread = threading.Thread(target=run_graph, args=(run, session, text), daemon=True)
        thread.start()

        def event_stream():
            try:
                while True:
                    item = run.events.get()
                    if item is None:
                        break
                    event, data = item
                    yield f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            finally:
                run.cancel.set()
                if run.broker is not None:
                    run.broker.cancel_all()
                with runs_lock:
                    runs.pop(run.run_id, None)
                    # 不要在这里提前移除 active_runs：客户端断开时 run_graph 可能还在
                    # 执行，提前放行会让同一会话出现两个并发写入。active_runs 统一由
                    # run_graph 的 finally 在真正结束时清理。

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.post("/api/sessions/{session_id}/compact", dependencies=[Depends(require_token)])
    def compact_now(request: Request, session_id: str) -> dict:
        with runs_lock:
            if session_id in active_runs:
                raise HTTPException(status_code=409, detail="已有回复正在生成")
            try:
                session = store.load(session_id)
            except SessionStoreError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            key = effective_model_key(session)
            try:
                profile = get_profile(key)
            except ModelConfigError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            target = _fallback_profile(key) or profile
            try:
                changed = ensure_summary(
                    session,
                    profile,
                    target,
                    SYSTEM_PROMPT,
                    _tool_tokens_for(target.key),
                    model_factory,
                    store.save,
                    force=True,
                )
            except SummaryError as exc:
                raise HTTPException(status_code=502, detail=f"压缩失败：{exc}") from exc
            if not changed:
                raise HTTPException(status_code=400, detail="没有可更新的摘要")
            payload = session_json(session, attachment_link(request))
            payload["summary_model"] = key
            return payload

    @app.get("/api/attachments/{attachment_id}")
    def get_attachment(
        attachment_id: str,
        token_param: str = Query("", alias="token"),
        x_desktop_token: str = Header(default=""),
    ) -> Response:
        # <img> 标签带不了自定义请求头，所以额外接受 ?token= 查询参数。
        if token and token_param != token and x_desktop_token != token:
            raise HTTPException(status_code=401, detail="缺少或无效的桌面 token")
        try:
            raw, mime = attachment_store.read(attachment_id)
        except AttachmentError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return Response(
            content=raw,
            media_type=mime,
            headers={"Cache-Control": "private, max-age=3600"},
        )

    @app.post("/api/runs/{run_id}/cancel", dependencies=[Depends(require_token)])
    def cancel_run(run_id: str):
        run = runs.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run 不存在")
        run.cancel.set()
        if run.broker is not None:
            run.broker.cancel_all()
        return {"cancelled": run_id}

    @app.post("/api/runs/{run_id}/approvals/{approval_id}", dependencies=[Depends(require_token)])
    def resolve_approval(run_id: str, approval_id: str, payload: dict):
        run = runs.get(run_id)
        if run is None or run.broker is None:
            raise HTTPException(status_code=404, detail="run 不存在")
        approved = _as_bool(payload.get("approved"))
        if not run.broker.resolve(approval_id, approved):
            raise HTTPException(status_code=404, detail="审批请求不存在或已处理")
        return {"approval_id": approval_id, "approved": approved}

    @app.post("/api/sessions/{session_id}/undo", dependencies=[Depends(require_token)])
    def undo_write(session_id: str):
        entries = undo_entries.get(session_id, [])
        try:
            path = undo_last(entries)
        except UndoEmpty as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"restored": str(path)}

    @app.get("/api/mcp/servers", dependencies=[Depends(require_token)])
    def mcp_servers() -> dict:
        try:
            return {"servers": [_mcp_status_json(item) for item in mcp.list_servers()]}
        except (McpError, McpManagerError) as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/api/mcp/catalog", dependencies=[Depends(require_token)])
    def mcp_catalog() -> dict:
        try:
            return {"presets": mcp.catalog()}
        except (McpError, McpManagerError) as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/mcp/servers", dependencies=[Depends(require_token)])
    def add_mcp_server(payload: dict) -> dict:
        preset_id = str(payload.get("preset_id") or "").strip()
        if not preset_id:
            raise HTTPException(status_code=400, detail="缺少 preset_id")
        env_values = payload.get("env") or {}
        if not isinstance(env_values, dict):
            raise HTTPException(status_code=400, detail="env 必须是对象")
        try:
            status = mcp.add_server(
                preset_id,
                env_values={str(k): str(v) for k, v in env_values.items()},
                permission=payload.get("permission"),
                name=payload.get("name"),
                # 启动握手可能要几十秒，放后台，避免请求卡住
                background=True,
            )
        except (McpError, McpManagerError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"server": _mcp_status_json(status)}

    @app.delete("/api/mcp/servers/{server_id}", dependencies=[Depends(require_token)])
    def remove_mcp_server(server_id: str) -> dict:
        try:
            mcp.remove_server(server_id)
        except (McpError, McpManagerError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"removed": server_id}

    @app.post("/api/mcp/servers/{server_id}/permission", dependencies=[Depends(require_token)])
    def set_mcp_permission(server_id: str, payload: dict) -> dict:
        try:
            status = mcp.set_permission(server_id, str(payload.get("permission") or ""))
        except (McpError, McpManagerError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"server": _mcp_status_json(status)}

    @app.post("/api/mcp/servers/{server_id}/restart", dependencies=[Depends(require_token)])
    def restart_mcp(server_id: str) -> dict:
        try:
            status = mcp.restart(server_id, background=True)
        except (McpError, McpManagerError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"server": _mcp_status_json(status)}

    @app.post("/api/mcp/servers/{server_id}/enable", dependencies=[Depends(require_token)])
    def enable_mcp(server_id: str, payload: dict) -> dict:
        try:
            status = mcp.set_enabled(
                server_id, _as_bool(payload.get("enabled"), default=True), background=True
            )
        except (McpError, McpManagerError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"server": _mcp_status_json(status)}

    @app.get("/api/models", dependencies=[Depends(require_token)])
    def models() -> dict:
        items = []
        for profile in list_profiles():
            if profile.provider == "ollama":
                available, reason = _probe_local(profile.base_url)
            else:
                available = bool(profile.api_key)
                reason = "" if available else "未配置 API Key"
            item = {
                "key": profile.key,
                "label": profile.label,
                "model": profile.model,
                "available": available,
                "supports_vision": profile.supports_vision,
                "context_window": profile.context_window,
            }
            if not available:
                item["reason"] = reason or f"未配置 {profile.key_env}"
            items.append(item)
        return {"models": items}

    @app.get("/api/agents", dependencies=[Depends(require_token)])
    def agents(session_id: str | None = None) -> dict:
        records = manager.list_records()
        if session_id:
            records = [record for record in records if record.session_id == session_id]
        return {
            "agents": [
                {
                    "id": record.id,
                    "task": record.task,
                    "session_id": record.session_id,
                    "status": record.status,
                    "created_at": record.created_at,
                    "started_at": record.started_at,
                    "finished_at": record.finished_at,
                    "result": record.result,
                    "error": record.error,
                }
                for record in records
            ]
        }

    @app.post("/api/agents/{agent_id}/cancel", dependencies=[Depends(require_token)])
    def cancel_agent(agent_id: str) -> dict:
        try:
            record = manager.cancel(agent_id)
        except SubagentError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"id": record.id, "status": record.status}

    @app.on_event("startup")
    def start_mcp_servers() -> None:
        # MCP 握手可能要几十秒；放后台线程，避免拖住 READY 和窗口启动。
        def run() -> None:
            try:
                mcp.start_enabled()
            except Exception:
                pass

        threading.Thread(target=run, name="mcp-startup", daemon=True).start()

    @app.on_event("shutdown")
    def stop_mcp_servers() -> None:
        try:
            mcp.stop_all()
        except Exception:
            pass

    return app


def main() -> None:
    # 打包后的 sidecar 本身即可作为子 agent worker，避免依赖外部 Python。
    if "--subagent-worker" in sys.argv:
        from subagent_worker import main as worker_main

        index = sys.argv.index("--subagent-worker")
        raise SystemExit(worker_main(sys.argv[index + 1 :]))

    # 打包后的 sidecar 本身即可作为 SQLite MCP 的 stdio 入口，
    # 避免依赖外部 Python 解释器。
    if "--mcp-sqlite" in sys.argv:
        from mcp_sqlite_server import mcp

        mcp.run()
        return

    import uvicorn

    # 只在真正提供服务时才构建应用：子 agent worker / SQLite MCP 进程
    # 走上面的分支，不应顺带初始化会话库与 MCP 管理器。
    data_root = Path(os.getenv("DSH_DATA_DIR", str(BASE_DIR)))
    app = create_app(BASE_DIR, DEFAULT_TOKEN, data_root=data_root)

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    config = uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="info")
    server = uvicorn.Server(config)
    original_startup = server.startup

    async def startup(sockets=None):
        await original_startup(sockets=sockets)
        port = server.servers[0].sockets[0].getsockname()[1]
        print(f"READY {json.dumps({'port': port})}", flush=True)

    server.startup = startup
    server.run()


if __name__ == "__main__":
    main()
