"""Synthetic worker used only by offline launcher lifecycle tests."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

task, framework = (Path(value) for value in sys.argv[1:3])
mode = sys.argv[3] if len(sys.argv) > 3 else "success"
sys.stdin.read()
counter = task / "fixture_calls.json"
calls = int(counter.read_text()) + 1 if counter.exists() else 1
counter.write_text(str(calls))
if mode != "no-session":
    identity = "different-session" if mode == "changed-session" and calls > 1 else "fixture-session"
    print(json.dumps({"type": "thread.started", "thread_id": identity}), flush=True)
if mode in {"fail-first", "changed-session", "no-session"} and calls == 1:
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}))
    raise SystemExit(0)

contract = json.loads((task / "task_contract.json").read_text())
decision = json.loads((task / "route_decision.json").read_text())
if mode == "native":
    (task / "plan.md").write_text("# Research Plan: Fixture\n## Objective\nExercise capture.\n## Steps\nOne offline event.\n## Todo\nT01\n## Expected Gaps\nNone.\n")
    worklog = {"contract_version": "online-research-v1-20260812", "status": "complete",
        "subject": "Synthetic topic", "task_id": contract["task_id"], "objective": contract["objective"],
        "todo": [{"id": "T01", "category": "fixture", "objective": "Exercise capture", "status": "complete",
                  "attempts": 1, "evidence_refs": [], "notes": "Synthetic metadata only"}],
        "search_iterations": [{"iteration": 1, "todo_ids": ["T01"], "query_summary": "Synthetic lookup",
            "outcome": "Offline capture", "new_cache_pages": 0, "new_evidence_records": 0}],
        "search_call_count": 0, "open_gaps": [], "frozen_pages": [], "frozen_page_count": 0,
        "evidence_count": 0, "completed_at": "synthetic"}
    (task / "searcher_worklog.json").write_text(json.dumps(worklog))
    (task / "research_report.md").write_text("# Research Report\n## Summary\nA synthetic native research result.\n## Evidence\n[Source](https://example.test/source)\n## Conflicts and Uncertainty\nSynthetic engineering check only.\n## Open Gaps\nNo scientific findings.\n")
    print(json.dumps({"type": "item.completed", "item": {"id": "native-1", "type": "web_search",
        "action": {"type": "search", "query": "Synthetic lookup"}, "url": "https://example.test/source"}}))
else:
    source = contract["input"]["documents"][0]
    ingested = json.loads(subprocess.check_output([
        sys.executable, str(framework / "tools/local_ingest.py"), "--task-root", str(task), "--path", source,
    ], text=True))
    page = task / ingested["markdown_path"]
    payload = {"cache_key": ingested["cache_key"], "start_line": 1,
               "end_line": len(page.read_text().splitlines()), "task_summary": "Synthetic fixture source"}
    subprocess.check_output([
        sys.executable, str(framework / "tools/research_tools.py"), "--task-root", str(task),
        "archive", "--payload-json", json.dumps(payload),
    ])
    (task / "research_report.md").write_text(
        "# Research Report\n## Summary\nA synthetic report exercising the deployed launcher.\n"
        "## Evidence\nThe fixture reports 42 observations, with source provenance: " + ingested["cache_key"] +
        "\n## Conflicts and Uncertainty\nNo scientific judgment is made.\n## Open Gaps\nNone within this synthetic scope.\n"
    )
if decision.get("deliverable") == "structured" and (decision.get("coder_mode") == "none" or mode == "coder"):
    codebook = json.loads(Path(contract["codebook_path"]).read_text())
    report = {"task_id": contract["task_id"], "status": "complete", "summary": "Synthetic extraction",
              "execution_route": decision["selected_route"], "final_output": {key: None for key in codebook},
              "open_gaps": [], "evidence_file": "evidence.ndjson"}
    (task / "output/final_report.json").write_text(json.dumps(report))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}}))
