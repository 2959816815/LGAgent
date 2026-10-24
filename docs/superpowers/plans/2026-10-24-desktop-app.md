# LangGraph Agent Desktop App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给现有 Python LangGraph Agent 做一个开发版 Electron 桌面工作台：会话、流式聊天、模型切换、工具卡片和子 agent 任务台。

**Architecture:** Electron 主进程启动 Python FastAPI sidecar，React 渲染进程通过本地 HTTP + SSE 访问。Python 复用现有 `SessionStore`、`ModelRuntime` 构建逻辑、`SubagentManager`；桌面端只新增传输层和界面，不重写 agent。

**Tech Stack:** Python 3.12、FastAPI 0.141、uvicorn 0.52、LangGraph、LangChain OpenAI、Electron 33+、React 18、TypeScript、Vite、Vitest、Testing Library。

**Spec:** `docs/superpowers/specs/2026-10-24-desktop-app-design.md`

---

## File Structure

### Python

| 文件 | 职责 |
|---|---|
| `session_store.py` | 会话 JSON 持久化；新增 `model_key` 字段 |
| `desktop_api.py` | FastAPI 应用：鉴权、会话、模型、SSE、子 agent |
| `tests/test_desktop_api.py` | API 单元/集成测试 |
| `requirements-desktop.txt` | FastAPI/uvicorn/httpx 依赖声明 |

### Electron / React

| 文件 | 职责 |
|---|---|
| `desktop/package.json` | Electron/Vite/React 依赖与脚本 |
| `desktop/main.js` | 启动 sidecar、创建窗口、IPC |
| `desktop/preload.js` | 只暴露 `getConfig()` |
| `desktop/vite.config.ts` | Vite 开发服务器 |
| `desktop/index.html` | 渲染入口 |
| `desktop/src/main.tsx` | React 挂载 |
| `desktop/src/App.tsx` | 三栏状态与数据流 |
| `desktop/src/api.ts` | REST + SSE 客户端 |
| `desktop/src/styles.css` | 深色主题 token 与布局 |
| `desktop/src/components/Sidebar.tsx` | 会话列表 |
| `desktop/src/components/Chat.tsx` | 消息流 |
| `desktop/src/components/Composer.tsx` | 输入框、模型胶囊、发送/停止 |
| `desktop/src/components/ToolCard.tsx` | 工具调用卡片 |
| `desktop/src/components/RightPanel.tsx` | 任务/工具/上下文 |
| `desktop/src/components/AgentCard.tsx` | 子 agent 卡片 |
| `desktop/src/types.ts` | 前后端共享的 TS 类型 |

### 修改

| 文件 | 改动 |
|---|---|
| `session_store.py` | `Session.model_key`、`SessionMeta.model_key`、保存/加载兼容 |
| `.gitignore` | 忽略 `desktop/node_modules`、`desktop/dist` |

---

## Task 1: 会话模型字段与桌面 API 骨架

**Files:**
- Modify: `session_store.py:38-54, 116-133, 135-155, 157-180, 182-204`
- Create: `desktop_api.py`
- Create: `tests/test_desktop_api.py`
- Create: `requirements-desktop.txt`

- [ ] **Step 1: 写会话 `model_key` 的失败测试**

在 `tests/test_desktop_api.py` 写：

```python
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from desktop_api import create_app
from session_store import SessionStore


class SessionModelKeyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = SessionStore(self.root / ".sessions")

    def test_create_and_load_model_key(self):
        session = self.store.create("桌面端", model_key="deepseek")
        self.store.save(session)
        loaded = self.store.load(session.id)
        self.assertEqual(loaded.model_key, "deepseek")

    def test_old_session_without_model_key_is_compatible(self):
        session = self.store.create("旧会话")
        path = self.root / ".sessions" / f"{session.id}.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw.pop("model_key", None)
        path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        loaded = self.store.load(session.id)
        self.assertIsNone(loaded.model_key)
        self.assertIsNone(self.store.list_sessions()[0].model_key)

    def test_meta_exposes_model_key(self):
        self.store.create("会话 A", model_key="qwen")
        meta = self.store.list_sessions()[0]
        self.assertEqual(meta.model_key, "qwen")


class DesktopSessionApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.app = create_app(root=self.root, token="test-token")
        self.client = TestClient(self.app)
        self.headers = {"X-Desktop-Token": "test-token"}

    def test_health_does_not_require_token(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_session_crud(self):
        created = self.client.post(
            "/api/sessions",
            json={"title": "测试会话", "model_key": "local"},
            headers=self.headers,
        )
        self.assertEqual(created.status_code, 200)
        session_id = created.json()["id"]
        self.assertEqual(created.json()["model_key"], "local")

        listed = self.client.get("/api/sessions", headers=self.headers)
        self.assertEqual(len(listed.json()["sessions"]), 1)

        detail = self.client.get(f"/api/sessions/{session_id}", headers=self.headers)
        self.assertEqual(detail.json()["title"], "测试会话")

        deleted = self.client.delete(f"/api/sessions/{session_id}", headers=self.headers)
        self.assertEqual(deleted.status_code, 200)

    def test_session_requires_token(self):
        response = self.client.get("/api/sessions")
        self.assertEqual(response.status_code, 401)

    def test_model_patch_persists(self):
        created = self.client.post(
            "/api/sessions",
            json={"title": "模型测试"},
            headers=self.headers,
        ).json()
        response = self.client.patch(
            f"/api/sessions/{created['id']}/model",
            json={"model_key": "deepseek"},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        detail = self.client.get(f"/api/sessions/{created['id']}", headers=self.headers)
        self.assertEqual(detail.json()["model_key"], "deepseek")

    def test_unknown_model_is_400(self):
        created = self.client.post(
            "/api/sessions", json={"title": "模型测试"}, headers=self.headers
        ).json()
        response = self.client.patch(
            f"/api/sessions/{created['id']}/model",
            json={"model_key": "nope"},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 400)

    def test_models_do_not_leak_keys(self):
        response = self.client.get("/api/models", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        for item in response.json()["models"]:
            self.assertNotIn("api_key", item)
```

- [ ] **Step 2: 运行测试确认失败**

Run:
```powershell
D:\Anaconda3\envs\rag\python.exe -m unittest -v tests.test_desktop_api.SessionModelKeyTests
```
Expected: FAIL，`ImportError: cannot import name 'desktop_api'` 或 `TypeError: SessionStore.create() got an unexpected keyword argument 'model_key'`。

