# Model Switching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `/model` terminal command that switches the running agent among local Ollama `qwen3.5:2b`, DeepSeek `deepseek-flash`, and DashScope `qwen3-vl-plus`, with subagents inheriting the current model.

**Architecture:** A new `model_registry.py` owns model profiles, aliases, and `ChatOpenAI` construction. `ModelRuntime` in `graph_agent.py` builds a fresh graph for a selected key and only swaps state after the build succeeds. `SubagentManager` passes the current key to each worker via `--model-key`; the worker builds its own graph with that key.

**Tech Stack:** Python 3.12 (`D:\Anaconda3\envs\rag`), `langchain-openai`, LangGraph, `unittest`, existing `SessionStore` / `SubagentManager`.

---

## File Map

| File | Responsibility |
|---|---|
| `model_registry.py` (new) | Profiles, selector aliases, env lookup, `build_chat_model`, `model_table`, `initial_model_key` |
| `tests/test_model_registry.py` (new) | Registry unit tests |
| `tests/test_graph_model.py` (new) | `build_graph(model_key=...)` and `active_model(key)` tests |
| `graph_agent.py` | Registry wiring, `ModelRuntime`, `/model` handling, main-loop model injection |
| `session_cli.py` | `/model` command registration and help text |
| `tests/test_model_switch.py` (new) | Runtime and `/model` command integration tests |
| `subagent_manager.py` | Store current model key; pass `--model-key` to worker |
| `subagent_worker.py` | Parse `--model-key`; build graph for that key |
| `subagent_fake_worker.py` | Accept (and ignore) `--model-key` so existing manager tests keep working |
| `tests/test_subagent_manager.py` | Assert spawn passes the key |
| `tests/test_subagent_worker.py` | Assert worker forwards the key |

Run all tests from `D:\Vibecoding\Langgraph`:

```bash
D:\Anaconda3\envs\rag\python.exe -m unittest discover -s tests -v
```

Baseline: 123 tests pass before this plan.

---

### Task 1: Model Registry

**Files:**
- Create: `D:\Vibecoding\Langgraph\model_registry.py`
- Create: `D:\Vibecoding\Langgraph\tests\test_model_registry.py`

- [ ] **Step 1: Write the failing registry tests**

Create `tests/test_model_registry.py`:

```python
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from model_registry import (
    DEFAULT_MODEL_KEY,
    ModelConfigError,
    build_chat_model,
    get_profile,
    initial_model_key,
    list_profiles,
    model_table,
    resolve_selector,
)


class ModelRegistryTests(unittest.TestCase):
    def test_three_profiles_have_expected_defaults(self):
        profiles = {profile.key: profile for profile in list_profiles()}
        self.assertEqual(list(profiles), ["local", "deepseek", "qwen"])
        self.assertEqual(profiles["local"].model, "qwen3.5:2b")
        self.assertEqual(profiles["local"].provider, "ollama")
        self.assertEqual(profiles["deepseek"].model, "deepseek-flash")
        self.assertEqual(profiles["qwen"].model, "qwen3-vl-plus")
        self.assertEqual(profiles["deepseek"].key_env, "DEEPSEEK_API_KEY")
        self.assertEqual(profiles["qwen"].key_env, "DASHSCOPE_API_KEY")
        self.assertTrue(profiles["qwen"].supports_vision)

    def test_resolve_selector_aliases(self):
        cases = {
            "1": "local",
            "local": "local",
            "OLLAMA": "local",
            "qwen3.5": "local",
            "本地": "local",
            "2": "deepseek",
            "DEEPSEEK": "deepseek",
            "deepseek-v4.1-flash": "deepseek",
            "v4.1-flash": "deepseek",
            "3": "qwen",
            "qwen3-vl-plus": "qwen",
            "qwen-vl": "qwen",
            "千问": "qwen",
        }
        for selector, expected in cases.items():
            with self.subTest(selector=selector):
                self.assertEqual(resolve_selector(selector), expected)

    def test_unknown_selector_raises(self):
        with self.assertRaises(ModelConfigError) as ctx:
            resolve_selector("gpt-4")
        self.assertIn("未知模型", str(ctx.exception))

    def test_missing_qwen_key_raises(self):
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": ""}, clear=False):
            with self.assertRaises(ModelConfigError) as ctx:
                build_chat_model("qwen")
        self.assertIn("DASHSCOPE_API_KEY", str(ctx.exception))

    def test_missing_deepseek_key_raises(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}, clear=False):
            with self.assertRaises(ModelConfigError) as ctx:
                build_chat_model("deepseek")
        self.assertIn("DEEPSEEK_API_KEY", str(ctx.exception))

    def test_initial_key_mapping_and_fallback(self):
        with patch.dict(os.environ, {"LLM_PROVIDER": "deepseek"}, clear=False):
            self.assertEqual(initial_model_key(), "deepseek")
        with patch.dict(os.environ, {"LLM_PROVIDER": "dashscope"}, clear=False):
            self.assertEqual(initial_model_key(), "qwen")
        with patch.dict(os.environ, {"LLM_PROVIDER": "ollama"}, clear=False):
            self.assertEqual(initial_model_key(), "local")
        with patch.dict(os.environ, {"LLM_PROVIDER": "nonsense"}, clear=False):
            self.assertEqual(initial_model_key(), DEFAULT_MODEL_KEY)

    def test_model_table_marks_current_profile(self):
        table = model_table("deepseek")
        self.assertIn("[模型] 当前: deepseek", table)
        self.assertIn("local", table)
        self.assertIn("deepseek", table)
        self.assertIn("qwen", table)
        self.assertIn("qwen3-vl-plus", table)

    @patch("model_registry.ChatOpenAI")
    def test_build_chat_model_uses_profile_fields(self, chat_openai):
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "test-key"}, clear=False):
            build_chat_model("qwen")
        kwargs = chat_openai.call_args.kwargs
        self.assertEqual(kwargs["model"], "qwen3-vl-plus")
        self.assertEqual(kwargs["api_key"], "test-key")
        self.assertEqual(kwargs["base_url"], "https://dashscope.aliyuncs.com/compatible-mode/v1")
        self.assertEqual(kwargs["temperature"], 0)
        self.assertEqual(kwargs["timeout"], 60)
        self.assertEqual(kwargs["max_retries"], 2)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
D:\Anaconda3\envs\rag\python.exe -m unittest -v tests.test_model_registry
```

Expected: `ModuleNotFoundError: No module named 'model_registry'`.

- [ ] **Step 3: Write the registry implementation**

Create `model_registry.py`:

