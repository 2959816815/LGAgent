# Model Context Adaptation Design

Date: 2026-11-01
Status: approved for implementation

## Background

The desktop agent supports three built-in models with very different context
windows:

| Key | Provider | Runtime window |
| --- | --- | --- |
| `local` | Ollama `qwen3.5:2b` | 4096 today, 16384 after this change |
| `deepseek` | DeepSeek API | 128000 |
| `qwen` | DashScope API | 262144 |

Users often work on a long session with DeepSeek or Qwen, then lose network
access and need to continue with the local model. The current context handling
cannot do this safely:

1. `compact_session` is destructive. It replaces older messages with a summary
   and deletes the originals, so switching back to a large model cannot restore
   the transcript.
2. It keeps a fixed 10 messages, not a token budget. Ten recent messages can be
   larger than the local window by themselves.
3. It summarizes with the model being switched to. After a network failure that
   is the local 2B model, which must read a transcript far larger than its own
   window.
4. `estimate_tokens` uses `chars / 4`. Chinese text is close to one token per
   character, so thresholds fire far too late.
5. On a local run, historical image parts are still hydrated to base64 data
   URLs even though the local profile does not declare vision support.

User decisions:

- Automatic fallback to the local model on network-class failures.
- The full transcript is always preserved. Compaction becomes a derived view.
- Local runtime window: 16384, with a core tool subset (no Office, subagent,
  or MCP tools). Measured data forced this choice: the 19 built-in tool schemas
  total 6265 characters (roughly 5-6k tokens), and the local 2B model prefills
  at about 45 tokens per second on this CPU. With 8192 tokens the tool and
  system overhead alone exceeds the window.
- When the network returns, show a button to switch back; do not switch
  automatically.
- Maintain a rolling summary while online (approach B), so a fallback never
  needs to summarize with the local model.

Scope is the development tree only. Do not rebuild or reinstall the packaged
application, and do not change the installed copy's configuration or Ollama
model name.

## Goals

1. Keep every session message in the store. No context operation deletes
   messages.
2. Build a per-model context view that fits the target model's window, with an
   explicit reserve for output, tool schemas, system prompt, and a safety
   margin.
3. Maintain a rolling summary while a cloud model is active, so switching to
   the local model is instantaneous and requires no LLM call.
4. Detect network-class cloud failures during a run, switch the session to the
   local model, and retry the same turn once.
5. Surface fallback and recovery in the UI without a full page reload.
6. Use a CJK-aware token estimate and expose authoritative context statistics
   from the backend instead of duplicating estimates in the frontend.
7. Run the local model with a 16384-token runtime window in development.
8. Give local runs a core tool subset: `get_current_time`, `read_file`,
   `write_file`, `edit_file`, `web_search`, `web_fetch`, `download_file`,
   `read_csv`, `read_json`, `read_pdf`. The schema is about 3.2k characters.
   Local runs do not load Office, subagent, or MCP tools.

## Non-Goals

- Automatic switch back to the cloud model.
- Offline message queueing or later synchronization.
- Exact tokenizer-accurate counting per provider. A conservative heuristic with
  a safety margin is enough for budgeting.
- Changing cloud model configuration, prices, or provider selection.
- Image downscaling or OCR. Images are either counted or replaced by text.
- Rewriting tool descriptions. The local core subset is selected by tool name;
  the existing descriptions stay unchanged.
- Giving local runs the Office, subagent, or MCP tools. Those require a larger
  prompt than the local model can serve at acceptable speed.
- Rebuilding, packaging, or installing the desktop application.
- Changing the installed application's Ollama model or server-wide Ollama
  environment variables.

## Architecture

Core principle: **the transcript is the ledger; context is a view.**

```
session.messages (full, append-only)
        │
        ├── summary + summary_upto (rolling, cloud-maintained)
        │
        ▼
context_builder.build_view(session, profile, system_prompt, tool_tokens)
        │
        ▼
[system prompt] + [summary system message] + [recent turns within budget]
        │
        ▼
LangGraph run with the selected model
```

New file `context_builder.py` owns token estimation, summary maintenance, and
view construction. `desktop_api.py` owns orchestration: when to call the
builder, fallback retry, SSE events, and API responses. Session stores own
persistence only. The frontend renders server-provided context statistics.

## Data Model

`Session` gains two fields:

- `summary_upto: int = 0` - number of leading messages covered by `summary`.
  Invariant: `summary` describes `messages[0:summary_upto]`.
