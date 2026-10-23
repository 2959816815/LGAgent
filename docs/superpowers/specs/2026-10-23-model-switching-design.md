# Model Switching Design

## Background

`graph_agent.py` currently chooses one LLM at startup from module globals:

- `PROVIDER` (`ollama` / `deepseek` / `dashscope`)
- `OLLAMA_MODEL`, `DEEPSEEK_MODEL`, `QWEN_MODEL`
- `build_llm()` reads those globals; `build_graph()` binds tools once.

The agent already has a terminal command layer in `session_cli.py` and a process-based subagent layer in `subagent_manager.py` / `subagent_worker.py`. This spec adds a `/model` command that switches among three models **during the current run**, with no config-file persistence.

## Goals

1. Three selectable models:
   - `local`: local Ollama `qwen3.5:2b`
   - `deepseek`: official DeepSeek API `deepseek-flash` (V4.1-Flash-1017), displayed as `deepseek-v4.1-flash`
   - `qwen`: Alibaba DashScope `qwen3-vl-plus` (multimodal-capable; this feature only uses text)
2. `/model` lists models and switches by number, key, or alias.
3. Switching rebuilds the LangGraph graph successfully **before** changing runtime state; failure leaves the previous model active.
4. Current session messages are preserved. Switching does not itself make a network call.
5. New subagents inherit the current model key; already-running subagents keep the model they were started with.
6. No image input is added in this feature. `qwen3-vl-plus` is only used as a text/tool-calling model for now.

## Non-Goals

- Persisting model choice across restarts or per session.
- Automatic fallback when an API fails.
- Image/audio/video attachments or a multimodal message type.
- Per-subagent manual model selection.
- Changing the model of an already-running subagent.
- A model picker UI; terminal command only.

## Model Registry

New file: `model_registry.py`.

```python
class ModelConfigError(RuntimeError):
    """Raised when a model profile cannot be built."""

@dataclass(frozen=True)
class ModelProfile:
    key: str                 # local | deepseek | qwen
    label: str               # user-facing display name
    provider: str            # ollama | deepseek | dashscope
    model: str               # actual API model id
    base_url: str
    api_key: str | None      # None for local Ollama
    key_env: str | None      # env var used for the missing-key message
    timeout: float
    max_retries: int
    supports_vision: bool
```

Profiles:

| key | label | model | base URL | key |
|---|---|---|---|---|
| `local` | `本地 qwen3.5` | `OLLAMA_MODEL` (default `qwen3.5:2b`) | `OLLAMA_BASE_URL` (default `http://127.0.0.1:11434/v1`) | `"ollama"` (dummy) |
| `deepseek` | `deepseek-v4.1-flash` | `DEEPSEEK_MODEL` (default `deepseek-flash`) | `DEEPSEEK_BASE_URL` (default `https://api.deepseek.com`) | `DEEPSEEK_API_KEY` |
| `qwen` | `千问多模态` | `QWEN_MODEL` (default `qwen3-vl-plus`) | `DASHSCOPE_BASE_URL` (default `https://dashscope.aliyuncs.com/compatible-mode/v1`) | `DASHSCOPE_API_KEY` |

Notes:

- `deepseek-flash` is the official text model that currently maps to DeepSeek-V4.1-Flash-1017. `deepseek-v4.1-flash` is Alibaba Model Studio's model id for the same generation and is not the official DeepSeek API id; the display label is kept user-friendly, the API call uses `DEEPSEEK_MODEL`.
- `qwen3-vl-plus` supports text/image/video input and Function Calling on DashScope's OpenAI-compatible endpoint. Only text tool calls are exercised in this feature.
- `supports_vision` is metadata for future work; no code path consumes images yet.

Public API:

```python
DEFAULT_MODEL_KEY = "local"

def list_profiles() -> list[ModelProfile]
def get_profile(key: str) -> ModelProfile
def resolve_selector(selector: str) -> str
def initial_model_key() -> str
def build_chat_model(key: str) -> ChatOpenAI
def model_table(current_key: str) -> str
```

`resolve_selector` accepts, case-insensitively:

- `1`, `local`, `ollama`, `qwen3.5`, `本地` → `local`
- `2`, `deepseek`, `deepseek-flash`, `deepseek-v4.1-flash`, `v4.1-flash` → `deepseek`
- `3`, `qwen`, `qwen3-vl`, `qwen3-vl-plus`, `qwen-vl`, `千问` → `qwen`

