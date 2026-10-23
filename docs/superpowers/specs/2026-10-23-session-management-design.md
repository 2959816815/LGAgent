# 会话管理设计

- 日期：2026-10-23
- 状态：待用户 review
- 范围：`graph_agent.py` 终端 CLI 的会话新建、列出、切换、删除，以及当前会话的历史消息查看

## 1. 背景与目标

当前 `graph_agent.py` 只有一个进程内的 `messages` 列表：

- 退出程序后对话历史全部丢失；
- 无法同时保留多个话题；
- 无法回到之前的会话继续聊；
- 没有查看历史消息的入口。

本设计要解决的问题：

1. 会话可以跨重启保存；
2. 可以新建、列出、切换、删除会话；
3. 切换或重启后能恢复完整上下文继续对话；
4. 可以分页查看当前会话的历史消息；
5. 不破坏现有 Agent 图、工具调用和文本/办公文件功能。

## 2. 非目标（第一版不做）

- 会话重命名、搜索、导出、导入；
- 跨会话直接浏览另一个会话的历史（先 `/switch` 再 `/history`）；
- 多进程同时读写同一个会话目录；
- 会话内容加密、云同步；
- 自动摘要、自动裁剪超长上下文；
- SQLite 或 LangGraph checkpointer 存储；
- Web/图形界面。

## 3. 方案选择

考虑过三种存储：

- **JSON 文件（采用）**：每个会话一个文件，透明、可备份、无新增依赖，适合单机 CLI。
- **SQLite 单库**：事务和查询更好，但查看、迁移、测试更重。
- **LangGraph SqliteSaver**：消息状态由 checkpointer 管理，但标题、列表、删除仍需自定义，还要改造现有手动 `messages` 流程，第一版偏重。

采用 JSON 文件 + 斜杠命令。

已确认当前环境可用：

- `langchain_core.messages.messages_to_dict`
- `langchain_core.messages.messages_from_dict`

二者可以完整保存 AI 工具调用、`ToolMessage` 和工具结果。

## 4. 数据模型

### 4.1 目录

```text
D:\Vibecoding\Langgraph\.sessions\
├── active.json
├── 20261023-191500-a1b2.json
└── 20261023-192000-c3d4.json
```

- 目录位于 `graph_agent.py` 同目录下的 `.sessions/`。
- `.sessions/` 加入 `.gitignore`，聊天内容不提交。
- 测试使用独立临时目录，不写真实 `.sessions/`。

### 4.2 会话文件

```json
{
  "schema_version": 1,
  "id": "20261023-191500-a1b2",
  "title": "工作记录",
  "created_at": "2026-10-23T19:15:00+08:00",
  "updated_at": "2026-10-23T19:20:00+08:00",
  "messages": []
}
```

字段说明：

- `schema_version`：固定为 `1`，为以后迁移预留；不支持的值按损坏文件处理。
- `id`：会话 ID，同时是文件名（不含 `.json`）。
- `title`：显示标题，允许中文和空格，不参与文件路径。
- `created_at` / `updated_at`：带时区的 ISO 8601 字符串。
- `messages`：`messages_to_dict()` 的结果，不包含 `SystemMessage`。
- 文件内 `id` 必须与请求/文件名一致，否则视为损坏。

### 4.3 active.json

```json
{"active_id": "20261023-191500-a1b2"}
```

- 只记录当前会话 ID。
- 读取时校验 ID 格式；无效则视为没有 active。
- 写入使用原子替换。

### 4.4 会话 ID

格式：

```text
YYYYMMDD-HHMMSS-<4位十六进制随机数>
```

示例：`20261023-191500-a1b2`。

- 用本地时间生成，随机后缀避免同一秒冲突。
- 只允许 `^[0-9]{8}-[0-9]{6}-[0-9a-f]{4}$`。
- 文件名只使用该校验后的 ID，标题不进入路径。
- 生成后若文件已存在，重新生成。

### 4.5 标题