- `summary_updated_at: str = ""` - ISO timestamp of the last summary update.

`summary_count` is retained for compatibility and UI display. Its value is
kept equal to `summary_upto`.

Legacy sessions: if `summary` is non-empty and `summary_upto` is 0, the summary
is treated as a pre-history summary. It is still injected into the view, but it
covers none of the stored messages. Old sessions that had already lost messages
cannot recover them; they simply keep working.

JSON store: serialize and deserialize the new fields with defaults.

SQLite store: add columns with the existing `_ensure_summary_columns` style.

```sql
ALTER TABLE sessions ADD COLUMN summary_upto INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sessions ADD COLUMN summary_updated_at TEXT NOT NULL DEFAULT '';
```

Update the upsert statement and the row mapping.

## Token Estimation

`estimate_tokens(messages) -> int` counts text content, image parts, and a
per-message overhead, then applies a safety multiplier.

- CJK characters (U+3000-U+303F, U+3400-U+4DBF, U+4E00-U+9FFF,
  U+F900-U+FAFF, U+FF00-U+FFEF, U+20000-U+2FA1F): 1 token each.
- ASCII letters, digits, and spaces: 0.25 tokens each.
- Other characters: 0.5 tokens each.
- Each message: 4 tokens overhead.
- Each image part: 1000 tokens.
- Total is multiplied by 1.15 and rounded up.

A text-only variant, `estimate_text_tokens(text) -> int`, applies the same
character rules without the message overhead. `truncate_to_tokens(text, limit)`
returns the longest prefix within the limit and appends `…[摘要已截断]` when it
cuts. Summary caps and chunk sizing use these helpers.

Constants live in `context_builder.py` and are imported by the API. The old
`estimate_tokens` in `desktop_api.py` is removed.

## Budget

For a profile with window `W`:

```
system_tokens    = estimate_tokens([SystemMessage(SYSTEM_PROMPT)])
tool_tokens      = measured schema tokens, or 2000 if measurement fails
output_reserve   = clamp(int(W * 0.15), 512, 4096)
safety_margin    = int(W * 0.10)
conversation     = W - system_tokens - tool_tokens - output_reserve - safety_margin
```

If `conversation <= 0`, raise `ContextBudgetError`. The API turns this into an
SSE error; it must never be hidden by silently truncating history.

Derived budgets:

```
summary_cap      = clamp(int(conversation * 0.25), 400, 2000)
tail_target      = int(conversation * 0.75)
max_message      = max(512, int(conversation * 0.40))
```

Tool schema tokens are measured once per tool-name tuple by serializing the
OpenAI tool schemas and running the same estimator. The measurement is cached
in-process. The measured set is the one a run would use:

- Local (`provider == "ollama"`): the ten core tools only, about 3.2k
  characters of schema. No subagent or MCP tools.
- Cloud: all built-in tools, the subagent tools for the current manager, and
  enabled MCP tools.
- If MCP tools cannot be built, measure without them and use the 2000-token
  fallback if the measurement itself fails.

With the core subset at a 16384 window, the conversation budget is roughly:

```
16384 - 620 (system) - 3200 (tools) - 2457 (output) - 1638 (margin) ≈ 8469
```

The actual view can be smaller; the budget is an upper bound. Prefill speed is
about 45 tokens/second on this CPU, so a full view still costs several minutes.
Offline turns are expected to be slow.

## View Construction

`build_view(session, profile, system_prompt, tool_tokens, attachments)`
returns `(messages, stats)`.

1. Compute the budget above.
2. Start with `SystemMessage(system_prompt)`.
3. If `session.summary` is non-empty, truncate it to `summary_cap`, wrap it as
   `SystemMessage("【较早对话的摘要】\n" + summary)`, and append it.
4. Walk the uncovered messages, `session.messages[session.summary_upto:]`, from
   newest to oldest in whole turns. Messages already covered by the summary
   are never sent again, so the summary and the raw transcript cannot duplicate
   each other in the view. A turn begins at a `HumanMessage` and includes all
   following `AIMessage` and `ToolMessage` entries until the next
   `HumanMessage`. Include a turn only if it fits the remaining budget. Always
   include the newest turn. If `summary_upto` is out of range, clamp it to 0
   and use the full history.
5. If the newest turn alone exceeds the budget, keep its `HumanMessage` and
   truncate each later message in that turn to `max_message` tokens with the
   marker `\n…[内容过长已截断]`.
