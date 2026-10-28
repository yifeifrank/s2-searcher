"""Integration boundaries unique to S2's native and Inquiry entry points."""
import asyncio
import contextlib
import io
import json
import sys
import unittest
from unittest import mock
import test_launcher_lifecycle as lifecycle
import inquiry
import batch
from tools.task_integrity import verify_completion

class S2IntegrationTests(unittest.TestCase):
    def fixture(self):
        t = lifecycle.LifecycleTests()
        t.setUp()
        self.addCleanup(t.doCleanups)
        return t

    def test_native_structured_lifecycle_and_sealed_resume(self):
        t = self.fixture()
        t.row.update(route="online_agent", deliverable="structured")
        t.args.codex_search_mode = "native"
        result = t.run_row("native")
        self.assertEqual(result["status"], "pass", result)
        task = t.tasks / "example"
        verify_completion(task)
        self.assertTrue((task / "output/final_report.json").is_file())
        sources = (task / "cache/native_sources.ndjson").read_bytes()
        t.args.resume = True
        result = t.run_row("native")
        self.assertEqual(result["status"], "resumed", result)
        self.assertEqual(len(t.commands), 1)
        self.assertEqual(sources, (task / "cache/native_sources.ndjson").read_bytes())

    def test_inquiry_progress_does_not_change_sealed_research(self):
        t = self.fixture()
        with mock.patch.object(sys, "argv", ["inquiry.py", "Synthetic question", "--task-id", "question",
                "--tasks-dir", str(t.tasks), "--native-archive-mode", "metadata"]):
            args = inquiry.parse_args()
        commands = []
        def command(model, task_root, *unused, **kwargs):
            commands.append(1)
            return [sys.executable, str(lifecycle.ROOT / "tests/fixtures/fake_worker.py"),
                    str(task_root), str(t.workspace), "native"]
        with mock.patch.object(inquiry, "FRAMEWORK_ROOT", t.workspace), \
             mock.patch.object(batch, "FRAMEWORK_ROOT", t.workspace), \
             mock.patch.object(batch, "codex_command", side_effect=command), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(asyncio.run(inquiry.run(args)), 0)
            task = t.tasks / "question"
            verify_completion(task)
            before = {p.relative_to(task).as_posix(): p.read_bytes() for p in task.rglob("*") if p.is_file()}
            args.resume = True
            self.assertEqual(asyncio.run(inquiry.run(args)), 0)
            verify_completion(task)
        self.assertEqual(commands, [1])
        self.assertEqual(before, {p.relative_to(task).as_posix(): p.read_bytes() for p in task.rglob("*") if p.is_file()})
        receipt = json.loads((t.tasks / ".inquiry_status/question/inquiry_result.json").read_text())
        self.assertEqual(receipt["status"], "resumed")
