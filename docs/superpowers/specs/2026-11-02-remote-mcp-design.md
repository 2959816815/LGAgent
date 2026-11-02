# Remote MCP with Resources and Prompts Design

Date: 2026-11-02
Status: approved for implementation planning

## Background

The desktop agent currently supports only local stdio MCP servers. The
implementation uses a custom synchronous JSON-RPC client
(`mcp_client.StdioMcpClient`) that implements:

- `initialize`
- `notifications/initialized`
- `tools/list`
- `tools/call`
- `notifications/tools/list_changed` (via a dirty flag)

It has no network transport and no support for resources or prompts.
`McpManager` stores `command`, `cwd`, `env`, and `permission` per server, and
the MCP panel can add stdio servers from a small built-in catalog.

The installed Python environment has `mcp` 1.24.0, which provides:

- `mcp.client.streamable_http.streamablehttp_client`
- `mcp.client.sse.sse_client`
- `mcp.client.session.ClientSession` with `list_tools`, `call_tool`,
  `list_resources`, `read_resource`, `list_resource_templates`,
  `list_prompts`, `get_prompt`, `subscribe_resource`, and notifications.

User decisions:

- Support remote MCP over Streamable HTTP (preferred) and legacy SSE.
- Static headers / bearer tokens with DPAPI encryption; reserve an OAuth
  extension point but do not implement OAuth in this phase.
- Remote servers expose the full surface: tools, resources, prompts.
- Local stdio servers stay tools-only for now, to avoid regressing the existing
  Playwright/GitHub/SQLite integrations.
- Resources: agent-callable tools plus a UI browser; prompts: UI picker only.
- Add remote servers through a manual form and standard `mcpServers` JSON
  import; no remote preset catalog.
- Binary resources show metadata only; prompts prefill the composer and do not
  auto-send; remote connections reconnect automatically with backoff.

This phase writes the design only. No application code, packaging, or installed
application changes are made.

## Goals

1. Add remote MCP servers over Streamable HTTP and SSE alongside existing stdio
   servers, without changing stdio behavior.
2. Store remote configuration (`url`, `transport`, `headers`, TLS and timeout
   settings) in `mcp_servers.json`, with header secrets encrypted and redacted.
3. Expand `${ENV}` placeholders in URLs, headers, and secrets at launch time.
4. Support the complete remote MCP surface: tools, resources, resource
   templates, prompts, and list-changed notifications.
5. Expose remote resources to the agent through `list_resources` and
   `read_resource` tools, and to the user through the MCP panel.
6. Expose remote prompts through a UI picker that pre-fills the chat composer.
7. Add servers through a manual remote form or by importing a standard
   `mcpServers` JSON object, with previews and validation.
8. Reconnect dropped remote sessions automatically with capped exponential
   backoff, while keeping the existing manual retry behavior.
9. Keep the current stdio client and all existing MCP tests working.

## Non-Goals

- OAuth 2.1, dynamic client registration, browser authorization, and token
  refresh. The client interface leaves room for an auth provider, but this phase
  accepts static headers only.
- Resources and prompts for local stdio servers. Those remain tools-only.
- Resource subscriptions (`resources/subscribe`) and update notifications.
- Server-initiated sampling, elicitation, or roots. The client advertises no
  support and returns method-not-found.
- Remote MCP preset catalogs or automatic server discovery.
- Binary resource content in prompts or model context. Binary resources are
  summarized by MIME type and size.
- Packaging or installing the desktop application.

## Architecture

```
McpManager
  ├─ stdio entry  -> StdioMcpClient          (tools only, unchanged)
  └─ remote entry -> RemoteMcpClient          (official mcp SDK)
                       └─ one asyncio loop thread per server
                            ClientSession over streamablehttp_client | sse_client
                            tools / resources / prompts / notifications
```

New and changed modules:

