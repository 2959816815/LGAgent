# 给 Agent 增加写文件/修改文件能力 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 LangGraph Agent 中增加 `write_file` 和 `edit_file` 两个工具，让模型能按用户指令新建、覆盖或局部修改文本文件，并在修改已有文件前自动生成 `.bak` 备份。

**Architecture:** 继续使用 `graph_agent.py` 的单文件结构。新增路径解析、备份、二进制检测、原子写入和编码检测几个小 helper，再把两个 `@tool` 函数注册进 `TOOLS`，由现有 `ToolNode` 自动执行。测试用标准库 `unittest`，先写失败测试，再实现最小代码。

**Tech Stack:** Python 3.12、LangGraph、LangChain Core、`unittest`、本地 Ollama `qwen3.5:2b`。本机 Python 用 `D:\Anaconda3\envs\rag\python.exe`。

---

## 背景与约束

- 工作区：`D:\Vibecoding\Langgraph`
- `graph_agent.py` 实现前 301 行，完成后 522 行；已有 `read_file` 工具和交互式终端循环。
- 设计文档：`docs/superpowers/specs/2026-10-22-write-file-agent-design.md`
- 写入范围不限制目录，包括 `.env` 和 `graph_agent.py` 本身。
- 只处理文本；不删除文件、不追加、不创建目录、不执行命令。
- 已有文件先备份 `.bak`，冲突依次 `.bak.1` 到 `.bak.99`。
- 单次写入/编辑上限 5,000,000 字节。
- 父目录不存在直接报错。
- 测试目录必须用默认权限的普通目录；`tempfile.TemporaryDirectory` 在 DSH 下会创建 0700 目录，随后写入会被拒绝，`mkstemp` 甚至可能卡住。
- 本目录不是独立 git 仓库（`Langgraph` 在父仓库 `D:\Vibecoding` 里是未跟踪目录，父仓库还有无关暂存改动）。因此本计划用“测试通过 + 人工检查”作为检查点，不执行 `git commit`，避免把无关改动带进提交。

## 执行偏差（2026-10-22 完成）

- `TemporaryDirectory` 在这台机器的 DSH 沙箱下不可用：它创建的 0700 目录会让 Python 写入报 `PermissionError`，`mkstemp` 还可能一直卡住。`test_read_file.py` 和 `test_write_file.py` 都改成工作区内的默认权限目录（`uuid4` 后缀，`setUp` 创建、`tearDown` 用 `shutil.rmtree`）。
- NUL 测试原文件名 `nul.txt` 在 Windows 会被解析成设备路径 `\\.\nul`，已改为 `nul-content.txt`。
- `executing-plans` skill 未安装，按本计划内联执行。Task 4 用临时脚本直接调用 `graph.invoke` 验证：本地 `qwen3.5:2b` 依次调用 `read_file` → `edit_file`，文件内容和 `.bak` 均正确。

## 文件结构

| 文件 | 操作 | 职责 |
|---|---|---|
| `graph_agent.py` | 修改 | 增加写文件/编辑工具、helper、提示词和工具注册 |
| `test_write_file.py` | 新建 | 20 个 `unittest` 用例，覆盖 `write_file` 和 `edit_file` |
| `docs/superpowers/plans/2026-10-22-write-file-agent.md` | 本文件 | 实现计划 |

`graph_agent.py` 仍然保持单文件；实现前 301 行，完成后 545 行，仍可在一个上下文里维护，暂不拆分模块。

---

## Task 1: 实现 `write_file` 与写入基础 helper

**Files:**
- Create: `D:\Vibecoding\Langgraph\test_write_file.py`
- Modify: `D:\Vibecoding\Langgraph\graph_agent.py`（imports、常量、helper、`write_file`）

- [x] **Step 1: 写 `write_file` 的失败测试**

创建 `test_write_file.py`，先只包含 `write_file` 的 9 个用例：

