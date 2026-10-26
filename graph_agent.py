r"""graph_agent.py — 一个最小可用的 LangGraph Agent。

默认调用本地 Ollama 的 qwen3.5:2b（无需联网、无需 API Key）：
    ollama serve          # 确保 Ollama 已启动
    ollama list           # 应能看到 qwen3.5:2b
    python graph_agent.py # 启动后在终端输入，exit 退出

读取文件示例（启动后在终端输入）：
    你: 读取 D:\...\notes.md 并总结
    你: 从 D:\...\big.log 的第 20001 个字符开始读 20000 字
    你: 分析 D:\...\data.csv 和 D:\...\config.json

写入/修改文件示例：
    你: 把 D:\...\notes.md 里的“旧内容”改成“新内容”
    你: 新建 D:\...\todo.md，写上今天的三件事，父目录不存在就创建

写入和局部修改都会先在原文件旁生成 .bak 备份；只支持文本文件，单次写入上限 5MB。
读取文本文件最多加载 20MB，可通过 offset/max_chars 分页返回。

默认模型由 LLM_PROVIDER 决定（ollama/deepseek/dashscope）；启动后随时用：
    /model              # 列出三个模型
    /model 2            # 切到 deepseek-v4.1-flash
    /model qwen         # 切到 qwen3-vl-plus

本机依赖装在 conda 环境 rag 中；如果默认 python 报 ModuleNotFoundError：
    D:\Anaconda3\envs\rag\python.exe graph_agent.py
"""

from __future__ import annotations

import codecs
import csv
import io
import json
import os
import shutil
import stat
import tempfile
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from model_registry import (
    ModelConfigError,
    ModelProfile,
    build_chat_model,
    get_profile,
    initial_model_key,
    model_table,
    resolve_selector,
)
from web_search_tool import web_search
from web_tools import download_file, web_fetch

from typing import Any, Callable

from session_cli import (
    FILE_TOOL_ACTIONS,
    HELP_TEXT,
    SessionCommandError,
    confirm,
    format_history,
    format_session_list,
    parse_command,
    resolve_session_target,
)
from session_store import (
    DEFAULT_TITLE,
    Session,
    SessionStore,
    SessionStoreError,
    clean_title,
)

from office_tools import OFFICE_TOOLS
from approval import wrap_mcp_tools, wrap_tools
from subagent_manager import SubagentManager

# 始终加载脚本同目录下的 .env，避免从其他目录运行时找不到配置
load_dotenv(Path(__file__).resolve().with_name(".env"))

# ollama（默认，本地）或 deepseek（云端）
PROVIDER = os.getenv("LLM_PROVIDER", "ollama").strip().lower()

# 本地 Ollama 走 OpenAI 兼容接口；模型名就是 ollama list 里显示的名字
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3.5:2b")

# 云端 DeepSeek 配置（仅 LLM_PROVIDER=deepseek 时使用）
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

SYSTEM_PROMPT = (
    "你是一个简洁、可靠的中文助手。需要实时信息、新闻或网上资料时，"
    "用 web_search 搜索，不要凭记忆编造最新事实；搜索结果不足时如实说明。"
    "需要读取网页正文时用 web_fetch（只支持公开 http/https 页面）；"
    "下载软件、压缩包、文档等文件时用 download_file，默认保存到 downloads/，"
    "指定 path 可换目录，覆盖已有文件必须显式 overwrite=true；"
    "读取文件或目录用 read_file，大文件可用 offset/max_chars 分页；"
    "CSV 用 read_csv，JSON 用 read_json。"
    "文本文件新建/覆盖用 write_file，可用 create_parents 创建父目录、encoding 指定编码；"
    "局部修改用 edit_file。"
    "Word 用 read_docx/write_docx/edit_docx；Excel 用 read_xlsx/write_xlsx/edit_xlsx；"
    "PPT 用 read_pptx/write_pptx/edit_pptx；PDF 只读用 read_pdf。"
    "修改已有办公文件前先用对应的 read_* 工具确认原文；"
    "写入或修改完成后，只报告文件路径和完成状态，不要复述写入或修改的内容；"
    "路径优先使用绝对路径；工具返回错误时如实说明，不要猜测文件内容。"
)

