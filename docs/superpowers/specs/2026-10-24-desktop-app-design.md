# LangGraph Agent 桌面端设计

- 日期：2026-10-24
- 状态：已确认，待实现
- 范围：开发版桌面工作台（MVP）
- 参考：DeepSeek Harness 桌面端的三栏工作台布局

## 1. 背景与目标

现有 Agent 是 Python CLI：

- `graph_agent.py`：LangGraph agent、会话循环、模型选择、工具。
- `session_store.py` / `session_cli.py`：会话持久化与命令。
- `model_registry.py`：本地 qwen3.5、DeepSeek、千问 VL 三个模型。
- `subagent_store.py` / `subagent_manager.py` / `subagent_tools.py`：子 agent。
- 文件、Office、`web_search` 工具已经可用。

目标是把这套能力装进一个本地桌面应用：

- 左侧会话，中间聊天，右侧任务台。
- 聊天逐字流式输出。
- 模型按会话切换并记住。
- 工具调用和子 agent 状态可见、可停止。

第一版只做**开发版**：能在这台机器上通过开发命令启动，不要求安装包。

## 2. 已确认的决策

| 项目 | 决定 |
|---|---|
| 形态 | Electron + React + TypeScript 桌面壳 |
| Python | FastAPI sidecar，复用现有 Python 模块 |
| 通信 | 本地 HTTP + SSE |
| 布局 | 三栏常驻：会话 / 聊天 / 任务台 |
| 右栏 | 标签页：任务、工具、上下文 |
| 左栏 | 按“今天 / 昨天 / 更早”分组 |
| 模型 | 每个会话记住自己的模型 |
| 模型入口 | 输入框左下角模型胶囊，保留 `/model` 命令 |
| 输出 | 逐字流式 + 工具/任务状态事件 |
| 主题 | 深色单色，冷蓝强调 |
| 窗口 | 标准 Windows 边框，最小 1200×760 |
| 停止生成 | 第一版包含 |
| 交付 | 开发版先跑通，安装包后续 |

## 3. 非目标

第一版不做：

- Windows 安装包 / 自动更新。
- 浅色主题。
- 图片、音频、文件上传。
- 多窗口、远程访问、手机端。
- 会话重命名、导出、文件夹分组。
- 多用户、权限系统。
- 把 Python 依赖打进 exe。

## 4. 总体架构

```
┌──────────────────────────────────────────────┐
│ Electron 主进程                               │
│  - 单实例锁                                   │
│  - 启动/关闭 Python sidecar                   │
│  - 创建 BrowserWindow                         │
│  - 通过 preload 暴露 baseUrl/token            │
└───────────────┬──────────────────────────────┘
                │ contextBridge
┌───────────────▼──────────────────────────────┐
│ React 渲染进程                                │
│  - 三栏 UI                                    │
│  - fetch + SSE                                │
│  - 不启用 nodeIntegration                     │
└───────────────┬──────────────────────────────┘
                │ HTTP 127.0.0.1:<random>
┌───────────────▼──────────────────────────────┐
│ Python FastAPI sidecar                        │
│  - 会话 API                                   │
│  - 消息流 API                                 │
│  - 模型切换 API                               │
│  - 子 agent API                               │
│  - 调用现有 LangGraph agent                   │
└──────────────────────────────────────────────┘
```

### 4.1 进程职责

**Electron 主进程**

- 启动时生成随机 token。
- 以 `--port 0` 启动 Python，读取 stdout 的 `READY {"port": ...}`。
- 等待 `/api/health` 成功，超时 10 秒则显示错误对话框。
- 把 `baseUrl` 和 `token` 通过 preload 传给渲染进程。
- 退出时先请求 sidecar 关闭，再强杀进程树。
- 单实例：第二次启动聚焦已有窗口。

**Python sidecar**

- 只绑定 `127.0.0.1`，不监听 `0.0.0.0`。
- 所有 `/api/*` 请求要求 `X-Desktop-Token`，health 除外。
- 复用 `SessionStore`、`ModelRuntime`、`SubagentManager`，不复制业务逻辑。
- 第一版全局同时只允许一个进行中的回复；其他会话可以浏览，发送时提示等待。

**React 渲染进程**

- `nodeIntegration: false`、`contextIsolation: true`、`sandbox: true`。
- 不直接访问文件系统或执行命令。
- 通过 `window.desktop` 获取 baseUrl/token；其余全部走 HTTP。

### 4.2 开发启动

```
pnpm dev        # Vite 渲染进程 + Electron 主进程
python -m uvicorn desktop_server:app --host 127.0.0.1 --port 0
```