```python
r"""write_file / edit_file 工具的单元测试（标准库 unittest，零第三方依赖）。

运行（本机建议用含 LangChain 的 rag 环境）：
    D:\Anaconda3\envs\rag\python.exe -m unittest -v test_write_file.py

测试临时目录放在工作区内，避免 DSH 沙箱拒绝系统临时目录。
"""

from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path

from graph_agent import write_file

_WORKSPACE = Path(__file__).resolve().parent


def call_write(path: Path | str, content: str) -> str:
    """调用 LangChain 工具对象，等价于模型发起一次 tool call。"""
    return write_file.invoke({"path": str(path), "content": content})


class WriteFileTests(unittest.TestCase):
    def setUp(self) -> None:
        # 不用 TemporaryDirectory：它在这种 DSH 沙箱下创建 0700 目录，
        # 随后在该目录里 mkstemp 会卡住。这里用默认权限的普通目录。
        self.root = _WORKSPACE / f".test_write_file_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_create_utf8_file(self) -> None:
        target = self.root / "new.txt"

        result = call_write(target, "hello 世界")

        self.assertIn("[新建]", result)
        self.assertEqual(target.read_text(encoding="utf-8"), "hello 世界")

    def test_overwrite_creates_backup(self) -> None:
        target = self.root / "notes.txt"
        target.write_text("旧内容", encoding="utf-8")

        result = call_write(target, "新内容")

        self.assertIn("[写入]", result)
        self.assertIn("[备份]", result)
        self.assertEqual(target.read_text(encoding="utf-8"), "新内容")
        backup = self.root / "notes.txt.bak"
        self.assertEqual(backup.read_text(encoding="utf-8"), "旧内容")

    def test_backup_collision_uses_numbered_suffix(self) -> None:
        target = self.root / "notes.txt"
        target.write_text("当前内容", encoding="utf-8")
        (self.root / "notes.txt.bak").write_text("更早内容", encoding="utf-8")

        result = call_write(target, "最新内容")

        self.assertIn("[备份]", result)
        self.assertEqual(target.read_text(encoding="utf-8"), "最新内容")
        self.assertEqual(
            (self.root / "notes.txt.bak").read_text(encoding="utf-8"),
            "更早内容",
        )
        self.assertEqual(
            (self.root / "notes.txt.bak.1").read_text(encoding="utf-8"),
            "当前内容",
        )

    def test_missing_parent_directory_is_rejected(self) -> None:
        target = self.root / "missing" / "demo.txt"

        result = call_write(target, "hello")

        self.assertIn("[错误]", result)
        self.assertIn("父目录不存在", result)
        self.assertFalse(target.parent.exists())

    def test_directory_target_is_rejected(self) -> None:
        target = self.root / "folder"
        target.mkdir()

        result = call_write(target, "hello")

        self.assertIn("[错误]", result)
        self.assertIn("目标是目录", result)
        self.assertTrue(target.is_dir())

    def test_nul_in_content_is_rejected(self) -> None:
        target = self.root / "nul-content.txt"

        result = call_write(target, "a\x00b")

        self.assertIn("[错误]", result)
        self.assertIn("NUL", result)
        self.assertFalse(target.exists())

    def test_oversized_content_is_rejected(self) -> None:
        target = self.root / "big.txt"

        result = call_write(target, "a" * 5_000_001)

        self.assertIn("[错误]", result)
        self.assertIn("内容过大", result)
        self.assertFalse(target.exists())

    def test_binary_target_is_not_overwritten(self) -> None:
        target = self.root / "blob.bin"
        target.write_bytes(b"abc\x00def")

        result = call_write(target, "text")

        self.assertIn("[错误]", result)
        self.assertIn("二进制", result)
        self.assertEqual(target.read_bytes(), b"abc\x00def")
        self.assertFalse((self.root / "blob.bin.bak").exists())

    def test_chinese_content_and_path_with_spaces(self) -> None:
        target = self.root / "中文 文件.txt"

        result = call_write(target, "第一行\n第二行")

        self.assertIn("[新建]", result)
        self.assertEqual(
            target.read_text(encoding="utf-8"),
            "第一行\n第二行",
        )


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: 运行测试，确认失败**

Run:

```powershell
cd D:\Vibecoding\Langgraph
D:\Anaconda3\envs\rag\python.exe -m unittest -v test_write_file.py
```

Expected: `ImportError: cannot import name 'write_file' from 'graph_agent'`，9 个测试无法导入。

- [x] **Step 3: 实现 imports、常量、helper 和 `write_file`**

把 `graph_agent.py` 的 import 区（当前 18-22 行）替换为（`os` 已有，新增 `shutil`、`stat`、`tempfile`）：

```python
import os
import shutil
import stat
import tempfile
from datetime import datetime
from pathlib import Path
```

在读取上限常量后（当前 52-57 行附近）加入：

```python
# 文件写入/编辑的安全上限
_MAX_WRITE_BYTES = 5_000_000
_MAX_EDIT_FILE_BYTES = 5_000_000
_MAX_BINARY_CHECK_BYTES = 8_192
_MAX_BACKUP_INDEX = 99
```

在 `_list_directory` 之后、`get_current_time` 之前加入：

```python
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
```

在 `read_file` 之后、`TOOLS` 之前加入：

```python
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
```

- [x] **Step 4: 运行测试，确认 9 个全部通过**

Run:

```powershell
cd D:\Vibecoding\Langgraph
D:\Anaconda3\envs\rag\python.exe -m unittest -v test_write_file.py
```

Expected:

```text
Ran 9 tests in ...s