SYSTEM_PROMPT += (
    "\n\n子 agent 编排：当任务可以拆成多个独立子任务时，"
    "用 spawn_agent 创建子 agent；用 list_agents 查看状态；"
    "用 wait_agent 等待结果。不要为简单任务创建子 agent。"
)

# 文件读取的安全上限
# 允许分页读取较大的文本文件；单次返回仍受 max_chars 限制。
_MAX_READ_BYTES = 20_000_000
_MAX_DIR_ENTRIES = 200
_DEFAULT_MAX_CHARS = 20_000
_MIN_MAX_CHARS = 100
_MAX_MAX_CHARS = 50_000
_MAX_STRUCTURED_ROWS = 200
_MAX_STRUCTURED_CELL_CHARS = 200

# write_file 支持的编码；限制在白名单内，避免写出带 NUL 的 UTF-16 等格式。
_WRITE_ENCODINGS = {
    "utf-8": "utf-8",
    "utf8": "utf-8",
    "utf-8-sig": "utf-8-sig",
    "utf8-sig": "utf-8-sig",
    "gb18030": "gb18030",
    "gbk": "gbk",
}

# 文件写入/编辑的安全上限
_MAX_WRITE_BYTES = 5_000_000
_MAX_EDIT_FILE_BYTES = 5_000_000
_MAX_BINARY_CHECK_BYTES = 8_192
_MAX_BACKUP_INDEX = 99


def _coerce_max_chars(value: int) -> int:
    """把 max_chars 限制在安全范围内。"""
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = _DEFAULT_MAX_CHARS
    return max(_MIN_MAX_CHARS, min(number, _MAX_MAX_CHARS))


def _coerce_offset(value: int) -> int:
    """把分页起点限制为非负整数。"""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, number)


def _coerce_max_rows(value: int) -> int:
    """把 CSV 最大行数限制在安全范围内。"""
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = 20
    return max(1, min(number, _MAX_STRUCTURED_ROWS))


def _normalize_write_encoding(value: str) -> str:
    """把 write_file 的 encoding 参数规范化为白名单编码名。"""
    key = str(value or "utf-8").strip().lower().replace("_", "-")
    if key not in _WRITE_ENCODINGS:
        raise ValueError(
            "不支持的编码 "
            f"{value!r}；支持 utf-8、utf-8-sig、gb18030、gbk。"
        )
    return _WRITE_ENCODINGS[key]


def _decode(raw: bytes) -> str:
    """按常见中文编码依次尝试解码。"""
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _load_text_file(path: Path) -> tuple[str, int, int]:
    """读取文本文件，返回 (文本, 文件总字节数, 实际读取字节数)。"""
    size = path.stat().st_size
    with path.open("rb") as handle:
        raw = handle.read(_MAX_READ_BYTES)

    if b"\x00" in raw[:_MAX_BINARY_CHECK_BYTES]:
        raise ValueError(f"这是二进制文件，暂不支持读取: {path}")

    return _decode(raw), size, len(raw)


def _read_text_file(path: Path, max_chars: int, offset: int = 0) -> str:
    """分段读取一个文本文件，返回带元信息和分页提示的内容字符串。"""
    limit = _coerce_max_chars(max_chars)
    start_offset = _coerce_offset(offset)

    try:
        text, size, raw_len = _load_text_file(path)
    except ValueError as exc:
        return f"[错误] {exc}"

    total = len(text)
    start = min(start_offset, total)
    end = min(start + limit, total)
    page = text[start:end]

    notes = []
    if size > raw_len:
        notes.append(f"文件共 {size} 字节，只读取了前 {raw_len} 字节")

    if total > 0:
        if start >= total:
            notes.append("已到文件末尾")
        else:
            if start > 0:
                notes.append(f"从第 {start + 1} 字符开始")
            if end < total:
                notes.append(f"内容超过 {limit} 字符，已截断")
                notes.append(f"共 {total} 字符，可用 offset={end} 继续")
            elif start > 0:
                notes.append(f"共 {total} 字符")

    header = f"[文件] {path} ({size} 字节)"
    if notes:
        header += " | " + "；".join(notes)

    if page:
        return f"{header}\n{page}"
    return f"{header}\n(空文件)" if total == 0 else f"{header}\n(已到文件末尾)"


