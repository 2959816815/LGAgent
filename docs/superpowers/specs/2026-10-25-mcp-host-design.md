# MCP 宿主设计（本地 stdio）

日期：2026-10-25
状态：已与用户确认设计，待实现

## 1. 背景

桌面端已经有 16 个内置工具（文件、Office、网页搜索）、会话、模型切换、子 Agent 和写文件审批。继续把每个联网能力硬编码进 `graph_agent.TOOLS` 会带来三个问题：

1. 工具数量增长后，主文件、提示词和测试都越来越难维护。
2. 新增浏览器、GitHub、数据库等能力时，必须改 Agent 核心代码。
3. 联网工具启动慢或失败时，容易拖垮本地文件/Office 功能。

因此增加一个 MCP（Model Context Protocol）宿主层：把外部能力做成 MCP Server，由 Python sidecar 启动、发现和调用。第一版只支持本机 stdio Server，并预置 Playwright。

## 2. 目标

- 本地 stdio MCP Server 的启动、初始化、工具发现、调用和退出。
- MCP 工具动态注册进 LangGraph，不修改核心工具列表。
- MCP 完全可选：断网、Server 崩溃、启动超时都不能影响文件、Office、会话、模型和本地模型。
- 第一版预置 Playwright MCP，项目内安装依赖和 Chromium。
- 配置文件 + 桌面端 MCP 状态面板。
- 按 Server 配置 `auto` / `ask` / `deny` 权限。
- 为后续远程 HTTP/SSE MCP 留出接口，但第一版不实现。

## 3. 非目标

- 远程 HTTP/SSE MCP Server。
- MCP 插件市场、在线安装、自动更新。
- 可视化编辑 Server 命令和参数；新增 Server 仍改 `mcp_servers.json`。
- 让 MCP Server 访问桌面端内部对象或 UI。
- 自动重试所有失败；第一版只做手动重试和一次性启动。
- 把 MCP 工具与内置工具做同名覆盖。

## 4. 总体架构

```text
Electron renderer
  ├─ 聊天 / 工具事件 / MCP 状态面板
  └─ HTTP + SSE
        │
Python FastAPI sidecar
  ├─ McpManager
  │    ├─ 读取 mcp_servers.json
  │    ├─ 后台启动已启用 Server
  │    ├─ 健康状态 / 工具清单 / 重启
  │    └─ 退出时结束所有子进程
  ├─ McpClient（每个 Server 一个）
  │    └─ JSON-RPC over stdio
  ├─ mcp_tools（MCP -> LangChain StructuredTool）
  ├─ 核心 TOOLS（永远存在）
  └─ LangGraph（核心工具 + 当前健康 MCP 工具）
```

关键原则：

- **核心工具不依赖 MCP 启动结果。**
- **MCP 工具按运行快照注册。** 每次构建图时只取当前 `ready` 且未 `deny` 的工具。
- **一个 Server 失败不影响其他 Server，也不影响主流程。**
- **MCP 的 stdout 只用于协议，日志走 stderr。**

## 5. 配置

项目根目录新增 `mcp_servers.json`。第一版内容：

```json
{
  "servers": {
    "playwright": {
      "command": "node",
      "args": ["desktop/node_modules/@playwright/mcp/cli.js"],
      "cwd": ".",
      "env": {},
      "enabled": true,
      "permission": "auto",
      "startup_timeout_ms": 30000,
      "tool_timeout_ms": 60000
    }
  }
}
```

字段：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `command` | 是 | 可执行文件，不经 shell |
| `args` | 否 | 参数数组 |
| `cwd` | 否 | 相对项目根目录；默认项目根 |
| `env` | 否 | 额外环境变量；支持 `${VAR}` 展开 |
| `enabled` | 否 | 默认 `true` |
| `permission` | 否 | `auto` / `ask` / `deny`，默认 `ask` |
| `startup_timeout_ms` | 否 | 默认 30000 |
| `tool_timeout_ms` | 否 | 默认 60000 |

规则：

- 不执行 shell，不拼命令字符串。
- `cwd` 解析后必须位于项目根目录内。
- `${VAR}` 只从当前进程环境读取；缺失时该 Server 启动失败并记录明确错误。
- 配置缺失或 JSON 无效时不阻止应用启动；MCP 面板显示配置错误。
- 不把真实 Token 写进仓库；示例配置只出现环境变量名。

## 6. MCP 客户端

新增 `mcp_client.py`，实现最小 stdio JSON-RPC 客户端，不引入额外 Python MCP SDK，避免 async 运行时和现有同步 sidecar 冲突。

### 6.1 启动

```text
Popen([command, *args], cwd=cwd, env=base_env + server.env,
      stdin=PIPE, stdout=PIPE, stderr=PIPE, text=True)
```

- `stderr` 由后台线程持续读取，保留最近 4KB 作为错误详情。
- `stdout` 按行读取 JSON；每行一个 JSON-RPC 消息。
- 非 JSON 行只记录日志，不崩溃。

