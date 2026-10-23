r"""graph_agent.py — 一个最小可用的 LangGraph Agent。

默认调用本地 Ollama 的 qwen3.5:2b（无需联网、无需 API Key）：
    ollama serve          # 确保 Ollama 已启动
    ollama list           # 应能看到 qwen3.5:2b
    python graph_agent.py # 启动后在终端输入，exit 退出

读取文件示例（启动后在终端输入）：
    你: 读取 D:\...\notes.md 并总结

写入/修改文件示例：
    你: 把 D:\...\notes.md 里的“旧内容”改成“新内容”
    你: 新建 D:\...\todo.md，写上今天的三件事

写入和局部修改都会先在原文件旁生成 .bak 备份；只支持文本文件，单个文件上限 5MB。

如需切回 DeepSeek，设置环境变量 LLM_PROVIDER=deepseek 即可：
    $env:LLM_PROVIDER="deepseek"; python graph_agent.py

本机依赖装在 conda 环境 rag 中；如果默认 python 报 ModuleNotFoundError：
    D:\Anaconda3\envs\rag\python.exe graph_agent.py
"""

from __future__ import annotations

import codecs
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

from office_tools import OFFICE_TOOLS

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
    "你是一个简洁、可靠的中文助手。需要实时信息时请调用工具，不要编造。"
    "读取文件或目录用 read_file；文本文件新建/覆盖用 write_file，局部修改用 edit_file。"
    "Word 用 read_docx/write_docx/edit_docx；Excel 用 read_xlsx/write_xlsx/edit_xlsx；"
    "PPT 用 read_pptx/write_pptx/edit_pptx；PDF 只读用 read_pdf。"
    "修改已有办公文件前先用对应的 read_* 工具确认原文；"
    "写入或修改完成后，只报告文件路径和完成状态，不要复述写入或修改的内容；"
    "路径优先使用绝对路径；工具返回错误时如实说明，不要猜测文件内容。"
)

# 文件读取的安全上限
_MAX_READ_BYTES = 1_000_000
_MAX_DIR_ENTRIES = 200
_DEFAULT_MAX_CHARS = 20_000
_MIN_MAX_CHARS = 100
_MAX_MAX_CHARS = 50_000

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


