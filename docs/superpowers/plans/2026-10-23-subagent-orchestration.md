# 子 Agent 编排 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有终端 LangGraph agent 中加入“主 agent 创建多个独立子进程子 agent、查询/等待结果、终端面板展示状态”的能力。

**Architecture:** 主进程持有 `SubagentManager`，每个子 agent 是一个 `subagent_worker.py` 独立 Python 进程；子进程把任务、状态、结果和消息原子写入 `.agents/<id>.json`，主进程轮询这些文件并通过 `.agents/<id>.log` 诊断崩溃。主 agent 通过 `spawn_agent` / `list_agents` / `wait_agent` 三个工具编排，`/agents` 提供终端快照。

**Tech Stack:** Python 3.12（conda 环境 `rag`）、LangGraph、LangChain、`subprocess`、`unittest`、JSON 文件存储。

---

## 文件结构

- Create: `subagent_store.py` — `AgentRecord`、原子 JSON 存储、损坏文件处理。
- Create: `subagent_cli.py` — `/agents` 表格和完成通知文本。
- Create: `subagent_manager.py` — 子进程启动、并发限制、轮询、等待、通知。
- Create: `subagent_worker.py` — 子进程入口，跑一次图并写回状态。
- Create: `subagent_tools.py` — 主 agent 的三个 LangChain 工具。
- Create: `subagent_fake_worker.py` — 测试用假 worker（不以 `test_` 开头）。
- Create: `test_subagent_store.py`、`test_subagent_cli.py`、`test_subagent_manager.py`、`test_subagent_tools.py`、`test_subagent_worker.py`。
- Modify: `graph_agent.py` — `build_graph` 支持子 agent 工具；主循环接入 Manager 和 `/agents`；`main` 创建 Manager。
- Modify: `session_cli.py` — 增加 `/agents` 命令和帮助文本。
- Modify: `test_session_flow.py` — 增加 `/agents` 集成覆盖。
- Modify: `.gitignore` — 已包含 `.sessions/`；追加 `.agents/`。

---

### Task 1: Agent 记录存储

**Files:**
- Create: `subagent_store.py`
- Test: `test_subagent_store.py`

- [ ] **Step 1: 写失败测试**

Create `test_subagent_store.py`:

```python
from __future__ import annotations

import json
import shutil
import unittest
import uuid
from pathlib import Path

from langchain_core.messages import HumanMessage

from subagent_store import (
    MAX_TASK_CHARS,
    AgentRecord,
    SubagentStore,
    SubagentStoreError,
    now_iso,
)


class SubagentStoreTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent / f".test_store_{uuid.uuid4().hex[:8]}"
        self.store = SubagentStore(self.root)
        self.record = self.store.create("整理周报", agent_id="agent-20261023-201500-a1b2")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_create_and_load(self):
        loaded = self.store.load(self.record.id)
        self.assertEqual(loaded.id, self.record.id)
        self.assertEqual(loaded.task, "整理周报")
        self.assertEqual(loaded.status, "running")
        self.assertTrue((self.root / f"{self.record.id}.json").exists())

    def test_save_round_trips_messages(self):
        self.record.messages = [HumanMessage(content="你好")]
        self.record.result = "完成"
        self.record.status = "succeeded"
        self.record.finished_at = now_iso()
        self.store.save(self.record)

        loaded = self.store.load(self.record.id)
        self.assertEqual(loaded.result, "完成")
        self.assertEqual(loaded.messages[0].content, "你好")
        self.assertEqual(loaded.status, "succeeded")

    def test_list_sorted_by_created_desc(self):
        second = self.store.create("B", agent_id="agent-20261023-201501-b2c3")
        second.created_at = "2026-10-23T20:15:01+08:00"
        self.record.created_at = "2026-10-23T20:15:00+08:00"
        self.store.save(second)
        self.store.save(self.record)

        records = self.store.list_records()
        self.assertEqual([r.id for r in records], [second.id, self.record.id])

    def test_corrupt_file_is_skipped_with_warning(self):
        bad = self.root / "agent-20261023-201502-c3d4.json"
        bad.write_text("{not json", encoding="utf-8")
        records = self.store.list_records()
        self.assertEqual(len(records), 1)
        self.assertTrue(any("损坏" in warning for warning in self.store.warnings))

    def test_rejects_invalid_status(self):
        self.record.status = "unknown"
        with self.assertRaises(SubagentStoreError):
            self.store.save(self.record)

    def test_rejects_too_long_task(self):
        with self.assertRaises(SubagentStoreError):
            self.store.create("x" * (MAX_TASK_CHARS + 1))

    def test_load_missing_raises(self):
        with self.assertRaises(SubagentStoreError):
            self.store.load("agent-20261023-201599-ffff")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `D:\Anaconda3\envs\rag\python.exe -m unittest -v test_subagent_store.py`

Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'subagent_store'`.