- [ ] **Step 3: 给 `session_store.py` 增加 `model_key`**

`Session` 和 `SessionMeta` 各加一个末尾可选字段：

```python
@dataclass
class Session:
    id: str
    title: str
    created_at: str
    updated_at: str
    messages: list[BaseMessage] = field(default_factory=list)
    model_key: str | None = None


@dataclass
class SessionMeta:
    id: str
    title: str
    created_at: str
    updated_at: str
    message_count: int
    model_key: str | None = None
```

`create()` 增加参数并传入：

```python
    def create(
        self,
        title: str = DEFAULT_TITLE,
        model_key: str | None = None,
    ) -> Session:
        ...
        session = Session(
            id=session_id,
            title=clean_title(title),
            created_at=_iso(now),
            updated_at=_iso(now),
            messages=[],
            model_key=model_key,
        )
```

`save()` 的 payload 增加：

```python
            "model_key": session.model_key,
```

`load()` 的返回改为：

```python
        model_key = raw.get("model_key")
        if model_key is not None and not isinstance(model_key, str):
            raise ValueError("model_key 必须是字符串或 null")
        return Session(session_id, title, created_at, updated_at, messages, model_key)
```

`list_sessions()` 的 `SessionMeta` 增加：

```python
                    model_key=session.model_key,
```

- [ ] **Step 4: 运行会话测试**

Run:
```powershell
D:\Anaconda3\envs\rag\python.exe -m unittest -v tests.test_desktop_api.SessionModelKeyTests
```
Expected: 3 tests PASS。

- [ ] **Step 5: 创建 `requirements-desktop.txt`**

```text
fastapi>=0.115
uvicorn>=0.30
httpx>=0.27
```

Run:
```powershell
D:\Anaconda3\envs\rag\python.exe -m pip install -r requirements-desktop.txt
```

- [ ] **Step 6: 创建 `desktop_api.py` 的会话/模型 API**

下面是完整骨架，后续 Task 2 只在这个文件里追加 SSE：

```python
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
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
```

- [ ] **Step 7: 运行 API 测试**

Run:
```powershell
D:\Anaconda3\envs\rag\python.exe -m unittest -v tests.test_desktop_api
```
Expected: 9 tests PASS（3 个 store + 6 个 API）。

- [ ] **Step 8: Commit**

```powershell
git add Langgraph/session_store.py Langgraph/desktop_api.py Langgraph/requirements-desktop.txt Langgraph/tests/test_desktop_api.py
git diff --cached --name-only
git commit -m "feat: add desktop session api"
```

---

## Task 2: SSE 消息流与取消

**Files:**
- Modify: `desktop_api.py`（追加 `RunState`、`runs`、`POST /messages`、`POST /cancel`）
- Modify: `tests/test_desktop_api.py`

- [ ] **Step 1: 追加失败测试**

在 `tests/test_desktop_api.py` 增加：

```python
class FakeChunk(AIMessageChunk):
    pass


class FakeGraph:
    def __init__(self):
        self.cancelled = False

    def stream(self, *args, **kwargs):
        yield ("messages", (FakeChunk(content="你"), {}))
        yield ("messages", (FakeChunk(content="好"), {}))
        yield ("updates", {"agent": {"messages": [AIMessage(content="你好")]}})
        yield ("values", {"messages": [HumanMessage(content="hi"), AIMessage(content="你好")]})


class StreamingApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.app = create_app(
            root=self.root,
            token="t",
            graph_builder=lambda manager, model_key: FakeGraph(),
        )
        self.client = TestClient(self.app)
        self.headers = {"X-Desktop-Token": "t"}

    def test_message_stream_tokens_and_done(self):
        session = self.client.post(
            "/api/sessions", json={"title": "流式"}, headers=self.headers
        ).json()
        response = self.client.post(
            f"/api/sessions/{session['id']}/messages",
            json={"text": "hi"},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        body = response.text
        self.assertIn("event: token", body)
        self.assertIn('"text": "你"', body)
        self.assertIn("event: done", body)
```

需要确保测试文件顶部导入：

```python
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
```

- [ ] **Step 2: 运行测试确认失败**

Run:
```powershell
D:\Anaconda3\envs\rag\python.exe -m unittest -v tests.test_desktop_api.StreamingApiTests
```
Expected: FAIL，`404` 或 `AttributeError`（`/messages` 不存在）。

- [ ] **Step 3: 在 `desktop_api.py` 增加运行状态与 SSE**

在 `create_app` 内、`manager = ...` 之后加入：

```python
import queue
import threading
import uuid
import asyncio
import time

class RunState:
    def __init__(self, run_id: str, session_id: str):
        self.run_id = run_id
        self.session_id = session_id
        self.cancel = threading.Event()
        self.events: queue.Queue = queue.Queue()

runs: dict[str, RunState] = {}
active_run_id: str | None = None
runs_lock = threading.Lock()
```

在 `POST /sessions` 之前加：

```python
    def emit(run: RunState, event: str, data: dict) -> None:
        run.events.put((event, data))

    def run_graph(run: RunState, session, text: str) -> None:
        from langchain_core.messages import AIMessageChunk, ToolMessage

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
```

添加消息端点（放在模型端点之后）：

```python
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
```

在文件顶部补导入：

```python
from fastapi.responses import StreamingResponse
```

- [ ] **Step 4: 运行流式测试**

Run:
```powershell
D:\Anaconda3\envs\rag\python.exe -m unittest -v tests.test_desktop_api.StreamingApiTests
```
Expected: 1 test PASS（token 流和 done 事件）。

- [ ] **Step 5: 运行全部桌面 API 测试**

Run:
```powershell
D:\Anaconda3\envs\rag\python.exe -m unittest -v tests.test_desktop_api
```
Expected: 10 tests PASS。

- [ ] **Step 6: Commit**

```powershell
git add Langgraph/desktop_api.py Langgraph/tests/test_desktop_api.py
git diff --cached --name-only
git commit -m "feat: stream desktop chat over sse"
```

---

## Task 3: Electron 壳与 sidecar 启动

**Files:**
- Create: `desktop/package.json`
- Create: `desktop/main.js`
- Create: `desktop/preload.js`
- Create: `desktop/vite.config.ts`
- Create: `desktop/index.html`
- Create: `desktop/tsconfig.json`
- Modify: `.gitignore`