def _decode(raw: bytes) -> str:
    """按常见中文编码依次尝试解码。"""
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _read_text_file(path: Path, max_chars: int) -> str:
    """读取一个文本文件，返回带元信息的内容字符串。"""
    limit = _coerce_max_chars(max_chars)
    size = path.stat().st_size

    with path.open("rb") as handle:
        raw = handle.read(_MAX_READ_BYTES)

    if b"\x00" in raw[:8192]:
        return f"[错误] 这是二进制文件，暂不支持读取: {path}"

    text = _decode(raw)
    truncated_bytes = size > len(raw)
    truncated_chars = len(text) > limit
    if truncated_chars:
        text = text[:limit]

    notes = []
    if truncated_bytes:
        notes.append(f"文件共 {size} 字节，只读取了前 {len(raw)} 字节")
    if truncated_chars:
        notes.append(f"内容超过 {limit} 字符，已截断")

    header = f"[文件] {path} ({size} 字节)"
    if notes:
        header += " | " + "；".join(notes)

    return f"{header}\n{text}" if text else f"{header}\n(空文件)"


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
def read_file(path: str, max_chars: int = 20000) -> str:
    """读取文本文件并返回内容；如果 path 是目录，则返回目录中的文件和子目录列表。

    支持绝对路径、相对路径、~ 和环境变量。只支持文本文件。
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
        return _read_text_file(target, max_chars)
    except PermissionError:
        return f"[错误] 没有权限访问: {target}"
    except OSError as exc:
        return f"[错误] 读取失败: {target} ({exc})"


@tool
def write_file(path: str, content: str) -> str:
    """新建或整体覆盖一个文本文件。

    新建或整体重写用本工具；局部修改请先用 read_file 读取，
    再用 edit_file。只支持文本文件；父目录必须已存在；
    已有文件会先自动备份为 .bak。
    """
    if not isinstance(content, str):
        return "[错误] content 必须是字符串。"

    try:
        target = _resolve_path(path)
    except (OSError, ValueError, RuntimeError) as exc:
        return f"[错误] 路径无效: {path!r} ({exc})"

    parent = target.parent
    if not parent.exists() or not parent.is_dir():
        return f"[错误] 父目录不存在: {parent}"

    if target.exists() and target.is_dir():
        return f"[错误] 目标是目录: {target}"

    try:
        data = content.encode("utf-8")
    except UnicodeEncodeError as exc:
        return f"[错误] 内容无法编码为 UTF-8: {exc}"

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
    lines = [f"{action} {target} ({len(data)} 字节, UTF-8)"]
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


TOOLS = [get_current_time, read_file, write_file, edit_file, *OFFICE_TOOLS]


def active_model() -> tuple[str, str, str]:
    """返回当前实际使用的 (provider, model, base_url)。"""
    if PROVIDER == "deepseek":
        return PROVIDER, DEEPSEEK_MODEL, DEEPSEEK_BASE_URL
    return PROVIDER, OLLAMA_MODEL, OLLAMA_BASE_URL


def build_llm() -> ChatOpenAI:
    """按 LLM_PROVIDER 创建模型客户端。"""
    if PROVIDER == "deepseek":
        if not DEEPSEEK_API_KEY:
            raise SystemExit("未找到 DEEPSEEK_API_KEY，请检查脚本同目录下的 .env 文件。")
        return ChatOpenAI(
            model=DEEPSEEK_MODEL,
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
            temperature=0,
            timeout=60,
            max_retries=2,
        )

    if PROVIDER != "ollama":
        raise SystemExit("LLM_PROVIDER 仅支持 ollama 或 deepseek。")

    # Ollama 的 OpenAI 兼容接口不校验 Key，随便填一个即可
    return ChatOpenAI(
        model=OLLAMA_MODEL,
        api_key="ollama",
        base_url=OLLAMA_BASE_URL,
        temperature=0,
        timeout=120,
        max_retries=1,
    )


def build_graph():
    """构建并编译 Agent 图：agent -> (tools -> agent)* -> END。"""
    llm_with_tools = build_llm().bind_tools(TOOLS)

    def agent_node(state: MessagesState) -> dict:
        response = llm_with_tools.invoke(state["messages"])
        return {"messages": [response]}

    builder = StateGraph(MessagesState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", ToolNode(TOOLS))
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", tools_condition)
    builder.add_edge("tools", "agent")
    return builder.compile()


EXIT_COMMANDS = {"exit", "quit", "q", "退出", "再见"}
CLEAR_COMMANDS = {"/clear", "clear", "清空"}


_FILE_TOOL_ACTIONS = {
    "write_file": "写入",
    "edit_file": "修改",
    "write_docx": "写入",
    "edit_docx": "修改",
    "write_xlsx": "写入",
    "edit_xlsx": "修改",
    "write_pptx": "写入",
    "edit_pptx": "修改",
}


def _print_tool_calls(messages: list, start: int) -> None:
    """打印本轮工具调用；写文件只显示路径，不把文件内容打到终端。"""
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
            action = _FILE_TOOL_ACTIONS.get(name)
            if action is None:
                print(f"[工具] {name}({args})")
                continue

            path = args.get("path", "?") if isinstance(args, dict) else "?"
            result = tool_results.get(call.get("id"), "")
            if result.startswith("[错误]"):
                print(f"[失败] {action}文件: {path}")
            elif result.startswith("[提示]"):
                print(f"[提示] 文件未变化: {path}")
            else:
                print(f"[完成] 已{action}: {path}")


def _print_reply(message) -> None:
    """只打印本轮最终回复，不再重复打印整段历史。"""
    content = message.content
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    text = str(content).strip()
    print(f"助手: {text or '（模型没有返回文本）'}")


def main() -> None:
    provider, model, base_url = active_model()
    print(f"[LLM] provider={provider} | model={model} | base_url={base_url}")
    print("输入问题开始对话；输入 exit / quit / q / 退出 结束。")

    graph = build_graph()
    messages = [SystemMessage(content=SYSTEM_PROMPT)]

    while True:
        try:
            question = input("\n你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            break

        if not question:
            continue
        if question.lower() in EXIT_COMMANDS:
            print("再见。")
            break
        if question.lower() in CLEAR_COMMANDS:
            messages = [SystemMessage(content=SYSTEM_PROMPT)]
            print("已清空对话历史。")
            continue

        messages.append(HumanMessage(content=question))
        start = len(messages) - 1
        try:
            result = graph.invoke(
                {"messages": messages},
                config={"recursion_limit": 25},
            )
        except KeyboardInterrupt:
            messages.pop()
            print("\n[已中断本次请求]")
            continue
        except Exception as exc:
            messages.pop()
            print(f"[错误] 调用模型失败: {exc}")
            continue

        messages = result["messages"]
        _print_tool_calls(messages, start)
        _print_reply(messages[-1])


if __name__ == "__main__":
    main()