- `/new 工作记录` 显式指定标题。
- 未指定时使用“新会话”。
- 当前标题仍为“新会话”时，第一条用户消息成功后自动变为该消息的前 30 个字符。
- 标题去掉首尾空白、换行和多余控制字符，最长 60 字符。
- 标题只用于显示，不用于文件名。

### 4.6 消息序列化

- 保存：使用 `messages_to_dict()`，过滤掉 `SystemMessage`。
- 加载：使用 `messages_from_dict()`，不保存旧的系统提示词。
- 启动/切换时补上当前 `SYSTEM_PROMPT`，这样以后修改提示词后旧会话也会使用新提示词。
- 保存的消息包括：用户消息、AI 文本、AI 工具调用、工具结果。
- 工具结果可能较大（例如文件读取上限 2 万字符），第一版原样保存，不做压缩。

## 5. 模块结构

### 5.1 `session_store.py`

负责数据与持久化，不负责终端交互。

```python
@dataclass
class Session:
    id: str
    title: str
    created_at: str
    updated_at: str
    messages: list[BaseMessage]

@dataclass
class SessionMeta:
    id: str
    title: str
    created_at: str
    updated_at: str
    message_count: int
```

`SessionStore` 接口：

```python
class SessionStore:
    def __init__(self, root: Path, clock: Callable[[], datetime] | None = None): ...
    def create(self, title: str = "新会话") -> Session: ...
    def load(self, session_id: str) -> Session: ...
    def save(self, session: Session) -> None: ...
    def list_sessions(self) -> list[SessionMeta]: ...
    def delete(self, session_id: str) -> None: ...
    def get_active_id(self) -> str | None: ...
    def set_active_id(self, session_id: str | None) -> None: ...
    def load_active_or_create(self) -> Session: ...
```

职责：

- 创建目录、校验 ID、读写 JSON；
- 原子写入（临时文件 + `os.replace`）；
- `save()` 更新 `updated_at`，并保证 `created_at` 不被改写；
- 会话列表按 `updated_at` 倒序，时间相同按 ID 倒序；
- 删除文件；如果删除的是 active，清空 active 指针；
- 损坏文件（JSON 错误、字段缺失、schema 不支持、ID 不一致）：列表时跳过；加载时抛出 `SessionStoreError`；
- active 缺失/损坏时，回退到最近更新的有效会话；没有则新建；
- `create()` 对标题做去空白、去控制字符、最长 60 字符的处理。

### 5.2 `session_cli.py`

负责命令解析、显示和确认交互，不直接读写文件。

主要接口：

```python
FILE_TOOL_ACTIONS = {
    "write_file": "写入",
    "edit_file": "修改",
    "write_docx": "写入",
    "edit_docx": "修改",
    "write_xlsx": "写入",
    "edit_xlsx": "修改",
    "write_pptx": "写入",
    "edit_pptx": "修改",
}

def parse_command(text: str) -> tuple[str, str] | None: ...
def resolve_session_target(target: str, metas: list[SessionMeta]) -> str: ...
def format_session_list(metas: list[SessionMeta], active_id: str | None) -> str: ...
def format_history(session: Session, page: int = 1, page_size: int = 20) -> str: ...
def confirm(prompt: str, input_fn=input) -> bool: ...
```

`parse_command()` 的约定：

- 返回 `None`：普通聊天文本；
- 返回 `("unknown", 原文)`：以 `/` 开头但命令未知；
- 返回 `(命令名, 参数)`：已知命令，命令名小写，参数保留原文。

`resolve_session_target()`：

- 全数字按 `metas` 的 1 起始编号解析；
- 否则按完整 ID 或唯一前缀解析；
- 不存在或前缀不唯一时抛 `SessionCommandError`。

- `graph_agent.py` 复用 `FILE_TOOL_ACTIONS`，避免终端输出和历史渲染各维护一份。
- `session_cli.py` 不导入 `graph_agent.py`，避免循环依赖。

### 5.3 `graph_agent.py`

只改 `main()` 及少量显示函数：

