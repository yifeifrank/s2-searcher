"""Offline regressions for reusable routing, evidence and execution behavior."""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import copy
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import batch
import local_ingest
import validate
import api_coder
from router import route_contract
from tools import research_tools as rt
from tools.task_integrity import batch_directory_lock, seal_completion, verify_completion


class CoreFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.task = self.root / "task"
        self.source = self.root / "source.txt"
        self.source.write_text("Alpha had 42 observations.\nBeta had 19.\n", encoding="utf-8")
        self.framework = json.loads((ROOT / "framework.json").read_text())
        self.contract = batch.contract_from_row({
            "task_id": "example", "objective": "Summarize the supplied source",
            "documents": [str(self.source)], "route": "local_agent",
        }, 0, self.framework)
        self.decision = route_contract(self.contract, self.framework)

    def paths(self, task=None):
        task = task or self.task
        pages = task / "cache/pages"
        shared = self.root / "shared/pages"
        pages.mkdir(parents=True, exist_ok=True)
        shared.mkdir(parents=True, exist_ok=True)
        return rt.TaskPaths(task, ROOT, pages, task / "cache/cache_index.jsonl",
                            task / "cache/research_log.ndjson", task / "evidence.ndjson",
                            shared, shared.parent / "index.jsonl")

    def prepare(self):
        batch.setup_task(self.task, self.contract, self.decision)
        report = "# Research Report\n## Summary\nA bounded summary of the supplied source.\n## Evidence\nThe source reports Alpha and Beta counts.\n## Conflicts and Uncertainty\nNo reconciliation was performed.\n## Open Gaps\nThis is a synthetic engineering fixture.\n"
        (self.task / "research_report.md").write_text(report)


class RoutingTests(unittest.TestCase):
    def test_compact_inline_defaults_to_direct(self):
        contract = {"input": {"inline_text": "Alpha had 42 observations."}}
        self.assertEqual(route_contract(contract)["selected_route"], "direct_api")

    def test_external_requirement_still_wins(self):
        contract = {"input": {"inline_text": "A lead", "requires_external_search": True}}
        self.assertEqual(route_contract(contract)["selected_route"], "online_agent")

    def test_structured_deliverable_is_independent_of_local_route(self):
        decision = route_contract({"input": {"document_scope": "fixed_collection"},
                                   "routing": {"deliverable": "structured"}})
        self.assertEqual((decision["selected_route"], decision["coder_mode"], decision["deliverable"]),
                         ("local_agent", "none", "structured"))
        prompt = batch.render_prompt(Path("/synthetic/task"), decision)
        self.assertIn("validate.py task", prompt)
        self.assertNotIn("Do not create output/final_report.json", prompt)

    def test_conflicting_deliverable_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "require deliverable=structured"):
            route_contract({"routing": {"requested_route": "local_agent", "coder_mode": "api",
                                         "deliverable": "research_report"}})

    def test_portable_reserved_names(self):
        for value in ("CON", "nul.txt", "LPT1", "aux", "batch_summary.json"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "Reserved"):
                batch.safe_task_id({"task_id": value}, 0)


