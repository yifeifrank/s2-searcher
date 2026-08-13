#!/usr/bin/env python3
"""Execute a compact, self-contained task through a Responses-compatible API."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.instruction_contract import extract_codebook

def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_env(path: Path) -> None:
    """Load a simple dotenv file without overriding an existing environment."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def run_project_tool(framework_root: Path, arguments: list[str]) -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, *arguments],
        cwd=framework_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"Tool failed: {arguments[0]}")
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError(f"Tool returned no JSON: {arguments[0]}")
    value = json.loads(lines[-1])
    if not isinstance(value, dict):
        raise RuntimeError(f"Tool returned non-object JSON: {arguments[0]}")
    return value


def ingest_and_archive(task_root: Path, framework_root: Path, source: Path, title: str, summary: str) -> None:
    item = run_project_tool(
        framework_root,
        [
            "tools/local_ingest.py",
            "--task-root",
            str(task_root),
            "--path",
            str(source),
            "--title",
            title,
        ],
    )
    page = task_root / str(item["markdown_path"])
    payload = {
        "cache_key": item["cache_key"],
        "start_line": 1,
        "end_line": len(page.read_text(encoding="utf-8").splitlines()),
        "labels": ["direct_api_input"],
        "source_path": str(source),
        "task_summary": summary,
    }
    run_project_tool(
        framework_root,
        [
            "tools/research_tools.py",
            "--task-root",
            str(task_root),
            "archive",
            "--payload-json",
            json.dumps(payload, ensure_ascii=False),
        ],
    )


