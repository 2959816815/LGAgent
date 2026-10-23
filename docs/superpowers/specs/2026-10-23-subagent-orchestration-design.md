# 子 Agent 编排功能设计

日期：2026-10-23

## 1. 目标

在现有终端 LangGraph agent 上增加一个主 agent 管理多个子 agent 的能力：

- 主 agent 可以通过工具按需创建一个或多个子 agent。
- 每个子 agent 在独立 Python 子进程中执行一个明确任务。
- 主 agent 可以继续和用户对话，也可以查询/等待子 agent 结果。
- 终端提供 `/agents` 快照面板，并在子 agent 完成或失败时打印通知。
- 子 agent 的任务、状态、结果和对话记录持久化到磁盘。

## 2. 已确认的产品选择

- 控制面板：终端表格，不做 Web 面板。
- 创建方式：主 agent 通过工具自动创建，不要求用户手动创建。
- 运行方式：每个子 agent 一个独立子进程，不是同进程线程或同步顺序执行。
- 生命周期：一个子 agent 只执行一个任务，完成后退出。
- 持久化：任务、状态、结果和完整对话都写到磁盘。
- 工具权限：子 agent 沿用主 agent 的文件/Office 工具，但不能再创建子 agent。
- 结果获取：异步创建，主 agent 用 `list_agents` 查询或用 `wait_agent` 等待。
- 面板刷新：`/agents` 快照 + 完成/失败通知，不做全屏实时刷新。
- 并发上限：同时最多 3 个 `running`；超过时直接拒绝，不排队。
- 模型：子 agent 沿用主 agent 当前模型，不支持每个子 agent 单独选模型。

## 3. 非目标

本次不实现：

- 取消/暂停/恢复子 agent。
- 任务排队或优先级调度。
- 子 agent 再创建子 agent。
- Web 控制台或全屏 TUI。
- 自动重试、自动拆分子任务、自动汇总多 agent 结果。
- 每个子 agent 独立模型或独立工具白名单。
- 跨机器/远程执行。

## 4. 总体架构

```text
┌─────────────────────────────────────────────┐
│ 主进程：graph_agent.py + session CLI        │
│                                             │
│  LangGraph 主 agent                         │
│    ├─ 文件/Office 工具                      │
│    ├─ spawn_agent(task)                     │
│    ├─ list_agents()                         │
│    └─ wait_agent(agent_id, timeout)         │
│                                             │
│  SubagentManager                            │
│    ├─ 启动 subagent_worker.py 子进程        │
│    ├─ 轮询 .agents/*.json                   │
│    └─ 输出完成/失败通知                     │
└───────────────┬─────────────────────────────┘
                │ subprocess.Popen
                ▼
┌─────────────────────────────────────────────┐
│ 子进程：subagent_worker.py                  │
│  - 读取自己的 AgentRecord                   │
│  - build_graph(include_subagent_tools=False)│
│  - 执行一个任务                             │
│  - 写回 succeeded / failed 和结果           │
└─────────────────────────────────────────────┘
```

主进程只负责编排和展示，不在主进程内执行子 agent 的图。子进程通过磁盘 JSON 记录状态，不依赖管道 IPC，避免子进程崩溃时主进程阻塞。

## 5. 模块边界

| 文件 | 职责 |
|---|---|
| `subagent_store.py` | AgentRecord 数据模型、原子读写、列表、状态校验 |
| `subagent_manager.py` | 创建子进程、并发限制、轮询、等待、通知队列 |
| `subagent_worker.py` | 子进程入口：读记录、跑图、写结果 |
| `subagent_tools.py` | 给主 agent 的三个 LangChain 工具 |
| `subagent_cli.py` | `/agents` 表格格式化和命令解析辅助 |
| `graph_agent.py` | 创建 Manager、注册工具、主循环接入 `/agents` |
| `session_cli.py` | 只增加 `/agents` 命令识别，不承载子 agent 逻辑 |
| `test_subagent_*.py` | 对应模块的单元/集成测试 |

## 6. 数据模型

### 6.1 AgentRecord

```json
{
  "schema_version": 1,
  "id": "agent-20261023-201500-a1b2",
  "task": "整理本周周报并生成 docx",
  "status": "running",
  "created_at": "2026-10-23T20:15:00+08:00",
  "started_at": "2026-10-23T20:15:01+08:00",
  "finished_at": null,
  "pid": 12345,
  "result": null,
  "error": null,
  "messages": [],
  "log_path": ".agents/agent-20261023-201500-a1b2.log"
}
```

字段约束：

- `status` 只允许 `running`、`succeeded`、`failed`。
- `id` 格式：`agent-YYYYMMDD-HHMMSS-<4 位十六进制>`，创建时保证唯一。
- `task` 非空，最长 4000 字符；超过 4000 字符直接拒绝，避免上下文失控。
- `messages` 使用 LangChain 的 `messages_to_dict` / `messages_from_dict` 序列化；保存完整对话，不保存 SystemMessage。
- `result` 是最终 AI 文本；失败时为 `null`。
- `log_path` 是相对项目根目录的路径。
- 时间使用本地时区 ISO 8601 字符串。

