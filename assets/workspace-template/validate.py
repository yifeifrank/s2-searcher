#!/usr/bin/env python3
"""Validate a generated project, research freeze, or completed task."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from tools.instruction_contract import extract_codebook


ONLINE_WORKLOG_VERSION = "online-research-v1-20260812"


class Validation:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def require(self, condition: bool, message: str) -> None:
        if not condition:
            self.errors.append(message)

    def finish(self, label: str) -> None:
        if self.errors:
            print(f"FAIL: {label}")
            for item in self.errors:
                print(f"- {item}")
            raise SystemExit(1)
        print(f"OK: {label}")


def load_json(path: Path, check: Validation, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:  # noqa: BLE001
        check.errors.append(f"Invalid {label} at {path}: {exc}")
        return None


def load_ndjson(path: Path, check: Validation, label: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        check.errors.append(f"Missing {label}: {path}")
        return rows
    for number, raw in enumerate(path.read_text(encoding="utf-8-sig", errors="replace").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            item = json.loads(raw)
        except json.JSONDecodeError as exc:
            check.errors.append(f"Invalid {label} JSON on line {number}: {exc}")
            continue
        if not isinstance(item, dict):
            check.errors.append(f"{label} line {number} is not an object")
            continue
        rows.append(item)
    return rows


def validate_schema(instance: Any, schema_path: Path, check: Validation, label: str) -> None:
    try:
        import jsonschema
    except ImportError:
        check.errors.append(f"jsonschema is required to validate {label}; install requirements.txt")
        return
    schema = load_json(schema_path, check, label + " schema")
    if not isinstance(schema, dict):
        return
    try:
        jsonschema.validate(instance, schema)
    except jsonschema.ValidationError as exc:
        check.errors.append(f"{label} schema validation failed: {exc.message}")


def resolve_contract_path(value: Any, framework_root: Path, task_root: Path) -> Path:
    raw = Path(str(value or "")).expanduser()
    if raw.is_absolute():
        return raw.resolve()
    framework_candidate = (framework_root / raw).resolve()
    if framework_candidate.exists():
        return framework_candidate
    return (task_root / raw).resolve()


def project_check(root: Path) -> None:
    check = Validation()
    required = [
        "README.md",
        "AGENTS.md",
        "CLAUDE.md",
        "framework.json",
        "router.py",
        "validate.py",
        "batch.py",
        "inquiry.py",
        "direct_api.py",
        "api_coder.py",
        "task_instruction.md",
        ".env.example",
        "inputs/instruction.md",
        "inputs/codebook.json",
        "inputs/inquiry_instruction.md",
        "inputs/inquiry_codebook.json",
        "schemas/task_contract.schema.json",
        "schemas/route_decision.schema.json",
        "schemas/final_report.schema.json",
        "schemas/evidence_record.schema.json",
        "schemas/searcher_worklog.schema.json",
        "tools/research_tools.py",
        "tools/local_ingest.py",
        "tools/instruction_contract.py",
        "prompts/coder/api_coder.md",
        "prompts/workflows/local_agent.md",
        "prompts/workflows/online_agent.md",
    ]
    for relative in required:
        check.require((root / relative).is_file(), f"Missing required file: {relative}")
    for relative in (".codex/agents", ".claude/agents", "prompts/roles"):
        check.require(not (root / relative).exists(), f"Standalone package must not include an agent-role directory: {relative}")

    schema_files = [
        "schemas/task_contract.schema.json",
        "schemas/route_decision.schema.json",
        "schemas/final_report.schema.json",
        "schemas/evidence_record.schema.json",
        "schemas/searcher_worklog.schema.json",
    ]
    for relative in schema_files:
        schema = load_json(root / relative, check, relative)
        check.require(isinstance(schema, dict), f"{relative} must contain a JSON object")

    config = load_json(root / "framework.json", check, "framework configuration")
    codebook = load_json(root / "inputs/codebook.json", check, "codebook")
    inquiry_codebook = load_json(
        root / "inputs/inquiry_codebook.json", check, "inquiry codebook"
    )
    check.require(isinstance(config, dict), "framework.json must contain an object")
    check.require(isinstance(codebook, dict), "inputs/codebook.json must contain an object")
    check.require(
        isinstance(inquiry_codebook, dict),
        "inputs/inquiry_codebook.json must contain an object",
    )
    inquiry_instruction = root / "inputs/inquiry_instruction.md"
    if inquiry_instruction.is_file() and isinstance(inquiry_codebook, dict):
        try:
            embedded_inquiry_codebook = extract_codebook(
                inquiry_instruction.read_text(encoding="utf-8-sig")
            )
        except ValueError as exc:
            check.errors.append(f"Invalid inquiry instruction contract: {exc}")
        else:
            check.require(
                embedded_inquiry_codebook == inquiry_codebook,
                "inquiry_codebook.json differs from the codebook embedded in inquiry_instruction.md",
            )
    if isinstance(config, dict):
        instructions_path = root / str(config.get("instructions_path", ""))
        codebook_path = root / str(config.get("codebook_path", ""))
        check.require(instructions_path.is_file(), "framework instructions_path does not name a file")
        check.require(codebook_path.is_file(), "framework codebook_path does not name a file")
        if instructions_path.is_file() and isinstance(codebook, dict):
            try:
                embedded_codebook = extract_codebook(
                    instructions_path.read_text(encoding="utf-8-sig")
                )
            except ValueError as exc:
                check.errors.append(f"Invalid combined instruction contract: {exc}")
            else:
                check.require(
                    embedded_codebook == codebook,
                    "inputs/codebook.json differs from the codebook embedded in instruction.md",
                )
        runtime = config.get("runtime_support")
        check.require(runtime in {"codex", "claude", "both"}, "runtime_support must be codex, claude, or both")
        check.require(config.get("coder_mode_default") == "none", "coder_mode_default must remain none")
        if runtime in {"codex", "both"}:
            codex_files = [
                ".codex/config.toml",
                ".agents/skills/research-tools/SKILL.md",
            ]
            for relative in codex_files:
                check.require((root / relative).is_file(), f"Missing Codex file: {relative}")
            config_path = root / ".codex/config.toml"
            codex_config = config_path.read_text(encoding="utf-8") if config_path.is_file() else ""
            check.require("[agents" not in codex_config, "Standalone framework must not register child agents")
            check.require("multi_agent" not in codex_config, "Standalone framework must not enable multi-agent support")
            check.require('sandbox_mode = "workspace-write"' in codex_config, "Codex must default to workspace-write")
            check.require('approval_policy = "never"' in codex_config, "Codex batch mode must deny escalation without prompting")
            check.require("[sandbox_workspace_write]" in codex_config and "network_access = true" in codex_config, "Codex workspace-write must permit online research network access")
            check.require("[agents.coder]" not in codex_config, "Coder must not be registered as a Codex agent")
            check.require(not (root / ".codex/agents/coder.toml").exists(), "Codex coder agent file must be absent")
        if runtime in {"claude", "both"}:
            claude_files = [
                ".claude/settings.json",
                ".claude/skills/research-tools/SKILL.md",
            ]
            for relative in claude_files:
                check.require((root / relative).is_file(), f"Missing Claude file: {relative}")
            claude_settings = load_json(root / ".claude/settings.json", check, "Claude settings")
            if isinstance(claude_settings, dict):
                permissions = claude_settings.get("permissions", {})
                sandbox = claude_settings.get("sandbox", {})
                check.require(permissions.get("defaultMode") == "dontAsk", "Claude must default to non-escalating dontAsk mode")
                check.require("Agent" in permissions.get("deny", []), "Claude must deny child-agent launches")
                check.require(sandbox.get("enabled") is True, "Claude Bash sandbox must be enabled")
                check.require(sandbox.get("failIfUnavailable") is True, "Claude sandbox must fail closed when unavailable")
            check.require(not (root / ".claude/agents/coder.md").exists(), "Claude coder agent file must be absent")

    marker_checks = [
        ("task_instruction.md", "Do not launch child agents"),
        ("prompts/workflows/online_agent.md", ONLINE_WORKLOG_VERSION),
        ("prompts/workflows/online_agent.md", "must_include"),
        ("prompts/workflows/online_agent.md", "--force-refresh"),
        ("prompts/workflows/online_agent.md", "--payload-json"),
        ("prompts/workflows/online_agent.md", "research_report.md"),
        ("prompts/workflows/online_agent.md", "library-search"),
        ("prompts/workflows/local_agent.md", "library-search"),
        ("prompts/coder/api_coder.md", "one independent API request"),
        ("api_coder.py", "max_retries=0"),
        ("api_coder.py", '"model_call_count": 1'),
        ("batch.py", "For `local_agent` and `online_agent`"),
        ("batch.py", "--unsafe-unattended"),
        ("batch.py", "api-failure-mode"),
        ("batch.py", "kill_process_tree"),
        ("batch.py", "progress_reporter"),
        ("batch.py", "session_state.json"),
        ("inquiry.py", '"work_mode": "inquiry"'),
        ("inquiry.py", "--unsafe-unattended"),
        ("tools/research_tools.py", "library-search"),
        ("tools/local_ingest.py", "--share-with-library"),
    ]
    for relative, marker in marker_checks:
        path = root / relative
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        check.require(marker in text, f"Preserved workflow marker missing from {relative}: {marker}")
    check.finish("project structure, prompts, and JSON inputs are valid")


def evidence_check(task_root: Path, check: Validation) -> tuple[list[dict[str, Any]], list[str]]:
    evidence = load_ndjson(task_root / "evidence.ndjson", check, "evidence archive")
    index_rows = load_ndjson(task_root / "cache" / "cache_index.jsonl", check, "cache index")
    indexed: dict[str, dict[str, Any]] = {}
    indexed_pages: set[str] = set()
    for position, row in enumerate(index_rows, 1):
        key = str(row.get("cache_key", "")).strip()
        relative = str(row.get("markdown_path", "")).replace("\\", "/")
        check.require(bool(key), f"Cache index row {position} has no cache_key")
        check.require(bool(relative), f"Cache index row {position} has no markdown_path")
        if key:
            indexed[key] = row
        if relative:
            page = (task_root / relative).resolve()
            try:
                page.relative_to(task_root.resolve())
            except ValueError:
                check.errors.append(f"Cache index row {position} escapes the task workspace: {relative}")
            else:
                check.require(page.is_file(), f"Cache index row {position} refers to missing page: {relative}")
                indexed_pages.add(relative)

    actual_pages = sorted(
        path.relative_to(task_root).as_posix()
        for path in (task_root / "cache" / "pages").glob("*.md")
    ) if (task_root / "cache" / "pages").exists() else []
    check.require(indexed_pages == set(actual_pages), "cache_index.jsonl does not exactly inventory cache/pages/*.md")

    for position, row in enumerate(evidence, 1):
        key = str(row.get("cache_key", "")).strip()
        start = row.get("start_line")
        end = row.get("end_line")
        check.require(bool(key), f"Evidence row {position} has no cache_key")
        check.require(key in indexed, f"Evidence row {position} uses an unindexed cache_key: {key}")
        check.require(isinstance(start, int) and not isinstance(start, bool) and start >= 1, f"Evidence row {position} has invalid start_line")
        check.require(
            isinstance(end, int) and not isinstance(end, bool) and isinstance(start, int) and end >= start,
            f"Evidence row {position} has invalid end_line",
        )
        entry = indexed.get(key, {})
        relative = str(entry.get("markdown_path", f"cache/pages/{key}.md"))
        page = task_root / relative
        if page.is_file() and isinstance(end, int):
            line_count = len(page.read_text(encoding="utf-8-sig", errors="replace").splitlines())
            check.require(end <= line_count, f"Evidence row {position} ends at {end}, but {relative} has {line_count} lines")
    return evidence, actual_pages


def validate_research_report(task_root: Path, check: Validation) -> None:
    report_path = task_root / "research_report.md"
    try:
        report = report_path.read_text(encoding="utf-8-sig")
    except Exception as exc:  # noqa: BLE001
        check.errors.append(f"Invalid research_report.md: {exc}")
        return
    for heading in (
        "# Research Report",
        "## Summary",
        "## Evidence",
        "## Conflicts and Uncertainty",
        "## Open Gaps",
    ):
        check.require(heading in report, f"research_report.md missing heading: {heading}")
    check.require(len(report.strip()) >= 120, "research_report.md is too short to be a useful frozen handoff")


def validate_online_research(
    task_root: Path,
    contract: dict[str, Any],
    evidence: list[dict[str, Any]],
    pages: list[str],
    check: Validation,
) -> None:
    plan_path = task_root / "plan.md"
    try:
        plan = plan_path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        check.errors.append(f"Invalid plan.md: {exc}")
        plan = ""
    for heading in ("# Research Plan:", "## Objective", "## Steps", "## Todo", "## Expected Gaps"):
        check.require(heading in plan, f"plan.md missing heading: {heading}")

    worklog = load_json(task_root / "searcher_worklog.json", check, "online research worklog")
    if not isinstance(worklog, dict):
        return
    framework_root = Path(__file__).resolve().parent
    validate_schema(worklog, framework_root / "schemas/searcher_worklog.schema.json", check, "Searcher worklog")
    check.require(worklog.get("contract_version") == ONLINE_WORKLOG_VERSION, "Online research worklog version mismatch")
    check.require(worklog.get("status") == "complete", "Online research worklog is not complete")
    check.require(worklog.get("task_id") == contract.get("task_id"), "Searcher worklog task_id differs from contract")

    todos = worklog.get("todo", [])
    todo_ids: list[str] = []
    blocked = 0
    if isinstance(todos, list):
        for position, item in enumerate(todos, 1):
            if not isinstance(item, dict):
                continue
            todo_id = item.get("id")
            if isinstance(todo_id, str):
                todo_ids.append(todo_id)
            check.require(bool(re.fullmatch(r"T\d{2,}", str(todo_id or ""))), f"Todo {position} has invalid id")
            status = item.get("status")
            check.require(status in {"complete", "blocked", "not_applicable"}, f"Todo {position} is not terminal")
            if status == "blocked":
                blocked += 1
                check.require(item.get("attempts") == 3, f"Blocked todo {todo_id} must record three attempts")
    check.require(len(todo_ids) == len(set(todo_ids)), "Online-research todo IDs are not unique")
    if blocked:
        check.require(bool(worklog.get("open_gaps")), "Blocked todos exist but open_gaps is empty")

    iterations = worklog.get("search_iterations", [])
    if isinstance(iterations, list):
        numbers = [item.get("iteration") for item in iterations if isinstance(item, dict)]
        check.require(numbers == list(range(1, len(iterations) + 1)), "Search iteration numbers are not exact and consecutive")
        for position, item in enumerate(iterations, 1):
            if not isinstance(item, dict):
                continue
            targeted = item.get("todo_ids")
            check.require(isinstance(targeted, list) and set(targeted) <= set(todo_ids), f"Search iteration {position} has invalid todo_ids")

    log_rows = load_ndjson(task_root / "cache" / "research_log.ndjson", check, "research log")
    searches = sum(row.get("action") == "search" for row in log_rows)
    declared = worklog.get("search_call_count")
    maximum = int(contract.get("budgets", {}).get("max_search_calls", 40))
    check.require(maximum >= 1, "Online max_search_calls must be at least 1")
    check.require(declared == searches, "Worklog search_call_count differs from research_log.ndjson")
    check.require(1 <= searches <= maximum, f"Search call count must be within 1..{maximum}")
    check.require(worklog.get("frozen_pages") == pages, "frozen_pages must exactly match cached pages in sorted order")
    check.require(worklog.get("frozen_page_count") == len(pages), "frozen_page_count is inaccurate")
    check.require(worklog.get("evidence_count") == len(evidence), "Worklog evidence_count is inaccurate")


def research_check(task_root: Path) -> None:
    check = Validation()
    contract = load_json(task_root / "task_contract.json", check, "task contract")
    decision = load_json(task_root / "route_decision.json", check, "route decision")
    check.require(isinstance(contract, dict), "task_contract.json must contain an object")
    check.require(isinstance(decision, dict), "route_decision.json must contain an object")
    evidence, pages = evidence_check(task_root, check)
    report_path = task_root / "research_report.md"
    try:
        report_text = report_path.read_text(encoding="utf-8-sig")
    except Exception as exc:  # noqa: BLE001
        check.errors.append(f"Invalid research_report.md: {exc}")
        report_text = ""
    for heading in (
        "# Research Report",
        "## Summary",
        "## Evidence",
        "## Conflicts and Uncertainty",
        "## Open Gaps",
    ):
        check.require(heading in report_text, f"research_report.md missing heading: {heading}")
    if isinstance(contract, dict) and isinstance(decision, dict):
        check.require(decision.get("selected_route") in {"local_agent", "online_agent"}, "research validation is only for agent routes")
        validate_research_report(task_root, check)
        if decision.get("selected_route") == "online_agent":
            validate_online_research(task_root, contract, evidence, pages, check)
    check.finish("research evidence and freeze are valid")


def task_check(task_root: Path) -> None:
    check = Validation()
    framework_root = Path(__file__).resolve().parent
    contract = load_json(task_root / "task_contract.json", check, "task contract")
    decision = load_json(task_root / "route_decision.json", check, "route decision")
    report = load_json(task_root / "output" / "final_report.json", check, "final report")
    check.require(isinstance(contract, dict), "task_contract.json must contain an object")
    check.require(isinstance(decision, dict), "route_decision.json must contain an object")
    check.require(isinstance(report, dict), "output/final_report.json must contain an object")
    evidence, pages = evidence_check(task_root, check)
    if not (isinstance(contract, dict) and isinstance(decision, dict) and isinstance(report, dict)):
        check.finish("completed task is internally consistent")
        return

    validate_schema(contract, framework_root / "schemas/task_contract.schema.json", check, "Task contract")
    validate_schema(decision, framework_root / "schemas/route_decision.schema.json", check, "Route decision")
    validate_schema(report, framework_root / "schemas/final_report.schema.json", check, "Final report")
    evidence_schema = framework_root / "schemas/evidence_record.schema.json"
    for position, row in enumerate(evidence, 1):
        validate_schema(row, evidence_schema, check, f"Evidence row {position}")

    check.require(report.get("task_id") == contract.get("task_id"), "Final report task_id differs from contract")
    check.require(report.get("execution_route") == decision.get("selected_route"), "Final report execution_route differs from route decision")
    check.require(report.get("evidence_file") == "evidence.ndjson", "Final report evidence_file must be evidence.ndjson")
    check.require(isinstance(report.get("final_output"), dict), "final_output must be an object")
    check.require(isinstance(report.get("open_gaps"), list), "open_gaps must be an array")
    if decision.get("selected_route") in {"local_agent", "online_agent"}:
        check.require(decision.get("coder_mode") == "api", "Agent-route final reports require coder_mode=api")
        validate_research_report(task_root, check)

    instructions_path = resolve_contract_path(contract.get("instructions_path"), framework_root, task_root)
    codebook_path = resolve_contract_path(contract.get("codebook_path"), framework_root, task_root)
    output_schema_path = resolve_contract_path(contract.get("output_schema_path"), framework_root, task_root)
    check.require(instructions_path.is_file(), f"Combined instruction file does not exist: {instructions_path}")
    codebook = load_json(codebook_path, check, "codebook") if codebook_path.is_file() else None
    check.require(isinstance(codebook, dict), f"Codebook does not contain an object: {codebook_path}")
    if instructions_path.is_file() and isinstance(codebook, dict):
        try:
            embedded_codebook = extract_codebook(
                instructions_path.read_text(encoding="utf-8-sig")
            )
        except ValueError as exc:
            check.errors.append(f"Invalid combined instruction contract: {exc}")
        else:
            check.require(
                embedded_codebook == codebook,
                "Contract codebook mirror differs from the object embedded in instruction.md",
            )
    if output_schema_path.is_file():
        candidate = report if output_schema_path.name == "final_report.schema.json" else report.get("final_output")
        validate_schema(candidate, output_schema_path, check, "Contract output")
    else:
        check.errors.append(f"Output schema does not exist: {output_schema_path}")

    if isinstance(codebook, dict) and isinstance(report.get("final_output"), dict):
        expected_keys = set(codebook)
        actual_keys = set(report["final_output"])
        check.require(actual_keys == expected_keys, "final_output top-level keys must exactly match the codebook")

    if decision.get("selected_route") == "online_agent":
        validate_online_research(task_root, contract, evidence, pages, check)
    check.finish("completed task is internally consistent")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("project")
    for name in ("research", "task"):
        command = subparsers.add_parser(name)
        command.add_argument("--task-root", required=True)
    args = parser.parse_args()
    if args.command == "project":
        project_check(Path.cwd().resolve())
    elif args.command == "research":
        research_check(Path(args.task_root).expanduser().resolve())
    else:
        task_check(Path(args.task_root).expanduser().resolve())


if __name__ == "__main__":
    main()