- `mcp_client.py`
  - Add a `McpClient` protocol/ABC that both clients implement for the common
    parts: `start`, `close`, `alive`, `tools`, `tools_dirty`,
    `refresh_tools`, and `call_tool`.
  - Keep `StdioMcpClient` tools-only. Resource and prompt methods exist on the
    remote client, not the protocol, or return "unsupported" for stdio.
- `remote_mcp_client.py` (new)
  - Synchronous facade over the async SDK.
  - Owns one daemon thread and one asyncio event loop per remote server.
  - Exposes `list_resources`, `read_resource`, `list_resource_templates`,
    `list_prompts`, and `get_prompt` in addition to the common methods.
- `mcp_resources.py` (new)
  - Dataclasses for resources, templates, prompts, prompt arguments, and prompt
    messages.
  - Conversion from SDK types and content formatting (text, binary summary,
    embedded resources).
- `mcp_manager.py`
  - Transport-aware config validation, launch dispatch, status, and imports.
  - Managed server records no longer assume a subprocess.
- `mcp_tools.py`
  - Existing tool wrapping, plus the two resource tools for remote servers.
- `desktop_api.py`
  - New remote configuration endpoints, import, test connection, resources, and
    prompts.
- `desktop/src/components/McpPanel.tsx`
  - Remote server form, JSON import, server detail with resources and prompts.

`McpManager` gains `add_remote_server(config)`, `test_remote_server(config)`,
and `import_servers(entries)`. The existing catalog-based `add_server` remains
for stdio presets.

## Configuration

`mcp_servers.json` entries gain optional remote fields. Existing entries remain
valid.

```json
{
  "id": "remote_demo",
  "name": "远程演示服务",
  "enabled": true,
  "transport": "streamable-http",
  "url": "https://example.com/mcp",
  "headers": {
    "Authorization": "Bearer ${REMOTE_MCP_TOKEN}",
    "X-Workspace": "demo"
  },
  "verify_tls": true,
  "timeout": 60,
  "permission": "ask"
}
```

Rules:

- `transport` is `stdio`, `streamable-http`, or `sse`.
- A missing `transport` means `stdio` for backward compatibility.
- Validation is transport-aware: `stdio` requires a non-empty `command` and
  ignores `url`/`headers`; remote requires `url` and ignores
  `command`/`cwd`/`env`; any other `transport` is rejected.
- There is no transport auto-detection. The user selects the transport in the
  manual form, or the JSON `type`/`transport` field determines it.
- `url` must be absolute `http` or `https`. Plain HTTP is allowed but produces a
  warning in the status payload and the UI.
- `headers` is an optional object of string keys and string values. A header
  value is encrypted with DPAPI before being written and decrypted only at
  launch when its name matches `TOKEN`, `KEY`, `SECRET`, `PASSWORD`, `PASSWD`,
  or `CREDENTIAL` case-insensitively, or is `authorization` or
  `proxy-authorization`, or is listed in `secret_headers`.
- `secret_headers` is an optional list of header names marked secret by the
  manual form. It lets users protect custom header names that do not match the
  automatic pattern. Status shows header names only, never values.
- Secrets should be placed in headers rather than URL query strings. The URL is
  stored as provided and shown in the UI; it is not redacted.
- `verify_tls` defaults to `true`. Disabling it is an explicit per-server
  choice and is shown as a warning in the UI.
- `timeout` is the request/tool timeout in seconds, default `60`. The connect
  timeout is fixed at `30` seconds. The SSE read timeout is fixed at `300`
  seconds.
- `permission` remains `auto`, `ask`, or `deny` and applies to tools.
- IDs match `^[a-z0-9][a-z0-9_-]{0,63}$`.

`${ENV}` expansion applies to the URL and header values after decryption. A
missing variable fails the launch with an error naming the variable. Expansion
does not apply to the transport, permission, or timeouts.

Status and logs never contain header values. Header names may be shown. Error
messages are passed through a redaction function that removes known secret
values and `Authorization`-style values.