```python
"""模型注册表：集中管理本地 Ollama、DeepSeek、DashScope 三个模型配置。"""

from __future__ import annotations

import os
from dataclasses import dataclass

from langchain_openai import ChatOpenAI

DEFAULT_MODEL_KEY = "local"

_PROVIDER_KEYS = {
    "ollama": "local",
    "deepseek": "deepseek",
    "dashscope": "qwen",
    "qwen": "qwen",
}

_ALIASES = {
    "1": "local",
    "local": "local",
    "ollama": "local",
    "qwen3.5": "local",
    "本地": "local",
    "2": "deepseek",
    "deepseek": "deepseek",
    "deepseek-flash": "deepseek",
    "deepseek-v4.1-flash": "deepseek",
    "v4.1-flash": "deepseek",
    "3": "qwen",
    "qwen": "qwen",
    "qwen3-vl": "qwen",
    "qwen3-vl-plus": "qwen",
    "qwen-vl": "qwen",
    "千问": "qwen",
}


class ModelConfigError(RuntimeError):
    """模型配置缺失或未知。"""


@dataclass(frozen=True)
class ModelProfile:
    key: str
    label: str
    provider: str
    model: str
    base_url: str
    api_key: str | None
    key_env: str | None
    timeout: float
    max_retries: int
    supports_vision: bool


def _env(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


def list_profiles() -> list[ModelProfile]:
    """每次读取环境变量，便于测试和运行时切换后读取最新 .env。"""
    return [
        ModelProfile(
            key="local",
            label="本地 qwen3.5",
            provider="ollama",
            model=_env("OLLAMA_MODEL", "qwen3.5:2b"),
            base_url=_env("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1"),
            api_key="ollama",
            key_env=None,
            timeout=120,
            max_retries=1,
            supports_vision=False,
        ),
        ModelProfile(
            key="deepseek",
            label="deepseek-v4.1-flash",
            provider="deepseek",
            model=_env("DEEPSEEK_MODEL", "deepseek-flash"),
            base_url=_env("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            api_key=_env("DEEPSEEK_API_KEY", ""),
            key_env="DEEPSEEK_API_KEY",
            timeout=60,
            max_retries=2,
            supports_vision=False,
        ),
        ModelProfile(
            key="qwen",
            label="千问多模态",
            provider="dashscope",
            model=_env("QWEN_MODEL", "qwen3-vl-plus"),
            base_url=_env(
                "DASHSCOPE_BASE_URL",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
            ),
            api_key=_env("DASHSCOPE_API_KEY", ""),
            key_env="DASHSCOPE_API_KEY",
            timeout=60,
            max_retries=2,
            supports_vision=True,
        ),
    ]


def resolve_selector(selector: str) -> str:
    """把数字、key 或别名归一化为 local/deepseek/qwen。"""
    raw = selector.strip().lower()
    if not raw:
        raise ModelConfigError("未知模型: 空；输入 /model 查看可用模型。")
    try:
        return _ALIASES[raw]
    except KeyError:
        raise ModelConfigError(
            f"未知模型: {selector}；输入 /model 查看可用模型。"
        ) from None


def get_profile(key: str) -> ModelProfile:
    canonical = resolve_selector(key)
    for profile in list_profiles():
        if profile.key == canonical:
            return profile
    raise ModelConfigError(f"未知模型: {key}；输入 /model 查看可用模型。")


def initial_model_key() -> str:
    raw = os.getenv("LLM_PROVIDER", "").strip().lower()
    return _PROVIDER_KEYS.get(raw, DEFAULT_MODEL_KEY)


def build_chat_model(key: str) -> ChatOpenAI:
    profile = get_profile(key)
    if profile.key != "local" and not profile.api_key:
        raise ModelConfigError(
            f"未找到 {profile.key_env}，无法切换到 {profile.label}。请检查 .env。"
        )
    return ChatOpenAI(
        model=profile.model,
        api_key=profile.api_key or "ollama",
        base_url=profile.base_url,
        temperature=0,
        timeout=profile.timeout,
        max_retries=profile.max_retries,
    )


def model_table(current_key: str) -> str:
    current = get_profile(current_key)
    lines = [f"[模型] 当前: {current.key} ({current.label})"]
    for index, profile in enumerate(list_profiles(), 1):
        lines.append(
            f"  {index}. {profile.key} | {profile.label} | "
            f"{profile.model} | {profile.base_url}"
        )
    return "\n".join(lines)
```

- [ ] **Step 4: Run the registry tests**

Run:

```bash
D:\Anaconda3\envs\rag\python.exe -m unittest -v tests.test_model_registry
```

Expected: `Ran 8 tests` — `OK`.

- [ ] **Step 5: Commit Task 1**

```bash
git add Langgraph/model_registry.py Langgraph/tests/test_model_registry.py
git commit -m "feat: add model registry"
```

---

### Task 2: Graph Model Plumbing

**Files:**
- Modify: `D:\Vibecoding\Langgraph\graph_agent.py:718-773`
- Create: `D:\Vibecoding\Langgraph\tests\test_graph_model.py`

- [ ] **Step 1: Write the failing graph-model tests**

Create `tests/test_graph_model.py`:

```python
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from graph_agent import active_model, build_graph


class FakeLLM:
    def __init__(self):
        self.tools = None

    def bind_tools(self, tools):
        self.tools = tools
        return self


class GraphModelTests(unittest.TestCase):
    def test_build_graph_forwards_model_key(self):
        with patch("graph_agent.build_llm", return_value=FakeLLM()) as build:
            graph = build_graph(
                manager=None,
                include_subagent_tools=False,
                model_key="qwen",
            )
        build.assert_called_once_with("qwen")
        self.assertIsNotNone(graph)

    def test_active_model_uses_requested_key(self):
        with patch.dict(os.environ, {"DEEPSEEK_MODEL": "deepseek-flash"}, clear=False):
            provider, model, base_url = active_model("deepseek")
        self.assertEqual(provider, "deepseek")
        self.assertEqual(model, "deepseek-flash")
        self.assertTrue(base_url.startswith("https://api.deepseek.com"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
D:\Anaconda3\envs\rag\python.exe -m unittest -v tests.test_graph_model
```

