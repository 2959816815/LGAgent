import os
import unittest
from unittest.mock import Mock, patch

import requests

from web_search_tool import web_search


class WebSearchToolTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(
            os.environ,
            {"TAVILY_API_KEY": "test-key"},
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def _response(self, payload, status=200):
        response = Mock()
        response.status_code = status
        response.json.return_value = payload
        return response

    @patch("web_search_tool.requests.post")
    def test_formats_results(self, post):
        post.return_value = self._response(
            {
                "results": [
                    {
                        "title": "量子计算新进展",
                        "url": "https://example.com/quantum",
                        "content": "研究人员公布了新的纠错方案。",
                    },
                    {
                        "title": "行业观察",
                        "url": "https://example.com/report",
                        "content": "未来五年市场规模预测。",
                    },
                ]
            }
        )

        result = web_search.invoke(
            {"query": "量子计算", "max_results": 2}
        )

        self.assertIn("[搜索结果] 量子计算", result)
        self.assertIn("1. 量子计算新进展", result)
        self.assertIn("URL: https://example.com/quantum", result)
        self.assertIn("摘要: 研究人员公布了新的纠错方案。", result)
        self.assertIn("2. 行业观察", result)
        self.assertNotIn("test-key", result)
        sent = post.call_args.kwargs["json"]
        self.assertEqual(sent["query"], "量子计算")
        self.assertEqual(sent["max_results"], 2)
        self.assertEqual(sent["search_depth"], "basic")
        self.assertFalse(sent["include_answer"])
        self.assertFalse(sent["include_raw_content"])
        headers = post.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer test-key")

    @patch("web_search_tool.requests.post")
    def test_clamps_max_results(self, post):
        post.return_value = self._response({"results": []})

        web_search.invoke({"query": "a", "max_results": 99})
        self.assertEqual(post.call_args.kwargs["json"]["max_results"], 10)

        web_search.invoke({"query": "b", "max_results": 0})
        self.assertEqual(post.call_args.kwargs["json"]["max_results"], 1)

    @patch("web_search_tool.requests.post")
    def test_invalid_max_results_uses_default(self, post):
        post.return_value = self._response({"results": []})

        web_search.func("abc", "not-a-number")

        self.assertEqual(post.call_args.kwargs["json"]["max_results"], 5)

    @patch("web_search_tool.requests.post")
    def test_empty_results(self, post):
        post.return_value = self._response({"results": []})

        result = web_search.invoke({"query": "没有结果"})

        self.assertEqual(result, "[搜索结果] 未找到相关内容。")

    @patch("web_search_tool.requests.post")
    def test_missing_api_key(self, post):
        with patch.dict(os.environ, {"TAVILY_API_KEY": ""}):
            result = web_search.invoke({"query": "测试"})

        self.assertIn("未配置 TAVILY_API_KEY", result)
        post.assert_not_called()

    @patch("web_search_tool.requests.post")
    def test_timeout(self, post):
        post.side_effect = requests.exceptions.Timeout()

        result = web_search.invoke({"query": "测试"})

        self.assertIn("搜索超时", result)

    @patch("web_search_tool.requests.post")
    def test_http_error(self, post):
        post.return_value = self._response({}, status=500)

        result = web_search.invoke({"query": "测试"})

        self.assertEqual(result, "[错误] 搜索请求失败（HTTP 500）。")

    @patch("web_search_tool.requests.post")
    def test_malformed_response(self, post):
        post.return_value = self._response({"results": "not-a-list"})

        result = web_search.invoke({"query": "测试"})

        self.assertEqual(result, "[错误] 搜索返回格式异常。")

    @patch("web_search_tool.requests.post")
    def test_truncates_long_snippet(self, post):
        post.return_value = self._response(
            {
                "results": [
                    {
                        "title": "长文",
                        "url": "https://example.com/long",
                        "content": "长" * 500,
                    }
                ]
            }
        )

        result = web_search.invoke({"query": "长文"})

        self.assertIn("…", result)
        self.assertLessEqual(len(result), 400)

    @patch("web_search_tool.requests.post")
    def test_empty_query(self, post):
        result = web_search.invoke({"query": "   "})

        self.assertEqual(result, "[错误] 搜索词不能为空。")
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
