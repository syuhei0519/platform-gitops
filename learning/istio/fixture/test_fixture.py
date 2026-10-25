# Istio の通信試験用サーバーが、版の表示と失敗シナリオを正しく返すことを確認します。
"""Accounting boundaries only; synthetic responses are not mesh runtime evidence."""
import importlib.util
from pathlib import Path
import unittest
import urllib.error

spec = importlib.util.spec_from_file_location("demo", Path(__file__).resolve().parents[1] / "chart/files/demo.py")
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


class Response:
    def __init__(self, body=b"v1\n", status=200):
        self.body, self.status = body, status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def read(self, _limit):
        return self.body


class Opener:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = 0

    def open(self, request, timeout):
        assert request.method == "GET" and timeout == 2
        assert request.get_header("Connection") == "close"
        self.calls += 1
        response = next(self.replies)
        if isinstance(response, Exception):
            raise response
        return response


class Accounting(unittest.TestCase):
    def run_split(self, v2):
        return demo.measure("split", Opener([Response(b"v2\n")] * v2 + [Response()] * (2000 - v2)))

    def test_stable_and_plain(self):
        for window in ["stable", "plain"]:
            result = demo.measure(window, Opener([Response()] * 200))
            self.assertTrue(result["passed"])

    def test_split_fixed_boundaries(self):
        for count in [160, 200, 240]:
            self.assertTrue(self.run_split(count)["passed"])
        for count in [159, 241]:
            self.assertFalse(self.run_split(count)["passed"])

    def test_failures_remain_in_denominator(self):
        replies = [Response(b"v2\n")] * 200 + [Response()] * 1796 + [
            urllib.error.HTTPError(demo.URL, 503, "unavailable", {}, None),
            urllib.error.URLError("synthetic timeout"), Response(b"v3\n"), Response(status=201)]
        opener = Opener(replies)
        result = demo.measure("split", opener)
        self.assertFalse(result["passed"])
        self.assertEqual(result["total"], 2000)
        self.assertEqual(opener.calls, 2000)
        self.assertEqual(result["http_failures"], 2)
        self.assertEqual(result["transport_failures"], 1)
        self.assertEqual(result["unknown_body"], 1)
        self.assertEqual(result["v2_fraction_of_all_requests"], .1)

    def test_stable_leak_fails(self):
        self.assertFalse(demo.measure("stable", Opener([Response(b"v2\n")] + [Response()] * 199))["passed"])

    def test_redirects_are_not_followed(self):
        self.assertIsNone(demo.NoRedirect().redirect_request(None, None, 302, "moved", {}, "http://other/"))


if __name__ == "__main__":
    unittest.main()