Expected: `TypeError: build_graph() got an unexpected keyword argument 'model_key'` (or an `active_model` signature error).

- [ ] **Step 3: Wire the registry into `graph_agent.py`**

Add this import after the LangGraph imports:

```python
from model_registry import (
    ModelConfigError,
    ModelProfile,
    build_chat_model,
    get_profile,
    initial_model_key,
    model_table,
    resolve_selector,
)
```

Replace `active_model`, `build_llm`, and `build_graph` (currently lines 718-773) with:

```python
def active_model(model_key: str | None = None) -> tuple[str, str, str]:
    """返回指定模型（默认 LLM_PROVIDER）的 (provider, model, base_url)。"""
    profile = get_profile(model_key or initial_model_key())
    return profile.provider, profile.model, profile.base_url


def build_llm(model_key: str | None = None) -> ChatOpenAI:
    """按模型 key 创建客户端；缺 key 时抛 ModelConfigError。"""
    return build_chat_model(model_key or initial_model_key())


def build_graph(
    manager=None,
    include_subagent_tools: bool = True,
    model_key: str | None = None,
):
    """构建并编译 Agent 图：agent -> (tools -> agent)* -> END。"""
    tools = list(TOOLS)
    if include_subagent_tools and manager is not None:
        from subagent_tools import build_subagent_tools

        tools = tools + build_subagent_tools(manager)

    llm_with_tools = build_llm(model_key).bind_tools(tools)

    def agent_node(state: MessagesState) -> dict:
        response = llm_with_tools.invoke(state["messages"])
        return {"messages": [response]}

    builder = StateGraph(MessagesState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", ToolNode(tools))
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", tools_condition)
    builder.add_edge("tools", "agent")
    return builder.compile()
```

- [ ] **Step 4: Run the graph-model tests**

```bash
D:\Anaconda3\envs\rag\python.exe -m unittest -v tests.test_graph_model
```

Expected: `Ran 2 tests` — `OK`.

- [ ] **Step 5: Commit Task 2**

```bash
git add Langgraph/graph_agent.py Langgraph/tests/test_graph_model.py
git commit -m "feat: build graph for a selected model"
```

---

### Task 3: ModelRuntime and `/model` Command

**Files:**
- Modify: `D:\Vibecoding\Langgraph\graph_agent.py` (add `ModelRuntime`; update loop/main)
- Modify: `D:\Vibecoding\Langgraph\session_cli.py:24-45`
- Create: `D:\Vibecoding\Langgraph\tests\test_model_switch.py`

- [ ] **Step 1: Write the failing runtime and command tests**

Create `tests/test_model_switch.py`:

