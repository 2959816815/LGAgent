from __future__ import annotations

import unittest

from langchain_core.messages import HumanMessage

from context_builder import (
    ContextBudgetError,
    TRUNCATE_MARKER,
    compute_budget,
    estimate_text_tokens,
    estimate_tokens,
    truncate_to_tokens,
)


class TokenEstimateTests(unittest.TestCase):
    def test_chinese_is_about_one_token_per_character(self):
        tokens = estimate_text_tokens("字" * 400)

        self.assertGreaterEqual(tokens, 400)
        self.assertLessEqual(tokens, 500)

    def test_ascii_is_about_four_chars_per_token(self):
        tokens = estimate_text_tokens("a" * 400)

        self.assertGreaterEqual(tokens, 100)
        self.assertLessEqual(tokens, 130)

    def test_message_overhead_is_counted(self):
        tokens = estimate_tokens([HumanMessage(content="hi")])

        self.assertGreater(tokens, 0)

    def test_images_are_counted(self):
        text_only = estimate_tokens([HumanMessage(content="字" * 100)])
        with_image = estimate_tokens(
            [
                HumanMessage(
                    content=[
                        {"type": "text", "text": "字" * 100},
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/png;base64,AA"},
                        },
                    ]
                )
            ]
        )

        self.assertGreaterEqual(with_image - text_only, 1000)


class BudgetTests(unittest.TestCase):
    def test_16k_local_budget_leaves_room_for_conversation(self):
        budget = compute_budget(16384, 620, 3200)

        self.assertGreater(budget["conversation"], 7000)
        self.assertLess(budget["conversation"], 10000)
        self.assertEqual(budget["max_message"], 3387)

    def test_tiny_window_raises(self):
        with self.assertRaises(ContextBudgetError):
            compute_budget(4096, 620, 5700)


class TruncateTests(unittest.TestCase):
    def test_truncation_respects_the_limit_and_marks_the_text(self):
        cut = truncate_to_tokens("字" * 1000, 100)

        self.assertLessEqual(estimate_text_tokens(cut), 100)
        self.assertTrue(cut.endswith(TRUNCATE_MARKER))

    def test_short_text_is_unchanged(self):
        self.assertEqual(truncate_to_tokens("短文本", 100), "短文本")


from types import SimpleNamespace

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

from context_builder import SUMMARY_PREFIX, build_view, measure_tool_tokens, split_turns


def profile(window=16384, vision=False):
    return SimpleNamespace(context_window=window, supports_vision=vision)


def session(messages, summary=""):
    return SimpleNamespace(messages=messages, summary=summary, summary_upto=0)


class ViewTests(unittest.TestCase):
    def test_view_keeps_the_newest_turn_and_drops_whole_old_turns(self):
        messages = []
        for index in range(40):
            factory = HumanMessage if index % 2 == 0 else AIMessage
            messages.append(factory(content="内容" * 400))

        view, stats = build_view(session(messages), profile(), "系统" * 100, 0)

        self.assertGreater(stats.dropped_turns, 0)
        self.assertLessEqual(stats.view_tokens, 16384)
        self.assertIsInstance(view[0], SystemMessage)
        self.assertIsInstance(view[-1], AIMessage)

    def test_tool_call_and_result_stay_together(self):
        messages = [
            HumanMessage(content="读文件"),
            AIMessage(
                content="",
                tool_calls=[{"id": "call-1", "name": "read_file", "args": {}}],
            ),
            ToolMessage(content="内容", tool_call_id="call-1", name="read_file"),
            AIMessage(content="完成"),
        ]

        view, _ = build_view(session(messages), profile(), "系统", 0)

        tool_index = next(
            index for index, message in enumerate(view) if isinstance(message, ToolMessage)
        )
        self.assertIsInstance(view[tool_index - 1], AIMessage)
        self.assertTrue(view[tool_index - 1].tool_calls)

    def test_summary_is_injected_and_capped_when_history_does_not_fit(self):
        messages = [
            (HumanMessage if index % 2 == 0 else AIMessage)(content="内容" * 400)
            for index in range(20)
        ]
        covered = SimpleNamespace(
            messages=messages,
            summary="摘要" * 5000,
            summary_upto=18,
        )

        view, stats = build_view(covered, profile(window=8192), "系统", 0)

        self.assertIn(SUMMARY_PREFIX, str(view[1].content))
        self.assertGreater(stats.summary_tokens, 0)
        self.assertLessEqual(stats.summary_tokens, stats.window)

    def test_full_history_is_used_when_it_fits(self):
        messages = [
            (HumanMessage if index % 2 == 0 else AIMessage)(content=f"msg-{index}")
            for index in range(10)
        ]
        covered = SimpleNamespace(messages=messages, summary="摘要", summary_upto=8)

        view, stats = build_view(covered, profile(), "系统", 0)

        contents = [str(message.content) for message in view]
        self.assertTrue(any("msg-0" in text for text in contents))
        self.assertTrue(any("msg-9" in text for text in contents))
        self.assertEqual(stats.summary_tokens, 0)
        self.assertEqual(stats.dropped_turns, 0)

    def test_summarized_messages_are_not_sent_again_when_history_does_not_fit(self):
        messages = [
            (HumanMessage if index % 2 == 0 else AIMessage)(
                content=f"msg-{index} " + "内容" * 400
            )
            for index in range(40)
        ]
        covered = SimpleNamespace(messages=messages, summary="摘要", summary_upto=30)

        view, stats = build_view(covered, profile(), "系统", 0)

        contents = [str(message.content) for message in view]
        self.assertFalse(any("msg-0 " in text or "msg-20 " in text for text in contents))
        self.assertTrue(any("msg-30 " in text for text in contents))
        self.assertTrue(any("msg-39 " in text for text in contents))
        self.assertGreater(stats.summary_tokens, 0)

    def test_images_are_replaced_for_non_vision_models(self):
        messages = [
            HumanMessage(
                content=[
                    {"type": "text", "text": "看图"},
                    {"type": "image_ref", "attachment_id": "a1"},
                ]
            )
        ]

        view, stats = build_view(session(messages), profile(vision=False), "系统", 0)

        self.assertEqual(stats.images_dropped, 1)
        self.assertIn("[图片已省略]", str(view[-1].content))

    def test_images_are_kept_for_vision_models(self):
        messages = [
            HumanMessage(
                content=[
                    {"type": "text", "text": "看图"},
                    {"type": "image_ref", "attachment_id": "a1"},
                ]
            )
        ]

        view, stats = build_view(
            session(messages), profile(vision=True), "系统", 0,
            hydrate=lambda items: items,
        )

        self.assertEqual(stats.images_dropped, 0)
        self.assertIsInstance(view[-1].content, list)

    def test_oversized_newest_turn_is_truncated(self):
        messages = [HumanMessage(content="字" * 100000)]

        view, stats = build_view(session(messages), profile(), "系统", 0)

        self.assertIn("内容过长已截断", str(view[-1].content))
        self.assertLessEqual(stats.view_tokens, 16384)

    def test_split_turns_starts_a_new_turn_at_each_human_message(self):
        turns = split_turns(
            [
                HumanMessage(content="a"),
                AIMessage(content="b"),
                HumanMessage(content="c"),
                AIMessage(content="d"),
            ]
        )

        self.assertEqual(len(turns), 2)
        self.assertEqual(len(turns[0]), 2)


