#!/usr/bin/env python3
"""Offline regression coverage for bounded, complete launcher output."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import tracemalloc
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import batch  # noqa: E402


def event(value: dict) -> bytes:
    return (json.dumps(value) + "\n").encode()


class OutputMemoryTests(unittest.TestCase):
    def test_dotenv_limits_are_loaded_before_execution(self):
        try:
            with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ):
                os.environ.pop("RESEARCH_OUTPUT_TAIL_BYTES", None)
                path = Path(temporary) / ".env"
                path.write_text("RESEARCH_OUTPUT_TAIL_BYTES=1234\n")
                batch.load_env(path)
                batch.configure_output_limits()
                self.assertEqual(batch.OUTPUT_TAIL_BYTES, 1234)
        finally:
            batch.configure_output_limits()

    def test_configured_limits_are_positive_and_finite(self):
        for raw in ("0", "-1", "bad", "16777217"):
            with self.subTest(raw=raw), patch.dict(os.environ, {"RESEARCH_OUTPUT_TAIL_BYTES": raw}):
                with self.assertRaises(ValueError):
                    batch._positive_output_limit("RESEARCH_OUTPUT_TAIL_BYTES", 262144, 16777216)
        with patch.dict(os.environ, {"RESEARCH_OUTPUT_TAIL_BYTES": "1234"}):
            self.assertEqual(batch._positive_output_limit("RESEARCH_OUTPUT_TAIL_BYTES", 262144, 16777216), 1234)

    def test_unicode_line_separator_inside_json_is_preserved(self):
        capture = batch.OutputCapture()
        capture.feed((json.dumps({"type": "thread.started", "thread_id": "alpha\u2028beta"},
                                ensure_ascii=False) + "\n").encode())
        output = capture.finish()
        self.assertFalse(output.capture_errors)
        self.assertEqual(batch.parse_session_events(output)["session_ref"], "alpha\u2028beta")

    def test_exact_record_boundary_remains_valid_when_resumed(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(batch, "EVENT_RECORD_BYTES", 1024):
            payload = json.dumps({"type": "noop", "value": ""}).encode()
            payload = json.dumps({"type": "noop", "value": "x" * (1024 - len(payload))}).encode()
            self.assertEqual(len(payload), 1024)
            path = Path(temporary) / "boundary.log"
            path.write_bytes(payload + b"\n")
            output = batch.read_output_log(path)
            self.assertFalse(output.capture_errors)
            self.assertEqual(len(list(batch._bounded_log_records(path))), 1)
            self.assertEqual(batch.extract_codex_native_archive(path), ([], []))

    def test_missing_claude_ids_are_distinct_across_attempts(self):
        summary = batch.OutputSummary()
        raw = b"diagnostic\n" + event({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Agent", "input": {"description": "synthetic"}}]}})
        for _ in range(2):
            capture = batch.OutputCapture()
            capture.feed(raw)
            summary += capture.finish()
        meta = batch.parse_session_events(summary)
        self.assertEqual(meta["child_invocation_count"], 2)
        self.assertEqual(meta["child_ids"], ["claude:line-2-block-0", "claude:line-4-block-0"])

    def test_disk_transcript_and_early_metadata_survive_large_output(self):
        prefix = event({"type": "thread.started", "thread_id": "synthetic-thread"})
        prefix += event({"type": "error", "message": "HTTP 429 rate limit"})
        prefix += event({"type": "item.completed", "item": {
            "type": "collab_tool_call", "tool": "spawn_agent",
            "receiver_thread_ids": ["synthetic-child"],
        }})
        suffix = event({"type": "turn.completed", "usage": {"input_tokens": 13, "output_tokens": 7}})
        code = ("import sys; w=sys.stdout.buffer; "
                f"w.write({prefix!r}); "
                "block=b'x'*4095+b'\\n'; "
                "[w.write(block) for _ in range(4096)]; "
                f"w.write({suffix!r}); w.flush()")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "attempt.log"
            tracemalloc.start()
            try:
                rc, output, timed_out = asyncio.run(batch.run_process(
                    [sys.executable, "-c", code], None, 30, batch.ProcessRegistry(), path))
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            self.assertEqual(rc, 0)
            self.assertFalse(timed_out)
            self.assertLessEqual(len(output), batch.OUTPUT_TAIL_BYTES)
            self.assertGreater(path.stat().st_size, 16 * 1024 * 1024)
            self.assertEqual(path.stat().st_size, output.output_bytes)
            self.assertLess(peak, 12 * 1024 * 1024)
            self.assertNotIn(b"synthetic-thread", output)
            meta = batch.parse_session_events(output)
            self.assertEqual(meta["session_ref"], "synthetic-thread")
            self.assertEqual(meta["usage"]["total_tokens"], 20)
            self.assertEqual(meta["child_invocation_count"], 1)
            self.assertIn("rate_limit", batch.extract_api_warnings(output, meta)["api_warning_categories"])
            resumed = batch.read_output_log(path)
            self.assertEqual(batch.parse_session_events(resumed), meta)
            self.assertEqual(resumed.output_bytes, output.output_bytes)

    def test_oversized_record_is_explicit_failure_with_complete_disk_output(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(batch, "EVENT_RECORD_BYTES", 1024):
            path = Path(temporary) / "oversize.log"
            rc, output, _ = asyncio.run(batch.run_process(
                [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'x'*4096+b'\\n')"],
                None, 10, batch.ProcessRegistry(), path))
            self.assertNotEqual(rc, 0)
            self.assertTrue(output.capture_errors)
            self.assertEqual(path.stat().st_size, 4097)

    def test_pretty_json_helper_and_strict_agent_events_are_distinct(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            code = "import json; print(json.dumps({'status':'success','count':3},indent=2))"
            rc, output, _ = asyncio.run(batch.run_process(
                [sys.executable, "-c", code], None, 10, batch.ProcessRegistry(),
                root / "helper.log", event_mode=False))
            self.assertEqual(rc, 0)
            self.assertFalse(output.capture_errors)
            self.assertFalse(batch.read_output_log(root / "helper.log", event_mode=False).capture_errors)
            rc, output, _ = asyncio.run(batch.run_process(
                [sys.executable, "-c", "print('{broken')"], None, 10,
                batch.ProcessRegistry(), root / "agent.log"))
            self.assertNotEqual(rc, 0)
            self.assertTrue(output.capture_errors)

    def test_child_identity_limit_fails_instead_of_silently_truncating(self):
        with patch.object(batch, "EVENT_CHILD_LIMIT", 2):
            capture = batch.OutputCapture()
            for child in ("one", "two", "three"):
                capture.feed(event({"type": "item.completed", "item": {
                    "type": "collab_tool_call", "tool": "spawn_agent", "receiver_thread_ids": [child]}}))
            self.assertTrue(capture.finish().capture_errors)

    def test_native_archive_reads_complete_path_without_full_file_read(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "events.log"
            with path.open("wb") as sink:
                sink.write(event({"type": "item.completed", "item": {
                    "id": "early", "type": "web_search", "action": {"type": "search", "query": "offline"},
                    "url": "https://example.org/offline"}}))
                for _ in range(256):
                    sink.write(b"x" * 4095 + b"\n")
            with patch.object(Path, "read_bytes", side_effect=AssertionError("whole-file read forbidden")):
                searches, sources = batch.extract_codex_native_archive(path)
            self.assertEqual(searches[0]["event_id"], "early")
            self.assertEqual(sources[0]["url"], "https://example.org/offline")


class FullLauncherTests(unittest.TestCase):
    def args(self, resume=False, retries=0):
        with patch.object(sys, "argv", ["batch.py", "--manifest", "unused.json", "--native-archive-mode", "metadata"]):
            args = batch.parse_args()
        args.resume = resume
        args.retries = retries
        args.direct_api_timeout_sec = 15
        args.coder_api_timeout_sec = 15
        return args

    def execute(self, tasks, row, args, command, validate):
        async def run():
            progress = batch.Progress(tasks, [row["task_id"]])
            registry = batch.ProcessRegistry()
            control = batch.BatchControl("abort", 1, 0, progress, registry)
            framework = json.loads((ROOT / "framework.json").read_text(encoding="utf-8"))
            return await batch.run_one(row, 0, framework, args, tasks, asyncio.Semaphore(1),
                                       progress, control, registry, batch.NativeSnapshotCoordinator())
        with patch.object(batch, "codex_command", side_effect=command), \
             patch.object(batch, "api_command", side_effect=command), \
             patch.object(batch, "validate_stage", side_effect=validate):
            return asyncio.run(run())

    def test_native_retry_and_resume_keep_full_logs_and_early_metadata(self):
        row = {"task_id": "offline", "objective": "Synthetic public topic", "route": "online_agent"}
        calls = []
        with tempfile.TemporaryDirectory() as temporary:
            tasks = Path(temporary)
            task = tasks / row["task_id"]
            def command(*args, **kwargs):
                calls.append(args)
                number = len(calls)
                prefix = event({"type": "thread.started", "thread_id": "synthetic-native"})
                prefix += event({"type": "item.completed", "item": {
                    "id": f"search-{number}", "type": "web_search",
                    "action": {"type": "search", "query": f"offline-{number}"},
                    "url": f"https://example.org/offline-{number}"}})
                suffix = event({"type": "turn.completed", "usage": {"input_tokens": 3, "output_tokens": 2}})
                code = ("import pathlib,sys; sys.stdin.read(); "
                        f"pathlib.Path({str(task / 'research_report.md')!r}).write_text('Synthetic report'); "
                        f"w=sys.stdout.buffer; w.write({prefix!r}); "
                        "[w.write(b'x'*4095+b'\\n') for _ in range(128)]; "
                        f"w.write({suffix!r}); w.flush(); sys.exit({1 if number < 3 else 0})")
                return [sys.executable, "-c", code]
            def validate(*args):
                return ((task / "research_report.md").exists(), "synthetic validation")
            result = self.execute(tasks, row, self.args(retries=1), command, validate)
            self.assertEqual(result["status"], "fail", result)
            self.assertEqual(result["total_tokens"], 10)
            first = (task / "session.attempt-1.log").read_bytes()
            second = (task / "session.attempt-2.log").read_bytes()
            self.assertEqual((task / "session.log").read_bytes(), first + second)
            self.assertEqual(calls[1][2], "synthetic-native")
            def require_new_attempt(*args):
                return (len(calls) >= 3, "force synthetic continuation")
            result = self.execute(tasks, row, self.args(resume=True), command, require_new_attempt)
            self.assertEqual(result["status"], "pass", result)
            self.assertEqual(result["total_tokens"], 15)
            self.assertEqual((task / "session.attempt-1.log").read_bytes(), first)
            self.assertEqual((task / "session.attempt-2.log").read_bytes(), second)
            third = (task / "session.attempt-3.log").read_bytes()
            self.assertEqual((task / "session.log").read_bytes(), first + second + third)
            searches = [json.loads(line) for line in (task / "cache/native_searches.ndjson").read_text().splitlines()]
            self.assertEqual([item["event_id"] for item in searches], ["search-1", "search-2", "search-3"])

    def test_direct_api_pretty_json_is_not_a_runtime_audit_failure(self):
        row = {"task_id": "direct", "objective": "Synthetic compact evidence", "route": "direct_api", "inline_text": "test"}
        def command(*args, **kwargs):
            return [sys.executable, "-c", "import json; print(json.dumps({'status':'success'},indent=2))"]
        with tempfile.TemporaryDirectory() as temporary:
            tasks = Path(temporary)
            result = self.execute(tasks, row, self.args(), command, lambda *a: (True, "synthetic validation"))
            self.assertEqual(result["status"], "pass", result)
            self.assertFalse(result["output_audit_errors"])
            attempt = tasks / "direct/session.attempt-1.log"
            self.assertEqual(attempt.read_bytes(), (tasks / "direct/session.log").read_bytes())

    def test_invalid_agent_record_stops_automatic_retries(self):
        row = {"task_id": "bad-agent", "objective": "Synthetic public topic", "route": "online_agent"}
        calls = []
        def command(*args, **kwargs):
            calls.append(1)
            return [sys.executable, "-c", "print('{broken')"]
        with tempfile.TemporaryDirectory() as temporary:
            args = self.args(retries=3)
            args.codex_search_mode = "provider"
            result = self.execute(Path(temporary), row, args, command,
                                  lambda *a: (True, "synthetic validation"))
            self.assertEqual(result["status"], "fail", result)
            self.assertEqual(len(calls), 1)
            self.assertTrue(result["output_audit_errors"])


if __name__ == "__main__":
    unittest.main()