- [ ] **Step 3: 实现存储**

Create `subagent_store.py`:

```python
from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from langchain_core.messages import BaseMessage, messages_from_dict, messages_to_dict

SCHEMA_VERSION = 1
MAX_TASK_CHARS = 4000
STATUSES = {"running", "succeeded", "failed"}
AGENT_ID_RE = re.compile(r"^agent-[0-9]{8}-[0-9]{6}-[0-9a-f]{4}$")


class SubagentStoreError(Exception):
    """子 agent 记录读写错误。"""


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def new_agent_id() -> str:
    return f"agent-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"


@dataclass
class AgentRecord:
    id: str
    task: str
    status: str = "running"
    created_at: str = field(default_factory=now_iso)
    started_at: str | None = None
    finished_at: str | None = None
    pid: int | None = None
    result: str | None = None
    error: str | None = None
    messages: list[BaseMessage] = field(default_factory=list)
    log_path: str = ""

    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "id": self.id,
            "task": self.task,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "pid": self.pid,
            "result": self.result,
            "error": self.error,
            "messages": messages_to_dict(self.messages),
            "log_path": self.log_path,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "AgentRecord":
        if not isinstance(raw, dict):
            raise ValueError("记录必须是对象")
        if raw.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("不支持的 schema_version")
        agent_id = raw.get("id")
        if not isinstance(agent_id, str) or not AGENT_ID_RE.fullmatch(agent_id):
            raise ValueError("非法 agent id")
        status = raw.get("status")
        if status not in STATUSES:
            raise ValueError("非法 status")
        task = raw.get("task")
        if not isinstance(task, str) or not task:
            raise ValueError("task 为空")
        try:
            messages = messages_from_dict(raw.get("messages", []))
        except Exception as exc:
            raise ValueError(f"messages 无法解析: {exc}") from exc
        return cls(
            id=agent_id,
            task=task,
            status=status,
            created_at=str(raw.get("created_at") or now_iso()),
            started_at=raw.get("started_at"),
            finished_at=raw.get("finished_at"),
            pid=raw.get("pid"),
            result=raw.get("result"),
            error=raw.get("error"),
            messages=messages,
            log_path=str(raw.get("log_path") or f".agents/{agent_id}.log"),
        )


class SubagentStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.warnings: list[str] = []

    def _path(self, agent_id: str) -> Path:
        if not AGENT_ID_RE.fullmatch(agent_id):
            raise SubagentStoreError(f"非法 agent id: {agent_id}")
        return self.root / f"{agent_id}.json"

    def create(self, task: str, agent_id: str | None = None) -> AgentRecord:
        task = task.strip()
        if not task:
            raise SubagentStoreError("任务不能为空")
        if len(task) > MAX_TASK_CHARS:
            raise SubagentStoreError(f"任务过长，最多 {MAX_TASK_CHARS} 字符")
        agent_id = agent_id or new_agent_id()
        path = self._path(agent_id)
        if path.exists():
            raise SubagentStoreError(f"agent 已存在: {agent_id}")
        record = AgentRecord(
            id=agent_id,
            task=task,
            log_path=f".agents/{agent_id}.log",
        )
        self.save(record)
        return record

    def save(self, record: AgentRecord) -> None:
        if record.status not in STATUSES:
            raise SubagentStoreError(f"非法 status: {record.status}")
        if not record.task.strip():
            raise SubagentStoreError("任务不能为空")
        path = self._path(record.id)
        tmp = self.root / f".{record.id}.{uuid.uuid4().hex}.tmp"
        try:
            tmp.write_text(
                json.dumps(record.to_dict(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(tmp, path)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise SubagentStoreError(f"保存 agent 失败: {record.id} ({exc})") from exc

    def load(self, agent_id: str) -> AgentRecord:
        path = self._path(agent_id)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            record = AgentRecord.from_dict(raw)
            if record.id != agent_id:
                raise ValueError("id 不一致")
            return record
        except FileNotFoundError as exc:
            raise SubagentStoreError(f"agent 不存在: {agent_id}") from exc
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise SubagentStoreError(f"agent 记录损坏: {agent_id} ({exc})") from exc

    def list_records(self) -> list[AgentRecord]:
        records: list[AgentRecord] = []
        for path in self.root.glob("*.json"):
            if path.name.startswith("."):
                continue
            try:
                records.append(self.load(path.stem))
            except SubagentStoreError as exc:
                self.warnings.append(str(exc))
        records.sort(key=lambda r: (r.created_at, r.id), reverse=True)
        return records

    def log_tail(self, agent_id: str, lines: int = 5) -> str:
        path = self.root / f"{agent_id}.log"
        if not path.exists():
            return ""
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return "\n".join(content.splitlines()[-lines:])
```