```python
from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage

from graph_agent import ModelRuntime, run_chat_loop
from model_registry import ModelConfigError
from session_store import SessionStore

USED_GRAPHS: list[str] = []


class RecordingGraph:
    def __init__(self, key: str):
        self.key = key

    def invoke(self, state, config):
        USED_GRAPHS.append(self.key)
        messages = list(state["messages"])
        messages.append(AIMessage(content=f"reply:{self.key}"))
        return {"messages": messages}


class FakeManager:
    def __init__(self):
        self.keys: list[str] = []

    def set_model_key(self, key: str) -> None:
        self.keys.append(key)


def fake_build(manager, model_key=None):
    return RecordingGraph(model_key)


def failing_build(manager, model_key=None):
    if model_key == "qwen":
        raise ModelConfigError("未找到 DASHSCOPE_API_KEY，无法切换到 千问多模态。")
    return RecordingGraph(model_key)


class ModelRuntimeAndCommandTests(unittest.TestCase):
    def setUp(self):
        USED_GRAPHS.clear()
        self.root = Path(__file__).resolve().parent.parent / f".test_model_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()
        self.store = SessionStore(self.root)
        self.session = self.store.create("模型测试")
        self.outputs: list[str] = []
        self.manager = FakeManager()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _run(self, inputs: list[str], runtime: ModelRuntime) -> None:
        iterator = iter(inputs)
        run_chat_loop(
            self.store,
            runtime.graph,
            self.session,
            input_fn=lambda prompt: next(iterator),
            output=self.outputs.append,
            runtime=runtime,
        )

    def test_runtime_initializes_and_switches(self):
        with patch("graph_agent.build_graph", side_effect=fake_build):
            runtime = ModelRuntime(self.manager, "local")
            self.assertEqual(runtime.key, "local")
            self.assertEqual(runtime.graph.key, "local")
            profile = runtime.switch("2")
        self.assertEqual(profile.key, "deepseek")
        self.assertEqual(runtime.key, "deepseek")
        self.assertEqual(runtime.graph.key, "deepseek")
        self.assertEqual(self.manager.keys, ["deepseek"])

    def test_failed_switch_keeps_previous_state(self):
        with patch("graph_agent.build_graph", side_effect=failing_build):
            runtime = ModelRuntime(self.manager, "local")
            with self.assertRaises(ModelConfigError):
                runtime.switch("qwen")
        self.assertEqual(runtime.key, "local")
        self.assertEqual(runtime.graph.key, "local")
        self.assertEqual(self.manager.keys, [])

    def test_same_model_is_noop(self):
        with patch("graph_agent.build_graph", side_effect=fake_build):
            runtime = ModelRuntime(self.manager, "local")
            old_graph = runtime.graph
            profile = runtime.switch("1")
        self.assertIs(runtime.graph, old_graph)
        self.assertEqual(profile.key, "local")
        self.assertEqual(self.manager.keys, [])

    def test_model_list_command(self):
        with patch("graph_agent.build_graph", side_effect=fake_build):
            runtime = ModelRuntime(self.manager, "local")
            self._run(["/model", "exit"], runtime)
        output = "\n".join(self.outputs)
        self.assertIn("[模型] 当前: local", output)
        self.assertIn("deepseek", output)
        self.assertIn("qwen3-vl-plus", output)

    def test_switch_command_uses_new_graph(self):
        with patch("graph_agent.build_graph", side_effect=fake_build):
            runtime = ModelRuntime(self.manager, "local")
            self._run(["/model 2", "你好", "exit"], runtime)
        output = "\n".join(self.outputs)
        self.assertIn("[模型] 已切换: deepseek-v4.1-flash", output)
        self.assertIn("[助手] reply:deepseek", output)
        self.assertEqual(USED_GRAPHS, ["deepseek"])
        self.assertEqual(self.manager.keys, ["deepseek"])

    def test_invalid_selector_does_not_change_model(self):
        with patch("graph_agent.build_graph", side_effect=fake_build):
            runtime = ModelRuntime(self.manager, "local")
            self._run(["/model nope", "exit"], runtime)
        self.assertIn("[错误] 未知模型", "\n".join(self.outputs))
        self.assertEqual(runtime.key, "local")

    def test_switch_preserves_session_messages(self):
        self.session.messages = [HumanMessage(content="旧消息")]
        with patch("graph_agent.build_graph", side_effect=fake_build):
            runtime = ModelRuntime(self.manager, "local")
            runtime.switch("deepseek")
        self.assertEqual([m.content for m in self.session.messages], ["旧消息"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
D:\Anaconda3\envs\rag\python.exe -m unittest -v tests.test_model_switch
```

Expected: `ImportError: cannot import name 'ModelRuntime' from 'graph_agent'`.

- [ ] **Step 3: Add `ModelRuntime` and update the loop**

Add `ModelRuntime` immediately after `build_graph`:

```python
class ModelRuntime:
    """保存当前模型、manager 和已编译图；切换成功后整体替换。"""

    def __init__(self, manager=None, key: str | None = None):
        self.manager = manager
        self.key = resolve_selector(key or initial_model_key())
        self.profile: ModelProfile = get_profile(self.key)
        self.graph = build_graph(self.manager, model_key=self.key)

    def switch(self, selector: str) -> ModelProfile:
        new_key = resolve_selector(selector)
        if new_key == self.key:
            return self.profile
        new_graph = build_graph(self.manager, model_key=new_key)
        if self.manager is not None:
            self.manager.set_model_key(new_key)
        self.key = new_key
        self.profile = get_profile(new_key)
        self.graph = new_graph
        return self.profile
```