Electron 开发模式由主进程拉起 Python；也允许单独启动 Python 调试。生产打包不在第一版范围。

## 5. 后端 API

统一前缀 `/api`。错误格式：

```json
{"error": {"code": "session_not_found", "message": "会话不存在"}}
```

### 5.1 会话

```
GET    /api/health
GET    /api/sessions
POST   /api/sessions                 {"title": "新会话"}
GET    /api/sessions/{id}
DELETE /api/sessions/{id}
PATCH  /api/sessions/{id}/model      {"model_key": "deepseek"}
POST   /api/sessions/{id}/messages   {"text": "..."}  → SSE
```

`GET /api/sessions` 返回会话摘要：

```json
{
  "sessions": [
    {
      "id": "20261024-153012-ab12",
      "title": "桌面端设计",
      "created_at": "2026-10-24T15:30:12",
      "updated_at": "2026-10-24T15:42:03",
      "message_count": 8,
      "model_key": "deepseek"
    }
  ]
}
```

删除正在生成的会话返回 `409`，由前端先停止再删除。

### 5.2 模型

```
GET   /api/models
PATCH /api/sessions/{id}/model
```

`GET /api/models` 返回可用模型列表（会话当前的 `model_key` 从会话详情读取）：

```json
{
  "models": [
    {"key": "local", "label": "本地 qwen3.5", "available": true},
    {"key": "deepseek", "label": "DeepSeek V4.1 Flash", "available": true},
    {"key": "qwen", "label": "千问 VL Plus", "available": false, "reason": "DASHSCOPE_API_KEY 未配置"}
  ]
}
```

切换模型：

- 只更新当前会话的 `model_key`。
- 生成中返回 `409`。
- 未知 key 返回 `400`。
- 切换后不重建历史消息，只影响下一轮。

### 5.3 消息流

`POST /api/sessions/{id}/messages` 返回 `text/event-stream`。

事件格式：

```
event: token
data: {"run_id":"r-1","text":"你"}

event: tool_start
data: {"run_id":"r-1","tool":"read_file","call_id":"call_1","args":{"path":"D:\\a.txt"}}

event: tool_end
data: {"run_id":"r-1","tool":"read_file","call_id":"call_1","status":"ok","duration_ms":210}

event: agent_update
data: {"run_id":"r-1","agent_id":"a-3","status":"running","task":"整理资料"}

event: done
data: {"run_id":"r-1","message_id":"m-12","interrupted":false}

event: error
data: {"run_id":"r-1","code":"model_error","message":"调用模型失败: ..."}
```

规则：

- 全局同时只允许一个 run；已有 run 时新请求返回 `409`。
- `token` 只承载助手文本增量。
- `tool_start/tool_end` 用于聊天里的工具卡片，也用于右栏“工具”标签。
- `agent_update` 由子 agent 状态轮询/事件桥接产生。
- `done` 之后前端保存最终消息；`error` 之后保留用户消息和错误提示。
- 客户端断开后，后端取消该 run，避免继续空转。

### 5.4 停止与子 agent

```
POST /api/runs/{run_id}/cancel
GET  /api/agents
```

停止：

- 设置取消标志，取消正在进行的模型/工具调用。
- 已生成的文本作为助手消息保存，`interrupted: true`。
- 返回 `done` 事件（如果连接还在）。

子 agent：

- 列表来自现有 `.agents` 存储，显示应用内全部子 agent。
- 第一版不按会话过滤：现有 `AgentRecord` 没有 `session_id`，不为 MVP 改存储格式。
- 状态、任务、耗时、结果摘要。
- 第一版支持“查看”和“停止”；停止通过现有 manager 完成。

## 6. 数据模型

### 6.1 Session 扩展

现有 Session JSON 增加可选字段：

```json
{
  "id": "...",
  "title": "...",
  "created_at": "...",
  "updated_at": "...",
  "model_key": "deepseek",
  "messages": []
}
```

兼容规则：

- 旧文件没有 `model_key`：读取时回退到默认模型，不立即改写文件。
- 第一次切换模型或下一轮对话后写回。
- `model_key` 非法：回退默认并记录警告，不让会话打不开。

### 6.2 消息

沿用现有 `messages` 结构：

- 用户消息：`{"role": "user", "content": "..."}`
- 助手消息：`{"role": "assistant", "content": "..."}`
- 工具消息：沿用 LangGraph 的 `ToolMessage` 序列化结果。

桌面端只负责展示，不新增独立消息数据库。

### 6.3 模型选择