- [ ] **Step 1: 写 `desktop/package.json`**

```json
{
  "name": "langgraph-agent-desktop",
  "private": true,
  "version": "0.1.0",
  "main": "main.js",
  "scripts": {
    "dev": "concurrently -k \"vite --port 5173\" \"wait-on http://localhost:5173 && electron .\"",
    "build": "vite build",
    "test": "vitest run"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  },
  "devDependencies": {
    "@testing-library/jest-dom": "^6.4.8",
    "@testing-library/react": "^16.0.1",
    "@types/react": "^18.3.5",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.1",
    "concurrently": "^9.0.1",
    "electron": "^33.0.0",
    "jsdom": "^25.0.1",
    "typescript": "^5.6.2",
    "vite": "^5.4.8",
    "vitest": "^2.1.1",
    "wait-on": "^8.0.1"
  }
}
```

Run:
```powershell
cd D:\Vibecoding\Langgraph\desktop
pnpm install
```

- [ ] **Step 2: 写 `desktop/main.js`**

```javascript
const { app, BrowserWindow, ipcMain } = require("electron");
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const readline = require("readline");

const PROJECT_ROOT = path.resolve(__dirname, "..");
let mainWindow = null;
let backend = null;
let config = null;

function findPython() {
  const candidates = [
    process.env.DSH_PYTHON,
    "D:\\Anaconda3\\envs\\rag\\python.exe",
    "python",
  ].filter(Boolean);
  return candidates.find((candidate) => candidate === "python" || fs.existsSync(candidate));
}

function startBackend() {
  return new Promise((resolve, reject) => {
    const token = crypto.randomBytes(24).toString("hex");
    const python = findPython();
    backend = spawn(python, ["-m", "desktop_api", "--port", "0"], {
      cwd: PROJECT_ROOT,
      env: { ...process.env, DSH_DESKTOP_TOKEN: token },
      stdio: ["ignore", "pipe", "pipe"],
    });

    const timer = setTimeout(() => reject(new Error("sidecar 启动超时")), 15000);
    const lines = readline.createInterface({ input: backend.stdout });
    lines.on("line", (line) => {
      if (!line.startsWith("READY ")) return;
      clearTimeout(timer);
      const { port } = JSON.parse(line.slice(6));
      config = { baseUrl: `http://127.0.0.1:${port}`, token };
      resolve(config);
    });
    backend.stderr.on("data", (chunk) => {
      console.error(`[sidecar] ${chunk.toString()}`);
    });
    backend.on("error", (error) => {
      clearTimeout(timer);
      reject(error);
    });
  });
}

async function createWindow() {
  await startBackend();
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1200,
    minHeight: 760,
    backgroundColor: "#0e0e0e",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  await mainWindow.loadURL("http://localhost:5173");
}

ipcMain.handle("desktop:config", () => config);

app.whenReady().then(createWindow).catch((error) => {
  console.error(error);
  app.quit();
});

app.on("window-all-closed", () => {
  if (backend) backend.kill();
  app.quit();
});

app.on("before-quit", () => {
  if (backend) backend.kill();
});
```

- [ ] **Step 3: 写 `desktop/preload.js`**

```javascript
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("desktop", {
  getConfig: () => ipcRenderer.invoke("desktop:config"),
});
```

- [ ] **Step 4: 写 Vite/TypeScript 配置和入口**

`desktop/vite.config.ts`：

```typescript
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true },
});
```

`desktop/tsconfig.json`：

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "jsx": "react-jsx",
    "strict": true,
    "skipLibCheck": true,
    "noEmit": true
  },
  "include": ["src"]
}
```

`desktop/index.html`：

```html
<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>LangGraph Agent</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 5: 更新 `.gitignore`**

```text
.sessions/
.agents/
desktop/node_modules/
desktop/dist/
desktop/.vite/
```

- [ ] **Step 6: 让 Python 打印 `READY` 行**

在 `desktop_api.py` 的 `main()` 里，把 `server.run()` 改成：

```python
    import sys

    original_startup = server.startup

    async def startup():
        await original_startup()
        port = server.servers[0].sockets[0].getsockname()[1]
        print(f"READY {json.dumps({'port': port})}", flush=True)

    server.startup = startup
    server.run()
```

- [ ] **Step 7: 手动确认 sidecar 能启动**

Run:
```powershell
cd D:\Vibecoding\Langgraph
$env:DSH_DESKTOP_TOKEN = "dev-token"
D:\Anaconda3\envs\rag\python.exe -m desktop_api --port 0
```
Expected: 打印 `READY {"port": ...}`，并停在 uvicorn 日志；Ctrl+C 退出。

- [ ] **Step 8: Commit**

```powershell
git add Langgraph/desktop Langgraph/.gitignore Langgraph/desktop_api.py
git diff --cached --name-only
git commit -m "feat: add electron desktop shell"
```

---

## Task 4: React 三栏壳与 API 客户端

**Files:**
- Create: `desktop/src/main.tsx`
- Create: `desktop/src/types.ts`
- Create: `desktop/src/global.d.ts`
- Create: `desktop/src/api.ts`
- Create: `desktop/src/App.tsx`
- Create: `desktop/src/styles.css`

- [ ] **Step 1: 写 `types.ts`**

```typescript
export type ModelKey = "local" | "deepseek" | "qwen";

export interface ModelInfo {
  key: ModelKey;
  label: string;
  model: string;
  available: boolean;
  reason?: string;
}

export interface SessionMeta {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
  model_key: ModelKey;
}

export interface Message {
  role: "user" | "assistant" | "tool" | "system";
  content: string;
  tool_calls?: { id: string; name: string; args: Record<string, unknown> }[];
  tool_call_id?: string;
  name?: string;
}

export interface SessionDetail extends SessionMeta {
  messages: Message[];
}

export interface AgentRecord {
  id: string;
  task: string;
  status: "running" | "succeeded" | "failed";
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  result?: string | null;
  error?: string | null;
}

export interface ToolEvent {
  tool: string;
  call_id: string;
  args?: Record<string, unknown>;
  status?: string;
  duration_ms?: number;
}
```

`desktop/src/global.d.ts`：

```typescript
interface DesktopConfig {
  baseUrl: string;
  token: string;
}

