# 给 LangGraph Agent 增加 Word / Excel / PPT / PDF 能力

- 日期：2026-10-23
- 状态：设计已确认（用户确认四节设计）
- 工作区：`D:\Vibecoding\Langgraph`

## 目标

在现有文本文件工具之外，让 Agent 能读取、新建和局部修改日常办公文件：

- Word：`.docx`
- Excel：`.xlsx`
- PowerPoint：`.pptx`
- PDF：`.pdf`（只读）

用户不需要安装 Microsoft Office 或 LibreOffice。使用纯 Python 库解析和生成 Open XML 文件。

## 范围

### 做

- 单文件读取、新建/整体写入、局部修改。
- Word 正文段落和表格。
- Excel 单元格值、公式、单个单元格修改。
- PPT 幻灯片文字、文本框和表格文字。
- PDF 按页提取文字。
- 修改前自动备份；终端只显示文件路径和状态，不显示写入内容。
- 错误返回中文提示，不把 traceback 抛给模型。

### 不做

- `.doc`、`.xls`、`.ppt` 等旧二进制格式。
- `.docm`、`.xlsm`、`.pptm` 等带宏格式。
- 批量处理、目录递归、格式转换。
- PDF 修改、PDF 生成、OCR、扫描件文字识别。
- 图片、图表、批注、页眉页脚的读取或编辑。
- 插入/删除 Excel 行列、修改 PPT 版式、修改 Word 复杂域。
- 保留 100% 的复杂排版；原生库保存时可能丢失极少数高级元素。

## 已确认的决策

1. 第一版覆盖 Word/Excel/PPT 读写改，PDF 只读。
2. 修改已有文件时尽量保留原格式，只改指定内容；做不到安全保留时明确提示，不悄悄重建整个文件。
3. 只做单文件；不做批量、不做格式转换。
4. 使用原生库，不依赖 Office。
5. 终端日志不打印写入/修改内容，只打印路径和完成状态。

## 架构

新增独立模块：

```text
D:\Vibecoding\Langgraph\
├── graph_agent.py              # 现有 LangGraph 流程；只导入并注册新工具
├── office_tools.py             # 10 个办公文件工具 + 共用辅助函数
├── test_office_tools.py        # 办公工具单元测试
├── requirements-office.txt     # 新增依赖及版本
└── docs/superpowers/specs/
    └── 2026-10-23-office-file-agent-design.md
```

`office_tools.py` 自带路径解析、备份、原子写入等辅助函数，不修改现有文本工具的实现，避免回归。

`graph_agent.py` 的改动：

```python
from office_tools import OFFICE_TOOLS

TOOLS = [get_current_time, read_file, write_file, edit_file, *OFFICE_TOOLS]
```

同时更新 `SYSTEM_PROMPT`，告诉模型按扩展名选择工具；并扩展 `_FILE_TOOL_ACTIONS`，让所有写入/修改工具都只显示路径和状态。

## 工具清单

共 10 个新工具：

| 格式 | 读取 | 新建/整体写入 | 局部修改 |
|---|---|---|---|
| Word | `read_docx` | `write_docx` | `edit_docx` |
| Excel | `read_xlsx` | `write_xlsx` | `edit_xlsx` |
| PPT | `read_pptx` | `write_pptx` | `edit_pptx` |
| PDF | `read_pdf` | — | — |

### 函数签名

```python
read_docx(path: str, max_chars: int = 20000) -> str
write_docx(path: str, content: str) -> str
edit_docx(path: str, old_text: str, new_text: str, replace_all: bool = False) -> str

read_xlsx(path: str, max_rows: int = 200, max_cols: int = 30, max_chars: int = 20000) -> str
write_xlsx(path: str, content: str, sheet_name: str = "Sheet1") -> str
edit_xlsx(path: str, sheet: str, cell: str, value: str) -> str

read_pptx(path: str, max_chars: int = 20000) -> str
write_pptx(path: str, content: str) -> str
edit_pptx(path: str, slide: int, old_text: str, new_text: str, replace_all: bool = False) -> str

read_pdf(path: str, max_pages: int = 50, max_chars: int = 20000) -> str
```

所有路径参数都支持绝对路径、相对路径、`~` 和环境变量。所有工具都校验扩展名，扩展名不匹配直接返回 `[错误]`。