### Auth provider seam

Remote connections obtain their headers from an auth provider:

```python
class RemoteAuthProvider:
    def prepare(self, config: dict) -> dict[str, str]: ...
    def on_auth_error(self, status_code: int, body: str) -> None: ...
```

This phase ships only `StaticHeaderAuthProvider`: it decrypts secret headers,
expands `${ENV}`, and returns the header dict. A future OAuth provider can
implement the same interface, refresh tokens, and signal a retry through
`on_auth_error` without changing `RemoteMcpClient` or the manager. No OAuth
code, browser flow, or token storage is part of this phase.

## JSON Import

`POST /api/mcp/import` accepts either:

```json
{"mcpServers": {"name": {"command": "npx", "args": ["-y", "pkg"]}}}
```

or the inner `{"name": {...}}` map. The body may be a JSON object or a string
containing JSON.

Entry detection:

- `command` present: stdio. A string command becomes `[command, *args]`; an
  array is used as-is. `env`, `cwd`, and `permission` are copied.
- `url` present: remote. `type` or `transport` maps
  `sse`/`http`/`streamable-http`/`streamableHttp` to the canonical transport.
  `headers` are copied.
- Neither: validation error for that entry.

Validation is all-or-nothing: every entry is parsed and validated first. The
import endpoint takes a `confirm` flag. With `confirm: false` it returns the
preview and writes nothing; with `confirm: true` it writes all entries or none.
If any entry fails, nothing is imported and the response lists per-entry
errors.

IDs come from the map key, lowercased and sanitized to the allowed ID charset.
Conflicts with existing IDs or with other imported entries get a `-2`, `-3`, …
suffix. With `confirm: false` the response is:

```json
{
  "entries": [
    {"id": "github", "name": "github", "transport": "stdio", "conflict": false},
    {"id": "remote_demo", "name": "remote_demo", "transport": "streamable-http", "conflict": false}
  ],
  "errors": []
}
```

The frontend first calls the endpoint with `confirm: false`, shows the preview
and any errors, then calls it again with `confirm: true` only after the user
confirms.

## Remote Client

`RemoteMcpClient` is a synchronous facade over the asynchronous SDK. It owns:

- One daemon thread with a new asyncio event loop.
- One `ClientSession` while connected.
- Cached tools, resources, resource templates, and prompts.
- Dirty flags for each cache.
- A supervisor task that reconnects after a successful initial connection.

Construction parameters: `url`, `transport`, `headers`, `verify_tls`,
`timeout`, `tool_timeout`, `sse_read_timeout`, and `server_name`.

### Startup

`start()` performs the initial connection and waits up to 30 seconds:

1. Start the event-loop thread.
2. Open the transport:
   - `streamable-http`: `streamablehttp_client` with
     `terminate_on_close=True`.
   - `sse`: `sse_client`.
3. Open `ClientSession` and call `initialize()`.
4. Read capabilities and `serverInfo`.
5. Fetch caches according to capabilities:
   - `tools` → `tools/list`
   - `resources` → `resources/list` and `resources/templates/list`
   - `prompts` → `prompts/list`
6. Mark the client ready and return.

Cache failure handling:

- A `tools/list` failure fails startup; the server is not usable.
- A resources or prompts fetch failure is non-fatal: that cache stays empty,
  a warning is recorded, and the client continues running. Warnings appear in
  the status payload.

If the initial connection fails, `start()` raises `McpError` with the sanitized
error, closes the loop and thread, and does not retry in the background. The
existing manager behavior then stores the failure and the user can click retry.

### Reconnect

After a successful start, a supervisor task watches the session. If the session
ends without an explicit `close()`:

- Set status to `failed` with the last error.
- Retry while the client is not stopped: delays 1, 2, 5, 10, 30, then 60
  seconds indefinitely.
