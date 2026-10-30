# 给 LangGraph Agent 增加文件读取能力

- 日期：2026-10-20
- 状态：设计已确认（用户以“执行”授权）
- 工作区：`D:\Vibecoding\Langgraph`

## 目标

在现有 `graph_agent.py` 的 LangGraph 流程中新增一个 `read_file` 工具，让模型可以在对话中按需读取文本文件或列出目录内容。只读，不写、不删、不执行。

## 已确认的决策

1. **读取范围**：整台电脑任意路径（绝对路径、相对路径、`~`、环境变量均可）。
2. **文件类型**：纯文本类（txt / md / py / json / csv / yaml / 代码等），不处理 PDF、Office、图片 OCR。
3. **使用方式**：对话中由模型自动调用工具，不增加启动参数。
4. **工具形态**：单个 `read_file`；传文件返回内容，传目录返回条目列表。
5. **不新增第三方依赖**。

## 终端交互模式（2026-10-21）

`python graph_agent.py` 启动后进入循环：

- 终端显示 `你:`，输入问题后由模型根据内容决定是否调用工具。
- 每轮只打印本轮的工具调用和最终回复，不重复打印历史。
- `exit` / `quit` / `q` / `退出` / `再见` 结束；`/clear` / `clear` / `清空` 清空对话历史。
- 空输入忽略；`Ctrl+C` / EOF 安全退出；模型调用异常会移除本轮输入并继续等待。

## 工具接口

```python
read_file(path: str, max_chars: int = 20000) -> str
```

### 文件

- 最多读取 1,000,000 原始字节。
- 前 8KB 含 NUL 字节视为二进制，返回错误。
- 解码顺序：`utf-8-sig` → `gb18030` → `utf-8`（`errors="replace"`）。
- 返回字符数默认 20,000，`max_chars` 限制在 100–50,000（模型可请求较小片段，避免一次塞入过多内容）。
- 超长时截断，并在头部注明原始字节数和截断原因。

### 目录

- 最多列出 200 项，目录在前、文件在后，按名称排序。
- 目录项格式：`[DIR]  name/`
- 文件项格式：`[FILE] name (size 字节)`
- 超过 200 项时在头部注明总数。

### 错误处理

所有可预期错误都返回 `[错误] ...` 字符串，不向 LangGraph 抛异常：

- 路径为空
- 路径无效
- 文件/目录不存在
- 无权限
- 二进制文件
- 其他 `OSError`

## 代码改动

文件：`graph_agent.py`

1. 新增常量：读取上限、目录条目上限、默认/最小/最大字符数。
2. 新增私有辅助函数：
   - `_coerce_max_chars(value) -> int`
   - `_decode(raw: bytes) -> str`
   - `_list_directory(path: Path) -> str`
   - `_read_text_file(path: Path, max_chars: int) -> str`
3. 新增 `@tool` 函数 `read_file`。
4. `TOOLS = [get_current_time, read_file]`。
5. `SYSTEM_PROMPT` 增加读取文件/目录的指引，要求优先使用绝对路径、不要编造内容。

LangGraph 图结构不变：`START -> agent -> (tools -> agent)* -> END`。

## 安全边界

用户明确选择任意文件访问。工具会读取调用方给定的路径，包括 `.env` 等敏感文件；这是已知取舍。后续如需收紧，可增加 `READ_FILE_ALLOWED_ROOTS` 环境变量做可选白名单，本次不做。

## 测试

新增 `test_read_file.py`，使用标准库 `unittest`，覆盖：

1. 正常 UTF-8 文本（含中文）
2. 目录列表
3. 文件不存在
4. 二进制拒绝
5. 超长截断
6. GB18030 编码
7. 含空格的路径

运行方式：

```powershell
cd D:\Vibecoding\Langgraph
D:\Anaconda3\envs\rag\python.exe -m unittest -v test_read_file.py
```

## 验证结果（2026-10-20）

- 单元测试：`D:\Anaconda3\envs\rag\python.exe -m unittest -v test_read_file.py`，7 项全部通过。
- 端到端：`qwen3.5:2b` 收到“读取 sample_note.txt”后自动调用 `read_file`，正确返回“项目代号：蓝鲸 / 上线时间：2026-11-01”。
- 环境说明：默认 `python` 指向 Anaconda base，缺少 `langchain_core`；本机实际使用 `rag` 环境（Python 3.12，langchain_core 1.6.1）。
- DSH 沙箱：`workspace-write` 下 Python 写临时文件会被拒绝，测试使用完全权限执行；这属于测试环境限制，不影响 `read_file` 的只读功能。

## 不在本次范围

- 写文件、删除文件、执行命令
- PDF / Word / Excel / 图片 OCR
- 递归目录树、文件搜索、glob
- 目录白名单