def _list_directory(path: Path) -> str:
    """列出一个目录的直接子项，最多 _MAX_DIR_ENTRIES 项。"""
    entries = sorted(
        path.iterdir(),
        key=lambda item: (not item.is_dir(), item.name.lower()),
    )
    total = len(entries)
    visible = entries[:_MAX_DIR_ENTRIES]

    lines = []
    for entry in visible:
        try:
            if entry.is_dir():
                lines.append(f"[DIR]  {entry.name}/")
            else:
                lines.append(f"[FILE] {entry.name} ({entry.stat().st_size} 字节)")
        except OSError:
            lines.append(f"[?]    {entry.name} (无法读取)")

    header = f"[目录] {path}（共 {total} 项"
    if total > _MAX_DIR_ENTRIES:
        header += f"，只显示前 {_MAX_DIR_ENTRIES} 项"
    header += "）"

    if not lines:
        return f"{header}\n(空目录)"
    return header + "\n" + "\n".join(lines)


def _load_structured_text(raw_path: str) -> tuple[Path | None, str, str | None]:
    """为结构化读取工具定位并读取文本文件。"""
    try:
        target = _resolve_path(raw_path)
    except (OSError, ValueError, RuntimeError) as exc:
        return None, "", f"[错误] 路径无效: {raw_path!r} ({exc})"

    if not target.exists():
        return target, "", f"[错误] 文件不存在: {target}"
    if target.is_dir():
        return target, "", f"[错误] 目标是目录，请用 read_file: {target}"
    if not target.is_file():
        return target, "", f"[错误] 不是普通文件: {target}"

    try:
        text, _, _ = _load_text_file(target)
    except ValueError as exc:
        return target, "", f"[错误] {exc}"
    except OSError as exc:
        return target, "", f"[错误] 读取失败: {target} ({exc})"

    return target, text, None


def _json_type(value: object) -> str:
    """返回 JSON 值的简短类型名，容器附带长度。"""
    if isinstance(value, dict):
        return f"object({len(value)})"
    if isinstance(value, list):
        return f"array({len(value)})"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if value is None:
        return "null"
    return type(value).__name__


def _clip_cell(value: object) -> str:
    """把单元格压成单行，避免一个超长字段撑爆输出。"""
    text = str(value).replace("\r", " ").replace("\n", " ")
    if len(text) > _MAX_STRUCTURED_CELL_CHARS:
        return text[:_MAX_STRUCTURED_CELL_CHARS] + "…"
    return text