- On success, refresh all caches and return to `running`; reset the delay.
- A manual restart (`McpManager.restart`) closes the old client and creates a
  new one, resetting the backoff.

If the reconnect attempt fails before the server is running, the status error
is updated; the loop continues.

### Sync bridge

- Public methods build the coroutine and submit it with
  `asyncio.run_coroutine_threadsafe` to the client loop.
- The result is awaited with the configured timeout.
- If the loop is closed or the client is stopped, raise `McpError`.
- Caches are guarded by a `threading.Lock`; dirty flags use
  `threading.Event`.
- `close()` sets the stop event, cancels the supervisor and session tasks, exits
  the transport contexts, stops the loop, and joins the thread with a 5-second
  timeout.

### HTTP client

Remote transports use a custom `httpx.AsyncClient` factory:

- `headers` from the config.
- `verify=verify_tls`.
- `trust_env=True` so system proxy settings are honored.
- `follow_redirects=False` to avoid cross-host redirects.
- Connect timeout 30 seconds; read timeout from `sse_read_timeout` for SSE and
  request timeout for Streamable HTTP.

### Notifications

The session message handler observes:

- `notifications/tools/list_changed` → set the tools dirty flag.
- `notifications/resources/list_changed` → set the resources dirty flag.
- `notifications/prompts/list_changed` → set the prompts dirty flag.
- `notifications/resources/updated` → ignored in this phase.

The existing manager watcher refreshes the corresponding cache when a dirty
flag is set. Refreshing resources also refreshes templates. A failed refresh
keeps the previous cache and records a warning; it does not stop the server.

### Server-initiated requests

The client advertises no sampling, elicitation, or roots capability. Requests
for those methods are answered with a method-not-found error. They are not
surfaced to the model or the user.

## Resources

Resource data:

```python
McpResource(uri, name, description, mime_type)
McpResourceTemplate(uri_template, name, description, mime_type)
```

The remote client fetches `resources/list` and
`resources/templates/list` when the server declares the `resources`
capability. Reading a resource calls `resources/read`.

Content formatting in `mcp_resources.py`:

- `TextResourceContents` → the text, truncated at 100,000 characters with
  `\n…[资源内容已截断]`.
- `BlobResourceContents` → `[二进制资源] mime=<mime> size=<bytes>`; the base64
  payload is never sent to the model or inserted into the chat.
- `EmbeddedResource` → formatted as text when it contains text; otherwise the
  same binary summary.
- Unknown content types → a short type summary.

### Agent tools

For a running remote server whose capabilities include `resources`:

- `mcp__<server_id>__list_resources()` returns a numbered list of name, URI,
  MIME type, and description. Empty resources return
  `[MCP 资源] 该服务没有可列出的资源。`
- `mcp__<server_id>__read_resource(uri: str)` reads a resource. The URI must
  exactly match an entry from the cached `resources/list`; otherwise the tool
  returns an error listing the allowed URIs. Templates are displayed in the UI
  but are not readable through the agent tool in this phase.
- These two tools are added only when the server is running and declares the
  capability.
- Resource tools are not counted against `max_tools`; the count applies to the
  server's own tools. The agent tool list therefore grows by at most two per
  resource-capable remote server.

## Prompts

Prompt data:

```python
McpPrompt(name, description, arguments=[McpPromptArgument(name, description, required)])
McpPromptMessage(role, text)
```

The remote client fetches `prompts/list` when the server declares the `prompts`
capability. `get_prompt(name, arguments)` calls `prompts/get` and converts the
returned messages to `McpPromptMessage` objects:

- Text content is kept.
- Image/audio/resource content is summarized as `[图片]`, `[音频]`, or
  `[资源: uri]`; binary payloads are not inserted.
- The combined prompt text is capped at 20,000 characters.

Prompts are never exposed as agent tools. In the MCP panel, the user selects a
prompt, fills required arguments, previews the result, and clicks
「插入输入框」. The frontend inserts:

