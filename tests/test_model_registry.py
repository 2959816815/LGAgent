from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from model_registry import (
    DEFAULT_MODEL_KEY,
    ModelConfigError,
    build_chat_model,
    get_profile,
    initial_model_key,
    list_profiles,
    model_table,
    resolve_selector,
)


class ModelRegistryTests(unittest.TestCase):
    def test_three_profiles_have_expected_defaults(self):
        # 测试内置默认值，不依赖开发者 .env 里的 OLLAMA_MODEL 等覆盖项。
        with patch.dict(os.environ, {}, clear=True):
            profiles = {profile.key: profile for profile in list_profiles()}
        self.assertEqual(list(profiles), ["local", "deepseek", "qwen"])
        self.assertEqual(profiles["local"].model, "qwen3.5:2b")
        self.assertEqual(profiles["local"].provider, "ollama")
        self.assertEqual(profiles["deepseek"].model, "deepseek-flash")
        self.assertEqual(profiles["qwen"].model, "qwen3-vl-plus")
        self.assertEqual(profiles["deepseek"].key_env, "DEEPSEEK_API_KEY")
        self.assertEqual(profiles["qwen"].key_env, "DASHSCOPE_API_KEY")
        self.assertTrue(profiles["qwen"].supports_vision)

    def test_resolve_selector_aliases(self):
        cases = {
            "1": "local",
            "local": "local",
            "OLLAMA": "local",
            "qwen3.5": "local",
            "本地": "local",
            "2": "deepseek",
            "DEEPSEEK": "deepseek",
            "deepseek-v4.1-flash": "deepseek",
            "v4.1-flash": "deepseek",
            "3": "qwen",
            "qwen3-vl-plus": "qwen",
            "qwen-vl": "qwen",
            "千问": "qwen",
        }
        for selector, expected in cases.items():
            with self.subTest(selector=selector):
                self.assertEqual(resolve_selector(selector), expected)

    def test_unknown_selector_raises(self):
        with self.assertRaises(ModelConfigError) as ctx:
            resolve_selector("gpt-4")
        self.assertIn("未知模型", str(ctx.exception))

    def test_missing_qwen_key_raises(self):
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": ""}, clear=False):
            with self.assertRaises(ModelConfigError) as ctx:
                build_chat_model("qwen")
        self.assertIn("DASHSCOPE_API_KEY", str(ctx.exception))

    def test_missing_deepseek_key_raises(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}, clear=False):
            with self.assertRaises(ModelConfigError) as ctx:
                build_chat_model("deepseek")
        self.assertIn("DEEPSEEK_API_KEY", str(ctx.exception))

    def test_initial_key_mapping_and_fallback(self):
        with patch.dict(os.environ, {"LLM_PROVIDER": "deepseek"}, clear=False):
            self.assertEqual(initial_model_key(), "deepseek")
        with patch.dict(os.environ, {"LLM_PROVIDER": "dashscope"}, clear=False):
            self.assertEqual(initial_model_key(), "qwen")
        with patch.dict(os.environ, {"LLM_PROVIDER": "ollama"}, clear=False):
            self.assertEqual(initial_model_key(), "local")
        with patch.dict(os.environ, {"LLM_PROVIDER": "nonsense"}, clear=False):
            self.assertEqual(initial_model_key(), DEFAULT_MODEL_KEY)

    def test_model_table_marks_current_profile(self):
        table = model_table("deepseek")
        self.assertIn("[模型] 当前: deepseek", table)
        self.assertIn("local", table)
        self.assertIn("deepseek", table)
        self.assertIn("qwen", table)
        self.assertIn("qwen3-vl-plus", table)

    def test_local_profile_allows_slow_cpu_inference(self):
        profile = get_profile("local")
        self.assertGreaterEqual(profile.timeout, 300)
        self.assertEqual(profile.max_retries, 0)

    @patch("model_registry.ChatOpenAI")
    def test_build_chat_model_uses_profile_fields(self, chat_openai):
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "test-key"}, clear=False):
            build_chat_model("qwen")
        kwargs = chat_openai.call_args.kwargs
        self.assertEqual(kwargs["model"], "qwen3-vl-plus")
        self.assertEqual(kwargs["api_key"], "test-key")
        self.assertEqual(kwargs["base_url"], "https://dashscope.aliyuncs.com/compatible-mode/v1")
        self.assertEqual(kwargs["temperature"], 0)
        self.assertEqual(kwargs["timeout"], 60)
        self.assertEqual(kwargs["max_retries"], 2)


if __name__ == "__main__":
    unittest.main()