- [ ] **Step 4: 运行测试确认通过**

Run: `D:\Anaconda3\envs\rag\python.exe -m unittest -v test_subagent_store.py`

Expected: `Ran 7 tests ... OK`.

- [ ] **Step 5: 提交**

```bash
git add Langgraph/subagent_store.py Langgraph/test_subagent_store.py
git commit -m "feat: add subagent record store"
```

---

### Task 2: 终端表格和通知文本

**Files:**
- Create: `subagent_cli.py`
- Test: `test_subagent_cli.py`

- [ ] **Step 1: 写失败测试**

Create `test_subagent_cli.py`:

```python
from __future__ import annotations

import unittest

from subagent_cli import format_agent_notification, format_agent_table
from subagent_store import AgentRecord


class SubagentCliTests(unittest.TestCase):
    def _record(self, **kwargs):
        base = dict(
            id="agent-20261023-201500-a1b2",
            task="整理周报",
            status="succeeded",
            created_at="2026-10-23T20:15:00+08:00",
            started_at="2026-10-23T20:15:01+08:00",
            finished_at="2026-10-23T20:15:31+08:00",
            result="已生成周报.docx",
        )
        base.update(kwargs)
        return AgentRecord(**base)

    def test_empty_table(self):
        self.assertIn("没有子 agent", format_agent_table([]))

    def test_table_has_columns_and_values(self):
        table = format_agent_table([self._record()])
        self.assertIn("ID", table)
        self.assertIn("状态", table)
        self.assertIn("succeeded", table)
        self.assertIn("整理周报", table)
        self.assertIn("30s", table)
        self.assertIn("已生成周报.docx", table)

    def test_long_text_is_truncated(self):
        table = format_agent_table([self._record(task="很长" * 50, result="结果" * 100)])
        self.assertIn("…", table)

    def test_notification_success_and_failure(self):
        ok = format_agent_notification(self._record())
        self.assertIn("[子agent]", ok)
        self.assertIn("完成", ok)
        bad = format_agent_notification(
            self._record(status="failed", result=None, error="boom")
        )
        self.assertIn("失败", bad)
        self.assertIn("boom", bad)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `D:\Anaconda3\envs\rag\python.exe -m unittest -v test_subagent_cli.py`

Expected: FAIL with `ModuleNotFoundError: No module named 'subagent_cli'`.

- [ ] **Step 3: 实现格式化**

Create `subagent_cli.py`:

```python
from __future__ import annotations

from datetime import datetime

from subagent_store import AgentRecord

ID_WIDTH = 30
STATUS_WIDTH = 10
TASK_WIDTH = 20
DURATION_WIDTH = 8
RESULT_WIDTH = 34


def _short(text: str | None, width: int) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= width:
        return value
    return value[: max(0, width - 1)] + "…"


def _duration(record: AgentRecord) -> str:
    try:
        start = datetime.fromisoformat(record.started_at or record.created_at)
        end = (
            datetime.fromisoformat(record.finished_at)
            if record.finished_at
            else datetime.now().astimezone()
        )
        seconds = max(0, int((end - start).total_seconds()))
    except (TypeError, ValueError):
        return "-"
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    return f"{minutes}m{sec:02d}s"


def format_agent_table(records: list[AgentRecord]) -> str:
    if not records:
        return "[子agent] 当前没有子 agent。"
    lines = [
        f"{'ID':<{ID_WIDTH}} {'状态':<{STATUS_WIDTH}} "
        f"{'任务':<{TASK_WIDTH}} {'耗时':<{DURATION_WIDTH}} 结果"
    ]
    for record in records:
        result = record.result if record.status == "succeeded" else record.error
        lines.append(
            f"{record.id:<{ID_WIDTH}} {record.status:<{STATUS_WIDTH}} "
            f"{_short(record.task, TASK_WIDTH):<{TASK_WIDTH}} "
            f"{_duration(record):<{DURATION_WIDTH}} "
            f"{_short(result, RESULT_WIDTH)}"
        )
    return "\n".join(lines)


def format_agent_notification(record: AgentRecord) -> str:
    if record.status == "succeeded":
        return f"[子agent] {record.id} 完成: {_short(record.result, 100)}"
    if record.status == "failed":
        return f"[子agent] {record.id} 失败: {_short(record.error, 100)}"
    return ""