OK
```

- [x] **Step 5: 检查点（不 commit）**

```powershell
cd D:\Vibecoding\Langgraph
D:\Anaconda3\envs\rag\python.exe -m unittest -v test_write_file.py
```

Expected: 仍然 `OK`。本目录没有独立 git 仓库，不做 `git add/commit`；以测试通过作为检查点。

---

## Task 2: 实现 `edit_file` 与编码检测

**Files:**
- Modify: `D:\Vibecoding\Langgraph\test_write_file.py`（import 和 11 个 edit 用例）
- Modify: `D:\Vibecoding\Langgraph\graph_agent.py`（`_decode_for_edit`、`edit_file`）

- [x] **Step 1: 加入 `edit_file` 的失败测试**

把 import 行改成：

```python
from graph_agent import edit_file, write_file
```

在模块级 `call_write` 函数之后、`class WriteFileTests` 之前，加入 helper：

```python
def call_edit(
    path: Path | str,
    old_text: str,
    new_text: str,
    replace_all: bool = False,
) -> str:
    """调用 edit_file 工具对象。"""
    return edit_file.invoke(
        {
            "path": str(path),
            "old_text": old_text,
            "new_text": new_text,
            "replace_all": replace_all,
        }
    )
```

在 `WriteFileTests` 类内部、最后一个 `write_file` 测试方法 `test_chinese_content_and_path_with_spaces` 之后，保持 4 空格缩进追加这 11 个测试方法；不要把它们放到类外：

```python
    def test_edit_single_occurrence(self) -> None:
        target = self.root / "single.txt"
        target.write_text("one two three", encoding="utf-8")

        result = call_edit(target, "one", "1")

        self.assertIn("[修改]", result)
        self.assertIn("替换 1 处", result)
        self.assertEqual(target.read_text(encoding="utf-8"), "1 two three")
        self.assertEqual(
            (self.root / "single.txt.bak").read_text(encoding="utf-8"),
            "one two three",
        )

    def test_edit_missing_old_text_does_not_write(self) -> None:
        target = self.root / "missing.txt"
        target.write_text("abc", encoding="utf-8")

        result = call_edit(target, "zzz", "x")

        self.assertIn("[错误]", result)
        self.assertIn("未找到", result)
        self.assertEqual(target.read_text(encoding="utf-8"), "abc")
        self.assertFalse((self.root / "missing.txt.bak").exists())

    def test_edit_multiple_without_replace_all_is_rejected(self) -> None:
        target = self.root / "multi.txt"
        target.write_text("a a", encoding="utf-8")

        result = call_edit(target, "a", "b")

        self.assertIn("[错误]", result)
        self.assertIn("出现 2 次", result)
        self.assertEqual(target.read_text(encoding="utf-8"), "a a")
        self.assertFalse((self.root / "multi.txt.bak").exists())

    def test_edit_replace_all(self) -> None:
        target = self.root / "all.txt"
        target.write_text("a a", encoding="utf-8")

        result = call_edit(target, "a", "b", replace_all=True)

        self.assertIn("替换 2 处", result)
        self.assertEqual(target.read_text(encoding="utf-8"), "b b")
        self.assertTrue((self.root / "all.txt.bak").exists())

    def test_edit_empty_old_text_is_rejected(self) -> None:
        target = self.root / "empty-old.txt"
        target.write_text("abc", encoding="utf-8")

        result = call_edit(target, "", "x")

        self.assertIn("[错误]", result)
        self.assertIn("old_text 不能为空", result)
        self.assertEqual(target.read_text(encoding="utf-8"), "abc")

    def test_edit_empty_new_text_deletes_fragment(self) -> None:
        target = self.root / "delete.txt"
        target.write_text("abc", encoding="utf-8")

        result = call_edit(target, "b", "")

        self.assertIn("[修改]", result)
        self.assertEqual(target.read_text(encoding="utf-8"), "ac")

    def test_edit_preserves_gb18030(self) -> None:
        target = self.root / "gb.txt"
        target.write_bytes("中文内容".encode("gb18030"))

        result = call_edit(target, "中文", "英文")

        self.assertIn("编码 gb18030", result)
        self.assertEqual(
            target.read_bytes().decode("gb18030"),
            "英文内容",
        )
        self.assertEqual(
            (self.root / "gb.txt.bak").read_bytes().decode("gb18030"),
            "中文内容",
        )

    def test_edit_preserves_utf8_bom(self) -> None:
        target = self.root / "bom.txt"
        target.write_bytes(b"\xef\xbb\xbf" + "你好".encode("utf-8"))

        result = call_edit(target, "你好", "您好")

        self.assertIn("[修改]", result)
        raw = target.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(raw.decode("utf-8-sig"), "您好")

    def test_edit_same_text_does_not_write(self) -> None:
        target = self.root / "same.txt"
        target.write_text("same", encoding="utf-8")
        before_mtime = target.stat().st_mtime_ns

        result = call_edit(target, "same", "same")

        self.assertIn("[提示]", result)
        self.assertEqual(target.stat().st_mtime_ns, before_mtime)
        self.assertFalse((self.root / "same.txt.bak").exists())

    def test_edit_creates_backup(self) -> None:
        target = self.root / "backup.txt"
        target.write_text("旧内容", encoding="utf-8")

        result = call_edit(target, "旧内容", "新内容")

        self.assertIn("[备份]", result)
        self.assertEqual(
            (self.root / "backup.txt.bak").read_text(encoding="utf-8"),
            "旧内容",
        )

    def test_edit_unencodable_new_text_does_not_write(self) -> None:
        target = self.root / "unencodable.txt"
        target.write_bytes("中文".encode("gb18030"))
        before = target.read_bytes()

        result = call_edit(target, "中文", "\ud800")

        self.assertIn("[错误]", result)
        self.assertIn("原编码", result)
        self.assertEqual(target.read_bytes(), before)
        self.assertFalse((self.root / "unencodable.txt.bak").exists())
