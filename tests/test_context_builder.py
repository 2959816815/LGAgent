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
