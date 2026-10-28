"""Run real subprocesses/validators with a synthetic worker; never call models."""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT.parents[1]
sys.path.insert(0, str(ROOT))
import batch
from tools.task_integrity import verify_completion


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "workspace"
        result = subprocess.run([sys.executable, str(SKILL / "scripts/create_workspace.py"),
                                 "--target", str(self.workspace), "--codebook", str(ROOT / "inputs/codebook.json")],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.framework = json.loads((self.workspace / "framework.json").read_text())
        self.source = self.workspace / "inputs/documents/example.txt"
        self.tasks = self.workspace / "tasks/fixture"
        self.row = {"task_id": "example", "objective": "Summarize synthetic local evidence",
                    "route": "local_agent", "documents": "inputs/documents/example.txt"}
        self.args = argparse.Namespace(runtime="codex", model="synthetic", coder_mode=None,
            resume=False, dry_run=False, retries=0, ignore_user_config=False, unsafe_unattended=False,
            child_agent_audit="enforce", session_timeout_sec=0, direct_api_timeout_sec=30,
            coder_api_timeout_sec=30, codex_search_mode="provider", native_archive_mode="metadata",
            native_retriever_backend="jina", native_retrieval_timeout_sec=30)
        self.commands = []

    def run_row(self, mode="success", row=None):
        selected_row = row or self.row

        def command(model, task_root, session_ref="", *args, **kwargs):
            self.commands.append((model, session_ref))
            return [sys.executable, str(ROOT / "tests/fixtures/fake_worker.py"),
                    str(task_root), str(self.workspace), mode]

        def api_command(task_root, coder=False):
            self.commands.append(("coder" if coder else "direct", ""))
            return [sys.executable, str(ROOT / "tests/fixtures/fake_worker.py"),
                    str(task_root), str(self.workspace), "coder" if coder else "success"]

        async def exercise():
            progress = batch.Progress(self.tasks, ["example"])
            registry = batch.ProcessRegistry()
            control = batch.BatchControl("abort", 1, 0, progress, registry)
            return await batch.run_one(selected_row, 0, self.framework, self.args, self.tasks,
                                       asyncio.Semaphore(1), progress, control, registry)

        with mock.patch.object(batch, "FRAMEWORK_ROOT", self.workspace), \
             mock.patch.object(batch, "codex_command", side_effect=command), \
             mock.patch.object(batch, "api_command", side_effect=api_command), \
             contextlib.redirect_stdout(io.StringIO()):
            return asyncio.run(exercise())

    def test_research_lifecycle_and_sealed_completed_resume(self):
        result = self.run_row()
        self.assertEqual(result["status"], "pass", result)
        task = self.tasks / "example"
        verify_completion(task)
        original = {p.relative_to(task).as_posix(): p.read_bytes() for p in task.rglob("*") if p.is_file()}
        self.args.resume = True
        resumed = self.run_row()
        self.assertEqual(resumed["status"], "resumed", resumed)
        self.assertEqual(len(self.commands), 1)
        self.assertEqual(original, {p.relative_to(task).as_posix(): p.read_bytes() for p in task.rglob("*") if p.is_file()})

    def test_same_session_structured_lifecycle(self):
        self.row["deliverable"] = "structured"
        result = self.run_row()
        self.assertEqual(result["status"], "pass", result)
        self.assertTrue((self.tasks / "example/output/final_report.json").is_file())
        verify_completion(self.tasks / "example")

    def test_missing_sealed_output_cannot_restart_worker(self):
        result = self.run_row()
        self.assertEqual(result["status"], "pass", result)
        (self.tasks / "example/research_report.md").unlink()
        self.args.resume = True
        result = self.run_row()
        self.assertEqual(result["status"], "fail", result)
        self.assertIn("completion manifest", result["error"])
        self.assertEqual(len(self.commands), 1)

    def test_failed_gate_then_same_session_continuation(self):
        self.args.retries = 1
        result = self.run_row("fail-first")
        self.assertEqual(result["status"], "pass", result)
        self.assertEqual([ref for _, ref in self.commands], ["", "fixture-session"])
        self.assertTrue((self.tasks / "example/session.attempt-2.log").exists())
        state = json.loads((self.tasks / "example/session_state.json").read_text())
        self.assertEqual([item["attempt"] for item in state["attempts"]], [1, 2])

    def test_missing_identity_cannot_start_an_independent_retry(self):
        self.args.retries = 2
        result = self.run_row("no-session")
        self.assertEqual(result["status"], "fail", result)
        self.assertEqual(len(self.commands), 1)
        self.args.resume = True
        resumed = self.run_row()
        self.assertEqual(resumed["status"], "fail", resumed)
        self.assertIn("session identity", resumed["error"])
        self.assertEqual(len(self.commands), 1)

    def test_explicit_partial_resume_keeps_old_attempt_logs(self):
        first = self.run_row("fail-first")
        self.assertEqual(first["status"], "fail", first)
        log = self.tasks / "example/session.attempt-1.log"
        original = log.read_bytes()
        self.args.resume = True
        result = self.run_row()
        self.assertEqual(result["status"], "pass", result)
        self.assertEqual(log.read_bytes(), original)
        self.assertTrue((self.tasks / "example/session.attempt-2.log").is_file())
        self.assertEqual(self.commands[-1][1], "fixture-session")

    def test_changed_session_is_rejected(self):
        self.args.retries = 1
        result = self.run_row("changed-session")
        self.assertEqual(result["status"], "fail", result)
        self.assertIn("different session", result["error"])
        self.assertFalse((self.tasks / "example/completion_manifest.json").exists())

    def test_direct_api_launch_path_uses_no_agent(self):
        self.row["route"] = "direct_api"
        result = self.run_row()
        self.assertEqual(result["status"], "pass", result)
        self.assertEqual(self.commands, [("direct", "")])

    def test_optional_api_coder_launch_path(self):
        self.row["coder_mode"] = "api"
        result = self.run_row()
        self.assertEqual(result["status"], "pass", result)
        self.assertEqual(self.commands, [("synthetic", ""), ("coder", "")])

    def test_row_model_override_reaches_worker_and_identity(self):
        self.row["model"] = "another-synthetic-model"
        result = self.run_row()
        self.assertEqual(result["status"], "pass", result)
        self.assertEqual(self.commands[0][0], self.row["model"])
        identity = json.loads((self.tasks / "example/input_identity.json").read_text())
        self.assertEqual(identity["execution"]["model"], self.row["model"])


class ScaffoldTests(unittest.TestCase):
    def test_user_literals_and_bytes_are_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            instruction = root / "instruction.md"
            supplied = b'# Contract\r\nKeep __PROJECT_NAME__ literal.\r\n## Codebook\r\n```json\r\n{"value":"__PROJECT_NAME__"}\r\n```\r\n'
            instruction.write_bytes(supplied)
            schema = root / "schema.json"
            schema.write_text('{"type":"object","description":"__PROJECT_NAME__"}')
            workspace = root / "workspace"
            result = subprocess.run([sys.executable, str(SKILL / "scripts/create_workspace.py"),
                                     "--target", str(workspace), "--instruction", str(instruction),
                                     "--output-schema", str(schema), "--project-name", 'quoted "name"'],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual((workspace / "inputs/instruction.md").read_bytes(), supplied)
            self.assertEqual((workspace / "schemas/user_output.schema.json").read_bytes(), schema.read_bytes())
            self.assertEqual(json.loads((workspace / "inputs/codebook.json").read_text()), {"value": "__PROJECT_NAME__"})

    def test_case_collision_fails_before_task_preparation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps([{"task_id": value, "objective": "A fixture"} for value in ("Case", "case")]))
            tasks = root / "tasks"
            result = subprocess.run([sys.executable, str(ROOT / "batch.py"), "--manifest", str(manifest),
                                     "--tasks-dir", str(tasks), "--dry-run"], cwd=ROOT, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("case-insensitive duplicate", result.stdout + result.stderr)
            self.assertFalse(tasks.exists())


if __name__ == "__main__":
    unittest.main()