interface Window {
  desktop: {
    getConfig(): Promise<DesktopConfig>;
  };
}
```

- [ ] **Step 2: 写 `api.ts`**

```typescript
import type {
  AgentRecord,
  Message,
  ModelInfo,
  SessionDetail,
  SessionMeta,
} from "./types";

let baseUrl = "";
let token = "";

export async function initApi(): Promise<void> {
  const config = await window.desktop.getConfig();
  baseUrl = config.baseUrl;
  token = config.token;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-Desktop-Token": token,
      ...(init.headers || {}),
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `请求失败: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  listSessions: () => request<{ sessions: SessionMeta[] }>("/api/sessions"),
  getSession: (id: string) => request<SessionDetail>(`/api/sessions/${id}`),
  createSession: (title = "新会话") =>
    request<SessionDetail>("/api/sessions", {
      method: "POST",
      body: JSON.stringify({ title }),
    }),
  deleteSession: (id: string) =>
    request<{ deleted: string }>(`/api/sessions/${id}`, { method: "DELETE" }),
  listModels: () => request<{ models: ModelInfo[] }>("/api/models"),
  setModel: (id: string, modelKey: string) =>
    request<{ model_key: string }>(`/api/sessions/${id}/model`, {
      method: "PATCH",
      body: JSON.stringify({ model_key: modelKey }),
    }),
  listAgents: () => request<{ agents: AgentRecord[] }>("/api/agents"),
};

export interface StreamHandlers {
  onToken(text: string): void;
  onToolStart(event: { tool: string; call_id: string; args: Record<string, unknown> }): void;
  onToolEnd(event: { tool: string; call_id: string; status: string; duration_ms: number }): void;
  onDone(event: { interrupted: boolean }): void;
  onError(message: string): void;
}

export async function streamMessage(
  sessionId: string,
  text: string,
  handlers: StreamHandlers,
  signal: AbortSignal,
): Promise<string | null> {
  const response = await fetch(`${baseUrl}/api/sessions/${sessionId}/messages`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Desktop-Token": token,
    },
    body: JSON.stringify({ text }),
    signal,
  });
  if (!response.ok || !response.body) {
    throw new Error(`发送失败: ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let runId: string | null = null;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const blocks = buffer.split("\n\n");
    buffer = blocks.pop() || "";
    for (const block of blocks) {
      const eventLine = block.split("\n").find((line) => line.startsWith("event: "));
      const dataLine = block.split("\n").find((line) => line.startsWith("data: "));
      if (!eventLine || !dataLine) continue;
      const event = eventLine.slice(7);
      const data = JSON.parse(dataLine.slice(6));
      if (event === "token") handlers.onToken(data.text);
      if (event === "tool_start") handlers.onToolStart(data);
      if (event === "tool_end") handlers.onToolEnd(data);
      if (event === "done") handlers.onDone(data);
      if (event === "error") handlers.onError(data.message);
      if (data.run_id) runId = data.run_id;
    }
  }
  return runId;
}

export async function cancelRun(runId: string): Promise<void> {
  await request(`/api/runs/${runId}/cancel`, { method: "POST" });
}

export function messageFromEvent(
  current: Message[],
  event: "user" | "assistant",
  text: string,
): Message[] {
  return [...current, { role: event, content: text }];
}
```

- [ ] **Step 3: 写 `main.tsx`**

```tsx
import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
```

- [ ] **Step 4: 写 `App.tsx` 三栏壳**

```tsx
import { useEffect, useState } from "react";
import { api, initApi } from "./api";
import type { SessionDetail, SessionMeta } from "./types";
import { Sidebar } from "./components/Sidebar";
import { Chat } from "./components/Chat";
import { RightPanel } from "./components/RightPanel";

export function App() {
  const [ready, setReady] = useState(false);
  const [sessions, setSessions] = useState<SessionMeta[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [active, setActive] = useState<SessionDetail | null>(null);

  async function refreshSessions() {
    const result = await api.listSessions();
    setSessions(result.sessions);
    return result.sessions;
  }

  async function openSession(id: string) {
    setActiveId(id);
    setActive(await api.getSession(id));
  }

  useEffect(() => {
    (async () => {
      await initApi();
      const list = await refreshSessions();
      if (list.length > 0) await openSession(list[0].id);
      setReady(true);
    })();
  }, []);

  if (!ready) return <div className="boot">正在连接本地服务…</div>;

  return (
    <div className="app-shell">
      <Sidebar
        sessions={sessions}
        activeId={activeId}
        onSelect={openSession}
        onCreate={async () => {
          const created = await api.createSession();
          await refreshSessions();
          await openSession(created.id);
        }}
        onDelete={async (id) => {
          await api.deleteSession(id);
          const list = await refreshSessions();
          if (activeId === id) {
            if (list[0]) await openSession(list[0].id);
            else setActive(null);
          }
        }}
      />
      <main className="chat-column">
        {active ? (
          <Chat
            session={active}
            onSessionChange={setActive}
            onRefreshSessions={refreshSessions}
          />
        ) : (
          <div className="empty">新建一个会话开始。</div>
        )}
      </main>
      <RightPanel sessionId={activeId} />
    </div>
  );
}
```

- [ ] **Step 5: 写 `styles.css` 的 token 和三栏布局**

```css
:root {
  color-scheme: dark;
  --bg: #0e0e0e;
  --panel: #151515;
  --panel-2: #1b1b1b;
  --border: #2a2a2a;
  --text: #e8e8e8;
  --muted: #888888;
  --faint: #666666;
  --accent: #7c9cff;
  --ok: #7fb98a;
  --warn: #d8a657;
  --err: #d97b7b;
  font-family: "Microsoft YaHei UI", "PingFang SC", system-ui, sans-serif;
  font-size: 13px;
}

* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text); }
button, textarea, input { font: inherit; }
button { color: inherit; }

.app-shell {
  display: grid;
  grid-template-columns: 260px minmax(0, 1fr) 320px;
  height: 100vh;
  min-width: 1200px;
}

.chat-column {
  display: flex;
  flex-direction: column;
  min-width: 0;
  background: #181818;
  border-left: 1px solid var(--border);
  border-right: 1px solid var(--border);
}

.boot, .empty {
  display: grid;
  place-items: center;
  height: 100vh;
  color: var(--muted);
}
```

- [ ] **Step 6: 写三个最小组件骨架（Task 5–7 扩展）**

`desktop/src/components/Sidebar.tsx`：

```tsx
import type { SessionMeta } from "../types";