- 启动时创建 `SessionStore`，恢复 active 会话；
- 每轮成功后保存；
- 命令分发；
- 切换/新建/删除时维护 active；
- 终端工具调用输出继续走现有 `_print_tool_calls()`；
- Agent 图 `build_graph()` 不变。

### 5.4 其他文件

- `.gitignore`：加入 `.sessions/`。
- `test_session_store.py`、`test_session_cli.py`：新增测试。
- 不修改 `office_tools.py`。

## 6. 终端交互

### 6.1 启动

```text
[LLM] provider=ollama | model=qwen3.5:2b | base_url=http://127.0.0.1:11434/v1
[会话] 工作记录 (20261023-191500-a1b2) | 共 12 条消息
输入 /help 查看会话命令；输入 exit / quit / q / 退出 结束。
```

- 有 active 会话：加载并显示。
- 没有 active，但有会话文件：选择最近更新的一个并设为 active。
- 一个会话都没有：创建“新会话”。
- active 文件损坏：提示后回退到最近会话。
- active 指向的文件损坏：提示后回退到最近有效会话。

### 6.2 命令

```text
/new [标题]          新建并切换；标题可省略
/list                列出会话；/sessions 同义
/switch <编号|ID>    切换；编号来自 /list
/delete <编号|ID>    删除；先 y/N 确认
/clear               清空当前会话历史；先 y/N 确认
/history [页码]      查看当前会话历史；默认第 1 页
/help                显示命令
```

- 命令名大小写不敏感。
- 未知的 `/xxx` 不发给模型，提示“未知命令，输入 /help”。
- `/new` 的标题允许空格，长度上限 60。
- `/switch`、`/delete` 的目标可以是 `/list` 中的 1 起始编号，也可以是完整 ID 或唯一 ID 前缀。
- 编号按当前 `list_sessions()` 的倒序结果解释；先 `/list` 再操作。
- 目标不唯一或不存在时给出明确错误，不做猜测。

### 6.3 普通输入与自动保存

- 普通输入照常调用 Agent。
- 模型调用成功后：
  1. 替换当前会话消息为 `result["messages"]` 中非系统消息；
  2. 必要时更新自动标题；
  3. 保存会话。
- 模型调用失败或 `Ctrl+C`：回滚本轮用户消息，不保存。
- 保存失败：打印错误，但保留内存中的消息；不退出程序。
- 正常退出时尽力再保存一次。

### 6.4 新建/切换

- `/new`：先保存当前会话；保存失败则取消新建。
- 新建成功后立即写文件、设置 active、打印新会话。
- `/switch`：先用操作前的会话列表解析目标，再保存当前会话；保存失败则取消切换。
- 先解析再保存，避免保存当前会话更新 `updated_at` 后把 `/list` 的编号顺序改掉。
- 切换成功后设置 active、打印标题和消息数，不自动打印历史。

### 6.5 删除

- `/delete` 必须带目标。
- 删除前显示标题和 ID，要求 `y/N` 确认；默认取消。
- 删除当前会话：
  - 删除文件；
  - 若还有会话，切到最近更新的一个；
  - 若没有，创建“新会话”。
- 删除非当前会话：当前会话不变。
- 删除失败不改变 active。

### 6.6 清空

- `/clear` 先确认。
- 确认后清空当前会话消息并保存，保留会话 ID 和标题。
- 这与删除会话不同：`/clear` 保留空会话，`/delete` 删除整个会话。

## 7. 历史消息

### 7.1 命令语义

```text
/history        第 1 页，最新 20 条
/history 2      第 2 页，更早的 20 条
```

- 只查看当前会话。
- 第 1 页是最新一页，页码越大越早。
- 每页 20 条消息，按时间正序显示。
- 页码必须是正整数；超出范围提示总页数。
- 空会话提示“当前会话还没有消息”。

### 7.2 显示格式

```text
[历史] 工作记录 (20261023-191500-a1b2)
第 1/2 页（最新） | 共 24 条消息

[用户] 帮我看看这个文件
[工具] read_file(path='D:\...\notes.md')
[工具结果] [文件] D:\...\notes.md (1234 字节) | ...
[助手] 这个文件主要讲...
```

