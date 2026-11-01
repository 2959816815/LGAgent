"""Build model-specific context views and maintain rolling summaries.

The session transcript is append-only. This module never deletes messages; it
only derives the message list that is sent to a model.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable, Sequence

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from session_store import sanitize_messages

TOKEN_SAFETY = 1.15
MESSAGE_OVERHEAD = 4
IMAGE_TOKEN_ESTIMATE = 1000
MIN_KEEP_TURNS = 2
MAX_SUMMARY_CHUNK_TOKENS = 6000
MAX_SUMMARY_CALLS = 3
SUMMARY_PREFIX = "【较早对话的摘要】"
TRUNCATE_MARKER = "\n…[内容过长已截断]"
SUMMARY_TRUNCATE_MARKER = "…[摘要已截断]"

_CJK_RANGES = (
    (0x3000, 0x303F),
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF),
    (0xFF00, 0xFFEF),
    (0x20000, 0x2FA1F),
)
_tool_token_cache: dict[tuple[str, ...], int] = {}


class ContextBudgetError(RuntimeError):
    """The model window cannot fit the system prompt and tools."""


class SummaryError(RuntimeError):
    """Summary generation failed."""


def _is_cjk(char: str) -> bool:
    code = ord(char)
    return any(low <= code <= high for low, high in _CJK_RANGES)


def _text_units(text: str) -> float:
    units = 0.0
    for char in str(text or ""):
        if _is_cjk(char):
            units += 1.0
        elif char.isascii() and (char.isalnum() or char.isspace()):
            units += 0.25
        else:
            units += 0.5
    return units


def estimate_text_tokens(text: str) -> int:
    return math.ceil(_text_units(text) * TOKEN_SAFETY)


def _content_text(content) -> str:
    if isinstance(content, list):
        return "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    return str(content or "")


def _image_count(message: BaseMessage) -> int:
    content = getattr(message, "content", "")
    if not isinstance(content, list):
        return 0
    return sum(
        1
        for part in content
        if isinstance(part, dict) and part.get("type") in {"image_url", "image_ref"}
    )


def estimate_tokens(messages: Iterable[BaseMessage]) -> int:
    units = 0.0
    for message in messages:
        units += _text_units(_content_text(getattr(message, "content", "")))
        units += _image_count(message) * IMAGE_TOKEN_ESTIMATE
        units += MESSAGE_OVERHEAD
    return math.ceil(units * TOKEN_SAFETY)


def truncate_to_tokens(
    text: str, limit: int, marker: str = TRUNCATE_MARKER
) -> str:
    text = str(text or "")
    if limit <= 0:
        return ""
    if estimate_text_tokens(text) <= limit:
        return text
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if estimate_text_tokens(text[:mid] + marker) <= limit:
            low = mid
        else:
            high = mid - 1
    return text[:low] + marker


def compute_budget(window: int, system_tokens: int, tool_tokens: int) -> dict:
    output_reserve = max(512, min(int(window * 0.15), 4096))
    safety_margin = int(window * 0.10)
    conversation = window - system_tokens - tool_tokens - output_reserve - safety_margin
    if conversation <= 0:
        raise ContextBudgetError(
            f"上下文预算不足：窗口 {window}，系统提示 {system_tokens}，"
            f"工具 {tool_tokens}，输出预留 {output_reserve}，余量 {safety_margin}"
        )
    return {
        "window": window,
        "system_tokens": system_tokens,
        "tool_tokens": tool_tokens,
        "output_reserve": output_reserve,
        "safety_margin": safety_margin,
        "conversation": conversation,
        "summary_cap": max(400, min(int(conversation * 0.25), 2000)),
        "tail_target": int(conversation * 0.75),
        "max_message": max(512, int(conversation * 0.40)),
    }


@dataclass(frozen=True)
class ContextStats:
    raw_tokens: int
    view_tokens: int
    window: int
    tool_tokens: int
    system_tokens: int
    summary_tokens: int
    recent_turns: int
    dropped_turns: int
    images_dropped: int
    fits: bool

    def as_dict(self) -> dict:
        return {
            "raw_tokens": self.raw_tokens,
            "view_tokens": self.view_tokens,
            "window": self.window,
            "tool_tokens": self.tool_tokens,
            "system_tokens": self.system_tokens,
            "summary_tokens": self.summary_tokens,
            "recent_turns": self.recent_turns,
            "dropped_turns": self.dropped_turns,
            "images_dropped": self.images_dropped,
            "fits": self.fits,
        }


def split_turns(messages: Sequence[BaseMessage]) -> list[list[BaseMessage]]:
    turns: list[list[BaseMessage]] = []
    for message in messages:
        if isinstance(message, HumanMessage) and turns:
            turns.append([message])
        elif not turns:
            turns.append([message])
        else:
            turns[-1].append(message)
    return turns


def _copy_message(message: BaseMessage, content) -> BaseMessage:
    if hasattr(message, "model_copy"):
        return message.model_copy(update={"content": content})
    return message.copy(update={"content": content})


def _replace_images(message: BaseMessage) -> tuple[BaseMessage, int]:
    content = getattr(message, "content", "")
    if not isinstance(content, list):
        return message, 0
    parts: list = []
    dropped = 0
    for part in content:
        if isinstance(part, dict) and part.get("type") in {"image_url", "image_ref"}:
            parts.append({"type": "text", "text": "[图片已省略]"})
            dropped += 1
        else:
            parts.append(part)
    if not dropped:
        return message, 0
    return _copy_message(message, parts), dropped


def _truncate_message(message: BaseMessage, max_tokens: int) -> BaseMessage:
    content = getattr(message, "content", "")
    if isinstance(content, list):
        remaining = max_tokens
        parts: list = []
        changed = False
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text = str(part.get("text", ""))
                cut = truncate_to_tokens(text, remaining)
                if cut != text:
                    changed = True
                parts.append({**part, "text": cut})
                remaining = max(0, remaining - estimate_text_tokens(cut))
            else:
                parts.append(part)
        return _copy_message(message, parts) if changed else message
    text = str(content)
    cut = truncate_to_tokens(text, max_tokens)
    return _copy_message(message, cut) if cut != text else message


def measure_tool_tokens(tools: Iterable) -> int:
    tools = list(tools)
    key = tuple(sorted(getattr(tool, "name", "") for tool in tools))
    cached = _tool_token_cache.get(key)
    if cached is not None:
        return cached
    try:
        from langchain_core.utils.function_calling import convert_to_openai_tool

        payload = [convert_to_openai_tool(tool) for tool in tools]
        value = estimate_text_tokens(json.dumps(payload, ensure_ascii=False))
    except Exception:  # noqa: BLE001
        value = 2000
    _tool_token_cache[key] = value
    return value


def build_view(
    session,
    profile,
    system_prompt: str,
    tool_tokens: int,
    hydrate: Callable[[list[BaseMessage]], list[BaseMessage]] | None = None,
) -> tuple[list[BaseMessage], ContextStats]:
    messages = list(getattr(session, "messages", []) or [])
    system_tokens = estimate_text_tokens(system_prompt) + MESSAGE_OVERHEAD
    budget = compute_budget(profile.context_window, system_tokens, tool_tokens)
    raw_tokens = estimate_tokens(messages)
    # 摘要已经覆盖的消息不再原样发给模型，避免摘要与原文重复占用窗口。
    coverage = int(getattr(session, "summary_upto", 0) or 0)
    if coverage < 0 or coverage >= len(messages):
        coverage = 0
    uncovered = messages[coverage:]

    def prepare(message: BaseMessage) -> tuple[BaseMessage, int]:
        if profile.supports_vision:
            return message, 0
        return _replace_images(message)

    full_prepared: list[BaseMessage] = []
    images_dropped = 0
    for message in messages:
        prepared_message, dropped = prepare(message)
        images_dropped += dropped
        full_prepared.append(prepared_message)

    selected: list[list[BaseMessage]] = []
    dropped_turns = 0
    summary_message = None
    summary_tokens = 0
    if estimate_tokens(full_prepared) <= budget["conversation"]:
        # 大窗口模型能装下完整历史时优先发原文，摘要不参与视图。
        selected = [list(turn) for turn in split_turns(full_prepared)]
    else:
        summary_text = truncate_to_tokens(
            getattr(session, "summary", "") or "",
            budget["summary_cap"],
            SUMMARY_TRUNCATE_MARKER,
        )
        summary_message = (
            SystemMessage(content=f"{SUMMARY_PREFIX}\n{summary_text}")
            if summary_text
            else None
        )
        summary_tokens = estimate_tokens([summary_message]) if summary_message else 0
        remaining = budget["conversation"] - summary_tokens
        turns = split_turns(uncovered)
        images_dropped = 0
        for turn in reversed(turns):
            prepared_turn = []
            for message in turn:
                prepared_message, dropped = prepare(message)
                images_dropped += dropped
                prepared_turn.append(prepared_message)
            cost = estimate_tokens(prepared_turn)
            if cost > remaining:
                break
            selected.insert(0, prepared_turn)
            remaining -= cost
        if not selected and turns:
            prepared_turn = []
            for message in turns[-1]:
                prepared_message, dropped = prepare(message)
                images_dropped += dropped
                prepared_turn.append(
                    _truncate_message(prepared_message, budget["max_message"])
                )
            selected = [prepared_turn]
        dropped_turns = max(0, len(turns) - len(selected))

    view: list[BaseMessage] = [SystemMessage(content=system_prompt)]
    if summary_message is not None:
        view.append(summary_message)
    for turn in selected:
        view.extend(turn)
    view = sanitize_messages(view)
    if profile.supports_vision and hydrate is not None:
        view = hydrate(view)
    view_tokens = estimate_tokens(view)
    stats = ContextStats(
        raw_tokens=raw_tokens,
        view_tokens=view_tokens,
        window=profile.context_window,
        tool_tokens=tool_tokens,
        system_tokens=system_tokens,
        summary_tokens=summary_tokens,
        recent_turns=len(selected),
        dropped_turns=dropped_turns,
        images_dropped=images_dropped,
        fits=view_tokens <= profile.context_window,
    )
    return view, stats


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _turn_starts(messages: Sequence[BaseMessage]) -> list[int]:
    starts = [0]
    for index, message in enumerate(messages):
        if index > 0 and isinstance(message, HumanMessage):
            starts.append(index)
    return starts


def _summary_prompt(previous: str, chunk: Sequence[BaseMessage], summary_cap: int) -> str:
    lines: list[str] = []
    for message in chunk:
        role = {"human": "用户", "ai": "助手", "tool": "工具"}.get(
            getattr(message, "type", ""), "消息"
        )
        text = _content_text(getattr(message, "content", "")).strip()
        if text:
            lines.append(f"{role}: {text}")
    transcript = "\n".join(lines)
    return (
        "请把下面这段新增对话合并进已有摘要，输出一份简洁的中文摘要。\n"
        "保留关键事实、结论、涉及的文件路径和未完成事项；不要编造内容。\n"
        f"摘要控制在约 {max(200, summary_cap)} 个汉字以内。\n\n"
        f"已有摘要：\n{previous or '（无）'}\n\n"
        f"新增对话：\n{transcript}"
    )


def ensure_summary(
    session,
    active_profile,
    target_profile,
    system_prompt: str,
    tool_tokens: int,
    build_model: Callable,
    save_session: Callable,
    force: bool = False,
) -> bool:
    """Summarize older turns into ``session.summary`` without deleting messages.

    ``target_profile`` is the small model the summary must be ready for; the
    summary is generated with ``active_profile`` (the cloud model).
    """
    messages = list(getattr(session, "messages", []) or [])
    system_tokens = estimate_text_tokens(system_prompt) + MESSAGE_OVERHEAD
    budget = compute_budget(target_profile.context_window, system_tokens, tool_tokens)
    tail_target = budget["tail_target"]
    summary_cap = budget["summary_cap"]

    start = int(getattr(session, "summary_upto", 0) or 0)
    if start < 0 or start > len(messages):
        start = 0
    if len(messages) - start <= MIN_KEEP_TURNS:
        return False

    starts = _turn_starts(messages)
    turn_count = len(starts) if messages else 0
    keep = min(MIN_KEEP_TURNS, turn_count)
    cutoff: int | None = None
    if force:
        cutoff = starts[turn_count - keep] if turn_count > keep else starts[0]
    else:
        if estimate_tokens(messages[start:]) <= tail_target:
            return False
        for boundary in starts:
            if boundary < start:
                continue
            if estimate_tokens(messages[boundary:]) <= tail_target:
                cutoff = boundary
                break
        if cutoff is None:
            cutoff = starts[turn_count - keep] if turn_count > keep else starts[0]
    cutoff = max(start, min(cutoff, len(messages)))
    gap = messages[start:cutoff]
    if not gap:
        return False

    chunks: list[list[BaseMessage]] = []
    current: list[BaseMessage] = []
    current_tokens = 0
    for message in gap:
        cost = estimate_tokens([message])
        if current and current_tokens + cost > MAX_SUMMARY_CHUNK_TOKENS:
            chunks.append(current)
            current = []
            current_tokens = 0
        current.append(message)
        current_tokens += cost
    if current:
        chunks.append(current)

    summary = getattr(session, "summary", "") or ""
    covered = start
    calls = 0
    for chunk in chunks:
        if calls >= MAX_SUMMARY_CALLS:
            break
        try:
            response = build_model(active_profile.key).invoke(
                [HumanMessage(content=_summary_prompt(summary, chunk, summary_cap))]
            )
        except Exception as exc:  # noqa: BLE001
            if covered > start:
                session.summary = summary
                session.summary_upto = covered
                session.summary_count = covered
                session.summary_updated_at = _now_iso()
                save_session(session)
            raise SummaryError(str(exc)) from exc
        text = _content_text(getattr(response, "content", "")).strip()
        if not text:
            raise SummaryError("模型返回了空摘要")
        combined = f"{summary}\n{text}".strip() if summary else text
        summary = truncate_to_tokens(combined, summary_cap, SUMMARY_TRUNCATE_MARKER)
        covered += len(chunk)
        calls += 1
    if covered == start:
        return False

    session.summary = summary
    session.summary_upto = covered
    session.summary_count = covered
    session.summary_updated_at = _now_iso()
    save_session(session)
    return True