export function Sidebar(props: {
  sessions: SessionMeta[];
  activeId: string | null;
  onSelect(id: string): void;
  onCreate(): void;
  onDelete(id: string): void;
}) {
  return (
    <aside className="sidebar">
      <button onClick={props.onCreate}>＋ 新会话</button>
      {props.sessions.map((session) => (
        <button
          key={session.id}
          className={session.id === props.activeId ? "active" : ""}
          onClick={() => props.onSelect(session.id)}
        >
          {session.title}
        </button>
      ))}
    </aside>
  );
}
```

`desktop/src/components/Chat.tsx`：

```tsx
import type { SessionDetail } from "../types";

export function Chat(props: {
  session: SessionDetail;
  onSessionChange(session: SessionDetail): void;
  onRefreshSessions(): Promise<unknown>;
}) {
  return (
    <div className="chat">
      <header>{props.session.title}</header>
      <div className="messages">
        {props.session.messages.map((message, index) => (
          <div key={index} className={`message ${message.role}`}>
            {message.content}
          </div>
        ))}
      </div>
    </div>
  );
}
```

`desktop/src/components/RightPanel.tsx`：

```tsx
export function RightPanel(props: { sessionId: string | null }) {
  return (
    <aside className="right-panel">
      <div className="tabs">任务 ｜ 工具 ｜ 上下文</div>
      <div className="empty">任务台</div>
    </aside>
  );
}
```

- [ ] **Step 7: 构建检查**

Run:
```powershell
cd D:\Vibecoding\Langgraph\desktop
pnpm build
```
Expected: `dist/` 生成，无 TypeScript 错误。

- [ ] **Step 8: Commit**

```powershell
git add Langgraph/desktop/src Langgraph/desktop/package.json Langgraph/desktop/tsconfig.json Langgraph/desktop/vite.config.ts Langgraph/desktop/index.html
git diff --cached --name-only
git commit -m "feat: add react desktop shell"
```

---

## Task 5: 会话侧栏与模型胶囊

**Files:**
- Modify: `desktop/src/components/Sidebar.tsx`
- Create: `desktop/src/components/Composer.tsx`
- Modify: `desktop/src/components/Chat.tsx`
- Modify: `desktop/src/styles.css`

- [ ] **Step 1: 实现 Sidebar 日期分组与搜索**

```tsx
import { useMemo, useState } from "react";
import type { SessionMeta } from "../types";

function dayGroup(iso: string): string {
  const date = new Date(iso);
  const now = new Date();
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const target = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  const days = Math.floor((start.getTime() - target.getTime()) / 86400000);
  if (days <= 0) return "今天";
  if (days === 1) return "昨天";
  return "更早";
}