## 读取结果的表示

读取结果统一是带定位信息的纯文本，方便模型继续编辑。

### Word

```text
[文档] D:\...\report.docx
[段落 1] 项目周报
[段落 2] 本周完成情况：
[表格 1]
  行 1: 姓名 | 任务 | 状态
  行 2: 张三 | 接口开发 | 完成
```

- 只读正文段落和表格；空段落不输出，但段落编号保持不变。
- 不读页眉、页脚、图片、批注、脚注。
- 超过 `max_chars` 时截断并提示。

### Excel

```text
[工作簿] D:\...\data.xlsx
[工作表] Sheet1 (A1:C3)
A1: 姓名 | B1: 年龄 | C1: 城市
A2: 张三 | B2: 30 | C2: 北京
A3: 李四 | B3: =SUM(B2:B2) | C3: 上海
```

- 公式返回公式字符串，不返回缓存计算值。
- 默认最多 200 行 × 30 列；超出时提示已截断。
- 空单元格不输出；合并单元格只显示左上角。
- 不读图表、图片、条件格式。

### PPT

```text
[演示文稿] D:\...\deck.pptx
[幻灯片 1] 标题: 项目汇报
  文本框: 本周进展
  文本框: - 完成接口开发
[幻灯片 2] 标题: 下一步
  文本框: - 联调
```

- 只读带文字的形状和表格文字；不读图片、图表、备注。
- 超过 `max_chars` 时截断并提示。

### PDF

```text
[PDF] D:\...\manual.pdf
[第 1 页]
这里是第一页文字……
[第 2 页]
这里是第二页文字……
```

- 只提取文字层；扫描版 PDF 没有文字时返回 `[提示] 未提取到文字，可能是扫描版 PDF`。
- 加密 PDF 如果无法解密，返回 `[错误] PDF 已加密，无法读取`。
- 最多 50 页；超过时截断并提示。

## 各格式行为

### Word

#### `read_docx`

1. 校验 `.docx`、文件存在、大小不超过 50,000,000 字节。
2. 用 `python-docx` 打开，遍历正文段落和表格。
3. 按上面的格式输出，并限制总字符数。
4. 文件损坏、不是合法 docx、权限不足时返回 `[错误]`。

#### `write_docx`

把 Markdown 子集转成 Word：

- `#` / `##` / `###`：Heading 1/2/3。
- `- ` / `* `：项目符号。
- `1. `：编号列表。
- 空行：分段。
- 管道表格：`| a | b |`，第二行是 `|---|---|` 分隔行。
- 其他行：普通段落。

规则：

- 新建或整体覆盖；已有文件先备份为 `.bak`。
- 父目录必须存在。
- 内容按 UTF-8 编码后不超过 5,000,000 字节。
- 不支持图片、超链接、复杂嵌套列表；遇到不认识的 Markdown 语法按普通文本写入。

#### `edit_docx`

1. 读取正文段落和表格单元格中的段落。
2. 查找 `old_text`：
   - 出现 0 次：`[错误] 未找到 old_text`。
   - 出现多次且 `replace_all=False`：报错，要求更长的唯一片段。
3. 替换：
   - `old_text` 完全位于单个 run 内：直接替换该 run 的文本，保留字体/加粗/颜色等格式。
   - 跨多个 run：把整段文本重建到第一个 run，保留段落样式；局部格式可能丢失，返回结果中加 `[提示] 跨格式片段已按段落样式重建`。
4. 保存前备份原文件；用内存中的 `BytesIO` 生成新 docx，再原子替换目标。
5. 不处理跨段落的 `old_text`；不修改页眉页脚。

### Excel

#### `read_xlsx`

1. 校验 `.xlsx`、大小、文件存在。
2. 用 `openpyxl.load_workbook(..., data_only=False, read_only=True)` 打开。
3. 遍历每个 sheet，输出使用范围和单元格。
4. 公式保留 `=` 开头；空单元格跳过；最多 200 行 × 30 列，总字符数不超过 `max_chars`。
5. 文件损坏、加密、权限不足时返回 `[错误]`。

#### `write_xlsx`

`content` 是 CSV 文本；`sheet_name` 默认 `Sheet1`。