6. If `profile.supports_vision` is false, replace every image part with the
   text part `[图片已省略]` and do not hydrate it.
7. Run the existing `sanitize_messages` over the assembled view so that
   incomplete tool-call sequences cannot reach the model.
8. Return statistics:

```python
{
    "raw_tokens": estimate_tokens(session.messages),
    "view_tokens": estimate_tokens(view),
    "window": profile.context_window,
    "tool_tokens": tool_tokens,
    "system_tokens": system_tokens,
    "summary_tokens": summary_tokens,
    "recent_turns": included_turns,
    "dropped_turns": dropped_turns,
    "images_dropped": images_replaced,
    "fits": view_tokens <= profile.context_window,
}
```

`dropped_turns` counts uncovered turns that did not fit the budget. Turns covered
by the summary are represented by the summary and are not counted as dropped.

The view is built fresh for every run. It is never persisted.

## Rolling Summary

`ensure_summary(session, profile, build_model, save_session)` is called at the
start of a run **only when the selected profile is not the local Ollama
provider**. Local runs never call it.

Algorithm:

1. `uncovered = session.messages[session.summary_upto:]`.
2. If `estimate_tokens(uncovered) <= tail_target`, return without changes.
3. Find a cutoff index `c` such that `messages[c:]` still fits `tail_target`,
   `c` is a turn boundary, and at least `MIN_KEEP_TURNS = 2` turns remain.
   Clamp `c` to `[session.summary_upto, len(messages)]`.
4. The gap is `messages[session.summary_upto:c]`.
5. Split the gap into chunks of at most 6000 estimated tokens, walking forward
   from `session.summary_upto`. For each chunk (at most 3 chunks per run), call
   the active cloud model once with the previous summary and the chunk
   transcript, asking for a single merged Chinese summary. The prompt preserves
   key facts, decisions, file paths, and open tasks, and forbids invention.
   Track `covered_upto`, the end index of the last successfully summarized
   chunk.
6. Truncate the merged summary to `summary_cap` tokens.
7. Update `session.summary`, `session.summary_upto = covered_upto`,
   `session.summary_count = covered_upto`, and `session.summary_updated_at`.
8. Save the session.

If the gap is larger than three chunks, `covered_upto` only reaches the end of
the third chunk; the rest stays uncovered and is handled on the next cloud run.
A failure mid-way keeps the coverage from the last successful chunk. A fallback
in between still works: the view uses the existing summary plus the newest
turns that fit.

Any exception is caught, logged as a warning, and ignored. The run continues
with the old summary.

The manual "compact" endpoint calls the same routine with `force=True`: it
summarizes everything except the last `MIN_KEEP_TURNS` turns even when the
normal threshold is not met. It still does not delete messages. Manual
compaction is the only path allowed to summarize with the local model, because
the user explicitly requested it; ordinary local runs never call
`ensure_summary`.

## Fallback Orchestration

In `run_graph`, the run is attempted with `key = effective_model_key(session)`.

`is_network_failure(exc)` walks `exc`, `exc.__cause__`, and `exc.__context__`
and returns true for:

- `httpx.ConnectError`, `httpx.ConnectTimeout`, `httpx.ReadTimeout`,
  `httpx.RemoteProtocolError`
- `openai.APIConnectionError`, `openai.APITimeoutError`
- `socket.gaierror`, `ConnectionError`, `ConnectionRefusedError`
- messages containing `connection refused`, `timed out`, `getaddrinfo failed`,
  `network is unreachable`, or `temporary failure in name resolution`

It returns false for HTTP status errors (400, 401, 403, 404, 422, 429), content
filtering, user cancellation, and context-length errors.

Cloud clients are built with a short connect timeout:
`httpx.Client(timeout=httpx.Timeout(profile.timeout, connect=5.0))`. A dead
network then fails in about five seconds instead of waiting for the full
60-second request timeout.

Fallback conditions:

- The failing profile is not the local provider.
- A fallback profile exists: `provider == "ollama"`, preferring `key == "local"`.
- `_probe_local(fallback.base_url)` succeeds.

If a condition fails, the original error is emitted as today.

On fallback:

1. Emit SSE:

```
event: model_fallback
data: {"run_id": "...", "from": "deepseek", "to": "local",
       "label": "本地 qwen3.5", "reason": "连接失败", "restart": true}
```

2. Set `session.model_key` to the fallback key, save the session, and update
   `manager.set_model_key(...)`.
3. Build the local view and a new graph with the same tools.
4. Retry the same turn exactly once. The user message is already stored; do not
   append it again.
