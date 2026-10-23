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
                if process is not None and process.poll() is None:
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        pass
                if process is None or process.poll() is not None:
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
