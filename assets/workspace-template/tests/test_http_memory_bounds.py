#!/usr/bin/env python3
"""Credential-free regression tests for bounded research HTTP responses."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import research_tools  # noqa: E402


class FakeResponse:
    """Model size-respecting network reads without opening a socket."""

    def __init__(self, body: bytes, headers: dict[str, str] | None = None):
        self.body = body
        self.position = 0
        self.headers = Message()
        for key, value in (headers or {}).items():
            self.headers[key] = value
        self.read_sizes: list[int] = []
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        if size <= 0:
            raise AssertionError("HTTP reads must request a positive bounded size")
        self.read_sizes.append(size)
        chunk = self.body[self.position:self.position + size]
        self.position += len(chunk)
        return chunk

    def close(self) -> None:
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


class HTTPBoundsTests(unittest.TestCase):
    def setUp(self) -> None:
        # Do not consult workstation credentials or perform accidental network I/O.
        self.environ = patch.dict(os.environ, {}, clear=True)
        self.environ.start()
        self.addCleanup(self.environ.stop)
        self.opener = patch.object(
            research_tools._OPENER, "open",
            side_effect=AssertionError("Real network access is forbidden in these tests"),
        )
        self.opener.start()
        self.addCleanup(self.opener.stop)

    def test_default_and_positive_environment_limits(self) -> None:
        self.assertEqual(
            research_tools._http_response_limit(),
            research_tools.DEFAULT_HTTP_MAX_RESPONSE_BYTES,
        )
        for value, expected in (("1", 1), ("32", 32), (" 128 ", 128)):
            with self.subTest(value=value), patch.dict(
                os.environ, {"RESEARCH_HTTP_MAX_RESPONSE_BYTES": value},
            ):
                self.assertEqual(research_tools._http_response_limit(), expected)

    def test_invalid_environment_limit_fails_before_open(self) -> None:
        for value in ("", "0", "-1", "nan", "1.5", "unlimited"):
            for helper in (research_tools.http_text, research_tools.http_json):
                with self.subTest(value=value, helper=helper.__name__), patch.dict(
                    os.environ, {"RESEARCH_HTTP_MAX_RESPONSE_BYTES": value},
                ), patch.object(research_tools, "_urlopen") as opener:
                    with self.assertRaises(research_tools.HTTPResponsePolicyError):
                        helper("https://example.invalid/data")
                    opener.assert_not_called()

    def test_exact_and_below_limit_text_and_json(self) -> None:
        for helper, body, expected in (
            (research_tools.http_text, b"hello", ("hello", "text/plain")),
            (research_tools.http_json, b'{"ok":true}', {"ok": True}),
        ):
            for extra in (0, 7):
                with self.subTest(helper=helper.__name__, extra=extra):
                    response = FakeResponse(body, {"Content-Type": "text/plain"})
                    with patch.dict(os.environ, {
                        "RESEARCH_HTTP_MAX_RESPONSE_BYTES": str(len(body) + extra),
                    }), patch.object(research_tools, "_urlopen", return_value=response) as opener:
                        self.assertEqual(helper("https://example.invalid/data"), expected)
                    self.assertTrue(response.closed)
                    self.assertEqual(response.position, len(body))
                    self.assertTrue(all(0 < n <= research_tools.HTTP_READ_CHUNK_BYTES
                                        for n in response.read_sizes))
                    request = opener.call_args.args[0]
                    self.assertEqual(request.get_header("Accept-encoding"), "identity")

    def test_declared_oversize_closes_without_reading(self) -> None:
        for helper in (research_tools.http_text, research_tools.http_json):
            with self.subTest(helper=helper.__name__):
                response = FakeResponse(b"unused", {"Content-Length": "101"})
                with patch.dict(os.environ, {"RESEARCH_HTTP_MAX_RESPONSE_BYTES": "100"}), \
                     patch.object(research_tools, "_urlopen", return_value=response):
                    with self.assertRaises(research_tools.HTTPResponseLimitError):
                        helper("https://example.invalid/data")
                self.assertEqual(response.read_sizes, [])
                self.assertTrue(response.closed)

    def test_stream_over_limit_with_missing_or_inaccurate_length(self) -> None:
        limit = research_tools.HTTP_READ_CHUNK_BYTES + 11
        for length in (None, "0", "3", "not-an-integer", "-1"):
            for helper in (research_tools.http_text, research_tools.http_json):
                with self.subTest(length=length, helper=helper.__name__):
                    headers = {} if length is None else {"Content-Length": length}
                    response = FakeResponse(b"x" * (limit + 100), headers)
                    with patch.dict(os.environ, {"RESEARCH_HTTP_MAX_RESPONSE_BYTES": str(limit)}), \
                         patch.object(research_tools, "_urlopen", return_value=response):
                        with self.assertRaises(research_tools.HTTPResponseLimitError):
                            helper("https://example.invalid/data")
                    self.assertTrue(response.closed)
                    # The sentinel costs at most one byte, regardless of header claims.
                    self.assertEqual(response.position, limit + 1)
                    self.assertLessEqual(max(response.read_sizes), research_tools.HTTP_READ_CHUNK_BYTES)

    def test_encoded_responses_rejected_without_reading(self) -> None:
        for encoding in ("gzip", "br", "deflate", "identity, gzip"):
            for helper in (research_tools.http_text, research_tools.http_json):
                with self.subTest(encoding=encoding, helper=helper.__name__):
                    response = FakeResponse(b"compressed", {"Content-Encoding": encoding})
                    with patch.object(research_tools, "_urlopen", return_value=response):
                        with self.assertRaisesRegex(research_tools.HTTPResponsePolicyError, "Content-Encoding"):
                            helper("https://example.invalid/data")
                    self.assertEqual(response.read_sizes, [])
                    self.assertTrue(response.closed)

    def test_identity_encoding_is_accepted(self) -> None:
        response = FakeResponse(b"text", {"Content-Encoding": " Identity "})
        with patch.object(research_tools, "_urlopen", return_value=response):
            self.assertEqual(research_tools.http_text("https://example.invalid/"), ("text", ""))
        self.assertTrue(response.closed)

    def test_http_error_body_closed_without_reading(self) -> None:
        for helper in (research_tools.http_text, research_tools.http_json):
            with self.subTest(helper=helper.__name__):
                response = FakeResponse(b"an unused and potentially large error body")
                error = HTTPError("https://example.invalid/", 500, "test", Message(), response)
                with patch.object(research_tools._OPENER, "open", side_effect=error):
                    with self.assertRaises(HTTPError) as raised:
                        helper("https://example.invalid/")
                self.assertIs(raised.exception, error)
                self.assertTrue(response.closed)
                self.assertEqual(response.read_sizes, [])

    def test_json_payload_failure_and_normal_request_close_response(self) -> None:
        response = FakeResponse(b'{"ok": true}')
        with patch.object(research_tools, "_urlopen", return_value=response) as opener:
            self.assertEqual(research_tools.http_json(
                "https://example.invalid/", method="POST", payload={"query": "test"},
            ), {"ok": True})
        self.assertTrue(response.closed)
        self.assertEqual(json.loads(opener.call_args.args[0].data), {"query": "test"})
        bad_response = FakeResponse(b"invalid json")
        with patch.object(research_tools, "_urlopen", return_value=bad_response):
            with self.assertRaises(json.JSONDecodeError):
                research_tools.http_json("https://example.invalid/")
        self.assertTrue(bad_response.closed)


class HTTPPolicyPropagationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environ = patch.dict(os.environ, {}, clear=True)
        self.environ.start()
        self.addCleanup(self.environ.stop)

    def test_exa_policy_errors_bypass_retry(self) -> None:
        for error_type in (research_tools.HTTPResponsePolicyError, research_tools.HTTPResponseLimitError):
            with self.subTest(error_type=error_type.__name__):
                error = error_type("configured HTTP body limit")
                with patch.dict(os.environ, {"EXA_API_KEY": "fake-local-test", "EXA_RETRIEVE_DELAYS": "1,2"}), \
                     patch.object(research_tools, "_retrieve_exa_once", side_effect=error) as request, \
                     patch.object(research_tools.time, "sleep") as sleep:
                    with self.assertRaises(error_type) as raised:
                        research_tools.retrieve_exa("https://example.invalid/", 30)
                self.assertIs(raised.exception, error)
                request.assert_called_once()
                sleep.assert_not_called()

    def test_search_logs_policy_failure_and_does_not_fallback(self) -> None:
        for error_type in (research_tools.HTTPResponsePolicyError, research_tools.HTTPResponseLimitError):
            with self.subTest(error_type=error_type.__name__), tempfile.TemporaryDirectory() as temporary:
                paths = SimpleNamespace(local_log=Path(temporary) / "research_log.ndjson")
                args = argparse.Namespace(task_root=temporary, search_intent=None,
                                          query="test query", backend="primary", num_results=3)
                error = error_type("response rejected")
                with patch.dict(os.environ, {"SEARCH_API_FALLBACKS": "second,third"}), \
                     patch.object(research_tools, "task_paths", return_value=paths), \
                     patch.object(research_tools, "search_backend", side_effect=error) as request, \
                     contextlib.redirect_stdout(io.StringIO()) as output:
                    with self.assertRaises(error_type) as raised:
                        research_tools.command_search(args)
                self.assertIs(raised.exception, error)
                request.assert_called_once_with("test query", 3, "primary", hl="", gl="")
                self.assertEqual(output.getvalue(), "")
                records = research_tools.read_ndjson(paths.local_log)
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0]["status"], "error")
                self.assertEqual(records[0]["backend"], "primary")
                self.assertEqual(records[0]["error"], "response rejected")

    def test_retrieve_logs_policy_failure_without_fallback_or_cache(self) -> None:
        for error_type in (research_tools.HTTPResponsePolicyError, research_tools.HTTPResponseLimitError):
            with self.subTest(error_type=error_type.__name__), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                paths = SimpleNamespace(local_log=root / "research_log.ndjson",
                                        local_index=root / "local_index.jsonl",
                                        global_index=root / "global_index.jsonl")
                args = argparse.Namespace(task_root=temporary, url="https://example.invalid/data",
                                          log_context="cap test", force_refresh=True,
                                          backend="primary", fallbacks="second,third", timeout=5)
                error = error_type("response rejected")
                with patch.object(research_tools, "task_paths", return_value=paths), \
                     patch.object(research_tools, "retrieve_backend", side_effect=error) as request, \
                     patch.object(research_tools, "store_cache") as cache, \
                     patch.object(research_tools, "materialize_global_hit") as materialize, \
                     contextlib.redirect_stdout(io.StringIO()) as output:
                    with self.assertRaises(error_type) as raised:
                        research_tools.command_retrieve(args)
                self.assertIs(raised.exception, error)
                request.assert_called_once_with("https://example.invalid/data", 5, "primary")
                cache.assert_not_called()
                materialize.assert_not_called()
                self.assertFalse(paths.local_index.exists())
                self.assertFalse(paths.global_index.exists())
                self.assertEqual(output.getvalue(), "")
                records = research_tools.read_ndjson(paths.local_log)
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0]["action"], "retrieve")
                self.assertEqual(records[0]["status"], "error")
                self.assertEqual(records[0]["backend"], "primary")
                self.assertEqual(records[0]["context"], "cap test")


if __name__ == "__main__":
    unittest.main()