Invalid selector raises `ModelConfigError("未知模型: ...")`.

`build_chat_model` uses `ChatOpenAI` for all three:

- `local`: `api_key="ollama"`, `base_url`, `model`, `timeout=120`, `max_retries=1`
- `deepseek`: `api_key=DEEPSEEK_API_KEY`, `base_url`, `model`, `timeout=60`, `max_retries=2`
- `qwen`: `api_key=DASHSCOPE_API_KEY`, `base_url`, `model`, `timeout=60`, `max_retries=2`

If a required API key is empty, raise `ModelConfigError("未找到 <ENV>，无法切换到 <label>。请检查 .env。")`. No network probe is made while switching.

`initial_model_key()` maps the existing `LLM_PROVIDER` values:

- `ollama` → `local`
- `deepseek` → `deepseek`
- `dashscope` / `qwen` → `qwen`
- unset/unknown → `local`

## Command Behavior

`/model` is added to `session_cli.py`'s known commands and help.

### List

`/model`, `/model list`:

```text
[模型] 当前: local (本地 qwen3.5)
  1. local      本地 qwen3.5        qwen3.5:2b        ollama
  2. deepseek   deepseek-v4.1-flash deepseek-flash    https://api.deepseek.com
  3. qwen       千问多模态           qwen3-vl-plus     https://dashscope.aliyuncs.com/compatible-mode/v1
```

`/model current` prints only the current profile, same format as the first line.

### Switch

`/model <selector>`:

1. Resolve the selector; unknown selector → `[错误] 未知模型: <x>；输入 /model 查看可用模型。`
2. Resolve the profile and build the new graph via `ModelRuntime.switch`.
3. If the selector is already active → `[模型] 已经是 <label>。`
4. On success:
   ```text
   [模型] 已切换: deepseek-v4.1-flash (deepseek-flash)
   ```
5. On build/config failure: print `[错误] ...`; current graph, key, and manager model are unchanged.

Switching must not clear `session.messages`. It affects the next user turn; the current in-flight turn always completes on the old graph.

### Scope

The chosen model lives only in `ModelRuntime` and `SubagentManager` in memory. `/new`, `/switch`, and process restart do not carry it over; restart uses `initial_model_key()`.

## Runtime Architecture

### `graph_agent.py`

- `build_llm(model_key: str | None = None)` delegates to `model_registry.build_chat_model`; `None` means `ModelRuntime`'s current key.
- `build_graph(manager=None, include_subagent_tools=True, model_key=None)` builds the LLM for that key and binds the same tool list. `model_key=None` uses `initial_model_key()` for compatibility.
- `active_model()` continues to return `(provider, model, base_url)`, using the current/initial key.
- `ModelRuntime`:
  ```python
  class ModelRuntime:
      def __init__(self, manager, key: str):
          self.manager = manager
          self.key = key
          self.profile = get_profile(key)
          self.graph = build_graph(manager, model_key=key)

      def switch(self, selector: str) -> ModelProfile:
          new_key = resolve_selector(selector)
          if new_key == self.key:
              return self.profile
          new_graph = build_graph(self.manager, model_key=new_key)
          self.key = new_key
          self.profile = get_profile(new_key)
          self.graph = new_graph
          self.manager.set_model_key(new_key)
          return self.profile
  ```
  `switch` builds the graph first; any exception leaves all fields untouched. `build_graph` is cheap (no API call).
- `run_chat_loop` gets an optional `runtime: ModelRuntime | None`. On `/model`:
  - `runtime is None` → `[错误] 模型切换未启用。`
  - call `runtime.switch`, then assign `runtime.graph` to the local `graph` variable used by the loop (the switch method returns the new profile, not the graph).
  - `/model` is handled before any graph invocation.
- `main()` creates the manager, then `runtime = ModelRuntime(manager, initial_model_key())`, and passes `runtime` into `run_chat_loop`. The startup banner prints the current profile.
- `_run_chat_loop_inner` remains the testable core; its new `runtime` parameter is optional.

### `subagent_manager.py`

