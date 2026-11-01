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