- 后端以会话为粒度保存 `model_key`。
- `SubagentManager` 使用当前选中会话的 key 启动 worker；第一版不把 `model_key` 写入 `AgentRecord`。
- 子 agent 完成后不回写主会话模型。
- CLI 的 `/model` 仍只影响当前进程；桌面端按会话持久化，两者不互相覆盖。

## 7. 前端设计

### 7.1 布局

```
┌────────────┬──────────────────────────┬──────────────┐
│ 左栏 260px │ 中栏 flex                 │ 右栏 320px    │
│            │                          │              │
│ 新会话     │ 标题 + 当前模型           │ 任务/工具/上下文│
│ 搜索       │ 消息流                   │              │
│ 今天       │                          │ 子 agent 卡片 │
│ 昨天       │                          │ 工具日志      │
│ 更早       │ 输入框 + 模型胶囊 + 发送  │ 上下文信息    │
└────────────┴──────────────────────────┴──────────────┘
```

- 右栏可折叠；折叠后中栏变宽。
- 窗口最小 1200×760，默认 1440×900。
- 三栏之间用 1px 边框分隔，不做大面积阴影。

### 7.2 左栏

- 顶部品牌：`LangGraph Agent`。
- “＋ 新会话”按钮。
- 搜索框：按标题过滤；空结果提示“没有匹配的会话”。
- 会话按日期分组：今天、昨天、更早。
- 每项：标题、模型圆点、更新时间；当前项高亮。
- 悬停显示删除按钮；删除需确认。
- 底部：设置入口（第一版只显示版本号和“模型配置来自 .env”）。

### 7.3 中栏

顶部：

- 会话标题（可截断）。
- 右侧显示当前模型名和连接状态点。

消息：

- 用户消息右对齐，窄气泡。
- 助手消息左对齐，Markdown 渲染；代码块可复制。
- 工具调用卡片：图标、工具名、关键参数、状态、耗时；默认折叠，点击展开。
- 子 agent 卡片：名称、任务、状态、耗时；点击跳转右栏任务详情。
- 写文件/改文件只显示路径和完成状态，不显示写入内容。

输入：

- 自适应高度 textarea，最多 8 行。
- Enter 发送，Shift+Enter 换行。
- 左下角模型胶囊；点击弹出三个模型，显示可用状态。
- 右侧发送按钮；生成中变为停止按钮。
- 生成中禁用模型切换和会话切换。

### 7.4 右栏

默认“任务”标签。

**任务**

- 应用内全部子 agent 的卡片列表。
- 每张卡：标题、任务摘要、状态、耗时、进度条。
- 运行中可停止；完成可查看结果。
- 空状态：“还没有子 agent 任务”。

**工具**

- 当前会话最近 50 次工具调用；历史部分从会话里的 `ToolMessage` 重建，实时部分由 SSE 事件追加。
- 每行：工具名、目标、状态、耗时。
- 点击展开参数和结果摘要；文件写入结果只显示路径。
- 空状态：“还没有工具调用”。

**上下文**

- 当前模型、模型可用状态。
- 会话 ID、消息数、估算 token 数。
- 工具调用次数、子 agent 数量。
- 不显示系统提示词全文。

### 7.5 视觉

| Token | 值 |
|---|---|
| 背景 | `#0e0e0e` |
| 面板 | `#151515` |
| 浮层 | `#1b1b1b` |
| 边框 | `#2a2a2a` |
| 主文字 | `#e8e8e8` |
| 次文字 | `#888888` |
| 弱文字 | `#666666` |
| 强调 | `#7c9cff` |
| 成功 | `#7fb98a` |
| 警告 | `#d8a657` |
| 错误 | `#d97b7b` |

- 字体：`"Microsoft YaHei UI", "PingFang SC", system-ui, sans-serif`。
- 基准字号 13px；标题 15px；辅助信息 11px。
- 圆角 6px（卡片 8px）；不使用大渐变。
- 状态点：运行中蓝色呼吸，完成绿色，失败红色。

### 7.6 交互细节

- 新会话创建后自动选中，输入框聚焦。
- 切换会话时取消当前滚动位置，加载历史后滚到底部。
- 流式输出时自动滚动到底部；用户手动上滚后暂停自动滚动，显示“回到底部”。
- 工具卡片默认折叠，失败时自动展开。
- 停止生成后保留已输出文本，并标记“已停止”。
- 断线时顶部显示黄色条：“连接已中断，正在重连…”，不显示假进度。

## 8. 流式实现