Update `_run_chat_loop_inner` signature (currently line 823) to add `runtime=None`:

```python
def _run_chat_loop_inner(
    store: SessionStore,
    graph,
    session: Session,
    input_fn: Callable[[str], str] = input,
    output: Callable[[str], None] = print,
    manager=None,
    runtime: ModelRuntime | None = None,
) -> None:
```

Inside the command dispatch, add this branch after the `help` branch:

```python
                elif name == "model":
                    if runtime is None:
                        output("[错误] 模型切换未启用。")
                    else:
                        selector = arg.strip()
                        if not selector or selector.lower() == "list":
                            output(model_table(runtime.key))
                        elif selector.lower() == "current":
                            output(f"[模型] 当前: {runtime.key} ({runtime.profile.label})")
                        else:
                            old_key = runtime.key
                            try:
                                profile = runtime.switch(selector)
                            except ModelConfigError as exc:
                                output(f"[错误] {exc}")
                            else:
                                graph = runtime.graph
                                if runtime.key == old_key:
                                    output(f"[模型] 已经是 {profile.label}。")
                                else:
                                    output(
                                        f"[模型] 已切换: {profile.label} "
                                        f"({profile.model})"
                                    )
```

Update `run_chat_loop` (currently line 988) to accept and forward `runtime`:

```python
def run_chat_loop(
    store,
    graph,
    session,
    input_fn=input,
    output=print,
    manager=None,
    runtime: ModelRuntime | None = None,
) -> None:
    if manager is None:
        return _run_chat_loop_inner(store, graph, session, input_fn, output, None, runtime)
    manager.start_monitor(output)
    try:
        return _run_chat_loop_inner(
            store, graph, session, input_fn, output, manager, runtime
        )
    finally:
        manager.shutdown()
```

- [ ] **Step 4: Add the command and help text in `session_cli.py`**

Add `"model"` to `KNOWN_COMMANDS`:

```python
KNOWN_COMMANDS = {
    "new",
    "list",
    "sessions",
    "switch",
    "delete",
    "clear",
    "history",
    "agents",
    "model",
    "help",
}
```

Add this line to `HELP_TEXT` immediately before `/agents`:

```text
  /model [编号|key]     查看或切换模型；/model 列出
```

- [ ] **Step 5: Run the switch tests**

```bash
D:\Anaconda3\envs\rag\python.exe -m unittest -v tests.test_model_switch
```

Expected: `Ran 7 tests` — `OK`.

- [ ] **Step 6: Run existing session tests**

```bash
D:\Anaconda3\envs\rag\python.exe -m unittest -v tests.test_session_flow
```

Expected: `Ran 5 tests` — `OK` (the new `runtime` argument is optional).

- [ ] **Step 7: Commit Task 3**

```bash
git add Langgraph/graph_agent.py Langgraph/session_cli.py Langgraph/tests/test_model_switch.py
git commit -m "feat: add /model switching command"
```

---

### Task 4: Subagent Model Inheritance

**Files:**
- Modify: `D:\Vibecoding\Langgraph\subagent_manager.py:21-90`
- Modify: `D:\Vibecoding\Langgraph\subagent_worker.py:34-47`
- Modify: `D:\Vibecoding\Langgraph\subagent_fake_worker.py:14-18`
- Modify: `D:\Vibecoding\Langgraph\tests\test_subagent_manager.py`
- Modify: `D:\Vibecoding\Langgraph\tests\test_subagent_worker.py`

- [ ] **Step 1: Add failing inheritance tests**

Append to `tests/test_subagent_manager.py`, inside `SubagentManagerTests`:

```python
    def test_spawn_passes_model_key(self):
        self.manager.set_model_key("qwen")
        with patch("subagent_manager.subprocess.Popen") as popen:
            popen.return_value.pid = 1234
            popen.return_value.poll.return_value = None
            self.manager.spawn("检查模型")
            command = popen.call_args.args[0]
        self.assertIn("--model-key", command)
        self.assertEqual(command[command.index("--model-key") + 1], "qwen")
```

Add this import at the top of the file:

```python
from unittest.mock import patch
```