@tool
def read_csv(path: str, max_rows: int = 20) -> str:
    """结构化读取 CSV：显示列名、分隔符和前若干行数据。

    max_rows 默认 20，最多 200。只做只读摘要，不修改文件。
    """
    target, text, error = _load_structured_text(path)
    if error is not None:
        return error
    if not text.strip():
        return f"[CSV] {target} (空文件)"

    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel

    try:
        rows = [
            row
            for row in csv.reader(io.StringIO(text), dialect)
            if row
        ]
    except csv.Error as exc:
        return f"[错误] CSV 解析失败: {target} ({exc})"

    if not rows:
        return f"[CSV] {target} (空文件)"

    header = rows[0]
    data = rows[1:]
    limit = _coerce_max_rows(max_rows)
    shown = data[:limit]
    width = max([len(header)] + [len(row) for row in data])

    delimiter_name = {
        ",": "逗号",
        ";": "分号",
        "\t": "制表符",
        "|": "竖线",
    }.get(dialect.delimiter, repr(dialect.delimiter))

    lines = [
        f"[CSV] {target} (数据行 {len(data)}, 列数 {len(header)}, "
        f"分隔符 {delimiter_name})",
        "[列] " + " | ".join(
            _clip_cell(header[index] if index < len(header) else "")
            for index in range(width)
        ),
    ]
    for row_number, row in enumerate(shown, start=1):
        cells = [
            _clip_cell(row[index] if index < len(row) else "")
            for index in range(width)
        ]
        lines.append(f"[行 {row_number}] " + " | ".join(cells))

    if len(data) > len(shown):
        lines.append(
            f"[提示] 仅显示前 {len(shown)} 行（共 {len(data)} 行）；"
            f"可调 max_rows（最多 {_MAX_STRUCTURED_ROWS}）"
        )

    return "\n".join(lines)


@tool
def read_json(path: str, max_chars: int = 20000) -> str:
    """结构化读取 JSON：显示顶层类型、键/数组摘要和格式化内容。"""
    target, text, error = _load_structured_text(path)
    if error is not None:
        return error

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return (
            f"[错误] JSON 解析失败: {target} "
            f"（第 {exc.lineno} 行第 {exc.colno} 列：{exc.msg}）"
        )

    kind = _json_type(data).split("(", 1)[0]
    summary = []
    if isinstance(data, dict):
        summary.append(f"[键] {len(data)} 个")
        for key, value in data.items():
            summary.append(f"[字段] {key}: {_json_type(value)}")
    elif isinstance(data, list):
        summary.append(f"[数组] 长度 {len(data)}")
        if data:
            types = sorted({_json_type(value) for value in data})
            summary.append("[元素类型] " + ", ".join(types))
    else:
        summary.append(f"[值类型] {_json_type(data)}")

    pretty = json.dumps(data, ensure_ascii=False, indent=2)
    limit = _coerce_max_chars(max_chars)
    truncated = len(pretty) > limit
    shown = pretty[:limit] if truncated else pretty

    lines = [
        f"[JSON] {target} (类型 {kind}, {len(text)} 字符)",
        *summary,
    ]
    if truncated:
        lines.append(
            f"[提示] 内容超过 {limit} 字符，已截断；"
            f"可调 max_chars（最多 {_MAX_MAX_CHARS}）"
        )
    lines.append(shown)
    return "\n".join(lines)


def _resolve_path(raw_path: str) -> Path:
    """清洗路径，展开环境变量/用户目录，并解析为真实绝对路径。"""
    text = (raw_path or "").strip().strip('"').strip("'")
    if not text:
        raise ValueError("路径为空。")
    expanded = os.path.expandvars(os.path.expanduser(text))
    return Path(os.path.abspath(expanded)).resolve()


def _looks_binary(path: Path) -> bool:
    """读取文件前 8KB，判断是否含 NUL 字节。"""
    with path.open("rb") as handle:
        return b"\x00" in handle.read(_MAX_BINARY_CHECK_BYTES)


def _next_backup_path(target: Path) -> Path:
    """返回第一个可用的备份路径：.bak、.bak.1 ... .bak.99。"""
    candidate = target.with_name(target.name + ".bak")
    if not os.path.lexists(candidate):
        return candidate

    for index in range(1, _MAX_BACKUP_INDEX + 1):
        candidate = target.with_name(f"{target.name}.bak.{index}")
        if not os.path.lexists(candidate):
            return candidate

    raise FileExistsError(f"备份文件名已用尽（最多 .bak.{_MAX_BACKUP_INDEX}）")