```text
[Prompt: <name>]
[user]
...
[assistant]
...
```

into the chat composer. It does not send automatically; the user can edit or
cancel before sending.

## Server Lifecycle and Status

`ManagedProcess` becomes a transport-neutral `ManagedServer` with:

- `client`: `StdioMcpClient` or `RemoteMcpClient`
- `transport`
- `started_at`

`McpServerStatus` gains:

- `transport`: `stdio`, `streamable-http`, or `sse`
- `resource_count`
- `prompt_count`
- `capabilities`: a small dict of booleans (`tools`, `resources`, `prompts`)
- `server_info`: name and version reported by the server, when available
- `warnings`: a list of non-fatal cache or notification refresh errors

Status values remain `starting`, `running`, `failed`, `stopped`, and
`disabled`. For remote clients:

- Before the initial connection completes: `starting`.
- While a session is connected: `running`.
- Immediately after a connection is lost: `failed` with the last error.
- During an active reconnection attempt: `starting`.
- Between reconnection attempts, during the backoff wait: `failed` with the
  last error.
- After `close()` or when disabled: `stopped` or `disabled`.

`start_enabled`, `set_enabled`, `restart`, `remove`, and `stop_all` keep their
current semantics. Enabling starts the server; disabling stops it. Restart
closes and recreates the client.

Tool refresh remains pull-based: `build_mcp_tools` is called at the start of
each agent run, so newly discovered tools are picked up on the next run.

## API

All endpoints use the existing desktop token.

### Add a server

`POST /api/mcp/servers` accepts two shapes:

```json
{"preset_id": "playwright", "env": {}}
```

and

```json
{
  "kind": "remote",
  "id": "remote_demo",
  "name": "远程演示",
  "url": "https://example.com/mcp",
  "transport": "streamable-http",
  "headers": {"Authorization": "Bearer ${REMOTE_MCP_TOKEN}"},
  "verify_tls": true,
  "timeout": 60,
  "permission": "ask"
}
```

The remote shape is validated, saved, and launched. A startup failure keeps the
configuration and returns the failed status with `last_error`.

### Import

`POST /api/mcp/import`

Body: `{"payload": <object or string>, "confirm": false}`.

With `confirm: false`, response 200:

```json
{
  "entries": [
    {"id": "github", "name": "github", "transport": "stdio", "conflict": false},
    {"id": "remote_demo", "name": "remote_demo", "transport": "streamable-http", "conflict": false}
  ],
  "errors": []
}
```

With `confirm: true`, response 200:

```json
{"imported": [{"id": "github", "transport": "stdio"}, {"id": "remote_demo", "transport": "streamable-http"}]}
```

With `confirm: true` and any validation error, response 400:
`{"detail": {"errors": ["..."]}}` with nothing imported.

### Test connection

`POST /api/mcp/servers/test`

Body: the remote configuration shape, without `id`/`name` required.

Response:

```json
{
  "ok": true,
  "transport": "streamable-http",
  "server_info": {"name": "demo", "version": "1.0"},
  "capabilities": {"tools": true, "resources": true, "prompts": true},
  "tools": 3,
  "resources": 2,
  "prompts": 1,
  "error": ""
}
```

The transient client is always closed. Network errors are returned as
`{"ok": false, "error": "<sanitized>"}` with HTTP 200 so the form can display
them.

### Resources

- `GET /api/mcp/servers/{id}/resources`

```json
{
  "resources": [{"uri": "demo://readme", "name": "README", "description": "", "mime_type": "text/plain"}],
  "templates": [{"uri_template": "demo://items/{id}", "name": "Item", "description": "", "mime_type": ""}]
}
```

- `GET /api/mcp/servers/{id}/resources/read?uri=...`

```json
{"uri": "demo://readme", "mime_type": "text/plain", "text": "...", "binary": false, "size": 123}
```