- 用标准库 `csv.reader` 解析，支持带引号的逗号。
- 第一行作为表头，后续行作为数据。
- 以 `=` 开头的单元格写成公式。
- 普通数字（整数/小数，且不是带前导零的编号）写成数值；其他写成字符串。
- 只创建一个 sheet；已有工作簿会被整体覆盖并先备份。
- `sheet_name` 不能为空，最长 31 字符，不能包含 `[ ] : * ? / \`。
- 内容 UTF-8 编码后不超过 5,000,000 字节。

#### `edit_xlsx`

- `sheet` 必须精确匹配现有工作表名；不存在则报错。
- `cell` 必须是 `A1` 形式（列字母 + 行号），否则报错。
- `value` 按与 `write_xlsx` 相同的规则转换：`=` 开头为公式，数字转数值，其余为字符串。
- 只改一个单元格；保留其他 sheet、样式和公式。
- 保存前备份；用 `BytesIO` 保存后原子替换。
- 已知限制：`openpyxl` 保存时可能丢失图表等极少数高级对象；不会主动删除，但无法保证 100% 保留。

### PPT

#### `read_pptx`

1. 校验 `.pptx`、大小、文件存在。
2. 用 `python-pptx` 打开，遍历幻灯片、带 `text_frame` 的形状和表格。
3. 按页输出标题、文本框、表格文字；不读图片/图表/备注。
4. 限制总字符数。

#### `write_pptx`

`content` 用 `---` 单独一行分页：

```markdown
# 项目汇报
本周进展
- 完成接口开发
- 完成联调

---

# 下一步
- 上线
```

- 每个分页块的第一行 `# 标题` 作为幻灯片标题，其余行作为正文。
- `- ` 开头作为项目符号；其他作为普通段落。
- 使用默认模板的“标题和内容”版式；不套用复杂主题。
- 新建或整体覆盖；已有文件先备份。
- 内容 UTF-8 编码后不超过 5,000,000 字节。

#### `edit_pptx`

- `slide` 从 1 开始；越界报错。
- 在该页所有文本框和表格中查找 `old_text`。
- 出现 0 次报错；多次且 `replace_all=False` 报错。
- 单个 run 内替换保留字体/字号/颜色；跨 run 时按段落重建，保留段落样式但可能丢局部格式，返回 `[提示]`。
- 只改文字，不改版式、位置、动画。

### PDF

#### `read_pdf`

1. 校验 `.pdf`、大小、文件存在。
2. 用 `pypdf.PdfReader` 打开。
3. 如果是加密 PDF，先尝试空密码解密；仍失败则返回 `[错误] PDF 已加密，无法读取`。
4. 逐页 `extract_text()`，最多 50 页，总字符数不超过 `max_chars`。
5. 所有页都没有文字时返回 `[提示] 未提取到文字，可能是扫描版 PDF`。
6. 不做 OCR，不修改 PDF。

## 共用规则

### 路径

1. 去掉首尾空白和成对的单/双引号。
2. 展开 `~` 和环境变量。
3. 转为绝对路径并 `Path.resolve()` 解析符号链接。
4. 路径为空、无效、父目录不存在时返回 `[错误]`。

### 大小限制

- 单个办公文件读取/编辑上限：50,000,000 字节。
- 写入内容（Markdown/CSV/PPT 文本）上限：5,000,000 字节。
- 读取输出默认上限：20,000 字符；`read_docx`/`read_pptx`/`read_pdf` 可通过 `max_chars` 调整，最高 50,000。
- `read_xlsx` 默认最多 200 行 × 30 列。

### 备份

- 修改已有文件前，复制为同目录 `.bak`。
- `.bak` 已存在时依次使用 `.bak.1`、`.bak.2`，最多到 `.bak.99`。
- 备份失败则不写入，返回 `[错误] 备份失败`。
- 新建文件不产生备份。

### 原子写入

1. 在内存中生成新文件字节（`BytesIO`）。
2. 在目标同目录创建临时文件，写入、`flush`、`os.fsync`。
3. `os.replace` 原子替换目标。
4. 失败时删除临时文件，原文件不变；尽量保留原权限位。

不使用 `tempfile.TemporaryDirectory()` 的 0700 目录，避免 DSH 下 `mkstemp` 挂起的历史问题。

### 错误处理

