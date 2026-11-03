from __future__ import annotations

import unittest

from mcp_auth import StaticHeaderAuthProvider


class StaticHeaderAuthProviderTests(unittest.TestCase):
    def test_prepare_returns_a_copy_of_headers(self):
        provider = StaticHeaderAuthProvider()
        config = {"headers": {"Authorization": "Bearer t", "X-Trace": "1"}}

        headers = provider.prepare(config)

        self.assertEqual(headers, {"Authorization": "Bearer t", "X-Trace": "1"})
        headers["Authorization"] = "changed"
        self.assertEqual(config["headers"]["Authorization"], "Bearer t")

    def test_on_auth_error_is_a_no_op(self):
        self.assertIsNone(StaticHeaderAuthProvider().on_auth_error(401, "denied"))
