"""用户自定义模型配置（DSH_MODELS_JSON）的解析与回退。"""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from model_registry import get_profile, list_profiles, resolve_selector


def payload() -> str:
    return json.dumps(
        [
            {
                "id": "myapi",
                "kind": "api",
                "label": "我的 API",
                "base_url": "https://example.com/v1",
                "model": "foo-1",
                "api_key": "sk-test",
            },
            {
                "id": "mylocal",
                "kind": "local",
                "label": "我的本地模型",
                "base_url": "http://127.0.0.1:11434/v1",
                "model": "qwen3.5:2b",
            },
        ],
        ensure_ascii=False,
    )


class DynamicProfileTests(unittest.TestCase):
    def test_profiles_come_from_env_json(self):
        with patch.dict(os.environ, {"DSH_MODELS_JSON": payload()}):
            profiles = list_profiles()

        self.assertEqual([p.key for p in profiles], ["myapi", "mylocal"])
        api, local = profiles
        self.assertEqual(api.label, "我的 API")
        self.assertEqual(api.base_url, "https://example.com/v1")
        self.assertEqual(api.model, "foo-1")
        self.assertEqual(api.api_key, "sk-test")
        self.assertEqual(local.provider, "ollama")

    def test_dynamic_ids_are_selectable(self):
        with patch.dict(os.environ, {"DSH_MODELS_JSON": payload()}):
            self.assertEqual(resolve_selector("myapi"), "myapi")
            self.assertEqual(resolve_selector("mylocal"), "mylocal")
            self.assertEqual(get_profile("myapi").model, "foo-1")

    def test_invalid_json_falls_back_to_builtin_profiles(self):
        with patch.dict(os.environ, {"DSH_MODELS_JSON": "not-json"}):
            keys = [p.key for p in list_profiles()]

        self.assertIn("deepseek", keys)
        self.assertIn("local", keys)

    def test_empty_list_falls_back_to_builtin_profiles(self):
        with patch.dict(os.environ, {"DSH_MODELS_JSON": "[]"}):
            keys = [p.key for p in list_profiles()]

        self.assertIn("deepseek", keys)

    def test_items_missing_id_or_model_are_skipped(self):
        broken = json.dumps(
            [
                {"id": "", "model": "x", "base_url": "https://a"},
                {"id": "ok", "kind": "api", "model": "y", "base_url": "https://b"},
            ]
        )
        with patch.dict(os.environ, {"DSH_MODELS_JSON": broken}):
            keys = [p.key for p in list_profiles()]

        self.assertEqual(keys, ["ok"])


if __name__ == "__main__":
    unittest.main()