```

- [x] **Step 2: 运行测试，确认 edit 用例失败**

Run:

```powershell
cd D:\Vibecoding\Langgraph
D:\Anaconda3\envs\rag\python.exe -m unittest -v test_write_file.py
```

Expected: `ImportError: cannot import name 'edit_file' from 'graph_agent'`，20 个测试无法导入。

- [x] **Step 3: 实现 `_decode_for_edit` 和 `edit_file`**

在 `write_file` 之后、`TOOLS` 之前加入：

```python
def _decode_for_edit(raw: bytes) -> tuple[str, str, bool]:
    """返回 (text, encoding, has_bom)；无法识别编码时 encoding 为空串。"""
    if raw.startswith(b"\xef\xbb\xbf"):
        try:
            return raw.decode("utf-8-sig"), "utf-8", True
        except UnicodeDecodeError:
            return "", "", False

    for encoding in ("utf-8", "gb18030"):
        try:
            return raw.decode(encoding), encoding, False
        except UnicodeDecodeError:
            continue

    return "", "", False
```

在 `write_file` 之后、`TOOLS` 之前加入：

```python
@tool
def edit_file(
    path: str,
    old_text: str,
    new_text: str,
    replace_all: bool = False,
) -> str:
    """把文本文件中精确匹配的 old_text 替换为 new_text。

    局部修改前必须先用 read_file 读取，并原样复制 old_text；
    默认只替换第一处，replace_all=True 替换全部；
    已有文件会先自动备份为 .bak。
    """
    if not isinstance(old_text, str) or not isinstance(new_text, str):
        return "[错误] old_text 和 new_text 必须是字符串。"
    if not old_text:
        return "[错误] old_text 不能为空。"

    try:
        target = _resolve_path(path)
    except (OSError, ValueError, RuntimeError) as exc:
        return f"[错误] 路径无效: {path!r} ({exc})"

    if not target.exists():
        return f"[错误] 文件不存在: {target}"
    if target.is_dir():
        return f"[错误] 目标是目录: {target}"
    if not target.is_file():
        return f"[错误] 目标不是普通文件: {target}"

    try:
        size = target.stat().st_size
        if size > _MAX_EDIT_FILE_BYTES:
            return (
                f"[错误] 文件过大，暂不支持编辑: {target} "
                f"({size} 字节，上限 {_MAX_EDIT_FILE_BYTES} 字节)"
            )
        raw = target.read_bytes()
    except PermissionError:
        return f"[错误] 没有权限读取: {target}"
    except OSError as exc:
        return f"[错误] 读取失败: {target} ({exc})"

    if b"\x00" in raw[:_MAX_BINARY_CHECK_BYTES]:
        return f"[错误] 这是二进制文件，拒绝编辑: {target}"

    text, encoding, has_bom = _decode_for_edit(raw)
    if not encoding:
        return f"[错误] 无法识别编码，拒绝编辑: {target}"

    count = text.count(old_text)
    if count == 0:
        return (
            f"[错误] 未找到 old_text: {target}。"
            "请先用 read_file 读取，并原样复制要替换的片段。"
        )
    if count > 1 and not replace_all:
        return (
            f"[错误] old_text 出现 {count} 次，无法确定替换哪一处: {target}。"
            "请提供更长的唯一片段，或设置 replace_all=True。"
        )
    if old_text == new_text:
        return "[提示] 新旧文本相同，未修改。"

    replaced = text.replace(old_text, new_text, -1 if replace_all else 1)
    try:
        data = replaced.encode(encoding)
    except UnicodeEncodeError as exc:
        return f"[错误] 新内容无法用原编码 {encoding} 保存: {exc}"

    if has_bom:
        data = b"\xef\xbb\xbf" + data

    try:
        backup = _next_backup_path(target)
        shutil.copy2(target, backup)
    except OSError as exc:
        return f"[错误] 备份失败: {target} ({exc})"

    try:
        _atomic_write(target, data)
    except OSError as exc:
        return f"[错误] 写入失败: {target} ({exc})"

    replaced_count = count if replace_all else 1
    return (
        f"[修改] {target} (替换 {replaced_count} 处, 编码 {encoding})\n"
        f"[备份] {backup}"
    )