### 6.2 握手

1. 启动进程。
2. 发送 `initialize`，包含 `protocolVersion`、`capabilities`、`clientInfo`。
3. 收到结果后发送 `notifications/initialized`。
4. 调用 `tools/list`，缓存工具描述和 inputSchema。
5. 状态置为 `ready`。

握手总时长受 `startup_timeout_ms` 限制；超时杀掉进程并置 `failed`。

### 6.3 调用

`tools/call` 请求包含工具名和参数。结果只提取文本内容：

- `content` 中多个 `text` 项拼接。
- 非文本内容（图片、资源）先转成简短占位说明，不把二进制塞进上下文。
- 总输出截断到 20000 字符，并附 `[已截断]`。
- 工具执行超时受 `tool_timeout_ms` 限制。
- 协议错误、进程退出、超时统一返回 `[错误] MCP ...`，交给模型处理。

### 6.4 进程退出

- 应用退出时先发 `shutdown`（如果 Server 支持），再 terminate，最后 kill。
- 子进程退出后，客户端状态置 `stopped` 或 `failed`，并清空工具缓存。
- 不自动重启；MCP 面板提供“重试启动”。

## 7. MCP 管理器

新增 `mcp_manager.py`：

```python
class McpManager:
    def load_config(self) -> None: ...
    def start_enabled(self) -> None: ...       # 后台线程，不阻塞
    def list_servers(self) -> list[dict]: ...
    def list_tools(self) -> list[McpToolSpec]: ...
    def call_tool(self, server: str, tool: str, args: dict) -> str: ...
    def restart(self, name: str) -> None: ...
    def set_enabled(self, name: str, enabled: bool) -> None: ...
    def shutdown(self) -> None: ...
```

状态：

- `disabled`：配置为 disabled，或用户关闭。
- `starting`：进程已启动，握手未完成。
- `ready`：握手完成，有工具清单。
- `failed`：启动/握手失败。
- `stopped`：进程已退出。

约束：

- `start_enabled()` 在 FastAPI startup 中启动一个 daemon 线程，不阻塞窗口。
- 每个 Server 独立线程/客户端；一个失败不取消其他。
- 工具列表按 Server 缓存；`ready` 才暴露。
- 工具总数上限 100；超过时只注册前 100 个并记录警告。
- `list_servers()` 不返回敏感 env 值。

## 8. 工具注册

新增 `mcp_tools.py`：

```python
def build_mcp_tools(manager: McpManager) -> list[StructuredTool]: ...
```

- 工具名：`mcp_<server>_<tool>`，只保留字母、数字、下划线，重复时加短后缀。
- 描述：MCP 工具 description；为空时用 `MCP tool <name> from <server>`。
- 参数：把 MCP `inputSchema` 转成 Pydantic 模型后作为 `args_schema`；只支持 JSON Schema 的对象类型、基础类型、数组和嵌套对象，遇到不支持的字段时降级为 `dict` 参数并在描述中保留原始 schema。
- 执行：调用 `manager.call_tool`，异常转 `[错误]` 字符串。
- 输出：沿用 MCP 客户端的 20000 字符截断。

`graph_agent.build_graph` 增加可选参数：

```python
def build_graph(
    manager=None,
    include_subagent_tools: bool = True,
    model_key: str | None = None,
    extra_tools: list | None = None,
):
    tools = list(TOOLS)
    ...
    if extra_tools:
        tools = tools + list(extra_tools)
    tools = wrap_tools(tools)
```

- `extra_tools` 只由桌面端传入；CLI 不传，行为不变。
- `desktop_api.run_graph` 构建图时传入 `mcp_manager.build_tools()`。
- 测试中的 `graph_builder` 需要接受 `extra_tools` 关键字；现有 fake builder 同步更新。

## 9. 权限

权限按 Server 配置：

- `auto`：直接执行，不弹窗。
- `ask`：执行前通过现有 `ApprovalBroker` 发通用审批请求。
- `deny`：不注册该 Server 的任何工具。

`approval.py` 扩展：

- `build_preview` 对 `mcp_` 工具生成通用卡片：
  - `title`: `调用 MCP: <server> / <tool>`
  - `path`: Server 名
  - `diff`: 空
  - `detail`: JSON 参数，截断到 2000 字符
- `wrap_tools` 保持只包装写文件工具；新增 `wrap_mcp_tools`，对 `ask` 的 MCP 工具包审批。
- 拒绝时返回 `[错误] 用户拒绝执行 MCP 工具`；超时返回 `[错误] MCP 审批超时`。
- `auto` 的 MCP 工具不做撤销记录。

第一版 Playwright 配置为 `auto`；未来未知 Server 默认 `ask`。

## 10. 桌面 API

新增端点（均需现有 token）：

```text
GET  /api/mcp/servers
POST /api/mcp/servers/{name}/restart
POST /api/mcp/servers/{name}/enabled   {"enabled": true|false}
```

`GET` 返回：