Binary resources return `binary: true`, `text: "[二进制资源] mime=... size=..."`,
and `size`.

These endpoints query the running client live and update the manager's cache
used for status counts. A server that is not running, or that lacks the
capability, returns 400. A live protocol failure returns 502 with a sanitized
error.

### Prompts

- `GET /api/mcp/servers/{id}/prompts`

```json
{"prompts": [{"name": "summarize", "description": "总结", "arguments": [{"name": "text", "description": "内容", "required": true}]}]}
```

- `POST /api/mcp/servers/{id}/prompts/get`

Body: `{"name": "summarize", "arguments": {"text": "..."}}`

```json
{"name": "summarize", "description": "", "messages": [{"role": "user", "text": "..."}]}
```

Prompt list/get endpoints also query the live client and update the prompt
cache. The same 400/502 rules apply.

Errors:

- 404: unknown server.
- 400: server not running, capability missing, invalid arguments, or missing
  `uri`.
- 502: remote protocol failure, with a sanitized message.

### Status

`GET /api/mcp/servers` gains the new fields described above. Header values are
never included.

## Frontend

`McpPanel` keeps the existing server cards and adds:

- A transport badge: 本地 stdio / 远程 HTTP / 远程 SSE.
- Counts: 工具, 资源, Prompt.
- Non-fatal warnings from the status payload are shown under the card.
- A "详情" toggle per server.
- Detail sections:
  - Tools: names only.
  - Resources: list with name, URI, MIME; 「读取」 shows a preview; 「插入对话」
    inserts a formatted block into the composer. Binary resources show the
    summary and disable insertion.
  - Resource templates: read-only list.
  - Prompts: list with a form for required arguments; 「获取并插入」 inserts the
    formatted prompt into the composer.
- "添加服务" becomes three modes:
  - 目录: the existing stdio catalog.
  - 手动远程: name, id, URL, transport select, header rows, TLS toggle, timeout,
    permission, and 「测试连接」.
  - JSON 导入: textarea, parse preview, conflicts, confirm.
- The panel polls `/api/mcp/servers` every 3 seconds while it is open, so
  remote reconnects and status transitions appear without a manual refresh.
  Polling stops when the panel unmounts.
- Header rows have a "secret" checkbox. Secret values are write-only: after
  saving, the form shows the header name and a placeholder, never the value.
- HTTP URLs and disabled TLS show inline warnings.

Insertion callbacks are passed from `App.tsx`; the existing chat composer state
is reused. A resource or prompt insertion appends to the current input rather
than replacing it, and never sends automatically.

## Security

- URL scheme allowlist: `http` and `https` only. Plain HTTP produces a warning.
- TLS verification is on by default. Disabling it is per server and clearly
  marked.
- Header values matching the secret pattern are DPAPI-encrypted at rest and
  decrypted only at launch. Status, logs, and errors never contain values.
- `${ENV}` expansion happens after decryption. Missing variables fail the
  launch with the variable name only.
- Redirects are not followed. The system proxy is honored through `trust_env`.
- Resource URIs are restricted to the server's `resources/list` for the agent
  tool. Resource text is capped at 100,000 characters; binary content is
  summarized.
- Prompt output is capped at 20,000 characters.
- Server-initiated sampling, elicitation, and roots requests are rejected with
  method-not-found; they are not forwarded to the user or model.
- The existing `auto`/`ask`/`deny` permission model applies to remote tools.
- Remote servers are explicitly user-configured; there is no discovery,
  automatic catalog, or auto-connect to arbitrary URLs.
- JSON imports validate every entry before writing; remote entries never
  execute commands.

## Testing

Backend unit tests:

- Config: legacy entries default to stdio; remote validation; URL schemes;
  transport values; timeout and TLS defaults.
- Secrets: header secret detection, DPAPI roundtrip, status/log redaction,
  `${ENV}` expansion and missing-variable errors.