5. If the retry succeeds, finish normally. The `done` event includes
   `model_key`.
6. If the retry fails, emit an error containing both causes. Never retry a
   second time.

The partial output of the failed cloud attempt is not persisted. It may have
been streamed already; `model_fallback` with `restart: true` tells the
frontend to discard it.

## Recovery Detection

New endpoint:

```
GET /api/models/{key}/health
```

- Requires the desktop token.
- For a cloud model: require a configured API key, resolve the base URL host
  and port, and attempt a TCP connection with a 2-second timeout. Report
  `{key, reachable, reason}`.
- For a local model: reuse `_probe_local`.

The frontend polls this endpoint every 30 seconds only while the fallback
banner is visible. It stops polling when the banner is dismissed, the user
switches back, or the component unmounts. The banner shows a button
"网络已恢复，切回 DeepSeek". Clicking it calls the existing
`PATCH /api/sessions/{id}/model`. The button is disabled while a run is active.

No automatic switch back.

## API Changes

`GET /api/sessions/{id}` and the session list detail payload gain:

```json
{
  "summary_upto": 12,
  "context": {
    "raw_tokens": 31000,
    "view_tokens": 9800,
    "window": 16384,
    "tool_tokens": 3200,
    "system_tokens": 620,
    "summary_tokens": 900,
    "recent_turns": 2,
    "dropped_turns": 9,
    "images_dropped": 1,
    "fits": true
  }
}
```

`PATCH /api/sessions/{id}/model` returns the same `context` object for the
target model as a preview. It does not modify messages or the summary.

`POST /api/sessions/{id}/compact`:

- Requires no active run (existing 409 behavior).
- Summarizes only; messages are unchanged.
- Returns session JSON including `context`.
- Returns 400 when fewer than `MIN_KEEP_TURNS + 1` turns exist.
- Returns 502 when summary generation fails.
- Uses the current model. If the current model is local, the response includes
  `"summary_model": "local"` so the UI can note lower summary quality.

SSE changes:

- New event `model_fallback`, described above.
- `done` gains `model_key`.
- Error events from a failed fallback include both the cloud and local failure
  reasons.

## Frontend

- `helpers.estimateContext` and its tests are removed. `Chat` and `RightPanel`
  read `session.context` and show view tokens / window, raw history tokens,
  summary size, and dropped turns. The line refreshes after each completed
  turn, when the server returns fresh statistics; there is no client-side
  estimate.
- The context success line reads, for example:
  `上下文 ≈ 9.8k / 16.4k · 原始历史 31k · 已摘要 12 条`.
- A banner component renders fallback and recovery states:
  - Fallback: "网络不可用，已自动切换到本地模型。上下文已压缩至 9.8k tokens。"
  - Local mode also shows "本地模型只启用核心工具（无 Office / 子 agent / MCP）".
  - Recovery available: the banner gains the switch-back button.
- `App` handles `model_fallback` by clearing the current streaming assistant
  text and tool cards for that run before applying subsequent tokens.
- The "立即压缩上下文" button is renamed "更新摘要". It calls the same endpoint
  and refreshes the session detail.
- Types in `types.ts` gain `summary_upto`, `context`, and the new SSE event.

## Local Runtime Window

Verified on 2026-11-01: Ollama 0.18.0's OpenAI-compatible endpoint ignores
`num_ctx`; a request with `"num_ctx": 8192` loaded the model with
`CONTEXT 4096` (checked with `ollama ps`). The app uses the OpenAI-compatible
endpoint, so setting `OLLAMA_CONTEXT_WINDOW` alone does not change the runtime
window.

Development setup:

1. Create a derived model with a Modelfile:

   ```text
   FROM qwen3.5:2b
   PARAMETER num_ctx 16384
   ```

   ```bash
   ollama create qwen3.5:2b-16k -f Modelfile.qwen35-16k
   ```

2. In the development `.env`:

   ```text
   OLLAMA_MODEL=qwen3.5:2b-16k
   OLLAMA_CONTEXT_WINDOW=16384
   ```

3. Verify with one request followed by `ollama ps`; the CONTEXT column must
   read 16384.

If the derived model still loads at 4096, the local provider must call
Ollama's native `/api/chat` with `options.num_ctx = 16384` instead of the
OpenAI-compatible endpoint. The budget logic is unchanged; only the local
client changes. Do not use the server-wide `OLLAMA_CONTEXT_LENGTH` variable,
because it would also affect the installed application.