Append to `tests/test_subagent_worker.py`, inside `SubagentWorkerTests`:

```python
    def test_main_forwards_model_key(self):
        with patch("subagent_worker.build_graph", return_value=FakeGraph()) as build:
            code = main(
                [
                    "--agent-id",
                    self.record.id,
                    "--project-root",
                    str(self.root),
                    "--model-key",
                    "deepseek",
                ]
            )
        self.assertEqual(code, 0)
        build.assert_called_once_with(
            include_subagent_tools=False,
            model_key="deepseek",
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
D:\Anaconda3\envs\rag\python.exe -m unittest -v tests.test_subagent_manager.SubagentManagerTests.test_spawn_passes_model_key tests.test_subagent_worker.SubagentWorkerTests.test_main_forwards_model_key
```

Expected: manager test fails because `set_model_key` does not exist; worker test fails because `--model-key` is unrecognized.

- [ ] **Step 3: Update `SubagentManager`**

Add this import near the top:

```python
from model_registry import initial_model_key, resolve_selector
```

Change the constructor signature to add `model_key`:

```python
    def __init__(
        self,
        project_root: Path,
        worker_path: Path | None = None,
        max_running: int = MAX_RUNNING,
        poll_interval: float = 0.5,
        model_key: str | None = None,
    ):
```

Add this after `self.poll_interval = poll_interval`:

```python
        self._model_key = resolve_selector(model_key or initial_model_key())
```

Add these methods after `running_count`:

```python
    @property
    def model_key(self) -> str:
        return self._model_key

    def set_model_key(self, key: str) -> None:
        """切换后续新建子 agent 使用的模型；运行中的不受影响。"""
        self._model_key = resolve_selector(key)
```

Add the model argument to the worker command, before `--agent-id`:

```python
                    [
                        sys.executable,
                        str(self.worker_path),
                        "--model-key",
                        self._model_key,
                        "--agent-id",
                        record.id,
                        "--project-root",
                        str(self.project_root),
                    ],
```

- [ ] **Step 4: Update the real and fake workers**

In `subagent_worker.py`, add the argument after `--project-root`:

```python
    parser.add_argument("--model-key", default=None)
```

Change the graph construction line:

```python
        graph = build_graph(
            include_subagent_tools=False,
            model_key=args.model_key,
        )
```

In `subagent_fake_worker.py`, add the same optional argument after `--project-root`:

```python
    parser.add_argument("--model-key", default=None)
```

- [ ] **Step 5: Run the inheritance tests**

```bash
D:\Anaconda3\envs\rag\python.exe -m unittest -v tests.test_subagent_manager.SubagentManagerTests.test_spawn_passes_model_key tests.test_subagent_worker.SubagentWorkerTests.test_main_forwards_model_key
```

Expected: `Ran 2 tests` — `OK`.

- [ ] **Step 6: Run the full subagent test files**

```bash
D:\Anaconda3\envs\rag\python.exe -m unittest -v tests.test_subagent_manager tests.test_subagent_worker
```

Expected: `Ran 14 tests` — `OK` (manager 10, worker 4).

- [ ] **Step 7: Commit Task 4**

```bash
git add Langgraph/subagent_manager.py Langgraph/subagent_worker.py Langgraph/subagent_fake_worker.py Langgraph/tests/test_subagent_manager.py Langgraph/tests/test_subagent_worker.py
git commit -m "feat: propagate model selection to subagents"
```

---

### Task 5: Main Wiring, Banner, and Full Verification

**Files:**
- Modify: `D:\Vibecoding\Langgraph\graph_agent.py:1005-1033`
- Modify: `D:\Vibecoding\Langgraph\graph_agent.py` module docstring (optional but recommended)

- [ ] **Step 1: Update `main()`**

Replace the current `main()` body (lines 1005-1033) with:

```python
def main() -> None:
    try:
        store = SessionStore(Path(__file__).resolve().parent / ".sessions")
    except OSError as exc:
        print(f"[错误] 无法创建会话目录: {exc}")
        return
    try:
        session = store.load_active_or_create()
    except SessionStoreError as exc:
        print(f"[错误] {exc}")
        return
    for warning in store.pop_warnings():
        print(f"[提示] {warning}")
    print(
        f"[会话] {session.title} ({session.id}) | "
        f"共 {len(session.messages)} 条消息"
    )
    print("输入 /help 查看会话命令；输入 exit / quit / q / 退出 结束。")

    manager = SubagentManager(
        project_root=Path(__file__).resolve().parent,
        model_key=initial_model_key(),
    )
    try:
        runtime = ModelRuntime(manager, initial_model_key())
    except ModelConfigError as exc:
        print(f"[错误] {exc}")
        manager.shutdown()
        return

    profile = runtime.profile
    print(
        f"[LLM] {profile.label} | provider={profile.provider} | "
        f"model={profile.model} | base_url={profile.base_url}"
    )
    run_chat_loop(
        store,
        runtime.graph,
        session,
        manager=manager,
        runtime=runtime,
    )
```

- [ ] **Step 2: Update the module docstring example**

Replace the DeepSeek-only paragraph (lines 20-21) with:

```text
默认模型由 LLM_PROVIDER 决定（ollama/deepseek/dashscope）；启动后随时用：
    /model              # 列出三个模型
    /model 2            # 切到 deepseek-v4.1-flash
    /model qwen         # 切到 qwen3-vl-plus
```

- [ ] **Step 3: Run the full test suite**

```bash
D:\Anaconda3\envs\rag\python.exe -m unittest discover -s tests -v
```

Expected: `Ran 142 tests` — `OK` (123 baseline + 8 registry + 2 graph-model + 7 switch + 1 manager + 1 worker).

- [ ] **Step 4: Manual smoke test with the local model**

```bash
$env:LLM_PROVIDER="ollama"
D:\Anaconda3\envs\rag\python.exe graph_agent.py
```

At the prompt:

```text
你: /model
你: /model local
你: 你好，只回复“本地正常”
你: exit
```

Expected:
- banner shows `本地 qwen3.5 | provider=ollama | model=qwen3.5:2b`
- `/model` lists three entries with `local` marked current
- `/model local` says `已经是 本地 qwen3.5。`
- reply contains `本地正常`

- [ ] **Step 5: Optional cloud smoke test (only if keys are available and cost is acceptable)**

```text
你: /model 2
你: 只回复“deepseek正常”
你: /model 3
你: 只回复“qwen正常”
你: /model 1
你: exit
```

Expected: switch confirmations; short replies. If a key/network is unavailable, the existing `[错误] 调用模型失败: ...` appears and the model remains selected; switch back with `/model 1`.

- [ ] **Step 6: Commit Task 5**

```bash
git add Langgraph/graph_agent.py
git commit -m "feat: wire model switcher into the CLI"
```

---

## Plan Self-Review

- [ ] **Spec coverage:** Each spec section maps to a task:
  - Model registry/profiles → Task 1
  - `build_llm` / `build_graph` model key → Task 2
  - `/model` list/switch/errors, `ModelRuntime` → Task 3
  - Subagent inheritance → Task 4
  - Startup banner, main wiring, full verification → Task 5
  - No image input, no persistence, no automatic fallback → intentionally absent
- [ ] **Placeholder scan:** Search this plan for `TBD`, `TODO`, `FIXME`, `implement later`, `add appropriate`. Expected: only this checklist line; do not leave real placeholders.
- [ ] **Type consistency:**
  - `ModelProfile` fields used by `model_table`, `build_chat_model`, `ModelRuntime`.
  - `ModelRuntime.switch` returns `ModelProfile`; `run_chat_loop` assigns `runtime.graph`, not the return value.
  - `SubagentManager.set_model_key` accepts any alias; `spawn` uses canonical `self._model_key`.
  - Worker `build_graph(..., model_key=args.model_key)` matches the new signature.
  - `/model` branch uses `runtime.key`, `runtime.profile.label`, `runtime.graph`.
- [ ] **Spec mismatch check:** `qwen3-vl-plus` is only used for text/tool calls in this plan; no image path is added. `deepseek` uses official `deepseek-flash`, display label `deepseek-v4.1-flash`.
- [ ] **Test count check:** 123 baseline + 8 registry + 2 graph-model + 7 switch + 1 manager + 1 worker = 142.

Fix any mismatch inline before execution.