- JSON import: stdio and remote entries, `command` string/array, `type`
  variants, ID sanitization, conflicts, all-or-nothing validation.
- Resource formatting: text, binary summary, embedded resources, truncation.
- Prompt formatting: multiple messages, argument validation, size cap.

Remote client integration tests use a local FastMCP test server with auth
headers, tools, resources, templates, prompts, and list-changed notifications.
Run the same suite over Streamable HTTP and SSE:

- Connect and read `serverInfo`/capabilities.
- `tools/list` and `tools/call`.
- `resources/list`, `resources/templates/list`, `resources/read`.
- `prompts/list`, `prompts/get`.
- Notification → dirty flag → refresh.
- Wrong/missing auth header → startup failure with a sanitized message.
- Timeout handling.
- Server stop and restart → reconnect with backoff, status transitions.
- `close()` exits the thread and loop within the timeout.

Manager/API tests:

- Add remote server, status fields, enable/disable/restart/remove.
- Test-connection endpoint closes its transient client.
- Import endpoint preview/validation and all-or-nothing behavior.
- Resource and prompt endpoints, capability errors, 404s, sanitized 502s.
- Agent resource tools: presence only when capable, URI allowlist, binary
  summary, size caps.
- Existing stdio MCP tests remain green.

Frontend tests:

- Remote add form, transport select, header rows, TLS/HTTP warnings.
- Test connection success/failure display.
- JSON import preview and confirmation.
- Server detail counts and sections.
- Resource preview/insert; prompt argument form/insert.
- Polling while the panel is open and cleanup on unmount.

Manual verification:

1. Start a local FastMCP demo server over Streamable HTTP and one over SSE.
2. Add both from the manual form; confirm counts and status.
3. Call a remote tool from chat.
4. Read a text resource from the agent tool and from the UI; insert it into the
   composer.
5. Fetch a prompt, fill arguments, and confirm it pre-fills without sending.
6. Stop the server, watch the status move to failed, restart it, and confirm
   automatic reconnection.
7. Import an `mcpServers` JSON with one stdio and one remote entry; confirm the
   preview and the resulting servers.

## Implementation Order

1. Config schema, migration, remote validation, secrets/redaction, and JSON
   import parsing with unit tests.
2. `McpClient` protocol and `McpResource`/`McpPrompt` dataclasses plus formatting.
3. `RemoteMcpClient`: asyncio bridge, Streamable HTTP and SSE connect, tools,
   sync API, close; integration tests against FastMCP.
4. Manager launch dispatch, status fields, reconnect supervisor, resource and
   prompt cache refresh.
5. Agent resource tools and tool inventory rules.
6. API additions: remote add, test connection, import, resources, prompts.
7. Frontend: remote form, JSON import, detail sections, resources/prompts,
   polling.
8. Full backend and frontend suites, then manual verification.

## Rollback

- Remote entries are additive. Removing them or disabling them returns the
  manager to its existing stdio behavior.
- Existing `mcp_servers.json` entries without `transport` remain valid and keep
  using `StdioMcpClient`.
- Deleting a remote entry stops its client and removes its configuration; no
  other data is affected.
- The implementation stays in the development tree until the user asks for a
  package.

## Risks

- The async SDK bridge must not leak event loops or threads on repeated
  connect/disconnect. Tests cover close and restart.
- Some SSE implementations differ in event formatting; the official SDK is the
  compatibility layer, and tests cover both transports.
- Windows proxy/TLS behavior may vary; `trust_env` and `verify_tls` are
  configurable per server.
- Without OAuth, hosted MCP servers that require browser authorization cannot
  be used yet; the headers flow covers API-key servers.
- Resource and prompt content is untrusted server output. It is inserted as
  data, but prompt injection remains possible; the UI labels its source and
  never auto-sends.
- A reconnecting remote server may flap; the capped backoff and manual restart
  bound the impact.