1. 前端 POST 消息，后端创建 `run_id`，注册任务。
2. 后端在独立工作线程中运行 LangGraph，事件经 `asyncio.Queue` 送回事件循环。
3. 事件循环写 SSE；每个事件一个 `event:` + `data:`。
4. 模型 token 来自 LangGraph 的 `on_chat_model_stream`；工具事件来自 `on_tool_start`/`on_tool_end`。
5. 结束后保存助手消息、更新会话 `updated_at`，发送 `done`。
6. 取消时设置 `threading.Event`，工具/模型返回后尽快退出；保存已生成文本。
7. 子 agent 状态由 `SubagentManager` 轮询，变化时推 `agent_update`；轮询间隔 1 秒。

## 9. 错误处理

| 场景 | 行为 |
|---|---|
| Python sidecar 启动失败 | Electron 弹窗显示日志路径和“重试/退出” |
| 端口占用 | sidecar 使用 `port=0` 自动选端口；仍失败则报错 |
| token 缺失/错误 | 后端 401；前端显示“本地服务认证失败” |
| 模型 API key 缺失 | 模型菜单该项置灰，标注原因 |
| 模型调用超时 | 聊天显示错误卡片；保留用户消息 |
| SSE 断开 | 前端标记连接中断，允许重试；后端取消 run |
| 会话 JSON 损坏 | 列表跳过并标记；不阻塞其他会话 |
| 删除运行中的会话 | 409，前端提示先停止 |
| 子 agent 启动失败 | 右栏卡片红色状态，显示错误摘要 |

## 10. 测试与验收

### 10.1 后端测试

- 会话 CRUD、`model_key` 兼容与持久化。
- 模型切换：合法/非法/生成中 409。
- 消息流：token 顺序、tool_start/tool_end 配对、done。
- 取消：部分文本保存、run 释放。
- 鉴权：无 token 401。
- 子 agent 列表返回全部记录，不按会话过滤。

### 10.2 前端测试

- 三栏渲染、右栏标签切换。
- 会话按日期分组、搜索、删除确认。
- 模型胶囊：可用/不可用、生成中禁用。
- 工具卡片折叠/展开。
- SSE 事件归并：token 追加、工具状态更新、done 落盘。
- 断线提示。

### 10.3 手动验收

1. `pnpm dev` 启动，窗口出现，左栏有历史会话。
2. 新建会话，发“你好”，文字逐字出现。
3. 让 Agent 读一个文件，聊天出现工具卡片，右栏工具标签有记录。
4. 写一个文件，聊天只显示路径，不显示内容。
5. 切到 DeepSeek，发一条消息；重启应用后该会话仍是 DeepSeek。
6. 新建会话，默认模型正确；打开旧会话不报错。
7. 让 Agent 创建子 agent，右栏任务出现并更新状态。
8. 生成中点停止，文本保留并标记已停止。
9. 关闭窗口，Python 进程退出，无残留端口。

## 11. 里程碑（供实现计划参考）

1. **M1 壳与 sidecar**：Electron 窗口、Python 启动握手、health、三栏静态布局。
2. **M2 会话**：列表、创建、切换、删除、搜索、历史加载。
3. **M3 聊天**：SSE 流式、消息渲染、工具卡片、停止。
4. **M4 模型**：模型胶囊、按会话持久化、切换禁用逻辑。
5. **M5 任务台**：子 agent 列表、工具日志、上下文标签。
6. **M6 联调与验收**：错误处理、手动验收、测试补齐。

## 12. 风险

1. **Python 启动慢**：首次 import LangGraph/Office 依赖可能 3–5 秒；窗口先显示骨架，sidecar 就绪后再加载数据。
2. **SSE 与同步 LangGraph 混用**：必须把阻塞调用放到工作线程，否则整个 API 会卡住。
3. **会话并发写**：桌面端和 CLI 同时操作同一 JSON 时可能覆盖；第一版约定不同时使用，后续加文件锁。
4. **子 agent 进程残留**：窗口关闭时必须调用 manager shutdown；启动时清理 orphan。
5. **模型切换与子 agent**：切换只影响后续 spawn；已在运行的任务保持原模型。
6. **开发环境依赖**：第一版依赖本机 `rag` 环境和 Node/pnpm；打包阶段再固化。

## 13. 验收标准

- 开发版能一条命令启动，不白屏。
- 三栏布局、深色主题、右栏三标签可用。
- 会话历史、模型记忆、流式聊天、工具卡片、子 agent 任务台全部可用。
- 停止生成、断线提示、sidecar 失败提示都有明确反馈。
- 后端测试与前端关键交互测试通过。
- 不修改现有 CLI 的行为和已有会话文件格式的兼容性。