```

- [ ] **Step 4: 运行测试确认通过**

Run: `D:\Anaconda3\envs\rag\python.exe -m unittest -v test_subagent_cli.py`

Expected: `Ran 4 tests ... OK`.

- [ ] **Step 5: 提交**

```bash
git add Langgraph/subagent_cli.py Langgraph/test_subagent_cli.py
git commit -m "feat: add subagent terminal formatting"
```

---

### Task 3: 子进程管理器

**Files:**
- Create: `subagent_manager.py`
- Create: `subagent_fake_worker.py`
- Test: `test_subagent_manager.py`

- [ ] **Step 1: 写假 worker**

Create `subagent_fake_worker.py`:

```python
"""测试用假 worker：根据任务前缀写成功/失败，或直接崩溃。"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from subagent_store import SubagentStore, now_iso


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--project-root", required=True)
    args = parser.parse_args(argv)

    store = SubagentStore(Path(args.project_root) / ".agents")
    record = store.load(args.agent_id)
    record.pid = os.getpid()
    record.started_at = now_iso()
    store.save(record)

    if record.task.startswith("crash"):
        return 1
    if record.task.startswith("slow"):
        time.sleep(2.0)
    else:
        time.sleep(0.05)
    if record.task.startswith("fail"):
        record.status = "failed"
        record.error = "fake failure"
    else:
        record.status = "succeeded"
        record.result = f"done:{record.task}"
    record.finished_at = now_iso()
    store.save(record)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 写失败测试**

Create `test_subagent_manager.py`:

```python
from __future__ import annotations

import shutil
import time
import unittest
import uuid
from pathlib import Path

from subagent_manager import SubagentError, SubagentManager
from subagent_store import SubagentStore, now_iso


class SubagentManagerTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent / f".test_manager_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()
        self.worker = Path(__file__).resolve().parent / "subagent_fake_worker.py"
        self.manager = SubagentManager(self.root, worker_path=self.worker)

    def tearDown(self):
        self.manager.shutdown()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_spawn_and_wait_success(self):
        record = self.manager.spawn("整理周报")
        finished = self.manager.wait(record.id, timeout=5)
        self.assertEqual(finished.status, "succeeded")
        self.assertEqual(finished.result, "done:整理周报")
        self.assertTrue((self.root / ".agents" / f"{record.id}.json").exists())

    def test_failed_task(self):
        record = self.manager.spawn("fail 任务")
        finished = self.manager.wait(record.id, timeout=5)
        self.assertEqual(finished.status, "failed")
        self.assertIn("fake failure", finished.error or "")

    def test_crash_without_record_is_marked_failed(self):
        record = self.manager.spawn("crash 任务")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.manager.refresh()
            loaded = self.manager.store.load(record.id)
            if loaded.status == "failed":
                break
            time.sleep(0.05)
        self.assertEqual(loaded.status, "failed")
        self.assertIn("进程异常退出", loaded.error or "")

    def test_concurrency_limit(self):
        for i in range(3):
            self.manager.spawn(f"slow {i}")
        with self.assertRaises(SubagentError):
            self.manager.spawn("slow 4")

    def test_wait_timeout(self):
        record = self.manager.spawn("slow timeout")
        finished = self.manager.wait(record.id, timeout=1)
        self.assertEqual(finished.status, "running")

    def test_unknown_agent(self):
        with self.assertRaises(SubagentError):
            self.manager.wait("agent-20261023-201599-ffff")

    def test_notifications_only_once(self):
        record = self.manager.spawn("整理周报")
        self.manager.wait(record.id, timeout=5)
        first = self.manager.pop_notifications()
        self.assertTrue(any(record.id in item for item in first))
        self.assertEqual(self.manager.pop_notifications(), [])

    def test_startup_orphan_is_failed(self):
        store = SubagentStore(self.root / ".agents")
        orphan = store.create("遗留任务", agent_id="agent-20261023-201500-dead")
        orphan.status = "running"
        store.save(orphan)
        manager = SubagentManager(self.root, worker_path=self.worker)
        loaded = manager.store.load(orphan.id)
        self.assertEqual(loaded.status, "failed")
        self.assertIn("重启", loaded.error or "")
        manager.shutdown()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: 运行测试确认失败**

Run: `D:\Anaconda3\envs\rag\python.exe -m unittest -v test_subagent_manager.py`

Expected: FAIL with `ModuleNotFoundError: No module named 'subagent_manager'`.

- [ ] **Step 4: 实现 Manager**

Create `subagent_manager.py`:

```python
from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

from subagent_cli import format_agent_notification, format_agent_table
from subagent_store import AgentRecord, SubagentStore, SubagentStoreError, now_iso

MAX_RUNNING = 3
WAIT_DEFAULT = 180
WAIT_MAX = 600


class SubagentError(Exception):
    """子 agent 编排错误。"""


class SubagentManager:
    def __init__(
        self,
        project_root: Path,
        worker_path: Path | None = None,
        max_running: int = MAX_RUNNING,
        poll_interval: float = 0.5,
    ):
        self.project_root = Path(project_root)
        self.project_root.mkdir(parents=True, exist_ok=True)
        self.store = SubagentStore(self.project_root / ".agents")
        self.worker_path = Path(worker_path or self.project_root / "subagent_worker.py")
        self.max_running = max_running
        self.poll_interval = poll_interval
        self._processes: dict[str, subprocess.Popen] = {}
        self._notified: set[str] = set()
        self._notifications: list[str] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._fail_orphans()

    def _fail_orphans(self) -> None:
        for record in self.store.list_records():
            if record.status == "running":
                record.status = "failed"
                record.finished_at = now_iso()
                record.error = "主程序重启，无法确认子进程状态"
                self.store.save(record)

    def running_count(self) -> int:
        return sum(1 for r in self.store.list_records() if r.status == "running")

    def spawn(self, task: str) -> AgentRecord:
        task = task.strip()
        if not task:
            raise SubagentError("任务不能为空")
        if len(task) > 4000:
            raise SubagentError("任务过长，最多 4000 字符")
        if self.running_count() >= self.max_running:
            raise SubagentError(f"当前已有 {self.max_running} 个子 agent 在运行")

        record = self.store.create(task)
        log_path = self.project_root / ".agents" / f"{record.id}.log"
        try:
            with open(log_path, "a", encoding="utf-8") as log:
                creationflags = (
                    getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    if sys.platform == "win32"
                    else 0
                )
                process = subprocess.Popen(
                    [
                        sys.executable,
                        str(self.worker_path),
                        "--agent-id",
                        record.id,
                        "--project-root",
                        str(self.project_root),
                    ],
                    cwd=str(self.project_root),
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    creationflags=creationflags,
                )
        except OSError as exc:
            latest = self.store.load(record.id)
            latest.status = "failed"
            latest.finished_at = now_iso()
            latest.error = f"启动子进程失败: {exc}"
            self.store.save(latest)
            raise SubagentError(f"启动子进程失败: {exc}") from exc

        latest = self.store.load(record.id)
        if latest.pid is None:
            latest.pid = process.pid
            self.store.save(latest)
        self._processes[record.id] = process
        return latest

    def _mark_failed(self, record: AgentRecord, error: str) -> None:
        latest = self.store.load(record.id)
        if latest.status != "running":
            return
        latest.status = "failed"
        latest.finished_at = now_iso()
        latest.error = error
        self.store.save(latest)

    def refresh(self) -> list[AgentRecord]:
        records = self.store.list_records()
        for record in records:
            process = self._processes.get(record.id)
            if record.status == "running" and process is not None:
                code = process.poll()
                if code is not None:
                    tail = self.store.log_tail(record.id)
                    self._mark_failed(
                        record,
                        f"进程异常退出（code={code}）" + (f"\n{tail}" if tail else ""),
                    )
            latest = self.store.load(record.id)
            if latest.status in {"succeeded", "failed"}:
                self._processes.pop(record.id, None)
                with self._lock:
                    if latest.id not in self._notified:
                        self._notified.add(latest.id)
                        self._notifications.append(format_agent_notification(latest))
        return self.store.list_records()

    def list_records(self) -> list[AgentRecord]:
        self.refresh()
        return self.store.list_records()

    def format_table(self) -> str:
        return format_agent_table(self.list_records())

    def wait(self, agent_id: str, timeout: int = WAIT_DEFAULT) -> AgentRecord:
        try:
            record = self.store.load(agent_id)
        except SubagentStoreError as exc:
            raise SubagentError(str(exc)) from exc
        timeout = max(1, min(WAIT_MAX, int(timeout)))
        deadline = time.monotonic() + timeout
        while record.status == "running":
            if time.monotonic() >= deadline:
                return record
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
            self.refresh()
            record = self.store.load(agent_id)
        return record

    def pop_notifications(self) -> list[str]:
        with self._lock:
            items = list(self._notifications)
            self._notifications.clear()
            return items

    def start_monitor(self, output) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()

        def loop() -> None:
            while not self._stop.is_set():
                try:
                    self.refresh()
                except Exception as exc:  # noqa: BLE001
                    output(f"[错误] 子 agent 监控: {exc}")
                for item in self.pop_notifications():
                    output(item)
                self._stop.wait(self.poll_interval)

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

    def stop_monitor(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def shutdown(self) -> None:
        self.stop_monitor()
        self.refresh()
        for record in self.store.list_records():
            if record.status != "running":
                continue
            process = self._processes.pop(record.id, None)
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            self._mark_failed(record, "主程序退出，任务已终止")
```

- [ ] **Step 5: 运行测试确认通过**

Run: `D:\Anaconda3\envs\rag\python.exe -m unittest -v test_subagent_manager.py`

Expected: `Ran 8 tests ... OK`.

- [ ] **Step 6: 提交**

```bash
git add Langgraph/subagent_manager.py Langgraph/subagent_fake_worker.py Langgraph/test_subagent_manager.py
git commit -m "feat: add subagent process manager"
```

---

### Task 4: 主 agent 工具

**Files:**
- Create: `subagent_tools.py`
- Test: `test_subagent_tools.py`

- [ ] **Step 1: 写失败测试**

Create `test_subagent_tools.py`:

```python
from __future__ import annotations

import unittest

from langchain_core.messages import HumanMessage

from subagent_manager import SubagentError
from subagent_store import AgentRecord
from subagent_tools import build_subagent_tools


class FakeManager:
    def __init__(self, record: AgentRecord | None = None, error: str | None = None):
        self.record = record
        self.error = error
        self.spawned: list[str] = []

    def spawn(self, task: str) -> AgentRecord:
        if self.error:
            raise SubagentError(self.error)
        self.spawned.append(task)
        return self.record

    def format_table(self) -> str:
        return "[子agent] 当前没有子 agent。"

    def wait(self, agent_id: str, timeout: int = 180) -> AgentRecord:
        if self.error:
            raise SubagentError(self.error)
        return self.record


class SubagentToolsTests(unittest.TestCase):
    def _tools(self, manager):
        return {tool.name: tool for tool in build_subagent_tools(manager)}

    def test_spawn_agent_success(self):
        record = AgentRecord(id="agent-20261023-201500-a1b2", task="整理周报")
        tools = self._tools(FakeManager(record))
        result = tools["spawn_agent"].invoke({"task": "整理周报"})
        self.assertIn("[已创建]", result)
        self.assertIn(record.id, result)

    def test_spawn_agent_error(self):
        tools = self._tools(FakeManager(error="已满"))
        result = tools["spawn_agent"].invoke({"task": "x"})
        self.assertIn("[错误]", result)
        self.assertIn("已满", result)

    def test_list_agents(self):
        tools = self._tools(FakeManager())
        self.assertIn("没有子 agent", tools["list_agents"].invoke({}))

    def test_wait_agent_statuses(self):
        ok = AgentRecord(
            id="agent-20261023-201500-a1b2",
            task="t",
            status="succeeded",
            result="好了",
        )
        tools = self._tools(FakeManager(ok))
        self.assertIn("[完成]", tools["wait_agent"].invoke({"agent_id": ok.id}))

        failed = AgentRecord(
            id="agent-20261023-201500-a1b2",
            task="t",
            status="failed",
            error="boom",
        )
        tools = self._tools(FakeManager(failed))
        self.assertIn("[失败]", tools["wait_agent"].invoke({"agent_id": failed.id}))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `D:\Anaconda3\envs\rag\python.exe -m unittest -v test_subagent_tools.py`

Expected: FAIL with `ModuleNotFoundError: No module named 'subagent_tools'`.

- [ ] **Step 3: 实现工具**

Create `subagent_tools.py`:

```python
from __future__ import annotations

from langchain_core.tools import tool

from subagent_manager import SubagentError, SubagentManager


def build_subagent_tools(manager: SubagentManager) -> list:
    @tool
    def spawn_agent(task: str) -> str:
        """创建一个独立子 agent 执行给定任务。只在任务适合并行/委派时调用。"""
        try:
            record = manager.spawn(task)
        except SubagentError as exc:
            return f"[错误] {exc}"
        return f"[已创建] {record.id} | {record.task[:80]}"

    @tool
    def list_agents() -> str:
        """列出所有子 agent 的状态、任务和结果摘要。"""
        try:
            return manager.format_table()
        except Exception as exc:  # noqa: BLE001
            return f"[错误] 查询子 agent 失败: {exc}"

    @tool
    def wait_agent(agent_id: str, timeout: int = 180) -> str:
        """等待指定子 agent 完成并返回结果；timeout 为秒，默认 180。"""
        try:
            record = manager.wait(agent_id, timeout)
        except SubagentError as exc:
            return f"[错误] {exc}"
        if record.status == "succeeded":
            return f"[完成] {record.id} | {record.result or ''}"
        if record.status == "failed":
            return f"[失败] {record.id} | {record.error or ''}"
        return f"[超时] {record.id} 仍在运行 | 任务: {record.task[:80]}"

    return [spawn_agent, list_agents, wait_agent]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `D:\Anaconda3\envs\rag\python.exe -m unittest -v test_subagent_tools.py`

Expected: `Ran 4 tests ... OK`.

- [ ] **Step 5: 提交**

```bash
git add Langgraph/subagent_tools.py Langgraph/test_subagent_tools.py
git commit -m "feat: add subagent orchestration tools"
```

---

### Task 5: 子进程 worker 与图构建

**Files:**
- Create: `subagent_worker.py`
- Modify: `graph_agent.py:746-760` (`build_graph` 支持子 agent 工具)
- Test: `test_subagent_worker.py`

- [ ] **Step 1: 写失败测试**

Create `test_subagent_worker.py`:

```python
from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from subagent_store import SubagentStore
from subagent_worker import _last_ai_text, main


class FakeGraph:
    def invoke(self, state, config):
        return {
            "messages": [
                *state["messages"],
                AIMessage(content="任务完成"),
            ]
        }


class SubagentWorkerTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent / f".test_worker_{uuid.uuid4().hex[:8]}"
        self.root.mkdir()
        self.store = SubagentStore(self.root / ".agents")
        self.record = self.store.create("写个报告")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_last_ai_text(self):
        messages = [HumanMessage(content="x"), AIMessage(content="结果")]
        self.assertEqual(_last_ai_text(messages), "结果")

    def test_main_success(self):
        with patch("subagent_worker.build_graph", return_value=FakeGraph()):
            code = main(["--agent-id", self.record.id, "--project-root", str(self.root)])
        self.assertEqual(code, 0)
        loaded = self.store.load(self.record.id)
        self.assertEqual(loaded.status, "succeeded")
        self.assertEqual(loaded.result, "任务完成")
        self.assertEqual(loaded.messages[-1].content, "任务完成")

    def test_main_failure(self):
        with patch("subagent_worker.build_graph", side_effect=RuntimeError("boom")):
            code = main(["--agent-id", self.record.id, "--project-root", str(self.root)])
        self.assertEqual(code, 1)
        loaded = self.store.load(self.record.id)
        self.assertEqual(loaded.status, "failed")
        self.assertIn("boom", loaded.error or "")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `D:\Anaconda3\envs\rag\python.exe -m unittest -v test_subagent_worker.py`

Expected: FAIL with `ModuleNotFoundError: No module named 'subagent_worker'`.

- [ ] **Step 3: 修改 `build_graph`**

In `graph_agent.py`, replace the existing `def build_graph():` block with:

```python
def build_graph(manager=None, include_subagent_tools: bool = True):
    """构建并编译 Agent 图：agent -> (tools -> agent)* -> END。"""
    tools = list(TOOLS)
    if include_subagent_tools and manager is not None:
        from subagent_tools import build_subagent_tools

        tools = tools + build_subagent_tools(manager)

    llm_with_tools = build_llm().bind_tools(tools)

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
```

- [ ] **Step 4: 实现 worker**

Create `subagent_worker.py`:

```python
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from graph_agent import build_graph
from subagent_store import SubagentStore, now_iso

SUBAGENT_SYSTEM_PROMPT = (
    "你是一个子 agent，只负责完成用户交给你的一个任务。"
    "你可以使用提供的文件和 Office 工具；完成后用简洁中文给出结果。"
    "你不能创建子 agent，也不要要求用户继续输入。"
)


def _last_ai_text(messages) -> str:
    for message in reversed(messages):
        if getattr(message, "type", None) != "ai":
            continue
        content = message.content
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            )
        return str(content).strip()
    return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--project-root", required=True)
    args = parser.parse_args(argv)

    store = SubagentStore(Path(args.project_root) / ".agents")
    record = store.load(args.agent_id)
    record.pid = os.getpid()
    record.started_at = now_iso()
    store.save(record)

    try:
        graph = build_graph(include_subagent_tools=False)
        result = graph.invoke(
            {
                "messages": [
                    SystemMessage(content=SUBAGENT_SYSTEM_PROMPT),
                    HumanMessage(content=record.task),
                ]
            },
            config={"recursion_limit": 25},
        )
        messages = [
            message
            for message in result["messages"]
            if not isinstance(message, SystemMessage)
        ]
        record.messages = messages
        record.result = _last_ai_text(messages)
        record.status = "succeeded"
        code = 0
    except Exception as exc:  # noqa: BLE001
        record.status = "failed"
        record.error = f"{type(exc).__name__}: {exc}"
        code = 1
    finally:
        record.finished_at = now_iso()
        store.save(record)
    return code


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: 运行测试确认通过**

Run: `D:\Anaconda3\envs\rag\python.exe -m unittest -v test_subagent_worker.py`

Expected: `Ran 3 tests ... OK`.

- [ ] **Step 6: 提交**

```bash
git add Langgraph/graph_agent.py Langgraph/subagent_worker.py Langgraph/test_subagent_worker.py
git commit -m "feat: add subagent worker process"
```

---

### Task 6: 主循环、`/agents` 和主程序集成

**Files:**
- Modify: `session_cli.py:24`、`:26-34`
- Modify: `graph_agent.py:810-995`
- Modify: `test_session_flow.py`
- Modify: `.gitignore`

- [ ] **Step 1: 增加 `/agents` 命令**

In `session_cli.py`, change:

```python
KNOWN_COMMANDS = {"new", "list", "sessions", "switch", "delete", "clear", "history", "help"}
```

to:

```python
KNOWN_COMMANDS = {
    "new",
    "list",
    "sessions",
    "switch",
    "delete",
    "clear",
    "history",
    "agents",
    "help",
}
```

And add one line to `HELP_TEXT` before `/help`:

```text
  /agents              查看子 agent 状态
```

- [ ] **Step 2: 写 `/agents` 集成测试**

Append to `test_session_flow.py` (inside `SessionFlowTests`):

```python
    def test_agents_command_without_manager(self):
        self._run(["/agents", "exit"])
        self.assertIn("没有子 agent", "\n".join(self.outputs))
```

- [ ] **Step 3: 运行测试确认失败**

Run: `D:\Anaconda3\envs\rag\python.exe -m unittest -v test_session_flow.SessionFlowTests.test_agents_command_without_manager`

Expected: FAIL because `/agents` is unknown and prints `[命令] 未知命令`.

- [ ] **Step 4: 接入主循环**

In `graph_agent.py`:

1. Add import near the other local imports:

```python
from subagent_manager import SubagentManager
```

2. Rename the existing `def run_chat_loop(` to:

```python
def _run_chat_loop_inner(
```

and add `manager=None` to its signature (keep all existing parameters).

3. Inside the command block, after the `history` branch, add:

```python
                elif name == "agents":
                    output(
                        manager.format_table()
                        if manager is not None
                        else "[子agent] 当前没有子 agent。"
                    )
```

4. After `_run_chat_loop_inner` ends, add the public wrapper:

```python
def run_chat_loop(
    store,
    graph,
    session,
    input_fn=input,
    output=print,
    manager=None,
) -> None:
    if manager is None:
        return _run_chat_loop_inner(store, graph, session, input_fn, output, None)
    manager.start_monitor(output)
    try:
        return _run_chat_loop_inner(store, graph, session, input_fn, output, manager)
    finally:
        manager.shutdown()
```

- [ ] **Step 5: 追加子 agent 提示词**

在 `graph_agent.py` 的 `SYSTEM_PROMPT` 定义之后加入：

```python
SYSTEM_PROMPT += (
    "\n\n子 agent 编排：当任务可以拆成多个独立子任务时，"
    "用 spawn_agent 创建子 agent；用 list_agents 查看状态；"
    "用 wait_agent 等待结果。不要为简单任务创建子 agent。"
)
```

- [ ] **Step 6: 修改 `main()`**

将：

```python
    graph = build_graph()
    run_chat_loop(store, graph, session)
```

改为：

```python
    manager = SubagentManager(project_root=Path(__file__).resolve().parent)
    graph = build_graph(manager=manager)
    run_chat_loop(store, graph, session, manager=manager)
```

- [ ] **Step 7: 更新 `.gitignore`**

Ensure `Langgraph/.gitignore` contains:

```text
.sessions/
.agents/
```

- [ ] **Step 8: 运行测试确认通过**

Run:

```bash
D:\Anaconda3\envs\rag\python.exe -m unittest -v test_session_flow.py
D:\Anaconda3\envs\rag\python.exe -m unittest discover
```

Expected: `test_session_flow` 5 tests OK；full suite `Ran 122 tests ... OK`（95 个旧测试 + 27 个新增）。

- [ ] **Step 9: 手动 smoke**

```bash
D:\Anaconda3\envs\rag\python.exe graph_agent.py
```

在终端输入：

```text
请创建两个子 agent：一个整理“项目周报”，一个“生成销售数据表”。然后查看 /agents。
```

Expected:

- 主 agent 调用 `spawn_agent` 两次。
- `/agents` 显示至少两个 `running`/`succeeded`。
- 完成后出现 `[子agent] ... 完成` 通知。
- `.agents/` 下有两个 JSON 和日志。

- [ ] **Step 10: 提交**

```bash
git add Langgraph/graph_agent.py Langgraph/session_cli.py Langgraph/test_session_flow.py Langgraph/.gitignore
git commit -m "feat: integrate subagent dashboard into chat loop"
```

---

## 自检清单

- [ ] 每个任务都能独立运行测试。
- [ ] 所有新增文件路径与计划一致。
- [ ] `spawn_agent` / `list_agents` / `wait_agent` 的名称在工具、提示词、测试中一致。
- [ ] 状态只使用 `running` / `succeeded` / `failed`。
- [ ] 并发上限 3 在 Manager 和测试中都覆盖。
- [ ] 主进程退出会终止子进程并写失败状态。
- [ ] `.agents/` 不进入 git。
- [ ] 现有 95 个测试保持通过。