### 7.3 截断与脱敏

- 用户、助手文本超过 800 字符截断，并加“（已截断）”。
- 工具结果超过 200 字符截断，换行替换为 ` | `。
- `write_file`、`edit_file`、`write_docx`、`edit_docx`、`write_xlsx`、`edit_xlsx`、`write_pptx`、`edit_pptx` 的工具调用只显示路径，不显示写入内容或替换文本。
- 其他工具调用参数最多显示 120 字符。
- 系统提示词不显示。
- AI 消息没有文本但有工具调用时，只显示工具行。

## 8. 错误处理

- 所有会话操作错误以 `[错误] ...` 打印，不抛到主循环外层。
- 会话 JSON 损坏：`list_sessions()` 跳过；`load()` 抛 `SessionStoreError`。
- active 指针损坏：警告并使用最近会话。
- 没有任何有效会话：创建新会话。
- 保存/删除失败：保留内存状态，打印错误。
- 路径穿越：ID 只允许固定格式；构造路径后再校验父目录。
- 临时文件写入失败：清理临时文件，保留原文件。
- 单进程使用，不加文件锁；不保证多进程并发安全。

## 9. 安全与隐私

- `.sessions/` 是本地明文 JSON，不做加密。
- `.gitignore` 防止误提交聊天内容。
- 标题、消息只用于终端显示，不拼接到 shell 命令。
- 会话 ID 不来自用户任意输入；用户输入的目标只用于查找，不直接作为路径。

## 10. 测试

### 10.1 `test_session_store.py`

- 创建会话：目录、文件、ID 格式、默认标题。
- 保存/加载：普通消息、AI 工具调用、`ToolMessage` 往返一致。
- `SystemMessage` 不写入文件。
- 列表按 `updated_at` 倒序，消息数正确。
- active 设置/读取/清除。
- 删除会话；删除 active 后 active 为空。
- 删除不存在的会话报错。
- 损坏 JSON 被列表跳过，加载报错。
- 非法 ID/路径穿越被拒绝。
- 原子写入失败时原文件不被破坏。
- `load_active_or_create()` 的回退逻辑。

### 10.2 `test_session_cli.py`

- `parse_command()`：普通文本返回 `None`、已知命令、未知命令返回 `("unknown", ...)`、缺参数、大小写。
- `format_session_list()`：当前标记、编号、标题、消息数。
- `resolve_session_target()`：编号、完整 ID、唯一前缀；歧义和不存在的错误。
- `/delete` 确认：`y` 删除，直接回车取消，EOF 取消。
- 删除当前会话后的回退。
- `format_history()`：空历史、第 1 页/更早页、超出页、截断、工具内容脱敏。

### 10.3 集成与手工验证

- `main()` 用 mock graph 或临时 store 验证：新建、聊天保存、切换、删除、恢复。
- 现有 70 个测试继续通过。
- 手工跑 `python graph_agent.py`：
  1. 新建会话并聊一句；
  2. `/list` 看到会话；
  3. `/new` 第二个会话并聊一句；
  4. `/switch` 回第一个，历史恢复；
  5. 重启，自动恢复 active；
  6. `/history` 分页查看；
  7. `/delete` 删除并确认回退。

## 11. 验收标准

- 重启后自动恢复上次 active 会话及完整历史。
- `/new`、`/list`、`/switch`、`/delete`、`/history` 均可用。
- 切换后模型能基于该会话的历史继续回答。
- 删除当前会话后仍有可用会话，不会卡死。
- 会话文件损坏时程序能提示并继续使用其他会话。
- 现有文本、CSV/JSON、Word/Excel/PPT/PDF 工具不受影响。
- 70 个现有测试 + 新增测试全部通过。

## 12. 后续可扩展

- `/rename` 重命名；
- `/history <编号|ID>` 跨会话查看；
- `/export` 导出 Markdown；
- 会话搜索；
- 自动摘要或历史裁剪；
- 多进程文件锁。