```

- [x] **Step 4: 运行测试，确认 20 个全部通过**

Run:

```powershell
cd D:\Vibecoding\Langgraph
D:\Anaconda3\envs\rag\python.exe -m unittest -v test_write_file.py
```

Expected:

```text
Ran 20 tests in ...s

OK
```

- [x] **Step 5: 检查点（不 commit）**

```powershell
cd D:\Vibecoding\Langgraph
D:\Anaconda3\envs\rag\python.exe -m unittest -v test_write_file.py
```

Expected: 仍然 `OK`。

---

## Task 3: 更新提示词、工具列表和模块说明

**Files:**
- Modify: `D:\Vibecoding\Langgraph\graph_agent.py`（模块 docstring、`SYSTEM_PROMPT`、`TOOLS`）

- [x] **Step 1: 更新模块 docstring**

在模块 docstring 的读取示例之后加入：

```text
写入文件示例（启动后在终端输入）：
    你: 在当前目录新建 demo.txt，内容为 hello
    你: 把 demo.txt 里的 hello 改成 hi
```

- [x] **Step 2: 更新 `SYSTEM_PROMPT`**

把当前 `SYSTEM_PROMPT` 替换为：

```python
SYSTEM_PROMPT = (
    "你是一个简洁、可靠的中文助手。需要实时信息时请调用工具，不要编造。"
    "需要读取文件或目录时调用 read_file；新建或整体重写文件时调用 write_file；"
    "局部修改文件时，先用 read_file 读取，原样复制 old_text，再调用 edit_file；"
    "写已有文件会自动备份为 .bak；写完告诉用户改了哪个文件、备份在哪；"
    "路径优先使用绝对路径；工具返回错误时如实说明，不要猜测文件内容。"
)
```

- [x] **Step 3: 更新 `TOOLS`**

把：

```python
TOOLS = [get_current_time, read_file]
```

改成：

```python
TOOLS = [get_current_time, read_file, write_file, edit_file]
```

- [x] **Step 4: 跑全量测试**

Run:

```powershell
cd D:\Vibecoding\Langgraph
D:\Anaconda3\envs\rag\python.exe -m unittest -v test_read_file.py test_write_file.py
```

Expected:

```text
Ran 27 tests in ...s

