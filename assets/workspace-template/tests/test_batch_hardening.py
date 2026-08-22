#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import batch  # noqa: E402


def event(value: dict) -> bytes:
    return (json.dumps(value) + "\n").encode("utf-8")


class EventParsingTests(unittest.TestCase):
    def test_codex_session_usage_and_child_agent_rejection(self) -> None:
        output = b"".join(
            [
                event({"type": "thread.started", "thread_id": "thread-1"}),
                event(
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "collab_tool_call",
                            "tool": "spawn_agent",
                            "agent_type": "worker",
                            "prompt": "Act as a focused evidence worker.",
                            "receiver_thread_ids": ["child-1"],
                        },
                    }
                ),
                event(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 100,
                            "cached_input_tokens": 20,
                            "output_tokens": 30,
                            "reasoning_output_tokens": 10,
                        },
                    }
                ),
            ]
        )
        meta = batch.parse_session_events(output)
        self.assertEqual(meta["session_ref"], "thread-1")
        self.assertEqual(meta["usage"]["total_tokens"], 130)
        audit = batch.child_agent_audit(
            {"selected_route": "online_agent"},
            meta,
        )
        self.assertFalse(audit["child_agent_audit_verified"])
        self.assertEqual(audit["child_invocation_count"], 1)

    def test_standalone_online_rejects_any_child(self) -> None:
        output = event(
            {
                "type": "item.completed",
                "item": {
                    "type": "collab_tool_call",
                    "tool": "spawn_agent",
                    "prompt": "You are a research child.",
                    "receiver_thread_ids": ["wrong-child"],
                },
            }
        )
        audit = batch.child_agent_audit(
            {"selected_route": "online_agent"},
            batch.parse_session_events(output),
        )
        self.assertFalse(audit["child_agent_audit_verified"])

    def test_claude_session_and_agent_tool_are_parsed(self) -> None:
        output = b"".join(
            [
                event({"type": "system", "subtype": "init", "session_id": "claude-1"}),
                event(
                    {
                        "type": "assistant",
                        "message": {
                            "content": [
                                {
                                    "type": "tool_use",
                                    "name": "Agent",
                                    "id": "tool-1",
                                    "input": {
                                        "subagent_type": "worker",
                                        "description": "Focused evidence agent",
                                    },
                                }
                            ]
                        },
                    }
                ),
                event(
                    {
                        "type": "result",
                        "session_id": "claude-1",
                        "usage": {
                            "input_tokens": 50,
                            "cache_read_input_tokens": 10,
                            "output_tokens": 15,
                        },
                        "is_error": False,
                    }
                ),
            ]
        )
        meta = batch.parse_session_events(output)
        self.assertEqual(meta["session_ref"], "claude-1")
        self.assertEqual(meta["child_invocation_count"], 1)
        self.assertEqual(meta["usage"]["cached_input_tokens"], 10)

    def test_native_search_capture_preserves_queries_actions_and_report_urls(self) -> None:
        output = b"".join(
            [
                event(
                    {
                        "type": "item.completed",
                        "item": {
                            "id": "search-1",
                            "type": "web_search",
                            "action": {"type": "search", "query": "official election archive"},
                        },
                    }
                ),
                event(
                    {
                        "type": "item.completed",
                        "item": {
                            "id": "message-1",
                            "type": "agent_message",
                            "text": "See [Election authority](https://example.gov/results).",
                        },
                    }
                ),
                event(
                    {
                        "type": "item.completed",
                        "item": {
                            "id": "open-1",
                            "type": "web_search",
                            "query": "https://example.gov/results",
                            "action": {"type": "other"},
                        },
                    }
                ),
            ]
        )
        searches, sources = batch.extract_codex_native_archive(
            output,
            "Evidence: [Election authority](https://example.gov/results).",
        )
        self.assertEqual(searches[0]["query"], "official election archive")
        self.assertEqual(searches[0]["action"], "search")
        self.assertEqual(searches[1]["action"], "open_page")
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["url"], "https://example.gov/results")
        self.assertEqual(sources[0]["title"], "Election authority")
        self.assertTrue(sources[0]["used_in_report"])
        self.assertEqual(
            sources[0]["observed_in"],
            ["agent_message", "native_search_event", "research_report"],
        )

    def test_codex_native_search_changes_acquisition_prompt_only(self) -> None:
        decision = {
            "selected_route": "online_agent",
            "workflow_prompt": "prompts/workflows/online_agent.md",
            "reasons": ["External search is required."],
        }
        batch.apply_runtime_search_mode(decision, "codex", "native")
        self.assertEqual(decision["workflow_prompt"], batch.CODEX_NATIVE_WORKFLOW)
        provider = {
            "selected_route": "online_agent",
            "workflow_prompt": "prompts/workflows/online_agent.md",
            "reasons": [],
        }
        batch.apply_runtime_search_mode(provider, "codex", "provider")
        self.assertEqual(provider["workflow_prompt"], "prompts/workflows/online_agent.md")

    def test_native_finalizer_writes_metadata_and_mechanical_worklog_fields(self) -> None:
        async def exercise(task_root: Path) -> None:
            (task_root / "cache" / "pages").mkdir(parents=True)
            (task_root / "evidence.ndjson").write_text("", encoding="utf-8")
            (task_root / "research_report.md").write_text(
                "# Research Report\n\n## Evidence\n\n[Official](https://example.gov/source)\n",
                encoding="utf-8",
            )
            (task_root / "searcher_worklog.json").write_text(
                json.dumps(
                    {
                        "search_call_count": 0,
                        "frozen_pages": [],
                        "frozen_page_count": 0,
                        "evidence_count": 0,
                    }
                ),
                encoding="utf-8",
            )
            output = event(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "search-1",
                        "type": "web_search",
                        "query": "site:example.gov official source",
                        "action": {"type": "search"},
                    },
                }
            )
            await batch.finalize_codex_native_archive(
                task_root,
                output,
                "metadata",
                "jina",
                30,
                batch.NativeSnapshotCoordinator(),
                batch.ProcessRegistry(),
            )

        with tempfile.TemporaryDirectory() as temporary:
            task_root = Path(temporary)
            asyncio.run(exercise(task_root))
            searches = [
                json.loads(line)
                for line in (task_root / "cache" / "native_searches.ndjson").read_text().splitlines()
            ]
            sources = [
                json.loads(line)
                for line in (task_root / "cache" / "native_sources.ndjson").read_text().splitlines()
            ]
            worklog = json.loads((task_root / "searcher_worklog.json").read_text())
        self.assertEqual(len(searches), 1)
        self.assertEqual(sources[0]["snapshot"]["status"], "not_requested")
        self.assertEqual(worklog["search_capture_mode"], "codex_native")
        self.assertEqual(worklog["search_call_count"], 1)
        self.assertEqual(worklog["native_source_count"], 1)


