from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from graph_agent import SYSTEM_PROMPT, build_graph, build_llm
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

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_TOKEN = os.getenv("DSH_DESKTOP_TOKEN", "")


def _text(content: Any) -> str:
    if isinstance(content, list):
        return "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    return str(content)


def _image_parts(text: str, attachments: Any) -> str | list[dict]:
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
        if len(data_url) > 8_000_000:
            raise HTTPException(400, "图片太大，请压缩到约 6MB 以内")
        parts.append({"type": "image_url", "image_url": {"url": data_url}})
    return parts


def _message_json(message: Any) -> dict:
    role = {
        "human": "user",
        "ai": "assistant",
        "tool": "tool",
        "system": "system",
    }.get(getattr(message, "type", ""), getattr(message, "type", "unknown"))
    data = {"role": role, "content": _text(message.content)}
    if isinstance(message.content, list):
        data["attachments"] = [
            {"type": "image", "data_url": part["image_url"]["url"]}
            for part in message.content
            if isinstance(part, dict)
            and part.get("type") == "image_url"
            and isinstance(part.get("image_url"), dict)
            and part["image_url"].get("url")
        ]
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


COMPACT_RATIO = 0.7
COMPACT_KEEP_RECENT = 10
SUMMARY_PREFIX = "【较早对话的摘要】"


def estimate_tokens(messages: list) -> int:
    """粗略估算 token 数（与前端一致：按字符数的 1/4）。"""
    chars = sum(len(_text(getattr(message, "content", ""))) for message in messages)
    return max(0, chars // 4)


def summarize_messages(messages: list, model_key: str) -> str:
    """调用模型把早期消息压缩成一段中文摘要。"""
    lines: list[str] = []
    for message in messages:
        role = {"human": "用户", "ai": "助手", "tool": "工具"}.get(
            getattr(message, "type", ""), "消息"
        )
        text = _text(message.content).strip()
        if text:
            lines.append(f"{role}: {text}")
    if not lines:
        return ""
    transcript = "\n".join(lines)[-20_000:]
    response = build_llm(model_key).invoke(
        [
            HumanMessage(
                content=(
                    "请把下面这段对话压缩成简洁的中文摘要，保留关键事实、结论、"
                    "涉及的文件路径和未完成事项；不要编造内容。\n\n" + transcript
                )
            )
        ]
    )
    return _text(response.content).strip()[:2_000]


def compact_session(
    session, model_key: str, context_window: int, summarizer=None
) -> bool:
    """上下文超阈值时压缩早期消息；返回是否真的压缩了。"""
    if context_window <= 0:
        return False
    if len(session.messages) <= COMPACT_KEEP_RECENT + 1:
        return False
    if estimate_tokens(session.messages) < int(context_window * COMPACT_RATIO):
        return False
    older = session.messages[:-COMPACT_KEEP_RECENT]
    if not older:
        return False
    try:
        summary = (summarizer or summarize_messages)(older, model_key)
    except Exception:  # noqa: BLE001
        # 摘要失败不阻塞正常对话
        return False
    if not summary:
        return False

    session.summary = (
        f"{session.summary}\n{summary}".strip() if session.summary else summary
    )
    session.summary_count += len(older)
    session.messages = session.messages[-COMPACT_KEEP_RECENT:]
    return True


def create_app(
    root: Path,
    token: str,
    graph_builder=build_graph,
    mcp_manager=None,
    data_root: Path | None = None,
    workspace: Path | None = None,
    summarizer=None,
) -> FastAPI:
    data_dir = Path(data_root or os.getenv("DSH_DATA_DIR") or root)
    store = _create_session_store(data_dir)
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

    def run_graph(run: RunState, session, text: str) -> None:
        from langchain_core.messages import AIMessageChunk

        final_messages = None
        tool_started: dict[str, float] = {}
        emitted_tool_end: set[str] = set()

        def emit_tool_end(message: ToolMessage) -> None:
            # 同一个 ToolMessage 可能同时出现在 messages 和 updates 两种模式里，
            # 按 call_id 去重，避免前端重复更新同一张工具卡片。
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
        try:
            key = session.model_key or initial_model_key()
            manager.set_model_key(key)
            manager.set_current_session(session.id)
            try:
                profile = get_profile(key)
                if compact_session(session, key, profile.context_window, summarizer):
                    store.save(session)
            except Exception:  # noqa: BLE001
                # 压缩失败不影响本次对话
                pass
            broker = ApprovalBroker(
                run.run_id,
                session.id,
                lambda event, data: emit(run, event, data),
                undo_entries.setdefault(session.id, []),
            )
            run.broker = broker
            set_approval_broker(broker)
            extra_tools = []
            try:
                extra_tools = build_mcp_tools(mcp, mcp.enabled_ids())
            except Exception:
                extra_tools = []
            graph = graph_builder(manager, model_key=key, extra_tools=extra_tools)
            messages = [SystemMessage(content=SYSTEM_PROMPT)]
            if session.summary:
                messages.append(
                    SystemMessage(content=f"{SUMMARY_PREFIX}\n{session.summary}")
                )
            messages.extend(session.messages)
            final_messages = messages
            for mode, payload in graph.stream(
                {"messages": messages},
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
            session.messages = [
                m for m in final_messages if not isinstance(m, SystemMessage)
            ]
            store.save(session)
        except Exception as exc:
            emit(run, "error", {"run_id": run.run_id, "code": "run_error", "message": str(exc)})
        finally:
            set_approval_broker(None)
            # 先释放会话锁，再发送 done。否则客户端收到 done 后立刻发下一条消息时，
            # active_runs 可能还没清理，会错误返回“已有回复正在生成”。
            with runs_lock:
                if active_runs.get(session.id) == run.run_id:
                    active_runs.pop(session.id, None)
            emit(run, "done", {"run_id": run.run_id, "interrupted": run.cancel.is_set()})
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

    def session_json(session) -> dict:
        return {
            "id": session.id,
            "title": session.title,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "model_key": session.model_key or initial_model_key(),
            "message_count": len(session.messages),
            "summary": session.summary,
            "summary_count": session.summary_count,
            "messages": [_message_json(m) for m in session.messages],
        }

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
    def create_session(payload: dict | None = None) -> dict:
        payload = payload or {}
        title = str(payload.get("title") or "新会话")
        model_key = payload.get("model_key") or initial_model_key()
        try:
            session = store.create(title, model_key=resolve_selector(model_key))
        except (SessionStoreError, ModelConfigError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        store.set_active_id(session.id)
        return session_json(session)

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
        session_id: str, limit: int | None = None, before: int | None = None
    ) -> dict:
        try:
            session = store.load(session_id)
        except SessionStoreError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        payload = session_json(session)
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
        return {"id": session_id, "model_key": key, "label": profile.label}

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
            key = session.model_key or initial_model_key()
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
                content = _image_parts(text, attachments)
                del session.messages[edit_index + 1 :]
                session.messages[edit_index] = HumanMessage(content=content)
            else:
                content = _image_parts(text, attachments)
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
            available = profile.key == "local" or bool(profile.api_key)
            item = {
                "key": profile.key,
                "label": profile.label,
                "model": profile.model,
                "available": available,
                "supports_vision": profile.supports_vision,
                "context_window": profile.context_window,
            }
            if not available:
                item["reason"] = f"未配置 {profile.key_env}"
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
