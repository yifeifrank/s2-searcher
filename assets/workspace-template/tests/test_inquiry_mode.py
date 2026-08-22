#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import inquiry  # noqa: E402
import research_tools  # noqa: E402
from router import route_contract  # noqa: E402


def arguments(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "question": "What explains variation in legislative oversight?",
        "runtime": "codex",
        "task_id": "inquiry-test",
        "tasks_dir": "tasks/inquiries",
        "model": "",
        "local_only": False,
        "multilingual": False,
        "max_search_calls": None,
        "max_runtime_seconds": None,
        "retries": 0,
        "resume": False,
        "ignore_user_config": False,
        "dry_run": True,
        "unsafe_unattended": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class InquiryContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.framework = json.loads((ROOT / "framework.json").read_text(encoding="utf-8"))

    def test_online_inquiry_reuses_existing_agent_contract(self) -> None:
        contract = inquiry.inquiry_contract(arguments(), self.framework)
        decision = route_contract(contract, self.framework)
        self.assertEqual(contract["metadata"]["work_mode"], "inquiry")
        self.assertEqual(contract["instructions_path"], "inputs/inquiry_instruction.md")
        self.assertEqual(decision["selected_route"], "online_agent")
        self.assertEqual(decision["coder_mode"], "none")

    def test_local_only_inquiry_uses_local_agent(self) -> None:
        contract = inquiry.inquiry_contract(arguments(local_only=True), self.framework)
        decision = route_contract(contract, self.framework)
        self.assertEqual(decision["selected_route"], "local_agent")
        self.assertEqual(decision["recommended_budgets"]["max_search_calls"], 0)

    def test_existing_task_requires_matching_resume(self) -> None:
        contract = inquiry.inquiry_contract(arguments(), self.framework)
        with tempfile.TemporaryDirectory() as temporary:
            task_root = Path(temporary)
            (task_root / "artifact.txt").write_text("existing", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "already contains files"):
                inquiry.ensure_task_target(task_root, contract, resume=False)
            (task_root / "task_contract.json").write_text(
                json.dumps(contract), encoding="utf-8"
            )
            inquiry.ensure_task_target(task_root, contract, resume=True)


class LibrarySearchTests(unittest.TestCase):
    def test_library_search_materializes_ranked_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            global_pages = root / "library" / "pages"
            global_pages.mkdir(parents=True)
            local_pages = root / "task" / "cache" / "pages"
            local_pages.mkdir(parents=True)
            text = "# Oversight\n\nLegislative oversight committees may hold subpoena authority.\n"
            cache_key = "example_oversight_1234"
            (global_pages / f"{cache_key}.md").write_text(text, encoding="utf-8")
            global_index = root / "library" / "index.jsonl"
            global_index.write_text(
                json.dumps(
                    {
                        "cache_key": cache_key,
                        "canonical_url": "https://example.org/oversight",
                        "backend": "test",
                        "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    }
                ) + "\n",
                encoding="utf-8",
            )
            paths = research_tools.TaskPaths(
                task_root=root / "task",
                framework_root=root,
                local_pages=local_pages,
                local_index=root / "task" / "cache" / "cache_index.jsonl",
                local_log=root / "task" / "cache" / "research_log.ndjson",
                evidence_file=root / "task" / "evidence.ndjson",
                global_pages=global_pages,
                global_index=global_index,
            )
            results = research_tools.search_library(
                paths, "legislative oversight", 5, materialize=True
            )
            self.assertEqual(results[0]["cache_key"], cache_key)
            self.assertEqual(results[0]["markdown_path"], f"cache/pages/{cache_key}.md")
            self.assertTrue((local_pages / f"{cache_key}.md").is_file())


if __name__ == "__main__":
    unittest.main()