```json
{
  "servers": [
    {
      "name": "playwright",
      "status": "ready",
      "enabled": true,
      "permission": "auto",
      "tool_count": 23,
      "error": null,
      "started_at": "2026-10-25T20:00:00+08:00"
    }
  ],
  "config_path": "D:\\Vibecoding\\Langgraph\\mcp_servers.json"
}
```

- `restart` 异步执行，接口立即返回；状态通过前端轮询或 SSE 更新。
- `enabled` 原子写回 `mcp_servers.json`，再启动/停止对应 Server。
- 所有错误只影响 MCP 面板，不返回 500 影响聊天。
- 退出时 `McpManager.shutdown()`。

## 11. 桌面 UI

右侧面板新增 **MCP** 标签页：

- Server 卡片：名称、状态圆点、权限、工具数。
- 状态：`启动中` / `就绪` / `失败` / `已停止` / `已禁用`。
- 错误信息可展开，显示 stderr 摘要。
- 按钮：`重试`、启用/停用开关。
- 顶部说明：MCP 是可选功能，失败不影响本地文件/Office。
- 首次加载时显示“正在启动 MCP Server…”，不阻塞聊天。

不新增设置页；新增 Server 仍编辑 JSON。

## 12. 离线与失败隔离

必须满足：

1. 断网启动应用：窗口正常打开；核心 16 个工具可用；MCP 状态为 `starting`/`failed`；不弹启动错误框。
2. Playwright 启动失败：聊天、文件、Office、本地模型、会话全部正常。
3. MCP 工具调用网络失败：返回 `[错误]` 给模型，不抛出到 FastAPI。
4. 一个 Server 崩溃：其他 Server 和核心工具不受影响。
5. 配置 JSON 损坏：应用仍启动；MCP 面板显示配置错误。
6. MCP 关闭：`build_graph` 的工具列表与当前版本完全一致。

## 13. 安全

- 不经过 shell；`command` 和 `args` 分开。
- `cwd` 限制在项目根目录内。
- 环境变量展开不写日志；错误信息不包含完整 Token。
- MCP stdout 只解析 JSON-RPC；其他输出丢弃到 stderr 日志。
- 工具输出 20000 字符上限。
- 工具名合法化，禁止覆盖核心工具名。
- `ask` 模式必须显示 Server、工具名和参数摘要。
- 第一版不开放远程 MCP，避免 SSRF 和远程鉴权面。

## 14. 测试

### 14.1 假 MCP Server

新增 `tests/fake_mcp_server.py`，用 stdio 实现：

- `initialize` / `initialized`
- `tools/list` 返回两个工具
- `tools/call` 返回文本
- `slow` 工具延迟响应
- `crash` 工具退出进程
- 启动失败模式

### 14.2 单元测试

- 握手成功、工具清单解析。
- `tools/call` 成功、错误、超时。
- Server 崩溃后状态 `failed`，核心工具仍存在。
- 配置缺失、JSON 损坏、`${VAR}` 展开。
- 工具名非法字符和重名。
- `auto` / `ask` / `deny` 权限。
- `build_graph(extra_tools=...)` 包含 MCP 工具。
- `run_graph` 中 MCP 工具失败不传播异常。

### 14.3 桌面测试

- `GET /api/mcp/servers` 返回状态。
- `restart` / `enabled` 更新配置。
- UI 渲染 MCP 卡片、错误、开关。
- 手动：联网时 Playwright 打开本地 HTML，点击按钮并读取文本；断网时启动应用，核心功能正常。

## 15. 安装与运行

- 在 `desktop` 中安装 `@playwright/mcp`。
- 安装 Chromium：`pnpm exec playwright install chromium`。
- `mcp_servers.json` 的 `args` 指向项目内 `desktop/node_modules/@playwright/mcp/cli.js`。
- 开发环境使用系统 Node。
- 打包版后续需要解析/捆绑 Node；第一版先保证开发版。
- 首次安装需要网络；安装完成后离线可启动 MCP 宿主（浏览器导航仍需要网络）。

## 16. 验收标准

- [ ] 应用启动不等待 MCP；MCP 失败不阻塞窗口。
- [ ] MCP 关闭时，核心工具数量和行为与当前版本一致。
- [ ] Playwright 就绪后，Agent 能调用 `mcp_playwright_*` 工具打开页面、点击、读取文本。
- [ ] 断网启动时，文件/Office/本地模型/会话正常。
- [ ] Server 崩溃只影响自身，MCP 面板显示错误并可重试。
- [ ] `auto` / `ask` / `deny` 行为符合定义。
- [ ] 所有新增 Python 测试通过，现有 179 个测试不回归。
- [ ] 桌面构建、类型检查和前端测试通过。

## 17. 后续扩展

- 远程 HTTP/SSE MCP：复用 `McpClient` 接口，增加 transport。
- MCP Server 可视化管理。
- 工具级 allowlist / 域名 allowlist。
- MCP 工具结果缓存和更细的上下文裁剪。
- 打包版 Node 运行时。
