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
STATUSES = {"running", "succeeded", "failed", "cancelled"}
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
    session_id: str = ""
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
            "session_id": self.session_id,
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
            session_id=str(raw.get("session_id") or ""),
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

    def create(
        self, task: str, agent_id: str | None = None, session_id: str = ""
    ) -> AgentRecord:
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
            session_id=session_id or "",
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