def _decode_for_edit(raw: bytes) -> tuple[str, str, bool]:
    """解码待编辑文件，返回 (文本, 编码, 是否带 UTF-8 BOM)。

    只接受 UTF-8（可带 BOM）或 GB18030，避免编辑时悄悄改变原编码。
    """
    if raw.startswith(codecs.BOM_UTF8):
        return raw.decode("utf-8-sig"), "utf-8", True
    try:
        return raw.decode("utf-8"), "utf-8", False
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("gb18030"), "gb18030", False
    except UnicodeDecodeError as exc:
        raise ValueError("文件编码不是 UTF-8 或 GB18030，暂不支持编辑。") from exc


def _atomic_write(target: Path, data: bytes) -> None:
    """同目录临时文件 + fsync + os.replace，尽量保留原权限位。"""
    mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else None
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.tmp-",
        dir=str(target.parent),
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(temp_name, mode)
        os.replace(temp_name, target)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


@tool
def get_current_time() -> str:
    """获取当前本地日期和时间。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@tool
def read_file(path: str, max_chars: int = 20000, offset: int = 0) -> str:
    """读取文本文件并返回内容；如果 path 是目录，则返回目录中的文件和子目录列表。

    支持绝对路径、相对路径、~ 和环境变量。只支持文本文件。
    大文件可用 offset（起始字符位置，从 0 开始）和 max_chars 分页读取。
    """
    raw_path = (path or "").strip().strip('"').strip("'")
    if not raw_path:
        return "[错误] 路径为空。"

    try:
        expanded = os.path.expandvars(os.path.expanduser(raw_path))
        target = Path(os.path.abspath(expanded))
    except (OSError, ValueError) as exc:
        return f"[错误] 路径无效: {raw_path} ({exc})"

    try:
        if not target.exists():
            return f"[错误] 文件或目录不存在: {target}"
        if target.is_dir():
            return _list_directory(target)
        if not target.is_file():
            return f"[错误] 不是普通文件或目录: {target}"
        return _read_text_file(target, max_chars, offset)
    except PermissionError:
        return f"[错误] 没有权限访问: {target}"
    except OSError as exc:
        return f"[错误] 读取失败: {target} ({exc})"


@tool
def write_file(
    path: str,
    content: str,
    create_parents: bool = False,
    encoding: str = "utf-8",
) -> str:
    """新建或整体覆盖一个文本文件。

    新建或整体重写用本工具；局部修改请先用 read_file 读取，
    再用 edit_file。只支持文本文件；默认要求父目录已存在，
    create_parents=True 时自动创建；encoding 支持 utf-8、utf-8-sig、
    gb18030、gbk；已有文件会先自动备份为 .bak。
    """
    if not isinstance(content, str):
        return "[错误] content 必须是字符串。"

    try:
        target = _resolve_path(path)
    except (OSError, ValueError, RuntimeError) as exc:
        return f"[错误] 路径无效: {path!r} ({exc})"

    try:
        codec = _normalize_write_encoding(encoding)
    except ValueError as exc:
        return f"[错误] {exc}"

    parent = target.parent
    if not parent.exists():
        if not create_parents:
            return f"[错误] 父目录不存在: {parent}"
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return f"[错误] 创建父目录失败: {parent} ({exc})"
    if not parent.is_dir():
        return f"[错误] 父目录不是目录: {parent}"

    if target.exists() and target.is_dir():
        return f"[错误] 目标是目录: {target}"

    try:
        data = content.encode(codec)
    except UnicodeEncodeError as exc:
        return f"[错误] 内容无法编码为 {codec}: {exc}"

    if len(data) > _MAX_WRITE_BYTES:
        return f"[错误] 内容过大: {len(data)} 字节，上限 {_MAX_WRITE_BYTES} 字节"
    if b"\x00" in data:
        return f"[错误] 内容包含 NUL，拒绝写入: {target}"

    existed = target.exists()
    backup: Path | None = None

    if existed:
        if not target.is_file():
            return f"[错误] 目标不是普通文件: {target}"
        try:
            if _looks_binary(target):
                return f"[错误] 目标是二进制文件，拒绝覆盖: {target}"
        except OSError as exc:
            return f"[错误] 读取目标失败: {target} ({exc})"

        try:
            backup = _next_backup_path(target)
            shutil.copy2(target, backup)
        except OSError as exc:
            return f"[错误] 备份失败: {target} ({exc})"

    try:
        _atomic_write(target, data)
    except OSError as exc:
        return f"[错误] 写入失败: {target} ({exc})"

    action = "[写入]" if existed else "[新建]"
    lines = [f"{action} {target} ({len(data)} 字节, {codec.upper()})"]
    if backup is not None:
        lines.append(f"[备份] {backup}")
    return "\n".join(lines)


@tool
def edit_file(
    path: str,
    old_text: str,
    new_text: str,
    replace_all: bool = False,
) -> str:
    """在文本文件中把 old_text 精确替换为 new_text。

    只做局部修改；old_text 必须唯一，除非 replace_all=True。
    保留原文件的 UTF-8/GB18030 编码与 UTF-8 BOM；写入前自动备份为 .bak。
    """
    if not isinstance(old_text, str) or not isinstance(new_text, str):
        return "[错误] old_text 和 new_text 必须是字符串。"
    if old_text == "":
        return "[错误] old_text 不能为空。"

    try:
        target = _resolve_path(path)
    except (OSError, ValueError, RuntimeError) as exc:
        return f"[错误] 路径无效: {path!r} ({exc})"

    if not target.exists():
        return f"[错误] 文件不存在: {target}"
    if not target.is_file():
        return f"[错误] 目标不是普通文件: {target}"

    try:
        size = target.stat().st_size
        if size > _MAX_EDIT_FILE_BYTES:
            return (
                f"[错误] 文件过大: {size} 字节，"
                f"编辑上限 {_MAX_EDIT_FILE_BYTES} 字节"
            )
        raw = target.read_bytes()
    except OSError as exc:
        return f"[错误] 读取失败: {target} ({exc})"

    if b"\x00" in raw[:_MAX_BINARY_CHECK_BYTES]:
        return f"[错误] 目标是二进制文件，拒绝编辑: {target}"

    try:
        text, encoding, has_bom = _decode_for_edit(raw)
    except ValueError as exc:
        return f"[错误] {exc} {target}"

    count = text.count(old_text)
    if count == 0:
        return f"[错误] 未找到 old_text: {target}"
    if count > 1 and not replace_all:
        return (
            f"[错误] old_text 出现 {count} 次，请提供更长的唯一片段，"
            "或设置 replace_all=true。"
        )

    updated = text.replace(old_text, new_text, -1 if replace_all else 1)
    if updated == text:
        return f"[提示] 内容没有变化，未写入: {target}"

    try:
        encoded = updated.encode(encoding)
    except UnicodeEncodeError:
        return (
            f"[错误] new_text 无法用原编码 {encoding} 表示，"
            f"拒绝写入: {target}"
        )

    if has_bom:
        encoded = codecs.BOM_UTF8 + encoded
    if len(encoded) > _MAX_EDIT_FILE_BYTES:
        return (
            f"[错误] 修改后文件过大: {len(encoded)} 字节，"
            f"上限 {_MAX_EDIT_FILE_BYTES} 字节"
        )
    if b"\x00" in encoded:
        return f"[错误] new_text 包含 NUL，拒绝写入: {target}"

    try:
        backup = _next_backup_path(target)
        shutil.copy2(target, backup)
    except OSError as exc:
        return f"[错误] 备份失败: {target} ({exc})"

    try:
        _atomic_write(target, encoded)
    except OSError as exc:
        return f"[错误] 写入失败: {target} ({exc})"

    replaced = count if replace_all else 1
    return (
        f"[修改] {target} (替换 {replaced} 处, 编码 {encoding})\n"
        f"[备份] {backup}"
    )


TOOLS = [
    get_current_time,
    read_file,
    read_csv,
    read_json,
    write_file,
    edit_file,
    web_search,
    web_fetch,
    download_file,
    *OFFICE_TOOLS,
]


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
    extra_tools: list[Any] | None = None,
):
    """构建并编译 Agent 图：agent -> (tools -> agent)* -> END。"""
    tools = list(TOOLS)
    if include_subagent_tools and manager is not None:
        from subagent_tools import build_subagent_tools

        tools = tools + build_subagent_tools(manager)
    if extra_tools:
        tools = tools + list(extra_tools)
    tools = wrap_tools(tools)
    tools = wrap_mcp_tools(tools)

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


EXIT_COMMANDS = {"exit", "quit", "q", "退出", "再见"}


def _print_tool_calls(messages: list, start: int, output=None) -> None:
    """打印本轮工具调用；写文件只显示路径，不把文件内容打到终端。"""
    if output is None:
        output = print
    tool_results = {}
    for message in messages[start:]:
        if getattr(message, "type", None) == "tool":
            call_id = getattr(message, "tool_call_id", None)
            if call_id:
                tool_results[call_id] = str(message.content)

    for message in messages[start:]:
        for call in getattr(message, "tool_calls", None) or []:
            name = call.get("name", "unknown")
            args = call.get("args", {})
            action = FILE_TOOL_ACTIONS.get(name)
            if action is None:
                output(f"[工具] {name}({args})")
                continue

            path = args.get("path", "?") if isinstance(args, dict) else "?"
            result = tool_results.get(call.get("id"), "")
            if result.startswith("[错误]"):
                output(f"[失败] {action}文件: {path}")
            elif result.startswith("[提示]"):
                output(f"[提示] 文件未变化: {path}")
            else:
                output(f"[完成] 已{action}: {path}")


def _print_reply(message, output=None) -> None:
    """只打印本轮最终回复，不再重复打印整段历史。"""
    if output is None:
        output = print
    content = message.content
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    text = str(content).strip()
    output(f"[助手] {text or '（模型没有返回文本）'}")


def _run_chat_loop_inner(
    store: SessionStore,
    graph,
    session: Session,
    input_fn: Callable[[str], str] = input,
    output: Callable[[str], None] = print,
    manager=None,
    runtime: ModelRuntime | None = None,
) -> None:
    """终端主循环；抽出来便于用 fake graph / 临时 store 测试。"""
    messages = [SystemMessage(content=SYSTEM_PROMPT), *session.messages]

    while True:
        try:
            raw = input_fn("\n你: ").strip()
        except (EOFError, KeyboardInterrupt):
            try:
                store.save(session)
            except SessionStoreError as exc:
                output(f"[错误] {exc}")
            output("\n再见。")
            break

        if not raw:
            continue

        parsed = parse_command(raw)
        if parsed is not None:
            name, arg = parsed
            try:
                if name == "help":
                    output(HELP_TEXT)
                elif name == "model":
                    if runtime is None:
                        output("[错误] 模型切换未启用。")
                    else:
                        selector = arg.strip()
                        if not selector or selector.lower() == "list":
                            output(model_table(runtime.key))
                        elif selector.lower() == "current":
                            output(
                                f"[模型] 当前: {runtime.key} "
                                f"({runtime.profile.label})"
                            )
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
                elif name == "list":
                    output(format_session_list(store.list_sessions(), session.id))
                    for warning in store.pop_warnings():
                        output(f"[提示] {warning}")
                elif name == "history":
                    try:
                        page = int(arg) if arg else 1
                    except ValueError as exc:
                        raise SessionCommandError("页码必须是正整数。") from exc
                    output(format_history(session, page))
                elif name == "agents":
                    output(
                        manager.format_table()
                        if manager is not None
                        else "[子agent] 当前没有子 agent。"
                    )
                elif name == "clear":
                    if not confirm("确认清空当前会话历史？[y/N] ", input_fn):
                        output("已取消。")
                        continue
                    session.messages = []
                    store.save(session)
                    messages = [SystemMessage(content=SYSTEM_PROMPT)]
                    output("已清空当前会话历史。")
                elif name == "new":
                    store.save(session)
                    session = store.create(arg or DEFAULT_TITLE)
                    store.set_active_id(session.id)
                    messages = [SystemMessage(content=SYSTEM_PROMPT)]
                    output(f"[会话] 已新建: {session.title} ({session.id})")
                elif name == "switch":
                    if not arg:
                        raise SessionCommandError("用法: /switch <编号|ID>")
                    metas = store.list_sessions()
                    target_id = resolve_session_target(arg, metas)
                    if target_id == session.id:
                        output("已经是当前会话。")
                        continue
                    store.save(session)
                    session = store.load(target_id)
                    store.set_active_id(session.id)
                    messages = [SystemMessage(content=SYSTEM_PROMPT), *session.messages]
                    output(
                        f"[会话] 已切换到: {session.title} ({session.id}) | "
                        f"共 {len(session.messages)} 条消息"
                    )
                elif name == "delete":
                    if not arg:
                        raise SessionCommandError("用法: /delete <编号|ID>")
                    metas = store.list_sessions()
                    target_id = resolve_session_target(arg, metas)
                    target_meta = next((m for m in metas if m.id == target_id), None)
                    if target_meta is None:
                        raise SessionCommandError(f"找不到会话: {target_id}")
                    if not confirm(
                        f"确认删除会话 '{target_meta.title}' ({target_id})？[y/N] ",
                        input_fn,
                    ):
                        output("已取消。")
                        continue
                    was_current = target_id == session.id
                    store.delete(target_id)
                    if was_current:
                        metas = store.list_sessions()
                        if metas:
                            session = store.load(metas[0].id)
                            store.set_active_id(session.id)
                            messages = [
                                SystemMessage(content=SYSTEM_PROMPT),
                                *session.messages,
                            ]
                            output(
                                f"[会话] 已删除当前会话，切换到: "
                                f"{session.title} ({session.id})"
                            )
                        else:
                            session = store.create(DEFAULT_TITLE)
                            store.set_active_id(session.id)
                            messages = [SystemMessage(content=SYSTEM_PROMPT)]
                            output("[会话] 已删除当前会话，已新建空会话。")
                    else:
                        output(f"[会话] 已删除: {target_meta.title} ({target_id})")
                else:
                    output("[命令] 未知命令，输入 /help 查看。")
            except (SessionStoreError, SessionCommandError, OSError) as exc:
                output(f"[错误] {exc}")
            continue

        if raw.lower() in EXIT_COMMANDS:
            try:
                store.save(session)
            except SessionStoreError as exc:
                output(f"[错误] {exc}")
            output("再见。")
            break

        messages.append(HumanMessage(content=raw))
        start = len(messages) - 1
        try:
            result = graph.invoke(
                {"messages": messages},
                config={"recursion_limit": 25},
            )
        except KeyboardInterrupt:
            messages.pop()
            output("\n[已中断本次请求]")
            continue
        except Exception as exc:
            messages.pop()
            output(f"[错误] 调用模型失败: {exc}")
            continue

        result_messages = result["messages"]
        session.messages = [
            message
            for message in result_messages
            if not isinstance(message, SystemMessage)
        ]
        if session.title == DEFAULT_TITLE:
            first_user = next(
                (m for m in session.messages if isinstance(m, HumanMessage)),
                None,
            )
            if first_user is not None:
                session.title = clean_title(str(first_user.content)[:30])
        try:
            store.save(session)
        except SessionStoreError as exc:
            output(f"[错误] 保存会话失败: {exc}")
        messages = [SystemMessage(content=SYSTEM_PROMPT), *session.messages]
        _print_tool_calls(result_messages, start, output)
        _print_reply(result_messages[-1], output)


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
        return _run_chat_loop_inner(
            store, graph, session, input_fn, output, None, runtime
        )
    manager.start_monitor(output)
    try:
        return _run_chat_loop_inner(
            store, graph, session, input_fn, output, manager, runtime
        )
    finally:
        manager.shutdown()


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


if __name__ == "__main__":
    main()