- 所有工具捕获 `OSError`、`ValueError`、库异常，转换成 `[错误] ...`。
- 错误信息包含文件路径和原因，不返回 traceback。
- 模型不应在工具报错后假装成功；系统提示会强调这一点。

### 终端日志

`_FILE_TOOL_ACTIONS` 增加：

```python
"write_docx": "写入",
"edit_docx": "修改",
"write_xlsx": "写入",
"edit_xlsx": "修改",
"write_pptx": "写入",
"edit_pptx": "修改",
```

`_print_tool_calls` 对参数做脱敏：文件类工具只打印 `path`，不打印 `content`、`old_text`、`new_text`、`value`。工具结果只打印：

```text
[完成] 已写入: D:\...\report.docx
[完成] 已修改: D:\...\data.xlsx
```

失败时打印路径和简短原因，不打印内容。

## 依赖

在 `D:\Anaconda3\envs\rag` 中安装：

```powershell
D:\Anaconda3\envs\rag\python.exe -m pip install python-docx openpyxl python-pptx
```

`pypdf` 已安装，无需重复安装。

新增 `requirements-office.txt`：

```text
python-docx>=1.1.2
openpyxl>=3.1.5
python-pptx>=1.0.2
pypdf>=5.0.0
```

不安装 Office、LibreOffice、pandoc，也不使用 COM。

## 测试

### 单元测试

新增 `test_office_tools.py`，使用标准库 `unittest`，临时目录放在工作区内（普通目录 + `uuid` 名称，最后 `shutil.rmtree`），避免 DSH 对系统临时目录的限制。

覆盖：

1. Word：
   - 新建 docx，读取段落/表格。
   - 修改单个 run 文本，保留加粗格式。
   - 修改跨 run 文本，返回提示且段落样式保留。
   - 备份生成、未找到文本、重复文本、错误扩展名。
2. Excel：
   - 新建 xlsx，读取值和公式。
   - 修改单元格，保留其他 sheet 和样式。
   - 公式、数字、字符串转换。
   - 非法 sheet、非法单元格、备份、错误扩展名。
3. PPT：
   - 新建 pptx，读取标题/正文。
   - 修改指定页文字，保留字体大小。
   - 页码越界、重复文本、备份。
4. PDF：
   - 用内置最小 PDF 样例读取文字。
   - 空文字 PDF 返回提示。
   - 用 `pypdf` 生成一个带密码的 PDF，确认返回 `[错误] PDF 已加密，无法读取`。
   - 不存在的文件、错误扩展名。
5. 通用：
   - 父目录不存在、超过大小上限、路径带空格/中文。
   - `_print_tool_calls` 不输出写入内容。
6. 回归：现有 `test_write_file.py` 21 个测试继续通过。

### 端到端

用本地 `qwen3.5:2b`：

1. 让 Agent 读取一个 docx 并总结。
2. 让 Agent 把 docx 中某句话改掉，确认终端只显示 `[完成] 已修改: <路径>`。
3. 让 Agent 新建一个 xlsx，写入两行数据，再读取确认。

如果 2B 模型在 14 个工具下选错工具，调整工具描述和 `SYSTEM_PROMPT` 后重测；仍失败则记录为模型能力限制，不通过放宽安全规则来绕过。

## 验收标准

- 10 个工具注册成功，模型能按扩展名选择。
- `.docx/.xlsx/.pptx` 能读、能新建/覆盖、能局部修改；`.pdf` 能读文字。
- 修改前生成 `.bak`；失败不破坏原文件；错误返回中文提示。
- 终端不显示写入/修改内容，只显示路径和状态。
- `test_office_tools.py` 全部通过，现有 21 个文件工具测试全部通过。
- 本地模型至少完成一次 docx 读取和一次 docx 修改的端到端验证。
- 不新增 Office/LibreOffice/COM 依赖。

## 实施顺序

1. 安装依赖，创建 `office_tools.py` 骨架和 `test_office_tools.py` 测试框架。
2. Word 读取、写入、修改 + 测试。
3. Excel 读取、写入、修改 + 测试。
4. PPT 读取、写入、修改 + 测试。
5. PDF 读取 + 测试。
6. 接入 `graph_agent.py`，更新提示词和日志脱敏。
7. 全量回归 + 本地模型端到端。
