from __future__ import annotations

import argparse
import json
import os
import queue
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from graph_agent import SYSTEM_PROMPT, build_graph
from model_registry import (
    ModelConfigError,
    get_profile,
    initial_model_key,
    list_profiles,
    resolve_selector,
)
from session_store import SessionStore, SessionStoreError
from subagent_manager import SubagentManager

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_TOKEN = os.getenv("DSH_DESKTOP_TOKEN", "")


def _text(content: Any) -> str:
    if isinstance(content, list):
        return "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    return str(content)


def _message_json(message: Any) -> dict:
    role = {
        "human": "user",
        "ai": "assistant",
        "tool": "tool",
        "system": "system",
    }.get(getattr(message, "type", ""), getattr(message, "type", "unknown"))
    data = {"role": role, "content": _text(message.content)}
    if isinstance(message, AIMessage):
        data["tool_calls"] = [
            {"id": call.get("id"), "name": call.get("name"), "args": call.get("args", {})}
            for call in (message.tool_calls or [])
        ]
    if isinstance(message, ToolMessage):
        data["tool_call_id"] = message.tool_call_id
        data["name"] = message.name
    return data


def create_app(
    root: Path,
    token: str,
    graph_builder=build_graph,
) -> FastAPI:
    store = SessionStore(root / ".sessions")
    manager = SubagentManager(project_root=root)

    class RunState:
        def __init__(self, run_id: str, session_id: str):
            self.run_id = run_id
            self.session_id = session_id
            self.cancel = threading.Event()
            self.events: queue.Queue = queue.Queue()

    runs: dict[str, RunState] = {}
    active_run_id: str | None = None
    runs_lock = threading.Lock()

    def emit(run: RunState, event: str, data: dict) -> None:
        run.events.put((event, data))

    def run_graph(run: RunState, session, text: str) -> None:
        from langchain_core.messages import AIMessageChunk

        key = session.model_key or initial_model_key()
        manager.set_model_key(key)
        graph = graph_builder(manager, model_key=key)
        messages = [SystemMessage(content=SYSTEM_PROMPT), *session.messages]
        final_messages = messages
        tool_started: dict[str, float] = {}
        try:
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
                        call_id = message.tool_call_id
                        emit(
                            run,
                            "tool_end",
                            {
                                "run_id": run.run_id,
                                "tool": message.name or "tool",
                                "call_id": call_id,
                                "status": "error" if str(message.content).startswith("[错误]") else "ok",
                                "duration_ms": int((time.monotonic() - tool_started.get(call_id, time.monotonic())) * 1000),
                            },
                        )
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
                                call_id = message.tool_call_id
                                emit(
                                    run,
                                    "tool_end",
                                    {
                                        "run_id": run.run_id,
                                        "tool": message.name or "tool",
                                        "call_id": call_id,
                                        "status": "error" if str(message.content).startswith("[错误]") else "ok",
                                        "duration_ms": int((time.monotonic() - tool_started.get(call_id, time.monotonic())) * 1000),
                                    },
                                )
                elif mode == "values":
                    final_messages = payload["messages"]
            session.messages = [
                m for m in final_messages if not isinstance(m, SystemMessage)
            ]
            store.save(session)
        except Exception as exc:
            emit(run, "error", {"run_id": run.run_id, "code": "run_error", "message": str(exc)})
        finally:
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

    def session_json(session) -> dict:
        return {
            "id": session.id,
            "title": session.title,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "model_key": session.model_key or initial_model_key(),
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

    @app.get("/api/sessions/{session_id}", dependencies=[Depends(require_token)])
    def get_session(session_id: str) -> dict:
        try:
            return session_json(store.load(session_id))
        except SessionStoreError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.delete("/api/sessions/{session_id}", dependencies=[Depends(require_token)])
    def delete_session(session_id: str) -> dict:
        try:
            store.delete(session_id)
        except SessionStoreError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"deleted": session_id}

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
        nonlocal active_run_id
        text = str(payload.get("text", "")).strip()
        if not text:
            raise HTTPException(status_code=400, detail="消息不能为空")
        with runs_lock:
            if active_run_id is not None:
                raise HTTPException(status_code=409, detail="已有回复正在生成")
            try:
                session = store.load(session_id)
            except SessionStoreError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            session.messages.append(HumanMessage(content=text))
            if session.title == "新会话":
                session.title = text[:30]
            store.save(session)
            run = RunState(f"run-{uuid.uuid4().hex[:8]}", session_id)
            runs[run.run_id] = run
            active_run_id = run.run_id
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
                with runs_lock:
                    runs.pop(run.run_id, None)
                    active_run_id = None

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.post("/api/runs/{run_id}/cancel", dependencies=[Depends(require_token)])
    def cancel_run(run_id: str):
        run = runs.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run 不存在")
        run.cancel.set()
        return {"cancelled": run_id}

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
            }
            if not available:
                item["reason"] = f"未配置 {profile.key_env}"
            items.append(item)
        return {"models": items}

    @app.get("/api/agents", dependencies=[Depends(require_token)])
    def agents() -> dict:
        return {
            "agents": [
                {
                    "id": record.id,
                    "task": record.task,
                    "status": record.status,
                    "created_at": record.created_at,
                    "started_at": record.started_at,
                    "finished_at": record.finished_at,
                    "result": record.result,
                    "error": record.error,
                }
                for record in manager.list_records()
            ]
        }

    return app


app = create_app(BASE_DIR, DEFAULT_TOKEN)


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    config = uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="info")
    server = uvicorn.Server(config)
    server.run()


if __name__ == "__main__":
    main()