def load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Cannot read {label} at {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be a JSON object: {path}")
    return value


def resolve_input(path_value: str, framework_root: Path, task_root: Path) -> Path:
    path = Path(path_value).expanduser()
    candidates = [path] if path.is_absolute() else [framework_root / path, task_root / path]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(f"Input document not found: {path_value}")


def parse_json_output(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
        cleaned = cleaned.rsplit("```", 1)[0].strip()
    value = json.loads(cleaned)
    if not isinstance(value, dict):
        raise RuntimeError("API output must be a JSON object")
    return value


def execute(task_root: Path, framework_root: Path) -> dict[str, Any]:
    load_env(framework_root / ".env")
    contract = load_object(task_root / "task_contract.json", "task contract")
    decision = load_object(task_root / "route_decision.json", "route decision")
    if decision.get("selected_route") != "direct_api":
        raise RuntimeError("direct_api.py may only execute a direct_api route decision")

    instructions_path = resolve_input(str(contract["instructions_path"]), framework_root, task_root)
    codebook_path = resolve_input(str(contract["codebook_path"]), framework_root, task_root)
    schema_path = resolve_input(str(contract["output_schema_path"]), framework_root, task_root)
    codebook = load_object(codebook_path, "extracted codebook mirror")
    output_schema = load_object(schema_path, "output schema")
    instruction_contract = instructions_path.read_text(encoding="utf-8-sig")
    try:
        embedded_codebook = extract_codebook(instruction_contract)
    except ValueError as exc:
        raise RuntimeError(f"Invalid combined instruction contract: {exc}") from exc
    if embedded_codebook != codebook:
        raise RuntimeError("Extracted codebook mirror differs from instruction.md")
    documents: list[dict[str, str]] = []
    inline_text = str(contract.get("input", {}).get("inline_text", ""))
    if inline_text:
        inline_path = task_root / "input" / "inline.txt"
        inline_path.parent.mkdir(parents=True, exist_ok=True)
        inline_path.write_text(inline_text, encoding="utf-8")
        documents.append({"path": "task_contract.input.inline_text", "content": inline_text})
        ingest_and_archive(
            task_root,
            framework_root,
            inline_path,
            "Inline task evidence",
            "Inline evidence supplied to the direct API route.",
        )
    for raw_path in contract.get("input", {}).get("documents", []):
        path = resolve_input(str(raw_path), framework_root, task_root)
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        documents.append({"path": str(path), "content": text})
        ingest_and_archive(
            task_root,
            framework_root,
            path,
            path.name,
            "Complete source supplied to the direct API route.",
        )

    character_limit = int(os.environ.get("DIRECT_API_MAX_INPUT_CHARACTERS", "500000"))
    model = os.environ.get("DIRECT_API_MODEL", "").strip()
    api_key = os.environ.get("DIRECT_API_KEY", "").strip() or os.environ.get("OPENAI_API_KEY", "").strip()
    base_url = os.environ.get("DIRECT_API_BASE_URL", "https://api.openai.com/v1").strip()
    if not model:
        raise RuntimeError("DIRECT_API_MODEL is not configured")
    if not api_key:
        raise RuntimeError("DIRECT_API_KEY (or OPENAI_API_KEY) is not configured")

    system = (
        "You perform evidence-bounded structured extraction. Read the complete combined Markdown instruction. Follow its prose for scope, evidence, interpretation, normalization, missing values, and quality control. "
        "Follow its embedded JSON codebook exactly for output fields, nesting, types, allowed values, and field-local format. The supplied extracted mirror is identical and exists for machine validation. "
        "Return one JSON object with exactly these top-level keys: final_output, summary, open_gaps. "
        "final_output contains the codebook-defined result; summary is a short string; open_gaps is an array of strings. "
        "Use explicit nulls or the codebook's missing-value convention when evidence is absent. Never invent facts."
    )
    user_payload = {
        "objective": contract.get("objective", ""),
        "task_metadata": contract.get("metadata", {}),
        "instruction_contract": instruction_contract,
        "extracted_codebook_mirror": codebook,
        "output_schema_guidance": output_schema,
        "documents": documents,
    }
    total_characters = len(json.dumps(user_payload, ensure_ascii=False))
    if total_characters > character_limit:
        raise RuntimeError(
            f"Direct API input is {total_characters} characters, exceeding {character_limit}; reroute to local_agent"
        )
    request_record = {
        "request_version": "direct-api-request-v1", "created_at": now(), "task_id": contract.get("task_id"),
        "model": model, "base_url": base_url, "system": system, "input": user_payload,
        "input_character_count": total_characters,
    }
    api_dir = task_root / "api"
    api_dir.mkdir(parents=True, exist_ok=True)
    (api_dir / "request.json").write_text(json.dumps(request_record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("Install requirements.txt before using direct_api") from exc

    request_timeout = float(os.environ.get("DIRECT_API_TIMEOUT_SECONDS", "300"))
    if request_timeout <= 0:
        raise RuntimeError("DIRECT_API_TIMEOUT_SECONDS must be positive")
    # The launcher also applies a process-level deadline. This client-level
    # timeout lets the SDK unwind cleanly before the process-tree watchdog fires.
    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=request_timeout,
        max_retries=0,
    )
    max_retries = int(contract.get("budgets", {}).get("max_retries", os.environ.get("DIRECT_API_MAX_RETRIES", "2")))
    effort = os.environ.get("DIRECT_API_REASONING_EFFORT", "").strip()
    last_error: Exception | None = None
    response = None
    attempts_used = 0
    for attempt in range(max_retries + 1):
        attempts_used = attempt + 1
        try:
            arguments: dict[str, Any] = {
                "model": model,
                "instructions": system,
                "input": json.dumps(user_payload, ensure_ascii=False),
                "text": {"format": {"type": "json_object"}},
            }
            if effort:
                arguments["reasoning"] = {"effort": effort}
            response = client.responses.create(**arguments)
            break
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt >= max_retries:
                break
            time.sleep(min(2 ** attempt, 8))
    if response is None:
        raise RuntimeError(f"Direct API failed after {max_retries + 1} attempts: {last_error}")

    raw = response.model_dump() if hasattr(response, "model_dump") else {"output_text": str(response)}
    (api_dir / "response.json").write_text(json.dumps(raw, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    execution_record = {
        "execution_version": "direct-api-execution-v1", "completed_at": now(), "model": model,
        "base_url": base_url, "attempts": attempts_used, "retry_count": max(0, attempts_used - 1),
        "request_timeout_seconds": request_timeout,
        "response_id": raw.get("id") if isinstance(raw, dict) else None,
    }
    (api_dir / "execution.json").write_text(json.dumps(execution_record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    parsed = parse_json_output(str(response.output_text))
    if {"final_output", "summary", "open_gaps"} <= parsed.keys():
        final_output = parsed["final_output"]
        summary = str(parsed["summary"])
        open_gaps = parsed["open_gaps"]
    else:
        final_output = parsed
        summary = "Direct API extraction completed."
        open_gaps = []
    if not isinstance(final_output, dict) or not isinstance(open_gaps, list):
        raise RuntimeError("API JSON must contain object final_output and array open_gaps")
    report = {
        "task_id": str(contract.get("task_id", "")), "status": "complete" if not open_gaps else "needs_more_research",
        "summary": summary, "execution_route": "direct_api", "final_output": final_output,
        "open_gaps": [str(item) for item in open_gaps], "evidence_file": "evidence.ndjson",
    }
    output_dir = task_root / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "final_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
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
