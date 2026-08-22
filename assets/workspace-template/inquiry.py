#!/usr/bin/env python3
"""Run one evidence-preserving inquiry through the existing research engine."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import batch


FRAMEWORK_ROOT = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question", help="Research question or objective")
    parser.add_argument("--runtime", choices=("codex", "claude"), default="codex")
    parser.add_argument("--task-id", default="", help="Stable task folder name; generated from the question by default")
    parser.add_argument("--tasks-dir", default="tasks/inquiries")
    parser.add_argument("--model", default="")
    parser.add_argument("--local-only", action="store_true", help="Use only workspace-library and local evidence; disable web search")
    parser.add_argument("--multilingual", action="store_true")
    parser.add_argument("--max-search-calls", type=int, default=None)
    parser.add_argument("--max-runtime-seconds", type=int, default=None)
    parser.add_argument("--retries", type=int, default=0)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--ignore-user-config", action="store_true", help="Codex only: ignore user config while retaining project config and auth")
    parser.add_argument("--dry-run", action="store_true", help="Create contracts, route, and prompt without launching a model")
    parser.add_argument(
        "--unsafe-unattended",
        action="store_true",
        help="Bypass runtime permissions; use only in an isolated disposable container or VM",
    )
    return parser.parse_args()


def inquiry_contract(args: argparse.Namespace, framework: dict) -> dict:
    question = str(args.question).strip()
    if not question:
        raise ValueError("question must not be empty")
    defaults = framework.get("defaults", {})
    max_search_calls = (
        int(args.max_search_calls)
        if args.max_search_calls is not None
        else int(defaults.get("max_search_calls", 40))
    )
    max_runtime_seconds = (
        int(args.max_runtime_seconds)
        if args.max_runtime_seconds is not None
        else int(defaults.get("session_timeout_seconds", 3600))
    )
    if max_search_calls < 1 or max_runtime_seconds < 1 or args.retries < 0:
        raise ValueError("search and runtime ceilings must be positive; retries cannot be negative")

    route = "local_agent" if args.local_only else "online_agent"
    row = {
        "task_id": str(args.task_id).strip(),
        "objective": question,
        "instructions_path": "inputs/inquiry_instruction.md",
        "codebook_path": "inputs/inquiry_codebook.json",
        "output_schema_path": "schemas/final_report.schema.json",
        "documents": [],
        "document_scope": "fixed_collection" if args.local_only else "open_web",
        "requires_external_search": not args.local_only,
        "route": route,
        "coder_mode": "none",
        "output_complexity": "moderate",
        "corpus_size": "unknown",
        "noise": "unknown",
        "identity_ambiguity": "unknown",
        "multilingual": bool(args.multilingual),
        "conflict_risk": "medium",
        "max_search_calls": max_search_calls,
        "max_runtime_seconds": max_runtime_seconds,
        "max_retries": int(defaults.get("api_max_retries", 2)),
    }
    contract = batch.contract_from_row(row, 0, framework)
    contract["metadata"] = {"work_mode": "inquiry"}
    return contract


def ensure_task_target(task_root: Path, contract: dict, resume: bool) -> None:
    if not task_root.exists() or not any(task_root.iterdir()):
        return
    if not resume:
        raise ValueError(
            f"Task workspace already contains files: {task_root}. "
            "Use --resume for the same question or choose a new --task-id."
        )
    contract_path = task_root / "task_contract.json"
    if not contract_path.is_file():
        raise ValueError(f"Cannot resume because task_contract.json is missing: {task_root}")
    previous = json.loads(contract_path.read_text(encoding="utf-8-sig"))
    if previous.get("objective") != contract.get("objective"):
        raise ValueError("Refusing to resume a task ID whose recorded question differs")
    if previous.get("metadata", {}).get("work_mode") != "inquiry":
        raise ValueError("Refusing to resume a non-inquiry task through inquiry.py")


def execution_args(args: argparse.Namespace, framework: dict) -> argparse.Namespace:
    defaults = framework.get("defaults", {})
    return argparse.Namespace(
        runtime=args.runtime,
        model=args.model,
        coder_mode=None,
        retries=args.retries,
        resume=args.resume,
        session_timeout_sec=0.0,
        direct_api_timeout_sec=float(defaults.get("direct_api_timeout_seconds", 300)),
        coder_api_timeout_sec=float(defaults.get("coder_api_timeout_seconds", 300)),
        progress_interval_sec=float(defaults.get("progress_interval_seconds", 10)),
        api_failure_mode="abort",
        quota_pause_seconds=900.0,
        quota_max_pause_seconds=0.0,
        child_agent_audit="enforce",
        ignore_user_config=args.ignore_user_config,
        unsafe_unattended=args.unsafe_unattended,
        dry_run=args.dry_run,
    )


async def run(args: argparse.Namespace) -> int:
    batch.load_env(FRAMEWORK_ROOT / ".env")
    framework = json.loads((FRAMEWORK_ROOT / "framework.json").read_text(encoding="utf-8"))
    contract = inquiry_contract(args, framework)
    task_id = batch.safe_task_id({"task_id": contract.get("task_id")}, 0)
    contract["task_id"] = task_id

    tasks_dir = Path(args.tasks_dir).expanduser()
    if not tasks_dir.is_absolute():
        tasks_dir = FRAMEWORK_ROOT / tasks_dir
    tasks_dir = tasks_dir.resolve()
    task_root = (tasks_dir / task_id).resolve()
    if tasks_dir not in task_root.parents:
        raise ValueError(f"Task escaped tasks directory: {task_id}")
    ensure_task_target(task_root, contract, args.resume)

    runner_args = execution_args(args, framework)
    progress = batch.Progress(task_root, [task_id])
    progress.path = task_root / "inquiry_progress.json"
    registry = batch.ProcessRegistry()
    control = batch.BatchControl("abort", 900.0, 0.0, progress, registry)
    stop_reporter = asyncio.Event()
    await progress.set_batch_state("dry-run" if args.dry_run else "running")
    reporter = asyncio.create_task(
        batch.progress_reporter(progress, runner_args.progress_interval_sec, stop_reporter)
    )
    try:
        result = await batch.run_one(
            {"contract": contract},
            0,
            framework,
            runner_args,
            tasks_dir,
            asyncio.Semaphore(1),
            progress,
            control,
            registry,
        )
    finally:
        stop_reporter.set()
        await reporter
        await registry.kill_all()

    result["work_mode"] = "inquiry"
    result["unsafe_unattended"] = bool(args.unsafe_unattended)
    successful = result.get("status") in {"pass", "resumed", "dry-run"}
    await progress.set_batch_state("complete" if successful else "incomplete", result=result)
    batch.atomic_json(task_root / "inquiry_result.json", result)
    print(
        json.dumps(
            {
                "status": result.get("status"),
                "task_id": task_id,
                "workspace": str(task_root),
                "research_report": str(task_root / "research_report.md"),
                "evidence": str(task_root / "evidence.ndjson"),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0 if successful else 1


def main() -> None:
    args = parse_args()
    try:
        raise SystemExit(asyncio.run(run(args)))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Inquiry setup failed: {exc}") from exc


if __name__ == "__main__":
    main()