export function Sidebar(props: {
  sessions: SessionMeta[];
  activeId: string | null;
  onSelect(id: string): void;
  onCreate(): void;
  onDelete(id: string): void;
}) {
  const [query, setQuery] = useState("");
  const groups = useMemo(() => {
    const filtered = props.sessions.filter((session) =>
      session.title.toLowerCase().includes(query.toLowerCase()),
    );
    return ["今天", "昨天", "更早"]
      .map((label) => ({
        label,
        items: filtered.filter((session) => dayGroup(session.updated_at) === label),
      }))
      .filter((group) => group.items.length > 0);
  }, [props.sessions, query]);

  return (
    <aside className="sidebar">
      <div className="brand">LangGraph Agent</div>
      <button className="new-session" onClick={props.onCreate}>＋ 新会话</button>
      <input
        className="search"
        placeholder="搜索会话"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
      />
      {groups.map((group) => (
        <section key={group.label}>
          <div className="group-label">{group.label}</div>
          {group.items.map((session) => (
            <div
              key={session.id}
              className={`session-item ${session.id === props.activeId ? "active" : ""}`}
            >
              <button className="session-main" onClick={() => props.onSelect(session.id)}>
                <span className={`model-dot ${session.model_key}`} />
                <span className="session-title">{session.title}</span>
                <span className="session-time">
                  {new Date(session.updated_at).toLocaleTimeString([], {
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </span>
              </button>
              <button
                className="delete"
                title="删除会话"
                onClick={() => {
                  if (confirm(`删除会话“${session.title}”？`)) props.onDelete(session.id);
                }}
              >
                ×
              </button>
            </div>
          ))}
        </section>
      ))}
      {groups.length === 0 && <div className="muted">没有匹配的会话</div>}
    </aside>
  );
}
```

- [ ] **Step 2: 实现 Composer 的模型胶囊**

```tsx
import { useEffect, useState } from "react";
import type { ModelInfo } from "../types";

export function Composer(props: {
  value: string;
  onChange(value: string): void;
  onSend(): void;
  onStop(): void;
  running: boolean;
  models: ModelInfo[];
  modelKey: string;
  onModelChange(key: string): void;
}) {
  const [open, setOpen] = useState(false);
  const current = props.models.find((model) => model.key === props.modelKey);

  useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    window.addEventListener("click", close);
    return () => window.removeEventListener("click", close);
  }, [open]);

  return (
    <div className="composer">
      <textarea
        value={props.value}
        placeholder="输入消息…"
        onChange={(event) => props.onChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            if (!props.running) props.onSend();
          }
        }}
      />
      <div className="composer-actions">
        <div className="model-wrap" onClick={(event) => event.stopPropagation()}>
          <button className="model-pill" onClick={() => setOpen((value) => !value)}>
            {current?.label || "选择模型"} ▾
          </button>
          {open && (
            <div className="model-menu">
              {props.models.map((model) => (
                <button
                  key={model.key}
                  disabled={!model.available || props.running}
                  onClick={() => {
                    props.onModelChange(model.key);
                    setOpen(false);
                  }}
                >
                  <span>{model.label}</span>
                  {!model.available && <small>{model.reason}</small>}
                </button>
              ))}
            </div>
          )}
        </div>
        {props.running ? (
          <button className="stop" onClick={props.onStop}>停止</button>
        ) : (
          <button className="send" onClick={props.onSend} disabled={!props.value.trim()}>
            发送
          </button>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 3: 接入 Chat 的模型加载与切换**

`Chat.tsx` 增加：

```tsx
import { useEffect, useState } from "react";
import { api } from "../api";
import type { ModelInfo, SessionDetail } from "../types";
import { Composer } from "./Composer";

export function Chat(props: {
  session: SessionDetail;
  onSessionChange(session: SessionDetail): void;
  onRefreshSessions(): Promise<unknown>;
}) {
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [draft, setDraft] = useState("");
  const [running] = useState(false);

  useEffect(() => {
    api.listModels().then((result) => setModels(result.models));
  }, []);

  async function changeModel(key: string) {
    await api.setModel(props.session.id, key);
    props.onSessionChange({ ...props.session, model_key: key as SessionDetail["model_key"] });
    await props.onRefreshSessions();
  }

  return (
    <div className="chat">
      <header>
        <span>{props.session.title}</span>
        <span className="model-badge">
          {models.find((model) => model.key === props.session.model_key)?.label}
        </span>
      </header>
      <div className="messages">
        {props.session.messages.map((message, index) => (
          <div key={index} className={`message ${message.role}`}>{message.content}</div>
        ))}
      </div>
      <Composer
        value={draft}
        onChange={setDraft}
        onSend={() => setDraft("")}
        onStop={() => undefined}
        running={running}
        models={models}
        modelKey={props.session.model_key}
        onModelChange={changeModel}
      />
    </div>
  );
}
```

- [ ] **Step 4: 增加模型胶囊样式**

```css
.sidebar { background: #101010; padding: 12px; border-right: 1px solid var(--border); }
.brand { font-weight: 700; margin-bottom: 12px; }
.new-session, .search { width: 100%; background: var(--panel-2); border: 1px solid var(--border); border-radius: 6px; padding: 7px 9px; margin-bottom: 8px; }
.session-item { display: flex; align-items: center; border-radius: 5px; }
.session-item.active { background: #242424; }
.session-main { flex: 1; display: flex; gap: 7px; align-items: center; background: none; border: 0; padding: 6px; text-align: left; }
.session-title { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.session-time { color: var(--faint); font-size: 10px; }
.model-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--faint); }
.model-dot.local { background: var(--ok); }
.model-dot.deepseek { background: var(--accent); }
.model-dot.qwen { background: #b58cff; }
.delete { display: none; background: none; border: 0; color: var(--faint); }
.session-item:hover .delete { display: block; }
.composer { border: 1px solid var(--border); border-radius: 8px; margin: 10px; padding: 8px; background: var(--panel); }
.composer textarea { width: 100%; min-height: 54px; max-height: 180px; resize: none; background: transparent; border: 0; color: var(--text); outline: none; }
.composer-actions { display: flex; justify-content: space-between; align-items: center; }
.model-wrap { position: relative; }
.model-pill { border: 1px solid #3a3a3a; background: transparent; border-radius: 12px; padding: 3px 9px; color: var(--muted); }
.model-menu { position: absolute; bottom: 30px; left: 0; width: 220px; background: var(--panel-2); border: 1px solid var(--border); border-radius: 8px; padding: 5px; }
.model-menu button { display: flex; flex-direction: column; width: 100%; text-align: left; background: none; border: 0; padding: 7px; border-radius: 4px; }
.model-menu button:hover:not(:disabled) { background: #2a2a2a; }
.model-menu button:disabled { color: var(--faint); }
.send, .stop { border: 0; border-radius: 5px; padding: 5px 13px; font-weight: 700; }
.send { background: var(--accent); color: #111; }
.stop { background: var(--err); color: #111; }
```

- [ ] **Step 5: 构建 + 手动检查**

Run:
```powershell
cd D:\Vibecoding\Langgraph\desktop
pnpm build
```
Expected: 构建通过。

- [ ] **Step 6: Commit**

```powershell
git add Langgraph/desktop/src
git diff --cached --name-only
git commit -m "feat: add desktop session sidebar and model picker"
```

---

## Task 6: 流式聊天、工具卡片与停止

**Files:**
- Modify: `desktop/src/components/Chat.tsx`
- Create: `desktop/src/components/ToolCard.tsx`
- Modify: `desktop/src/styles.css`

- [ ] **Step 1: 实现 ToolCard**

```tsx
export function ToolCard(props: {
  tool: string;
  args: Record<string, unknown>;
  status: "running" | "ok" | "error";
  durationMs?: number;
}) {
  return (
    <details className={`tool-card ${props.status}`}>
      <summary>
        <span className="tool-name">{props.tool}</span>
        <span className="tool-target">{String(props.args.path || props.args.query || "")}</span>
        <span className="tool-status">
          {props.status === "running" ? "运行中" : props.status === "error" ? "失败" : "完成"}
          {props.durationMs ? ` · ${props.durationMs}ms` : ""}
        </span>
      </summary>
      <pre>{JSON.stringify(props.args, null, 2)}</pre>
    </details>
  );
}
```

- [ ] **Step 2: 在 Chat 中接入 SSE**

把 `Chat.tsx` 的 state 改为：

```tsx
import { useEffect, useRef, useState } from "react";
import { api, cancelRun, streamMessage } from "../api";
import type { Message, ModelInfo, SessionDetail, ToolEvent } from "../types";
import { Composer } from "./Composer";
import { ToolCard } from "./ToolCard";

interface ToolState extends ToolEvent {
  status: "running" | "ok" | "error";
  durationMs?: number;
}
```

在组件内：

```tsx
  const [draft, setDraft] = useState("");
  const [running, setRunning] = useState(false);
  const [streamText, setStreamText] = useState("");
  const [tools, setTools] = useState<ToolState[]>([]);
  const [runId, setRunId] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  async function send() {
    if (!draft.trim() || running) return;
    const text = draft.trim();
    setDraft("");
    setRunning(true);
    setStreamText("");
    setTools([]);
    const optimistic: Message = { role: "user", content: text };
    props.onSessionChange({ ...props.session, messages: [...props.session.messages, optimistic] });
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const id = await streamMessage(
        props.session.id,
        text,
        {
          onToken: (chunk) => setStreamText((value) => value + chunk),
          onToolStart: (event) =>
            setTools((items) => [...items, { ...event, status: "running" }]),
          onToolEnd: (event) =>
            setTools((items) =>
              items.map((item) =>
                item.call_id === event.call_id
                  ? { ...item, status: event.status === "error" ? "error" : "ok", durationMs: event.duration_ms }
                  : item,
              ),
            ),
          onDone: () => undefined,
          onError: (message) => setStreamText((value) => `${value}\n[错误] ${message}`),
        },
        controller.signal,
      );
      setRunId(id);
    } catch (error) {
      setStreamText((value) => `${value}\n[错误] ${String(error)}`);
    } finally {
      setRunning(false);
      abortRef.current = null;
      const fresh = await api.getSession(props.session.id);
      props.onSessionChange(fresh);
      await props.onRefreshSessions();
    }
  }

  async function stop() {
    abortRef.current?.abort();
    if (runId) await cancelRun(runId).catch(() => undefined);
    setRunning(false);
  }
```

消息渲染：

```tsx
      <div className="messages">
        {props.session.messages.map((message, index) => (
          <div key={index} className={`message ${message.role}`}>{message.content}</div>
        ))}
        {streamText && <div className="message assistant">{streamText}</div>}
        {tools.map((tool) => (
          <ToolCard
            key={tool.call_id}
            tool={tool.tool}
            args={tool.args || {}}
            status={tool.status}
            durationMs={tool.durationMs}
          />
        ))}
      </div>
      <Composer
        value={draft}
        onChange={setDraft}
        onSend={send}
        onStop={stop}
        running={running}
        ...
      />
```

- [ ] **Step 3: 工具卡片样式**

```css
.tool-card { border: 1px solid var(--border); border-radius: 6px; margin: 6px 0; background: var(--panel); }
.tool-card summary { display: flex; gap: 8px; align-items: center; padding: 6px 8px; cursor: pointer; list-style: none; }
.tool-name { color: var(--text); }
.tool-target { flex: 1; color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tool-status { color: var(--faint); font-size: 11px; }
.tool-card.ok .tool-status { color: var(--ok); }
.tool-card.error .tool-status { color: var(--err); }
.tool-card pre { margin: 0; padding: 8px; border-top: 1px solid var(--border); color: var(--muted); overflow: auto; }
```

- [ ] **Step 4: 构建**

```powershell
cd D:\Vibecoding\Langgraph\desktop
pnpm build
```
Expected: PASS。

- [ ] **Step 5: 手动联调**

1. 启动 Python：`python -m desktop_api --port 0`，记下 `READY` 端口。
2. 启动 Vite：`pnpm dev`。
3. 在 Electron 窗口里发消息，确认逐字输出、工具卡片出现、停止按钮可用。

- [ ] **Step 6: Commit**

```powershell
git add Langgraph/desktop/src
git diff --cached --name-only
git commit -m "feat: stream chat and render tool cards"
```

---

## Task 7: 右栏任务台与子 agent 状态

**Files:**
- Modify: `desktop/src/components/RightPanel.tsx`
- Create: `desktop/src/components/AgentCard.tsx`
- Create: `desktop/src/components/ToolLog.tsx`
- Modify: `desktop/src/styles.css`

- [ ] **Step 1: 实现 AgentCard**

```tsx
import type { AgentRecord } from "../types";

export function AgentCard(props: { agent: AgentRecord }) {
  const { agent } = props;
  return (
    <div className={`agent-card ${agent.status}`}>
      <div className="agent-head">
        <span className="agent-task">{agent.task}</span>
        <span className="agent-state">
          {agent.status === "running" ? "运行中" : agent.status === "succeeded" ? "完成" : "失败"}
        </span>
      </div>
      <div className="agent-meta">
        {agent.started_at ? `开始 ${new Date(agent.started_at).toLocaleTimeString()}` : "等待启动"}
      </div>
      {agent.error && <div className="agent-error">{agent.error}</div>}
      {agent.result && <div className="agent-result">{agent.result}</div>}
    </div>
  );
}
```

- [ ] **Step 2: 实现 ToolLog**

```tsx
import type { ToolEvent } from "../types";

export function ToolLog(props: { tools: ToolEvent[] }) {
  if (props.tools.length === 0) return <div className="muted">还没有工具调用</div>;
  return (
    <div className="tool-log">
      {props.tools.slice(-50).reverse().map((tool) => (
        <div key={tool.call_id} className="tool-log-row">
          <span>{tool.tool}</span>
          <span className="muted">{String(tool.args?.path || tool.args?.query || "")}</span>
          <span className={tool.status === "error" ? "err" : "ok"}>
            {tool.status === "error" ? "失败" : "完成"}
          </span>
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 3: 实现 RightPanel 三标签与轮询**

```tsx
import { useEffect, useState } from "react";
import { api } from "../api";
import type { AgentRecord, ToolEvent } from "../types";
import { AgentCard } from "./AgentCard";
import { ToolLog } from "./ToolLog";

type Tab = "tasks" | "tools" | "context";

export function RightPanel(props: {
  sessionId: string | null;
  tools: ToolEvent[];
  modelLabel: string;
  messageCount: number;
}) {
  const [tab, setTab] = useState<Tab>("tasks");
  const [agents, setAgents] = useState<AgentRecord[]>([]);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const result = await api.listAgents();
        if (!cancelled) setAgents(result.agents);
      } catch {
        // 后端未就绪时保持上一次状态
      }
    }
    load();
    const timer = setInterval(load, 2000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [props.sessionId]);

  return (
    <aside className="right-panel">
      <nav className="tabs">
        {(["tasks", "tools", "context"] as Tab[]).map((item) => (
          <button key={item} className={tab === item ? "active" : ""} onClick={() => setTab(item)}>
            {item === "tasks" ? "任务" : item === "tools" ? "工具" : "上下文"}
          </button>
        ))}
      </nav>
      {tab === "tasks" && (
        <div className="panel-body">
          {agents.length === 0 ? (
            <div className="muted">还没有子 agent 任务</div>
          ) : (
            agents.map((agent) => <AgentCard key={agent.id} agent={agent} />)
          )}
        </div>
      )}
      {tab === "tools" && <ToolLog tools={props.tools} />}
      {tab === "context" && (
        <div className="panel-body context-list">
          <div>模型：{props.modelLabel}</div>
          <div>会话：{props.sessionId || "未选择"}</div>
          <div>消息数：{props.messageCount}</div>
          <div>估算 token：{Math.ceil(props.messageCount * 80)}</div>
          <div>子 agent：{agents.length}</div>
        </div>
      )}
    </aside>
  );
}
```

- [ ] **Step 4: 把 tools 提升到 App/Chat 共享**

在 `App.tsx` 增加 `const [tools, setTools] = useState<ToolEvent[]>([])`，传给 `Chat` 和 `RightPanel`；`Chat` 的 `onToolStart` 同时调用 `setTools`。`RightPanel` 传 `modelLabel` 和 `messageCount`。

- [ ] **Step 5: 样式**

```css
.right-panel { background: #151515; padding: 10px; overflow: auto; }
.tabs { display: flex; gap: 12px; border-bottom: 1px solid var(--border); padding-bottom: 7px; margin-bottom: 8px; }
.tabs button { background: none; border: 0; color: var(--muted); padding: 2px 0; }
.tabs button.active { color: var(--text); border-bottom: 2px solid var(--accent); }
.agent-card { border: 1px solid var(--border); border-radius: 6px; background: var(--panel-2); padding: 7px; margin-bottom: 7px; }
.agent-card.running { border-color: #3b4d86; }
.agent-card.failed { border-color: #6a3434; }
.agent-head { display: flex; justify-content: space-between; gap: 8px; }
.agent-task { color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.agent-state { font-size: 11px; color: var(--muted); }
.agent-meta { margin-top: 4px; font-size: 11px; color: var(--faint); }
.agent-error { margin-top: 4px; color: var(--err); font-size: 11px; }
.agent-result { margin-top: 4px; color: var(--muted); font-size: 11px; }
.tool-log-row { display: flex; gap: 8px; padding: 5px 0; border-bottom: 1px solid var(--border); font-size: 11px; }
.tool-log-row .muted { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ok { color: var(--ok); }
.err { color: var(--err); }
```

- [ ] **Step 6: 构建 + 手动检查**

```powershell
cd D:\Vibecoding\Langgraph\desktop
pnpm build
```
Expected: PASS。

- [ ] **Step 7: Commit**

```powershell
git add Langgraph/desktop/src
git diff --cached --name-only
git commit -m "feat: add desktop task panel"
```

---

## Task 8: 联调、文档与验收

**Files:**
- Create: `desktop/README.md`
- Modify: `docs/superpowers/specs/2026-10-24-desktop-app-design.md`（仅当实现有偏差）
- Modify: `tests/test_desktop_api.py`（补边界测试）

- [ ] **Step 1: 写 `desktop/README.md`**

```markdown
# LangGraph Agent Desktop (Dev)

## Run

1. Install Python deps:
   `D:\Anaconda3\envs\rag\python.exe -m pip install -r requirements-desktop.txt`
2. Install Node deps:
   `cd desktop && pnpm install`
3. Start:
   `cd desktop && pnpm dev`

The Electron main process starts `python -m desktop_api --port 0`.
Set `DSH_PYTHON` if the Python executable is not at
`D:\Anaconda3\envs\rag\python.exe`.

## Tests

`D:\Anaconda3\envs\rag\python.exe -m unittest discover -s tests -v`
`cd desktop && pnpm test`

## Manual acceptance

- Create/switch/delete sessions.
- Send a message and observe token streaming.
- Trigger `read_file`, `write_docx`, `web_search`; confirm tool cards.
- Switch models; restart and confirm the session remembers the model.
- Stop a running reply.
- Open the task panel while a subagent is running.
```

- [ ] **Step 2: 补 API 边界测试**

在 `tests/test_desktop_api.py` 增加：

```python
class DesktopValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = create_app(root=Path(self.tmp.name), token="t")
        self.client = TestClient(self.app)
        self.headers = {"X-Desktop-Token": "t"}

    def test_empty_message_is_400(self):
        session = self.client.post(
            "/api/sessions", json={"title": "空"}, headers=self.headers
        ).json()
        response = self.client.post(
            f"/api/sessions/{session['id']}/messages",
            json={"text": "   "},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 400)

    def test_missing_session_is_404(self):
        response = self.client.get("/api/sessions/20200101-000000-dead", headers=self.headers)
        self.assertEqual(response.status_code, 404)
```

- [ ] **Step 3: 运行完整 Python 测试**

```powershell
cd D:\Vibecoding\Langgraph
D:\Anaconda3\envs\rag\python.exe -m unittest discover -s tests -v
```
Expected: 现有测试 + 新增 API 测试全部 PASS；记录总数。

- [ ] **Step 4: 运行前端测试**

```powershell
cd D:\Vibecoding\Langgraph\desktop
pnpm test
```
Expected: 测试通过；如果尚未编写 Vitest 用例，先写 `desktop/src/App.test.tsx` 的三栏渲染测试：

```tsx
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./api", () => ({
  initApi: vi.fn(async () => {}),
  api: {
    listSessions: vi.fn(async () => ({ sessions: [] })),
    listModels: vi.fn(async () => ({ models: [] })),
  },
}));

import { App } from "./App";

beforeEach(() => {
  window.desktop = {
    getConfig: async () => ({ baseUrl: "http://test", token: "t" }),
  };
});

describe("App", () => {
  it("renders the three-column shell", () => {
    render(<App />);
    expect(screen.getByText("LangGraph Agent")).toBeTruthy();
  });
});
```

并在 `vite.config.ts` 使用：

```typescript
/// <reference types="vitest" />
```

并在 `defineConfig({ ... })` 中加入：

```typescript
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
  },
```

`desktop/src/test-setup.ts`：

```typescript
import "@testing-library/jest-dom/vitest";
```

- [ ] **Step 5: 手动验收**

按 `desktop/README.md` 的清单逐项执行，并记录：

- 窗口启动时间
- 首个 token 延迟
- 工具卡片是否出现
- 模型切换后重启是否保留
- 停止是否释放输入
- 子 agent 任务是否刷新

- [ ] **Step 6: 最终提交**

```powershell
git add Langgraph/desktop/README.md Langgraph/desktop/src Langgraph/tests/test_desktop_api.py Langgraph/desktop/vite.config.ts
git diff --cached --name-only
git commit -m "docs: add desktop dev guide and validation"
```

---

## Self-Review

- Spec coverage：会话/模型/SSE/工具/子 agent/三栏 UI 都落在 Task 1–8。
- 兼容性：`session_store.py` 对旧 JSON 缺 `model_key` 的处理在 Task 1 有测试。
- 类型一致性：`model_key` 在 Python 侧是 `str | None`，API 输出回退默认值；TS 侧是 `ModelKey`，两者在 Task 1/4 对齐。
- 已知简化：第一版全局只允许一个 run；子 agent 任务台显示全部任务，不按会话过滤。这两点已在 spec 中记录，不在计划里偷偷扩大范围。
- 没有实现安装包、浅色主题、图片上传；这些明确在非目标里。