class BudgetTests(unittest.TestCase):
    def test_generous_defaults_remain_user_overridable(self) -> None:
        framework = json.loads((ROOT / "framework.json").read_text(encoding="utf-8"))
        default_contract = batch.contract_from_row(
            {"task_id": "default", "objective": "Research a public topic"}, 0, framework
        )
        self.assertEqual(default_contract["budgets"]["max_search_calls"], 40)
        self.assertEqual(default_contract["budgets"]["max_runtime_seconds"], 3600)

        custom_contract = batch.contract_from_row(
            {
                "task_id": "custom",
                "objective": "Research a bounded public topic",
                "max_search_calls": 12,
                "max_runtime_seconds": 900,
            },
            1,
            framework,
        )
        self.assertEqual(custom_contract["budgets"]["max_search_calls"], 12)
        self.assertEqual(custom_contract["budgets"]["max_runtime_seconds"], 900)

    def test_worklog_schema_has_no_architecture_level_search_cap(self) -> None:
        schema = json.loads(
            (ROOT / "schemas/searcher_worklog.schema.json").read_text(encoding="utf-8")
        )
        self.assertNotIn("maximum", schema["properties"]["search_call_count"])

    def test_quota_is_fatal_but_reconnect_notice_is_not(self) -> None:
        output = b"".join(
            [
                event({"type": "error", "message": "Reconnecting... 1/5"}),
                event(
                    {
                        "type": "turn.failed",
                        "error": {"message": "You've hit your usage limit; purchase more credits."},
                    }
                ),
            ]
        )
        meta = batch.parse_session_events(output)
        warnings = batch.extract_api_warnings(output, meta)
        self.assertEqual(warnings["api_failure_categories"], ["quota"])
        self.assertEqual(meta["error_messages"], ["You've hit your usage limit; purchase more credits."])

    def test_best_effort_native_snapshot_error_does_not_abort_research(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            task_root = Path(temporary)
            log = task_root / "cache" / "research_log.ndjson"
            log.parent.mkdir(parents=True)
            log.write_text(
                json.dumps(
                    {
                        "action": "retrieve",
                        "status": "error",
                        "backend": "jina",
                        "context": "native_snapshot",
                        "error": "401 Unauthorized",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            warnings = batch.extract_research_tool_warnings(task_root)
        self.assertFalse(warnings["research_tool_failure"])
        self.assertEqual(warnings["research_tool_warning_count"], 0)

    def test_successful_provider_fallback_recovers_primary_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            task_root = Path(temporary)
            log = task_root / "cache" / "research_log.ndjson"
            log.parent.mkdir(parents=True)
            rows = [
                {
                    "action": "retrieve",
                    "status": "error",
                    "backend": "exa",
                    "url": "https://example.org/source",
                    "error": "401 Unauthorized",
                },
                {
                    "action": "retrieve",
                    "status": "success",
                    "backend": "firecrawl",
                    "url": "https://example.org/source",
                },
            ]
            log.write_text(
                "".join(json.dumps(row) + "\n" for row in rows),
                encoding="utf-8",
            )
            warnings = batch.extract_research_tool_warnings(task_root)
        self.assertFalse(warnings["research_tool_failure"])
        self.assertEqual(warnings["research_tool_failure_categories"], [])


class ProcessControlTests(unittest.TestCase):
    def test_resume_commands_keep_prompt_on_stdin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            task_root = Path(temporary)
            old = batch.os.environ.get("CODEX_EXE")
            batch.os.environ["CODEX_EXE"] = "/tmp/fake-codex"
            try:
                command = batch.codex_command("", task_root, "thread-123")
            finally:
                if old is None:
                    batch.os.environ.pop("CODEX_EXE", None)
                else:
                    batch.os.environ["CODEX_EXE"] = old
            self.assertEqual(command[0], "/tmp/fake-codex")
            self.assertLess(command.index("--ask-for-approval"), command.index("exec"))
            self.assertEqual(command[command.index("exec"):command.index("exec") + 2], ["exec", "resume"])
            self.assertEqual(command[-2:], ["thread-123", "-"])
            self.assertIn("workspace-write", command)
            self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", command)
            claude = batch.claude_command("", "session-123")
            self.assertIn("--resume", claude)
            self.assertIn("stream-json", claude)
            self.assertIn("dontAsk", claude)
            self.assertNotIn("--dangerously-skip-permissions", claude)

    def test_native_search_flag_is_explicit_and_provider_mode_stays_unchanged(self) -> None:
        task_root = Path(tempfile.mkdtemp())
        old = batch.os.environ.get("CODEX_EXE")
        batch.os.environ["CODEX_EXE"] = "/tmp/fake-codex"
        try:
            native = batch.codex_command("", task_root, native_search=True)
            provider = batch.codex_command("", task_root, native_search=False)
        finally:
            if old is None:
                batch.os.environ.pop("CODEX_EXE", None)
            else:
                batch.os.environ["CODEX_EXE"] = old
        self.assertIn("--search", native)
        self.assertLess(native.index("--search"), native.index("exec"))
        self.assertNotIn("--search", provider)

    def test_unsafe_unattended_requires_explicit_opt_in(self) -> None:
        task_root = Path(tempfile.mkdtemp())
        old = batch.os.environ.get("CODEX_EXE")
        batch.os.environ["CODEX_EXE"] = "/tmp/fake-codex"
        try:
            command = batch.codex_command("", task_root, unsafe_unattended=True)
        finally:
            if old is None:
                batch.os.environ.pop("CODEX_EXE", None)
            else:
                batch.os.environ["CODEX_EXE"] = old
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", command)
        self.assertIn("--dangerously-skip-permissions", batch.claude_command("", unsafe_unattended=True))

    def test_timeout_terminates_process(self) -> None:
        async def exercise(pid_path: Path) -> tuple[int, bytes, bool, float]:
            registry = batch.ProcessRegistry()
            started = time.time()
            result = await batch.run_process(
                [
                    sys.executable,
                    "-c",
                    (
                        "import pathlib, subprocess, sys, time; "
                        "child=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
                        "pathlib.Path(sys.argv[1]).write_text(str(child.pid)); time.sleep(60)"
                    ),
                    str(pid_path),
                ],
                None,
                0.25,
                registry,
            )
            return *result, time.time() - started

        with tempfile.TemporaryDirectory() as temporary:
            pid_path = Path(temporary) / "child.pid"
            returncode, output, timed_out, elapsed = asyncio.run(exercise(pid_path))
            child_pid = int(pid_path.read_text())
            if sys.platform.startswith("linux"):
                stat_path = Path(f"/proc/{child_pid}/stat")
                state = stat_path.read_text().split()[2] if stat_path.exists() else "gone"
                self.assertIn(state, {"gone", "Z"}, "child process survived the process-group timeout")
        self.assertTrue(timed_out)
        self.assertNotEqual(returncode, 0)
        self.assertIn(b"process tree terminated", output)
        self.assertLess(elapsed, 8)

    def test_abort_sets_batch_gate_and_writes_progress(self) -> None:
        async def exercise() -> tuple[batch.BatchControl, Path]:
            temporary = tempfile.TemporaryDirectory()
            self.addCleanup(temporary.cleanup)
            root = Path(temporary.name)
            progress = batch.Progress(root, ["task-1"])
            control = batch.BatchControl("abort", 1, 0, progress, batch.ProcessRegistry())
            action = await control.handle_global_failure("task-1", ["auth"], "authentication failed")
            self.assertEqual(action, "abort")
            return control, progress.path

        control, path = asyncio.run(exercise())
        self.assertTrue(control.abort_event.is_set())
        self.assertEqual(json.loads(path.read_text())["batch_state"], "aborting")


if __name__ == "__main__":
    unittest.main()