The installed application keeps `qwen3.5:2b` and 4096. Do not change
server-wide Ollama environment variables.

At 16384 the model plus KV cache is roughly 8 GB; the machine has 16 GB.
Close other memory-heavy applications before relying on local fallback.

Measured prefill on this machine: a 3265-token prompt took 73 seconds, about
45 tokens per second. A full local view with core tools is roughly 7-10k
tokens, so an offline turn can take two to four minutes before generation
starts. This is expected and is not a reason to enlarge the window further;
16384 is still a small window relative to the cloud models, and the view
trimming rules still apply.

## Error Handling and Edge Cases

- Summary generation failure: keep the old summary and full history, log a
  warning, continue the run.
- `ContextBudgetError`: emit an error explaining that the model's window is too
  small for the system prompt and tools. Do not silently drop the newest user
  message.
- A single newest message larger than the budget: truncate it to `max_message`
  tokens with the truncation marker.
- Tool-call pairing: trimming operates on whole turns, and `sanitize_messages`
  runs over the final view.
- Legacy summary with `summary_upto == 0`: inject it once; do not count it as
  coverage.
- Cancellation: a user cancel is not a network failure and never triggers
  fallback.
- Concurrent runs: existing `runs_lock` and `active_runs` remain authoritative.
  Summary updates happen inside `run_graph`, never in a background thread.
- Deleting a session removes messages and summary as before.
- Health checks never send API keys and never log them.
- No installer, packaging, or installed-app files change.

## Testing

Unit tests (`tests/test_context_builder.py`):

- CJK, ASCII, mixed, and image token estimates.
- Budget computation and `ContextBudgetError`.
- Whole-turn trimming; newest turn always included.
- Tool call/result pairing preserved.
- Oversized newest message truncated with marker.
- Summary included, truncated to `summary_cap`, omitted when there is no room.
- Images replaced for non-vision profiles and retained for vision profiles.
- Tool schema measurement cache.

Summary tests (`tests/test_context_compaction.py`, rewritten):

- `ensure_summary` is a no-op below `tail_target`.
- It advances `summary_upto` to a turn boundary and keeps at least two turns.
- Multiple chunks merge into one summary and respect `summary_cap`.
- Failure leaves summary and coverage unchanged.
- Local runs never call the summarizer.
- Legacy sessions with `summary_upto == 0` keep their summary.
- Manual compact never deletes messages.

API tests (`tests/test_model_fallback.py` plus existing `test_desktop_api.py`):

- Fake graph raises a `ConnectError`-shaped exception: assert `model_fallback`
  is emitted, `model_key` becomes `local`, the turn is retried once, and `done`
  reports `local`.
- 401, 400, and 429 do not trigger fallback.
- Local unavailable does not trigger fallback; the original error is reported.
- Retry failure reports both causes.
- No partial assistant message is saved.
- `GET /api/models/{key}/health` returns reachable/unreachable correctly.
- Session JSON and model-switch preview expose `context` and `summary_upto`.
- SQLite and JSON stores round-trip the new fields; SQLite migration adds
  columns to an existing database.

Frontend tests:

- Context line renders server statistics.
- Fallback banner appears on `model_fallback` and clears partial output.
- Recovery button appears when health polling reports reachable, and calls the
  model-switch API once.
- "更新摘要" calls compact and refreshes the session.

Manual verification:

1. Start the development app.
2. Send a few messages on DeepSeek.
3. Disable the network adapter.
4. Send a message: the app should switch to local immediately, show the banner,
   and answer from the local model without an intermediate summarization call.
5. Re-enable the network; within 30 seconds the switch-back button appears.
6. Click it and confirm the next reply uses DeepSeek with the full history.

No packaging or installer rebuild is part of testing.

## Implementation Order

1. Session fields and store migrations.
2. `context_builder.py` with estimator, budget, view builder, and summary
   maintenance.
3. Core tool subset and tool token measurement.
4. Replace the run-time message assembly in `desktop_api.py`.
5. Fallback classifier and retry orchestration.
6. New and changed API endpoints and SSE events.
7. Frontend types, context display, fallback banner, and recovery polling.
8. Development `.env` and derived Ollama model for 16384.
9. Tests and manual verification.

## Rollback

All changes are in the development tree. Reverting the branch restores the
previous behavior. The derived `qwen3.5:2b-16k` model can be removed with
`ollama rm qwen3.5:2b-16k`; the installed application does not reference it.
