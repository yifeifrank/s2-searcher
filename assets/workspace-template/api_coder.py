#!/usr/bin/env python3
"""Optionally code frozen research with one Responses-compatible API call."""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from direct_api import load_env, load_object, parse_json_output, resolve_input
from tools.instruction_contract import extract_codebook


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_ndjson(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        if not raw.strip():
            continue
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise RuntimeError(f"NDJSON row is not an object: {path}")
        rows.append(value)
    return rows


def archived_evidence(task_root: Path) -> list[dict[str, Any]]:
    index = {
        str(row.get("cache_key", "")): row
        for row in load_ndjson(task_root / "cache" / "cache_index.jsonl")
    }
    materialized: list[dict[str, Any]] = []
    for position, record in enumerate(load_ndjson(task_root / "evidence.ndjson"), 1):
        cache_key = str(record.get("cache_key", ""))
        start = int(record.get("start_line", 0))
        end = int(record.get("end_line", 0))
        indexed = index.get(cache_key, {})
        relative = str(indexed.get("markdown_path") or record.get("markdown_path") or f"cache/pages/{cache_key}.md")
        page = (task_root / relative).resolve()
        try:
            page.relative_to(task_root.resolve())
        except ValueError as exc:
            raise RuntimeError(f"Evidence row {position} escapes task workspace: {relative}") from exc
        lines = page.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        if start < 1 or end < start or end > len(lines):
            raise RuntimeError(f"Evidence row {position} has invalid range {start}-{end} for {relative}")
        materialized.append(
            {
                **record,
                "source_url": indexed.get("canonical_url") or record.get("url", ""),
                "markdown_path": relative.replace("\\", "/"),
                "archived_text": "\n".join(lines[start - 1:end]),
            }
        )
    return materialized


def execute(task_root: Path, framework_root: Path) -> dict[str, Any]:
    load_env(framework_root / ".env")
    contract = load_object(task_root / "task_contract.json", "task contract")
    decision = load_object(task_root / "route_decision.json", "route decision")
    if decision.get("selected_route") == "direct_api":
        raise RuntimeError("api_coder.py is only for a frozen local_agent or online_agent research package")
    if decision.get("coder_mode") != "api":
        raise RuntimeError("route_decision.json does not enable coder_mode=api")

    instruction_path = resolve_input(str(contract["instructions_path"]), framework_root, task_root)
    codebook_path = resolve_input(str(contract["codebook_path"]), framework_root, task_root)
    schema_path = resolve_input(str(contract["output_schema_path"]), framework_root, task_root)
    instruction = instruction_path.read_text(encoding="utf-8-sig")
    codebook = load_object(codebook_path, "extracted codebook mirror")
    if extract_codebook(instruction) != codebook:
        raise RuntimeError("Extracted codebook mirror differs from the combined instruction")

    research_report = task_root / "research_report.md"
    if not research_report.is_file():
        raise RuntimeError("research_report.md is missing; validate the research freeze before coding")
    evidence = archived_evidence(task_root)
    research_context: dict[str, Any] = {
        "objective": contract.get("objective", ""),
        "metadata": contract.get("metadata", {}),
        "instruction_contract": instruction,
        "extracted_codebook_mirror": codebook,
        "output_schema_guidance": load_object(schema_path, "output schema"),
        "research_report": research_report.read_text(encoding="utf-8-sig"),
        "archived_evidence": evidence,
    }
    worklog = task_root / "searcher_worklog.json"
    if worklog.is_file():
        research_context["searcher_worklog"] = load_object(worklog, "searcher worklog")

    max_characters = int(os.environ.get("EVIDENCE_CODER_MAX_INPUT_CHARACTERS", "500000"))
    serialized = json.dumps(research_context, ensure_ascii=False)
    if len(serialized) > max_characters:
        raise RuntimeError(
            f"Frozen coding input is {len(serialized)} characters, exceeding {max_characters}; "
            "refine the archive or configure a larger limit"
        )

    model = os.environ.get("EVIDENCE_CODER_MODEL", "").strip()
    api_key = os.environ.get("EVIDENCE_CODER_API_KEY", "").strip()
    base_url = os.environ.get("EVIDENCE_CODER_BASE_URL", "https://api.openai.com/v1").strip()
    timeout = float(os.environ.get("EVIDENCE_CODER_TIMEOUT_SECONDS", "300"))
    effort = os.environ.get("EVIDENCE_CODER_REASONING_EFFORT", "").strip()
    if not model or not api_key:
        raise RuntimeError("EVIDENCE_CODER_MODEL and EVIDENCE_CODER_API_KEY are required for coder_mode=api")

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("Install requirements.txt before using the optional API coder") from exc

    system = (framework_root / "prompts" / "coder" / "api_coder.md").read_text(encoding="utf-8")
    api_dir = task_root / "api"
    api_dir.mkdir(parents=True, exist_ok=True)
    request_record = {
        "request_version": "frozen-evidence-coder-v1",
        "created_at": now(),
        "model": model,
        "base_url": base_url,
        "input_character_count": len(serialized),
        "system": system,
        "input": research_context,
    }
    (api_dir / "coder_request.json").write_text(
        json.dumps(request_record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=0)
    arguments: dict[str, Any] = {
        "model": model,
        "instructions": system,
        "input": serialized,
        "text": {"format": {"type": "json_object"}},
    }
    if effort:
        arguments["reasoning"] = {"effort": effort}
    response = client.responses.create(**arguments)
    raw = response.model_dump() if hasattr(response, "model_dump") else {"output_text": str(response)}
    (api_dir / "coder_response.json").write_text(
        json.dumps(raw, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8"
    )

    parsed = parse_json_output(str(response.output_text))
    if not {"final_output", "summary", "open_gaps"} <= parsed.keys():
        raise RuntimeError("API coder response must contain final_output, summary, and open_gaps")
    final_output = parsed["final_output"]
    open_gaps = parsed["open_gaps"]
    if not isinstance(final_output, dict) or not isinstance(open_gaps, list):
        raise RuntimeError("API coder final_output must be an object and open_gaps must be an array")
    if set(final_output) != set(codebook):
        raise RuntimeError("API coder final_output top-level keys do not exactly match the codebook")

    report = {
        "task_id": str(contract.get("task_id", "")),
        "status": "complete" if not open_gaps else "needs_more_research",
        "summary": str(parsed["summary"]),
        "execution_route": str(decision.get("selected_route")),
        "final_output": final_output,
        "open_gaps": [str(item) for item in open_gaps],
        "evidence_file": "evidence.ndjson",
    }
    output_dir = task_root / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "final_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (api_dir / "coder_execution.json").write_text(
        json.dumps(
            {
                "execution_version": "single-call-api-coder-v1",
                "completed_at": now(),
                "model": model,
                "base_url": base_url,
                "response_id": raw.get("id") if isinstance(raw, dict) else None,
                "model_call_count": 1,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-root", required=True)
    parser.add_argument("--framework-root", default=str(Path(__file__).resolve().parent))
    args = parser.parse_args()
    report = execute(Path(args.task_root).expanduser().resolve(), Path(args.framework_root).expanduser().resolve())
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