### 6.2 存储

- 根目录：项目根下的 `.agents/`。
- 每个记录：`.agents/<id>.json`。
- 每个日志：`.agents/<id>.log`。
- `.agents/` 加入 `.gitignore`。
- 写 JSON 使用“临时文件 + `os.replace`”原子替换；读取时校验 `schema_version`、id、status、时间字段。
- 损坏文件不删除；`list_records()` 跳过并返回 warning，CLI 打印 `[提示]`。

## 7. 生命周期与状态流转

```text
创建记录(running)
   │
   ├─ Popen 启动失败 ──────────────► failed
   │
   ▼
子进程启动，写 pid/started_at
   │
   ├─ 正常完成 ───────────────────► succeeded(result)
   ├─ 模型/工具异常 ──────────────► failed(error)
   └─ 进程异常退出 ──────────────► failed(轮询补写)
```

### 7.1 创建

`SubagentManager.spawn(task)`：

1. 校验 task 非空且不超过 4000 字符。
2. 统计当前 `running` 数量；大于等于 3 时返回错误，不创建记录。
3. 生成 id，写初始记录（`status=running`，`pid=null`）。
4. 打开 `.agents/<id>.log`，用 `subprocess.Popen` 启动：
   ```text
   <当前 Python 解释器> subagent_worker.py --agent-id <id> --root <项目根>
   ```
5. `cwd` 为项目根；Windows 下加 `CREATE_NO_WINDOW`，stdout/stderr 都写入日志。
6. 写回 `pid`；写回前重新读取记录，只补充仍为空的 `pid`，不覆盖子进程已写的 `started_at`。Popen 抛异常时把记录改为 `failed`。
7. 返回记录和 `[已创建] <id>`。

### 7.2 子进程

`subagent_worker.py`：

1. 解析 `--agent-id`、`--root`。
2. 读取记录；不存在或状态不是 `running` 时退出。
3. 更新 `pid`、`started_at`。
4. 构建不带子 agent 工具的主图（`include_subagent_tools=False`）。
5. 使用独立的 `SUBAGENT_SYSTEM_PROMPT`（不包含“可以创建子 agent”的说明），用任务文本作为唯一 HumanMessage，调用一次图；提示要求直接完成并返回结果。
6. 成功：写 `succeeded`、`finished_at`、`result`、`messages`；`messages` 不包含 SystemMessage，`result` 取最后一条 AI 消息的文本。
7. 异常：写 `failed`、`finished_at`、`error`，保留已有 messages；退出码 1。
8. 无论成功失败都关闭日志句柄。

### 7.3 轮询与收尾

`SubagentManager.refresh()`：

- 读取所有记录。
- 对内存中仍有 Popen 句柄的记录，调用 `poll()`；进程已退出但记录仍为 `running` 时，补写 `failed`，错误为 `进程异常退出（code=<n>）`，并附日志最后 5 行。
- 对记录已是终态但尚未发送通知的，放入通知队列；用 `_notified` 集合记录已通知 id，保证每个 agent 只通知一次。
- 主进程启动时，若发现上次遗留的 `running` 记录，标记为 `failed`，错误为 `主程序重启，无法确认子进程状态`；不尝试按 pid 重新挂接。

### 7.4 主进程退出

`SubagentManager.shutdown()`：

- 停止监控线程，先 `refresh()` 一次，避免把刚完成的 agent 误判为运行中。
- 对仍 `running` 的子进程发送 `terminate()`，等待 3 秒，仍未退出则 `kill()`。
- 将这些记录标记为 `failed`，错误为 `主程序退出，任务已终止`。
- 不删除记录和日志，重启后仍能在 `/agents` 看到历史。

## 8. 主 agent 工具

工具通过 `subagent_tools.build_subagent_tools(manager)` 创建，绑定同一个 Manager 实例。

### 8.1 `spawn_agent`

```python
def spawn_agent(task: str) -> str
```

- 成功：`[已创建] <id> | <task 前 80 字>`
- 失败：`[错误] <原因>`，包括空任务、超长、并发已满、启动失败。
- 工具 docstring 明确：只在任务适合并行/委派时调用；不要把同一个任务重复创建多次。

### 8.2 `list_agents`

```python
def list_agents() -> str
```

- 空列表：`[子agent] 当前没有子 agent。`
- 否则输出表格：

```text
ID                         状态       任务                 耗时    结果
agent-20261023-201500-a1b2  running    整理本周周报          12s     -
agent-20261023-201455-c3d4  succeeded  生成销售数据表        45s     已生成 sales.xlsx
```

- 任务列最多 18 个字符，结果列最多 30 个字符，超出用 `…`。
- 按 `created_at` 倒序。

### 8.3 `wait_agent`

```python
def wait_agent(agent_id: str, timeout: int = 180) -> str
```

- 每 0.5 秒调用 `refresh()`，直到终态或超时。
- 成功：`[完成] <id> | <result>`
- 失败：`[失败] <id> | <error>`
- 超时：`[超时] <id> 仍在运行 | 任务: <task 前 80 字>`
- id 不存在：`[错误] 找不到子 agent: <id>`
- `timeout` 夹在 1–600 秒。