class InputAndValidationTests(CoreFixture):
    def test_changed_objective_cannot_replace_saved_contract(self):
        self.prepare()
        original = (self.task / "task_contract.json").read_bytes()
        changed = copy.deepcopy(self.contract)
        changed["objective"] = "A different question"
        with self.assertRaisesRegex(ValueError, "Resume refused"):
            batch.setup_task(self.task, changed, route_contract(changed, self.framework), resume=True)
        self.assertEqual((self.task / "task_contract.json").read_bytes(), original)

    def test_changed_source_cannot_resume(self):
        self.prepare()
        self.source.write_text("Alpha had 99 observations.")
        with self.assertRaisesRegex(ValueError, "Resume refused"):
            batch.setup_task(self.task, self.contract, self.decision, resume=True)

    def test_same_inputs_can_resume_but_default_cannot_overwrite(self):
        self.prepare()
        self.assertTrue(batch.setup_task(self.task, self.contract, self.decision, resume=True))
        with self.assertRaisesRegex(ValueError, "already contains"):
            batch.setup_task(self.task, self.contract, self.decision)

    def test_missing_source_fails_before_task_is_created(self):
        self.source.unlink()
        with self.assertRaisesRegex(ValueError, "Required input is missing"):
            batch.setup_task(self.task, self.contract, self.decision)
        self.assertFalse(self.task.exists())

    def test_relative_sources_are_resolved_before_launch(self):
        self.contract["input"]["documents"] = ["inputs/documents/example.txt"]
        self.prepare()
        saved = json.loads((self.task / "task_contract.json").read_text())
        self.assertEqual(saved["input"]["documents"], [str(ROOT / "inputs/documents/example.txt")])

    def test_default_research_gate_rejects_empty_contract(self):
        self.prepare()
        (self.task / "task_contract.json").write_text("{}")
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            validate.research_check(self.task)

    def test_evidence_schema_and_saved_bytes_are_checked(self):
        self.prepare()
        paths = self.paths()
        record = rt.store_cache(paths, "https://example.test/a", "Alpha had 42 observations.\n", "fixture")
        item = rt.validate_archive_citation(paths, {"cache_key": record["cache_key"], "start_line": 1, "end_line": 1})
        rt.append_ndjson(paths.evidence_file, item)  # archived_at deliberately omitted
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            validate.research_check(self.task)
        item["archived_at"] = rt.now_iso()
        paths.evidence_file.write_text(json.dumps(item) + "\n")
        with contextlib.redirect_stdout(io.StringIO()):
            validate.research_check(self.task)
        (paths.task_root / record["markdown_path"]).write_text("Alpha had 99 observations.\n")
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            validate.research_check(self.task)

    def test_completion_manifest_detects_mutation(self):
        self.prepare()
        seal_completion(self.task)
        verify_completion(self.task)
        (self.task / "research_report.md").write_text("changed")
        with self.assertRaisesRegex(ValueError, "differ"):
            verify_completion(self.task)

    def test_batch_directory_lock_prevents_competing_controller(self):
        with batch_directory_lock(self.root):
            with self.assertRaisesRegex(ValueError, "Another launcher"):
                with batch_directory_lock(self.root):
                    self.fail("Second controller acquired the lock")
        with batch_directory_lock(self.root):
            pass  # release is automatic; no stale lock cleanup required

    def test_api_coder_rejects_unresolved_capture_before_request(self):
        self.contract["routing"]["coder_mode"] = "api"
        self.decision = route_contract(self.contract, self.framework)
        self.prepare()
        rt.append_ndjson(self.task / "evidence.ndjson", {"cache_key": "missing", "start_line": 1,
            "end_line": 1, "archived_at": rt.now_iso(), "page_sha256": "0" * 64,
            "source_excerpt": "Missing capture", "content_kind": "source_text"})
        client = mock.Mock()
        with mock.patch.dict(sys.modules, {"openai": SimpleNamespace(OpenAI=client)}):
            with self.assertRaisesRegex(RuntimeError, "unindexed"):
                api_coder.execute(self.task, ROOT)
        client.assert_not_called()

    def test_api_coder_checks_real_archive_then_makes_one_mocked_call(self):
        self.contract["routing"]["coder_mode"] = "api"
        self.decision = route_contract(self.contract, self.framework)
        self.prepare()
        paths = self.paths()
        record = rt.store_cache(paths, "https://example.test/a", "Alpha had 42.\n", "fixture")
        evidence = rt.validate_archive_citation(paths, {"cache_key": record["cache_key"], "start_line": 1, "end_line": 1})
        evidence["archived_at"] = rt.now_iso()
        rt.append_ndjson(paths.evidence_file, evidence)
        response = mock.Mock(output_text=json.dumps({"final_output": {"replace_me": None}, "summary": "Fixture", "open_gaps": []}))
        response.model_dump.return_value = {"id": "synthetic-response"}
        client = mock.Mock()
        client.return_value.responses.create.return_value = response
        with mock.patch.dict(sys.modules, {"openai": SimpleNamespace(OpenAI=client)}), \
             mock.patch.dict(os.environ, {"EVIDENCE_CODER_MODEL": "synthetic", "EVIDENCE_CODER_API_KEY": "test-only-placeholder"}), \
             contextlib.redirect_stdout(io.StringIO()) as stream:
            result = api_coder.execute(self.task, ROOT)
        self.assertEqual(result["final_output"], {"replace_me": None})
        self.assertEqual(stream.getvalue(), "")
        client.return_value.responses.create.assert_called_once()