class ToolTokenTests(unittest.TestCase):
    def test_schema_size_is_measured_and_cached(self):
        class FakeTool:
            name = "fake"
            description = "说明" * 50
            args = {}

        first = measure_tool_tokens([FakeTool()])
        second = measure_tool_tokens([FakeTool()])

        self.assertGreater(first, 0)
        self.assertEqual(first, second)


from context_builder import SummaryError, ensure_summary


class FakeModel:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        if not self.replies:
            raise RuntimeError("没有更多回复")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return AIMessage(content=reply)


def big_session(count=30, size=400):
    messages = []
    for index in range(count):
        factory = HumanMessage if index % 2 == 0 else AIMessage
        messages.append(factory(content="内容" * size))
    session_obj = SimpleNamespace(
        messages=messages,
        summary="",
        summary_upto=0,
        summary_count=0,
        summary_updated_at="",
    )
    return session_obj


def local_profile(window=16384):
    return SimpleNamespace(context_window=window, supports_vision=False, key="local", provider="ollama")


def cloud_profile():
    return SimpleNamespace(context_window=128000, supports_vision=False, key="deepseek", provider="deepseek")


class SummaryMaintenanceTests(unittest.TestCase):
    def test_short_history_is_not_summarized(self):
        session_obj = big_session(count=4)
        model = FakeModel(["摘要"])

        changed = ensure_summary(
            session_obj, cloud_profile(), local_profile(), "系统", 0,
            lambda key: model, lambda session: None,
        )

        self.assertFalse(changed)
        self.assertEqual(model.calls, 0)

    def test_summary_advances_to_a_turn_boundary_and_keeps_two_turns(self):
        session_obj = big_session()
        model = FakeModel(["摘要"] * 5)

        changed = ensure_summary(
            session_obj, cloud_profile(), local_profile(), "系统", 0,
            lambda key: model, lambda session: None,
        )

        self.assertTrue(changed)
        self.assertGreater(session_obj.summary_upto, 0)
        self.assertEqual(session_obj.summary_upto % 2, 0)
        self.assertGreaterEqual(len(session_obj.messages) - session_obj.summary_upto, 4)

    def test_large_gap_is_chunked_and_merged(self):
        session_obj = big_session(count=40, size=900)
        model = FakeModel(["第一段", "第二段", "第三段"])

        changed = ensure_summary(
            session_obj, cloud_profile(), local_profile(), "系统", 0,
            lambda key: model, lambda session: None,
        )

        self.assertTrue(changed)
        self.assertGreaterEqual(model.calls, 2)
        self.assertIn("第一段", session_obj.summary)

    def test_summary_is_capped(self):
        session_obj = big_session()
        model = FakeModel(["长" * 100000] * 5)

        ensure_summary(
            session_obj, cloud_profile(), local_profile(), "系统", 0,
            lambda key: model, lambda session: None,
        )

        self.assertLessEqual(
            estimate_text_tokens(session_obj.summary),
            2000 + estimate_text_tokens("…[摘要已截断]"),
        )

    def test_failure_raises_and_keeps_previous_summary(self):
        session_obj = big_session()
        session_obj.summary = "旧摘要"

        with self.assertRaises(SummaryError):
            ensure_summary(
                session_obj, cloud_profile(), local_profile(), "系统", 0,
                lambda key: FakeModel([RuntimeError("模型不可用")]),
                lambda session: None,
            )

        self.assertEqual(session_obj.summary, "旧摘要")
        self.assertEqual(session_obj.summary_upto, 0)

    def test_force_ignores_the_threshold(self):
        session_obj = big_session(count=10)
        model = FakeModel(["手动摘要"])

        changed = ensure_summary(
            session_obj, cloud_profile(), local_profile(), "系统", 0,
            lambda key: model, lambda session: None, force=True,
        )

        self.assertTrue(changed)
        self.assertEqual(model.calls, 1)
        self.assertGreater(session_obj.summary_upto, 0)