- Constructor: `SubagentManager(store=None, max_running=3, model_key=None)`; `model_key=None` → `initial_model_key()`. Existing calls/tests remain valid.
- `self._model_key`; property `model_key`; `set_model_key(key)` validates via `resolve_selector`/`get_profile` and stores the canonical key.
- `spawn` child command adds `--model-key <key>` before the existing `--agent-id` / `--task` arguments.
- `format_agent_table` optionally shows model? No: keep table schema unchanged to avoid test churn. Model is shown in the banner and worker log only.

### `subagent_worker.py`

- `main(argv)` parses optional `--model-key` (argparse); default `None`.
- `graph = build_graph(include_subagent_tools=False, model_key=args.model_key)`; `None` → initial/default.
- Worker does not import `ModelRuntime`; it builds one graph for its lifetime.

## Error Handling

- `ModelConfigError` is caught in `ModelRuntime.switch`'s caller and printed as `[错误] ...`; old state remains.
- Missing key is detected before switching; no half-switched state.
- API/auth/network failures remain handled by the existing per-turn `except` in `run_chat_loop`: `[错误] 调用模型失败: ...`. The model stays selected so the user can retry or switch back.
- Subagent worker failure to build the model is caught by its existing top-level handler; the record becomes `failed` with the error text.
- An unknown `LLM_PROVIDER` at startup does not crash: `initial_model_key()` falls back to `local` and the banner shows the actual model.

## Testing

Unit tests:

- `test_model_registry.py`:
  - three profiles, labels, provider/base/key env;
  - selector aliases and case-insensitivity;
  - unknown selector raises;
  - missing `DASHSCOPE_API_KEY` / `DEEPSEEK_API_KEY` raises with env name;
  - `initial_model_key()` mapping and unknown fallback.
- `test_model_switch.py`:
  - `/model` list contains all three and current marker;
  - `/model 2` rebuilds graph and calls `manager.set_model_key("deepseek")`;
  - failed switch (mock `build_graph` raises) leaves key/graph/manager unchanged;
  - same-model switch is a no-op with message;
  - unknown selector error does not change state;
  - session messages unchanged after switch.
- `test_subagent_manager.py` extension:
  - default manager uses local;
  - `set_model_key("qwen")` then `spawn` command contains `--model-key qwen`.
- `test_subagent_worker.py` extension:
  - `--model-key deepseek` passes `model_key="deepseek"` into `build_graph`.

Integration:

- Start with local; `/model 1` shows already local; `/model 2` switches; `/model list` shows deepseek as current; `/model 3` then missing-key test via env monkeypatch.
- Verify a text turn after switch uses the new model (mock `build_graph` capture).
- Full suite must stay green; no regressions in session/subagent/text tests.

Live smoke (optional, not required for CI):

- If `DEEPSEEK_API_KEY` is present: switch to `deepseek`, send a one-line prompt, confirm reply and banner.
- If `DASHSCOPE_API_KEY` is present: switch to `qwen`, send a one-line text prompt (no image), confirm reply. Do not send large prompts; this costs money.

## Acceptance Criteria

1. `/model` lists exactly the three configured models with the current one marked.
2. `/model 2` / `/model deepseek` changes the model used by subsequent main-agent turns.
3. A failed switch (missing key, unknown selector, graph build error) leaves the previous model fully active.
4. Current session messages survive switching.
5. New subagents created after a switch use the new model; running subagents are unaffected.
6. `local` remains the default when `LLM_PROVIDER` is unset.
7. No new dependency is required; all three clients use the existing `langchain-openai` `ChatOpenAI`.
8. Existing 123 tests + new tests pass.

## Compatibility Notes

- `graph_agent.py:725-750` `build_llm()` is replaced by the registry-backed function; `graph_agent.py:753-773` `build_graph()` grows a `model_key` parameter with default behavior.
- `graph_agent.py:988-1002` `run_chat_loop` and `:1005-1033` `main` are updated; existing tests that import `run_chat_loop` keep working because `runtime` is optional.
- `session_cli.py:24-45` command table/help gains `/model`; `parse_command` already handles unknown commands, so no parser rewrite is needed.
- `subagent_manager.py:77-90` Popen argument list gains `--model-key`; existing tests that inspect only `--agent-id` / `--task` still pass after expected-command updates.
- `.env` already has `DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`, `DASHSCOPE_API_KEY`, `DASHSCOPE_BASE_URL`; no secret is printed.