class EvidenceTests(CoreFixture):
    def test_refresh_keeps_old_citation_and_latest_backend(self):
        paths = self.paths()
        first = rt.store_cache(paths, "https://example.test/a", "Alpha had 42.\n", "first")
        original = (self.task / first["markdown_path"]).read_bytes()
        second = rt.store_cache(paths, "https://example.test/a", "Alpha had 99.\n", "second")
        self.assertNotEqual(first["cache_key"], second["cache_key"])
        self.assertEqual((self.task / first["markdown_path"]).read_bytes(), original)
        self.assertEqual(rt.lookup_cache(paths.local_index, "https://example.test/a")["backend"], "second")
        item = rt.validate_archive_citation(paths, {"cache_key": first["cache_key"], "start_line": 1, "end_line": 1})
        self.assertEqual(item["source_excerpt"], "Alpha had 42.")

    def test_changed_local_bytes_create_revision_without_force(self):
        paths = self.paths()
        args = argparse.Namespace(task_root=str(self.task), path=str(self.source), title=None, force_refresh=False)
        with mock.patch.object(local_ingest, "task_paths", return_value=paths):
            with contextlib.redirect_stdout(io.StringIO()) as stream:
                local_ingest.command_ingest(args)
            first = json.loads(stream.getvalue())
            self.source.write_text("Alpha had 99 observations.\n")
            with contextlib.redirect_stdout(io.StringIO()) as stream:
                local_ingest.command_ingest(args)
            second = json.loads(stream.getvalue())
        self.assertNotEqual(first["cache_key"], second["cache_key"])
        self.assertIn("42", (self.task / first["markdown_path"]).read_text())

    def test_unlocated_evidence_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "require cache_key"):
            rt.validate_archive_citation(self.paths(), {"summary": "An unsupported claim"})

    def test_quote_and_ordered_fragments_are_distinct_from_summary(self):
        paths = self.paths()
        record = rt.store_cache(paths, "https://example.test/a", "Alpha 42; Beta 19.\n", "fixture")
        item = {"cache_key": record["cache_key"], "start_line": 1, "end_line": 1,
                "quote_fragments": ["Alpha", "Beta 19"], "summary": "Two reported counts"}
        rt.validate_archive_citation(paths, item)
        for extra in ({"exact_quote": "Alpha 99"}, {"quote_fragments": ["Beta", "Alpha"]}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                rt.validate_archive_citation(paths, {**item, **extra})

    def test_generated_summary_cannot_be_source_quotation(self):
        paths = self.paths()
        record = rt.store_cache(paths, "https://example.test/a", "Generated summary\n", "fixture", content_kind="summary")
        with self.assertRaisesRegex(ValueError, "summary"):
            rt.validate_archive_citation(paths, {"cache_key": record["cache_key"], "start_line": 1,
                                                 "end_line": 1, "exact_quote": "Generated summary"})

    def test_exa_summary_only_response_fails_source_retrieval(self):
        with mock.patch.object(rt, "http_json", return_value={"results": [{"summary": "A generated summary"}]}):
            with self.assertRaisesRegex(RuntimeError, "summary is not source text"):
                rt._retrieve_exa_once("https://example.test/a", 1, "dummy")

    def test_missing_global_page_fails_before_materializing(self):
        paths = self.paths()
        record = rt.store_cache(paths, "https://example.test/a", "Alpha\n", "fixture")
        (paths.global_pages / f"{record['cache_key']}.md").unlink()
        other = self.paths(self.root / "other")
        with self.assertRaisesRegex(ValueError, "Global cache snapshot"):
            rt.materialize_global_hit(other, record)
        self.assertFalse((other.task_root / record["markdown_path"]).exists())

    def test_external_search_import_saves_capture_and_log(self):
        paths = self.paths()
        captured = self.root / "search.json"
        captured.write_text(json.dumps({"results": [{"url": "https://example.test/a", "title": "Alpha"}]}))
        args = argparse.Namespace(task_root=str(self.task), results_path=str(captured), backend="native", query="Alpha")
        with mock.patch.object(rt, "task_paths", return_value=paths), contextlib.redirect_stdout(io.StringIO()):
            rt.command_record_search(args)
        row = rt.read_ndjson(paths.local_log)[0]
        self.assertEqual(row["action"], "search")
        self.assertEqual(hashlib.sha256((self.task / row["capture_path"]).read_bytes()).hexdigest(), row["capture_sha256"])

    def test_recovered_backend_quota_does_not_abort_batch(self):
        paths = self.paths()
        args = argparse.Namespace(task_root=str(self.task), search_intent=None, query="Alpha",
                                  backend="primary", num_results=3)
        with mock.patch.object(rt, "task_paths", return_value=paths), \
             mock.patch.dict(os.environ, {"SEARCH_API_FALLBACKS": "fallback"}), \
             mock.patch.object(rt, "search_backend", side_effect=[RuntimeError("HTTP 402 Payment Required"), [{"url": "https://example.test/a"}]]), \
             contextlib.redirect_stdout(io.StringIO()):
            rt.command_search(args)
        warnings = batch.extract_research_tool_warnings(self.task)
        self.assertEqual(warnings["research_tool_failure_categories"], [])
        self.assertEqual(warnings["research_tool_warning_count"], 0)
        self.assertEqual(len(rt.read_ndjson(paths.local_log)), 2)  # raw failure and recovery retained

    def test_exhausted_backend_quota_remains_fatal(self):
        paths = self.paths()
        args = argparse.Namespace(task_root=str(self.task), search_intent=None, query="Alpha",
                                  backend="primary", num_results=3)
        with mock.patch.object(rt, "task_paths", return_value=paths), \
             mock.patch.dict(os.environ, {"SEARCH_API_FALLBACKS": ""}), \
             mock.patch.object(rt, "search_backend", side_effect=RuntimeError("HTTP 402 Payment Required")), \
             self.assertRaises(SystemExit):
            rt.command_search(args)
        self.assertEqual(batch.extract_research_tool_warnings(self.task)["research_tool_failure_categories"], ["quota"])


class ProcessTests(unittest.TestCase):
    def test_timeout_retains_flushed_output_in_memory_and_live_log(self):
        async def exercise(path):
            return await batch.run_process([sys.executable, "-c", "import time; print('PRESERVE_MARKER', flush=True); time.sleep(60)"],
                                           None, 0.4, batch.ProcessRegistry(), path)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "live.log"
            rc, output, timed_out = asyncio.run(exercise(path))
            self.assertTrue(timed_out)
            self.assertNotEqual(rc, 0)
            self.assertIn(b"PRESERVE_MARKER", output)
            self.assertEqual(path.read_bytes(), output)


if __name__ == "__main__":
    unittest.main()