## 9. 主循环与面板

### 9.1 `/agents`

- `session_cli.parse_command` 增加 `agents` 命令。
- `run_chat_loop` 收到 `/agents` 时调用 `manager.format_table()`，打印表格。
- 如果 Manager 不在（例如测试或子进程），输出 `[子agent] 当前没有子 agent。`。

### 9.2 完成通知

- `SubagentManager` 维护通知队列；监控线程每 0.5 秒 `refresh()`。
- 主循环启动时调用 `manager.start_monitor(output)`。
- 通知格式：
  - `[子agent] <id> 完成: <result 前 100 字>`
  - `[子agent] <id> 失败: <error 前 100 字>`
- 通知线程直接调用 `output`；可能出现在用户输入行上方，但不改变输入内容。
- 主循环用 `try/finally` 包住，退出时调用 `manager.shutdown()`，避免异常退出留下子进程。
- 测试中不启动线程，直接调用 `refresh()` 并检查 `pop_notifications()`。

### 9.3 主 agent 提示词

在 `SYSTEM_PROMPT` 增加一段：

- 有多个可并行的独立任务时，可以调用 `spawn_agent`。
- 创建后先继续和用户沟通；需要结果时调用 `wait_agent`，只想看进度时调用 `list_agents`。
- 不要为简单的一步任务创建子 agent。
- 子 agent 不能继续创建子 agent。

## 10. 错误处理

| 场景 | 行为 |
|---|---|
| 空任务/超长任务 | 工具返回 `[错误]`，不创建记录 |
| 已有 3 个 running | 工具返回 `[错误] 当前已有 3 个子 agent 在运行` |
| Popen 启动失败 | 记录 `failed`，返回错误，日志保留 |
| 子进程异常退出 | 轮询补写 `failed`，附日志末尾 |
| 子 agent 模型/工具异常 | 子进程写 `failed`，保留已有 messages |
| `wait_agent` 超时 | 返回 `[超时]`，不杀进程 |
| 记录 JSON 损坏 | 跳过，warning，不影响其他记录 |
| `/agents` 无 Manager | 返回空列表提示 |
| 主进程退出 | 终止 running 子进程并标记 failed |

## 11. 测试计划

### 11.1 `test_subagent_store.py`

- 创建/读取/更新记录。
- 原子写：写入后 JSON 可解析。
- 状态非法、id 不一致、schema 不支持时报错。
- 损坏 JSON 被跳过并产生 warning。
- 列表按 created_at 倒序。

### 11.2 `test_subagent_manager.py`

使用一个假 worker 脚本（`subagent_fake_worker.py`，不以 `test_` 开头，避免被 unittest discover 当成测试模块）：

- 成功任务写 `succeeded`。
- 失败任务写 `failed`。
- 进程直接退出不写记录时，`refresh()` 补写 `failed`。
- 并发上限 3：第 4 个 spawn 返回错误。
- `wait_agent` 成功、失败、超时、id 不存在。
- 通知只发一次，`pop_notifications()` 后清空。
- 主进程启动时遗留 running 被标记 failed。

### 11.3 `test_subagent_tools.py`

- `spawn_agent` 成功/空任务/并发满。
- `list_agents` 空/有数据/截断。
- `wait_agent` 完成/失败/超时/未知 id。

### 11.4 `test_subagent_cli.py`

- `/agents` 命令解析。
- 表格列对齐、空列表、长文本截断。

### 11.5 回归

- 现有 95 个测试全部通过。
- 不真实调用本地模型；真实模型只做一次手动 smoke：
  1. 主 agent 创建 2 个子 agent。
  2. `/agents` 看到 running。
  3. 等待后看到 succeeded 和结果。
  4. 检查 `.agents/*.json` 与日志。

## 12. 验收标准

- 主 agent 能通过 `spawn_agent` 创建子 agent，且不阻塞当前对话。
- 子 agent 在独立进程中运行，主进程崩溃/重启不会误判为成功。
- 同时最多 3 个 running，第 4 个被明确拒绝。
- `/agents` 能显示 id、状态、任务、耗时、结果摘要。
- 子 agent 完成后终端出现一次通知。
- 记录和日志落盘，重启后仍可查看历史。
- 子 agent 不能创建子 agent。
- 现有 95 个测试保持通过，新增测试覆盖上述行为。

## 13. 风险与取舍

- **本地模型并发**：qwen3.5:2b 可能串行处理请求，3 个子 agent 不一定真正提速；面板仍能体现任务并行管理。
- **磁盘轮询**：0.5 秒轮询有轻微 I/O 开销；记录很小，可接受。
- **通知打断输入**：通知可能出现在输入行上方；选择快照+通知是为了避免全屏 TUI 与 `input()` 冲突。
- **进程清理**：主进程退出会终止子进程；这是为了避免孤儿进程，代价是长任务不能跨主程序生命周期继续。
- **无取消**：本次不做取消；用户只能等待完成或退出主程序。