OK
```

- [x] **Step 5: 用 Python 检查工具注册**

Run:

```powershell
cd D:\Vibecoding\Langgraph
D:\Anaconda3\envs\rag\python.exe -c "import graph_agent; print([tool.name for tool in graph_agent.TOOLS])"
```

Expected:

```text
['get_current_time', 'read_file', 'write_file', 'edit_file']
```

- [x] **Step 6: 检查点（不 commit）**

确认全量测试 27 个通过、工具列表包含 4 个工具。本目录没有独立 git 仓库，不执行 commit。

---

## Task 4: 本地模型端到端验证

**Files:**
- 临时产物：`D:\Vibecoding\Langgraph\agent_write_demo.txt`
- 临时产物：`D:\Vibecoding\Langgraph\agent_write_demo.txt.bak`（可能还有 `.bak.1`）

- [x] **Step 1: 确认 Ollama 和模型可用**

Run:

```powershell
ollama list
```

Expected: 列表中有 `qwen3.5:2b`。如果没有，先启动 `ollama serve` 并拉取模型。

- [x] **Step 2: 启动交互式 Agent**

Run:

```powershell
cd D:\Vibecoding\Langgraph
D:\Anaconda3\envs\rag\python.exe graph_agent.py
```

Expected:

```text
[LLM] provider=ollama | model=qwen3.5:2b | base_url=http://127.0.0.1:11434/v1
输入问题开始对话；输入 exit / quit / q / 退出 结束。
```

- [x] **Step 3: 让 Agent 新建文件**

在 `你:` 后输入：

```text
在当前目录新建 agent_write_demo.txt，内容为三行：第一行、第二行、第三行
```

Expected: 出现 `[工具] write_file(...)`，助手说明已新建文件。

- [x] **Step 4: 让 Agent 局部修改**

输入：

```text
把 agent_write_demo.txt 的第二行改成“改过的第二行”
```

Expected: 出现 `[工具] read_file(...)`，随后 `[工具] edit_file(...)`；助手说明替换了 1 处、备份在 `agent_write_demo.txt.bak`。

- [x] **Step 5: 让 Agent 读回验证**

输入：

```text
读取 agent_write_demo.txt 确认内容，并告诉我备份文件在哪
```

Expected: 出现 `[工具] read_file(...)`；助手说明当前内容和 `.bak` 路径。

输入 `exit` 退出。

- [x] **Step 6: 用 shell 检查落盘结果**

Run:

```powershell
cd D:\Vibecoding\Langgraph
Get-Content .\agent_write_demo.txt -Encoding UTF8
Get-ChildItem .\agent_write_demo.txt.bak*
```

Expected: 文本包含“改过的第二行”；存在 `agent_write_demo.txt.bak`，其内容是修改前的三行。

- [x] **Step 7: 清理临时文件**

Run:

```powershell
cd D:\Vibecoding\Langgraph
Remove-Item .\agent_write_demo.txt -ErrorAction SilentlyContinue
Remove-Item .\agent_write_demo.txt.bak* -ErrorAction SilentlyContinue
```

- [x] **Step 8: 检查点（不 commit）**

确认端到端工具调用顺序为 `write_file` → `read_file` → `edit_file` → `read_file`，且 `.bak` 内容正确。不执行 git commit。

---

## 计划自审

### 1. 规格覆盖

| 设计文档要求 | 对应任务 |
|---|---|
| `write_file` 新建/覆盖 | Task 1 |
| 父目录不存在报错 | Task 1 |
| 目标是目录报错 | Task 1 |
| 5MB 上限 | Task 1 / Task 2 |
| NUL 内容/二进制目标拒绝 | Task 1 |
| 自动 `.bak`、`.bak.N` | Task 1 |
| 原子写入、保留权限 | Task 1 |
| `edit_file` 精确替换 | Task 2 |
| 单处/多处/`replace_all` | Task 2 |
| UTF-8 BOM / GB18030 保留 | Task 2 |
| 编码无法保存时报错 | Task 2 |
| `old_text == new_text` 不写盘 | Task 2 |
| 提示词和 `TOOLS` 更新 | Task 3 |
| 端到端验证 | Task 4 |
| 不删除、不建目录、不追加 | 明确不在工具接口中实现 |

### 2. 占位符扫描

计划中每个代码步骤都给出了完整代码、精确路径和预期输出；没有 `TBD`、`TODO`、`implement later` 或“类似 Task N”的占位。

### 3. 类型和命名一致性

- 所有工具和 helper 都在 `graph_agent.py` 中定义，测试只 import `write_file` / `edit_file`。
- 常量名统一：`_MAX_WRITE_BYTES`、`_MAX_EDIT_FILE_BYTES`、`_MAX_BINARY_CHECK_BYTES`、`_MAX_BACKUP_INDEX`。
- helper 名统一：`_resolve_path`、`_looks_binary`、`_next_backup_path`、`_atomic_write`、`_decode_for_edit`。
- `write_file` / `edit_file` 的返回都以 `[新建]`、`[写入]`、`[修改]`、`[备份]`、`[提示]` 或 `[错误]` 开头，便于测试断言。

### 4. 已知偏差

- 计划没有 `git commit` 步骤：`D:\Vibecoding\Langgraph` 不是独立 git 仓库，父仓库有无关暂存改动。用测试通过作为检查点，避免误提交。
- `test_edit_unencodable_new_text_does_not_write` 使用孤立代理项 `"\ud800"` 触发编码失败；GB18030 本身覆盖绝大多数 Unicode 字符，只有代理项这类非法标量无法编码。
