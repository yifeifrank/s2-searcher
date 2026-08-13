#!/usr/bin/env python3
"""Run a resilient CSV/JSON research batch with one isolated task per row."""
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from router import route_contract


FRAMEWORK_ROOT = Path(__file__).resolve().parent
BATCH_VERSION = "research-batch-v5"
TASK_ID_RE = re.compile(r"[^a-zA-Z0-9._-]+")
TERMINAL_STATUSES = {
    "pass", "fail", "timeout", "skipped", "aborted", "resumed", "dry-run"
}
WRITE_RETRY_DELAYS = (0.25, 1.0, 3.0, 5.0)

API_WARNING_PATTERNS: list[tuple[str, str]] = [
    (
        "auth",
        r"401 Unauthorized|invalid[_ ]api[_ ]key|Missing bearer|authentication required|"
        r"failed to initialize in-process app-server client|attempt to write a readonly database",
    ),
    ("rate_limit", r"HTTP (?:error|Error):?\s*429|\b429\b.{0,80}rate|rate limit|too many requests"),
    (
        "quota",
        r"HTTP (?:error|Error):?\s*402|Payment Required|insufficient_quota|quota exceeded|"
        r"billing hard limit|usage limit|purchase more credits|credit balance is too low",
    ),
    ("server", r"HTTP (?:error|Error):?\s*5\d\d|internal server error|bad gateway|service unavailable|gateway timeout"),
    ("network", r"stream disconnected|error sending request|failed to connect|connection refused|connection reset|dns|name resolution"),
    ("runtime", r"\bpanic\b|failed to initialize|No such file or directory|Access is denied|os error 5"),
]
GLOBAL_FAILURE_CATEGORIES = {"auth", "quota"}

# ---------------------------------------------------------------------------
# Durable filesystem helpers
# ---------------------------------------------------------------------------

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


def _atomic_write(path: Path, data: bytes, delays: tuple[float, ...] = WRITE_RETRY_DELAYS) -> None:
    """Atomically write with retries for transient WSL/OneDrive mount failures."""
    path.parent.mkdir(parents=True, exist_ok=True)
    last_error: OSError | None = None
    for attempt in range(len(delays) + 1):
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
        try:
            temporary.write_bytes(data)
            os.replace(temporary, path)
            return
        except OSError as exc:
            last_error = exc
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            if attempt < len(delays):
                time.sleep(delays[attempt])
    raise OSError(f"Could not write {path} after {len(delays) + 1} attempts: {last_error}")


def write_text(path: Path, text: str) -> None:
    _atomic_write(path, text.encode("utf-8"))


def write_bytes(path: Path, data: bytes) -> None:
    _atomic_write(path, data)


def write_log_text(path: Path, text: str) -> str:
    """Best-effort diagnostic write; an exhausted mount retry must not kill valid work."""
    try:
        write_text(path, text)
        return ""
    except OSError as exc:
        message = str(exc)
        print(f"[WRITE-WARN] {path}: {message}", flush=True)
        return message


def write_log_bytes(path: Path, data: bytes) -> str:
    try:
        write_bytes(path, data)
        return ""
    except OSError as exc:
        message = str(exc)
        print(f"[WRITE-WARN] {path}: {message}", flush=True)
        return message


def atomic_json(path: Path, value: Any) -> None:
    write_text(path, json.dumps(value, indent=2, ensure_ascii=False) + "\n")


async def atomic_json_async(path: Path, value: Any) -> None:
    await asyncio.to_thread(atomic_json, path, value)


# ---------------------------------------------------------------------------
# Manifest, contracts, and task preparation
# ---------------------------------------------------------------------------

def load_manifest(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".json":
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise ValueError("JSON manifest must be an array of objects")
        return value
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def as_bool(value: Any, default: bool | None = None) -> bool | None:
    if value is None or str(value).strip() == "":
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"Invalid boolean value: {value!r}")


def documents_from(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    raw = str(value or "").strip()
    if not raw:
        return []
    if raw.startswith("["):
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    return [item.strip() for item in raw.split(";") if item.strip()]


def safe_task_id(row: dict[str, Any], index: int) -> str:
    supplied = str(row.get("task_id", "")).strip()
    if supplied:
        task_id = TASK_ID_RE.sub("_", supplied).strip("._-")
    else:
        objective = str(row.get("objective", f"task {index + 1}"))
        prefix = TASK_ID_RE.sub("_", objective.lower()).strip("._-")[:48] or f"task_{index + 1:04d}"
        fingerprint = hashlib.sha256(
            json.dumps(row, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:10]
        task_id = f"{prefix}_{fingerprint}"
    if task_id in {"", ".", ".."}:
        raise ValueError(f"Unsafe task ID in row {index + 1}")
    return task_id[:100]


def integer(row: dict[str, Any], key: str, default: int) -> int:
    value = row.get(key)
    return int(value) if value not in (None, "") else default


def contract_from_row(row: dict[str, Any], index: int, framework: dict[str, Any]) -> dict[str, Any]:
    if isinstance(row.get("contract"), dict):
        contract = dict(row["contract"])
        contract.setdefault("task_id", safe_task_id(row, index))
        return contract
    if row.get("contract_path"):
        path = Path(str(row["contract_path"])).expanduser()
        if not path.is_absolute():
            path = FRAMEWORK_ROOT / path
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict):
            raise ValueError(f"contract_path is not a JSON object: {path}")
        return value

    defaults = framework.get("defaults", {})
    route = str(row.get("route") or row.get("requested_route") or "auto")
    coder_mode = str(row.get("coder_mode") or "").strip()
    if not coder_mode and as_bool(row.get("separate_coder"), False):
        coder_mode = "api"
    coder_mode = coder_mode or str(framework.get("coder_mode_default", "none"))
    return {
        "contract_version": "research-task-v1",
        "task_id": safe_task_id(row, index),
        "objective": str(row.get("objective", "")).strip(),
        "instructions_path": str(row.get("instructions_path") or framework.get("instructions_path", "inputs/instruction.md")),
        "codebook_path": str(row.get("codebook_path") or framework.get("codebook_path", "inputs/codebook.json")),
        "output_schema_path": str(row.get("output_schema_path") or framework.get("output_schema_path", "schemas/final_report.schema.json")),
        "input": {
            "documents": documents_from(row.get("documents")),
            "inline_text": str(row.get("inline_text") or ""),
            "document_scope": str(row.get("document_scope") or "auto"),
            "requires_external_search": as_bool(row.get("requires_external_search"), None),
        },
        "routing": {
            "requested_route": route,
            "coder_mode": coder_mode,
        },
        "difficulty": {
            "output_complexity": str(row.get("output_complexity") or "moderate"),
            "corpus_size": str(row.get("corpus_size") or "unknown"),
            "noise": str(row.get("noise") or "unknown"),
            "identity_ambiguity": str(row.get("identity_ambiguity") or "unknown"),
            "multilingual": bool(as_bool(row.get("multilingual"), False)),
            "conflict_risk": str(row.get("conflict_risk") or "unknown"),
        },
        "budgets": {
            "max_search_calls": integer(row, "max_search_calls", int(defaults.get("max_search_calls", 40))),
            "max_runtime_seconds": integer(row, "max_runtime_seconds", int(defaults.get("session_timeout_seconds", 3600))),
            "max_retries": integer(row, "max_retries", int(defaults.get("api_max_retries", 2))),
        },
        "metadata": {"manifest_row": row, "manifest_index": index},
    }


def setup_task(task_root: Path, contract: dict[str, Any], decision: dict[str, Any]) -> None:
    (task_root / "cache" / "pages").mkdir(parents=True, exist_ok=True)
    (task_root / "output").mkdir(parents=True, exist_ok=True)
    atomic_json(task_root / "task_contract.json", contract)
    atomic_json(task_root / "route_decision.json", decision)
    for path in (
        task_root / "cache" / "cache_index.jsonl",
        task_root / "cache" / "research_log.ndjson",
        task_root / "evidence.ndjson",
    ):
        if not path.exists():
            write_text(path, "")


def render_prompt(task_root: Path, decision: dict[str, Any]) -> str:
    workflow = decision.get("workflow_prompt") or "none (direct_api owns execution)"
    return f"""Execute exactly one research task from the generated framework.

Framework root: {FRAMEWORK_ROOT.as_posix()}
Task workspace: {task_root.as_posix()}
Selected route: {decision['selected_route']}
Coder mode: {decision.get('coder_mode')}
Authoritative workflow prompt: {workflow}

Read AGENTS.md or CLAUDE.md, task_instruction.md, the task contract, route decision, and the selected workflow prompt completely. Read the contract's combined Markdown instruction, including its `## Codebook` JSON fence. Treat `codebook_path` as the machine-extracted mirror of that embedded object. Keep every task artifact inside the task workspace. Follow the recorded route exactly. Do not shell-launch nested Codex or Claude sessions.

For `local_agent` and `online_agent`, this standalone session is the complete research agent and must not spawn a child. Never launch a coder child: when `coder_mode=api`, the batch launcher owns that later API call.

Finish the agent stage only after `research_report.md` exists and `python3 validate.py research --task-root {task_root.as_posix()}` succeeds. Do not create `output/final_report.json`; the launcher creates it only for `direct_api` or `coder_mode=api`.
"""


# ---------------------------------------------------------------------------
# Runtime commands and process-tree control
# ---------------------------------------------------------------------------

def codex_prefix() -> list[str]:
    configured = os.environ.get("CODEX_EXE", "").strip()
    if configured:
        return [configured]
    if os.name != "nt":
        return ["codex"]
    native = shutil.which("codex.exe")
    if native:
        return [native]
    shim = shutil.which("codex") or shutil.which("codex.cmd")
    if shim:
        package = Path(shim).resolve().parent / "node_modules" / "@openai" / "codex"
        native_candidates = sorted(package.glob("node_modules/@openai/codex-*/vendor/*/bin/codex.exe"))
        if native_candidates:
            return [str(native_candidates[0])]
        entrypoint = package / "bin" / "codex.js"
        node = shutil.which("node")
        if entrypoint.is_file() and node:
            return [node, str(entrypoint)]
    raise FileNotFoundError("Could not locate a spawnable Codex CLI; set CODEX_EXE")


def claude_prefix() -> list[str]:
    configured = os.environ.get("CLAUDE_EXE", "").strip()
    if configured:
        return [configured]
    if os.name != "nt":
        return ["claude"]
    native = shutil.which("claude.exe")
    if native:
        return [native]
    shim = shutil.which("claude") or shutil.which("claude.cmd")
    if shim:
        entrypoint = Path(shim).resolve().parent / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js"
        node = shutil.which("node")
        if entrypoint.is_file() and node:
            return [node, str(entrypoint)]
    raise FileNotFoundError("Could not locate a spawnable Claude CLI; set CLAUDE_EXE")


def codex_command(
    model: str, task_root: Path, session_ref: str = "",
    ignore_user_config: bool = False, unsafe_unattended: bool = False,
) -> list[str]:
    permissions = (
        ["--dangerously-bypass-approvals-and-sandbox"]
        if unsafe_unattended
        else ["--sandbox", "workspace-write", "--ask-for-approval", "never"]
    )
    common = [
        "--skip-git-repo-check", "--json",
        "-o", str(task_root / "last_message.txt"),
    ]
    if ignore_user_config:
        common.append("--ignore-user-config")
    if model:
        common.extend(["-m", model])
    if session_ref:
        return codex_prefix() + [*permissions, "exec", "resume", *common, session_ref, "-"]
    return codex_prefix() + [
        *permissions, "exec", "--color", "never", *common,
        "-C", str(FRAMEWORK_ROOT), "-"
    ]


def claude_command(model: str, session_ref: str = "", unsafe_unattended: bool = False) -> list[str]:
    command = claude_prefix() + [
        "--print", "--output-format", "stream-json", "--verbose"
    ]
    if unsafe_unattended:
        command.append("--dangerously-skip-permissions")
    else:
        command.extend(["--permission-mode", "dontAsk"])
    if session_ref:
        command.extend(["--resume", session_ref])
    if model:
        command.extend(["--model", model])
    return command


def api_command(task_root: Path, coder: bool = False) -> list[str]:
    script = "api_coder.py" if coder else "direct_api.py"
    return [
        sys.executable, str(FRAMEWORK_ROOT / script),
        "--task-root", str(task_root), "--framework-root", str(FRAMEWORK_ROOT),
    ]


async def kill_process_tree(process: asyncio.subprocess.Process, grace_seconds: float = 5.0) -> None:
    """Terminate the complete process tree, then escalate to a tree-wide kill."""
    if process.returncode is not None:
        return
    if os.name == "nt":
        killer = await asyncio.create_subprocess_exec(
            "taskkill", "/F", "/T", "/PID", str(process.pid),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await killer.wait()
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        try:
            await asyncio.wait_for(process.wait(), timeout=grace_seconds)
        except asyncio.TimeoutError:
            pass
        return

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=grace_seconds)
        return
    except asyncio.TimeoutError:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=grace_seconds)
    except asyncio.TimeoutError:
        pass


class ProcessRegistry:
    def __init__(self) -> None:
        self.processes: set[asyncio.subprocess.Process] = set()
        self.lock = asyncio.Lock()

    async def add(self, process: asyncio.subprocess.Process) -> None:
        async with self.lock:
            self.processes.add(process)

    async def discard(self, process: asyncio.subprocess.Process) -> None:
        async with self.lock:
            self.processes.discard(process)

    async def kill_all(self, exclude: asyncio.subprocess.Process | None = None) -> int:
        async with self.lock:
            targets = [
                process for process in self.processes
                if process is not exclude and process.returncode is None
            ]
        for process in targets:
            await kill_process_tree(process)
        return len(targets)


async def run_process(
    command: list[str], prompt: str | None, timeout: float, registry: ProcessRegistry,
) -> tuple[int, bytes, bool]:
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(FRAMEWORK_ROOT),
        env=os.environ.copy(),
        stdin=asyncio.subprocess.PIPE if prompt is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=(os.name != "nt"),
    )
    await registry.add(process)
    timed_out = False
    try:
        communication = process.communicate(prompt.encode("utf-8") if prompt is not None else None)
        if timeout > 0:
            output, _ = await asyncio.wait_for(communication, timeout=timeout)
        else:
            output, _ = await communication
    except asyncio.TimeoutError:
        timed_out = True
        await kill_process_tree(process)
        output = b"Launcher timeout: process tree terminated.\n"
    finally:
        await registry.discard(process)
    return int(process.returncode if process.returncode is not None else -9), output or b"", timed_out


# ---------------------------------------------------------------------------
# Runtime event, warning, and child-agent audits
# ---------------------------------------------------------------------------

def _record_child(meta: dict[str, Any], child_id: str, text: str, method: str) -> None:
    meta["child_ids"].add(child_id)
    if len(meta["child_evidence"]) < 10:
        meta["child_evidence"].append({"id": child_id, "method": method})


def parse_session_events(output: bytes | None) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "session_ref": "",
        "usage": {
            "input_tokens": 0,
            "cached_input_tokens": 0,
            "output_tokens": 0,
            "reasoning_output_tokens": 0,
        },
        "usage_found": False,
        "turn_failed": False,
        "error_messages": [],
        "child_ids": set(),
        "child_evidence": [],
    }
    if not output:
        return _finalize_event_meta(meta)

    for line_number, raw in enumerate(output.decode("utf-8", errors="replace").splitlines(), 1):
        line = raw.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type", ""))

        if event_type == "thread.started":
            meta["session_ref"] = str(event.get("thread_id", ""))
        elif event_type == "turn.completed":
            usage = event.get("usage")
            if isinstance(usage, dict):
                meta["usage_found"] = True
                for key in meta["usage"]:
                    value = usage.get(key, 0)
                    if isinstance(value, int) and not isinstance(value, bool):
                        meta["usage"][key] += value
        elif event_type == "turn.failed":
            meta["turn_failed"] = True
            error = event.get("error")
            message = error.get("message") if isinstance(error, dict) else error
            if message:
                meta["error_messages"].append(str(message))
        elif event_type == "error":
            message = str(event.get("message", ""))
            if message and not message.startswith("Reconnecting") and "Falling back from WebSockets" not in message:
                meta["error_messages"].append(message)

        if event_type == "item.completed":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "collab_tool_call" and item.get("tool") == "spawn_agent":
                text = " ".join(
                    str(item.get(key, "")) for key in ("task_name", "agent_type", "prompt")
                )
                receivers = item.get("receiver_thread_ids")
                if isinstance(receivers, list) and receivers:
                    for receiver in receivers:
                        _record_child(meta, f"codex:{receiver}", text, "codex.spawn_agent")

        # Claude stream-json: assistant message content contains Agent tool_use blocks.
        message = event.get("message")
        content = message.get("content") if isinstance(message, dict) else event.get("content")
        if isinstance(content, list):
            for block_index, block in enumerate(content):
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                name = str(block.get("name", ""))
                if name.lower() not in {"agent", "task", "spawn_agent"}:
                    continue
                tool_input = block.get("input") if isinstance(block.get("input"), dict) else {}
                text = " ".join(
                    str(tool_input.get(key, ""))
                    for key in ("subagent_type", "agent_type", "name", "description", "prompt")
                )
                child_id = str(block.get("id") or f"line-{line_number}-block-{block_index}")
                _record_child(meta, f"claude:{child_id}", text, "claude.Agent")

        if event_type == "system" and event.get("subtype") == "init":
            meta["session_ref"] = str(event.get("session_id", ""))
        elif event_type == "result":
            if event.get("session_id"):
                meta["session_ref"] = str(event.get("session_id"))
            usage = event.get("usage")
            if isinstance(usage, dict):
                meta["usage_found"] = True
                mapping = {
                    "input_tokens": "input_tokens",
                    "cache_read_input_tokens": "cached_input_tokens",
                    "output_tokens": "output_tokens",
                }
                for source, target in mapping.items():
                    value = usage.get(source, 0)
                    if isinstance(value, int) and not isinstance(value, bool):
                        meta["usage"][target] += value
            if event.get("is_error"):
                meta["turn_failed"] = True
                meta["error_messages"].append(str(event.get("result", ""))[:500])
    return _finalize_event_meta(meta)


def _finalize_event_meta(meta: dict[str, Any]) -> dict[str, Any]:
    usage = meta["usage"]
    usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
    meta["child_ids"] = sorted(meta["child_ids"])
    meta["child_invocation_count"] = len(meta["child_ids"])
    return meta


def child_agent_audit(decision: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    route = decision.get("selected_route")
    child_count = int(meta.get("child_invocation_count", 0))
    errors: list[str] = []
    expected = "no agent session for direct_api"

    if route in {"local_agent", "online_agent"}:
        expected = f"standalone {route}; zero child agents"
        if child_count != 0:
            errors.append(f"{route} launched {child_count} child agent(s)")

    return {
        "child_agent_audit_verified": not errors,
        "child_agent_expected": expected,
        "child_agent_errors": errors,
        "child_invocation_count": child_count,
        "child_invocation_evidence": meta.get("child_evidence", []),
    }


def _short_line(value: str, limit: int = 500) -> str:
    compact = " ".join(value.strip().split())
    return compact if len(compact) <= limit else compact[: limit - 3] + "..."


def extract_api_warnings(output: bytes | None, meta: dict[str, Any], plain_diagnostics: bool = False) -> dict[str, Any]:
    warnings: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    candidates = list(meta.get("error_messages", []))
    if output:
        for raw in output.decode("utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                event = None
            if isinstance(event, dict):
                message = event.get("message")
                if isinstance(message, str):
                    candidates.append(message)
                item = event.get("item")
                if isinstance(item, dict) and item.get("type") == "command_execution":
                    continue
            lower = line.lower()
            diagnostic = lower.startswith(("error", "http", "runtimeerror", "openai."))
            diagnostic = diagnostic or "api.openai.com" in lower or "codex_" in lower
            if plain_diagnostics:
                diagnostic = diagnostic or any(
                    marker in lower
                    for marker in ("exception", "failed", "traceback", "unauthorized", "quota", "rate limit")
                )
            if diagnostic:
                candidates.append(line)
    for candidate in candidates:
        if "codex_core_plugins" in candidate or "chatgpt.com/backend-api/plugins" in candidate:
            continue
        for category, pattern in API_WARNING_PATTERNS:
            if re.search(pattern, candidate, flags=re.IGNORECASE):
                excerpt = _short_line(candidate)
                key = (category, excerpt)
                if key not in seen:
                    seen.add(key)
                    warnings.append({"category": category, "message": excerpt})
                break
        if len(warnings) >= 10:
            break
    categories = sorted({item["category"] for item in warnings})
    failure_categories = sorted(set(categories) & GLOBAL_FAILURE_CATEGORIES)
    return {
        "api_failure": bool(failure_categories),
        "api_warning_count": len(warnings),
        "api_warning_categories": categories,
        "api_failure_categories": failure_categories,
        "api_warnings": warnings,
    }


def merge_api_warning_info(*items: dict[str, Any]) -> dict[str, Any]:
    warnings: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        for warning in item.get("api_warnings", []):
            if not isinstance(warning, dict):
                continue
            category = str(warning.get("category", ""))
            message = str(warning.get("message", ""))
            key = (category, message)
            if key not in seen:
                seen.add(key)
                warnings.append({"category": category, "message": message})
    categories = sorted({warning["category"] for warning in warnings})
    failure_categories = sorted(set(categories) & GLOBAL_FAILURE_CATEGORIES)
    return {
        "api_failure": bool(failure_categories),
        "api_warning_count": len(warnings),
        "api_warning_categories": categories,
        "api_failure_categories": failure_categories,
        "api_warnings": warnings[:20],
    }


def extract_research_tool_warnings(task_root: Path, start_line: int = 0) -> dict[str, Any]:
    path = task_root / "cache" / "research_log.ndjson"
    warnings: list[dict[str, str]] = []
    failure_categories: set[str] = set()
    if not path.exists():
        return {
            "research_tool_failure": False,
            "research_tool_warning_count": 0,
            "research_tool_failure_categories": [],
            "research_tool_warnings": [],
        }
    try:
        lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()[start_line:]
    except OSError as exc:
        return {
            "research_tool_failure": True,
            "research_tool_warning_count": 1,
            "research_tool_failure_categories": [],
            "research_tool_warnings": [{"category": "research_log_read_error", "message": str(exc)}],
        }
    for raw in lines:
        if not raw.strip():
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or str(event.get("status", "")).lower() not in {"error", "failed", "fail", "timeout"}:
            continue
        message = " | ".join(
            str(event.get(key, "")).strip()
            for key in ("action", "backend", "query", "url", "error", "message")
            if str(event.get(key, "")).strip()
        )
        category = f"research_tool_{str(event.get('status', '')).lower()}"
        for api_category, pattern in API_WARNING_PATTERNS:
            if re.search(pattern, message, flags=re.IGNORECASE):
                category = api_category
                if api_category in GLOBAL_FAILURE_CATEGORIES:
                    failure_categories.add(api_category)
                break
        warnings.append({"category": category, "message": _short_line(message or raw)})
        if len(warnings) >= 10:
            break
    return {
        "research_tool_failure": bool(warnings),
        "research_tool_warning_count": len(warnings),
        "research_tool_failure_categories": sorted(failure_categories),
        "research_tool_warnings": warnings,
    }


def research_log_line_count(task_root: Path) -> int:
    path = task_root / "cache" / "research_log.ndjson"
    if not path.is_file():
        return 0
    try:
        return len(path.read_text(encoding="utf-8-sig", errors="replace").splitlines())
    except OSError:
        return 0


# ---------------------------------------------------------------------------
# Validation, session state, progress, and batch control
# ---------------------------------------------------------------------------

def validate_stage(task_root: Path, stage: str) -> tuple[bool, str]:
    result = subprocess.run(
        [sys.executable, "validate.py", stage, "--task-root", str(task_root)],
        cwd=FRAMEWORK_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return result.returncode == 0, result.stdout


def load_session_ref(task_root: Path, runtime: str) -> str:
    path = task_root / "session_state.json"
    if not path.is_file():
        return ""
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(value, dict) or value.get("runtime") != runtime:
        return ""
    return str(value.get("session_ref", ""))


def save_session_state(
    task_root: Path, runtime: str, session_ref: str, attempt: int, returncode: int,
    timed_out: bool, validation_ok: bool,
) -> None:
    path = task_root / "session_state.json"
    previous: dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(loaded, dict):
                previous = loaded
        except (OSError, json.JSONDecodeError):
            pass
    attempts = previous.get("attempts") if isinstance(previous.get("attempts"), list) else []
    attempts.append(
        {
            "attempt": attempt,
            "completed_at_epoch": time.time(),
            "returncode": returncode,
            "timed_out": timed_out,
            "validation_ok": validation_ok,
        }
    )
    atomic_json(
        path,
        {
            "session_state_version": "research-session-v1",
            "runtime": runtime,
            "session_ref": session_ref,
            "updated_at_epoch": time.time(),
            "attempts": attempts[-50:],
        },
    )


class Progress:
    def __init__(self, tasks_dir: Path, task_ids: list[str]) -> None:
        self.path = tasks_dir / "batch_progress.json"
        self.started_at = time.time()
        self.lock = asyncio.Lock()
        self.value: dict[str, Any] = {
            "batch_version": BATCH_VERSION,
            "batch_state": "starting",
            "total": len(task_ids),
            "tasks": {task_id: {"status": "queued"} for task_id in task_ids},
        }

    def _snapshot_unlocked(self) -> dict[str, Any]:
        snapshot = dict(self.value)
        tasks = {key: dict(value) for key, value in self.value["tasks"].items()}
        snapshot["tasks"] = tasks
        counts: dict[str, int] = {}
        for item in tasks.values():
            status = str(item.get("status", "unknown"))
            counts[status] = counts.get(status, 0) + 1
        snapshot["counts"] = counts
        snapshot["queued"] = sum(counts.get(key, 0) for key in ("queued", "prepared"))
        snapshot["running"] = counts.get("running", 0)
        snapshot["completed"] = sum(counts.get(key, 0) for key in TERMINAL_STATUSES)
        snapshot["elapsed_seconds"] = round(time.time() - self.started_at, 1)
        snapshot["updated_at_epoch"] = time.time()
        return snapshot

    async def update_task(self, task_key: str, **fields: Any) -> None:
        async with self.lock:
            self.value["tasks"].setdefault(task_key, {}).update(fields)
            snapshot = self._snapshot_unlocked()
        try:
            await atomic_json_async(self.path, snapshot)
        except OSError as exc:
            print(f"[PROGRESS-WARN] {self.path}: {exc}", flush=True)

    async def set_batch_state(self, state: str, **fields: Any) -> None:
        async with self.lock:
            self.value["batch_state"] = state
            self.value.update(fields)
            snapshot = self._snapshot_unlocked()
        try:
            await atomic_json_async(self.path, snapshot)
        except OSError as exc:
            print(f"[PROGRESS-WARN] {self.path}: {exc}", flush=True)

    async def snapshot(self) -> dict[str, Any]:
        async with self.lock:
            snapshot = self._snapshot_unlocked()
        try:
            await atomic_json_async(self.path, snapshot)
        except OSError as exc:
            print(f"[PROGRESS-WARN] {self.path}: {exc}", flush=True)
        return snapshot


async def progress_reporter(progress: Progress, interval: float, stop: asyncio.Event) -> None:
    while not stop.is_set():
        snapshot = await progress.snapshot()
        counts = snapshot["counts"]
        print(
            "[PROGRESS] "
            f"state={snapshot['batch_state']} done={snapshot['completed']}/{snapshot['total']} "
            f"queued={snapshot['queued']} running={snapshot['running']} "
            f"pass={counts.get('pass', 0)} fail={counts.get('fail', 0)} "
            f"timeout={counts.get('timeout', 0)} skipped={counts.get('skipped', 0)} "
            f"aborted={counts.get('aborted', 0)} elapsed={snapshot['elapsed_seconds']}s",
            flush=True,
        )
        try:
            await asyncio.wait_for(stop.wait(), timeout=max(0.5, interval))
        except asyncio.TimeoutError:
            pass


class BatchControl:
    def __init__(
        self, failure_mode: str, quota_pause_seconds: float, quota_max_pause_seconds: float,
        progress: Progress, registry: ProcessRegistry,
    ) -> None:
        self.failure_mode = failure_mode
        self.quota_pause_seconds = quota_pause_seconds
        self.quota_max_pause_seconds = quota_max_pause_seconds
        self.progress = progress
        self.registry = registry
        self.abort_event = asyncio.Event()
        self.run_gate = asyncio.Event()
        self.run_gate.set()
        self.abort_reason: dict[str, Any] = {}
        self.pause_lock = asyncio.Lock()
        self.pause_generation = 0
        self.quota_pause_total = 0.0

    async def wait_until_runnable(self) -> bool:
        while not self.abort_event.is_set():
            await self.run_gate.wait()
            if self.run_gate.is_set():
                return not self.abort_event.is_set()
        return False

    async def abort(self, task_id: str, categories: list[str], message: str) -> None:
        if self.abort_event.is_set():
            return
        self.abort_reason = {
            "task_id": task_id,
            "categories": categories,
            "message": message,
            "at_epoch": time.time(),
        }
        self.abort_event.set()
        self.run_gate.set()
        await self.progress.set_batch_state("aborting", abort_reason=self.abort_reason)
        killed = await self.registry.kill_all()
        print(f"[ABORT] {message}; terminated {killed} active process tree(s)", flush=True)

    async def pause_for_quota(self, task_id: str) -> bool:
        observed_generation = self.pause_generation
        async with self.pause_lock:
            if self.abort_event.is_set():
                return False
            if observed_generation != self.pause_generation:
                return True
            pause_seconds = max(0.05, self.quota_pause_seconds)
            if (
                self.quota_max_pause_seconds > 0
                and self.quota_pause_total + pause_seconds > self.quota_max_pause_seconds
            ):
                return False
            self.pause_generation += 1
            self.run_gate.clear()
            reason = {
                "task_id": task_id,
                "pause_seconds": pause_seconds,
                "pause_generation": self.pause_generation,
                "total_pause_seconds_before": self.quota_pause_total,
            }
            await self.progress.set_batch_state("paused_for_quota", pause_reason=reason)
            print(f"[PAUSE] quota failure from {task_id}; waiting {pause_seconds:g}s", flush=True)
            started = time.time()
            try:
                await asyncio.wait_for(self.abort_event.wait(), timeout=pause_seconds)
            except asyncio.TimeoutError:
                pass
            self.quota_pause_total += time.time() - started
            self.run_gate.set()
            if not self.abort_event.is_set():
                await self.progress.set_batch_state(
                    "running", quota_pause_total_seconds=round(self.quota_pause_total, 1)
                )
                print("[RESUME] quota pause elapsed; retrying failed work", flush=True)
                return True
            return False

    async def handle_global_failure(
        self, task_id: str, categories: list[str], message: str,
    ) -> str:
        if not categories or self.failure_mode == "continue":
            return "continue"
        if "auth" in categories or self.failure_mode == "abort":
            await self.abort(task_id, categories, message)
            return "abort"
        if "quota" in categories and self.failure_mode == "pause":
            if await self.pause_for_quota(task_id):
                return "retry"
            await self.abort(
                task_id,
                categories,
                f"{message}; quota pause limit reached ({self.quota_max_pause_seconds:g}s)",
            )
            return "abort"
        return "continue"


def resume_prompt(task_root: Path, validation: str) -> str:
    excerpt = _short_line(validation, 1800)
    return f"""Continue the same research task in {task_root.as_posix()}.

The launcher resumed this session because the research freeze did not yet validate. Inspect the existing task files and preserve valid work. Correct the concrete failure below, finish `research_report.md`, and rerun `python3 validate.py research --task-root {task_root.as_posix()}`. Do not launch a coder child or create output/final_report.json.

Validation failure:
{excerpt}
"""


def _result_base(task_id: str, index: int, decision: dict[str, Any], task_root: Path, started: float) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "manifest_index": index,
        "route": decision.get("selected_route"),
        "coder_mode": decision.get("coder_mode"),
        "workspace": str(task_root),
        "duration_s": round(time.time() - started, 2),
    }


async def skipped_result(
    task_id: str, index: int, decision: dict[str, Any], task_root: Path,
    started: float, progress: Progress, reason: str,
) -> dict[str, Any]:
    result = {
        **_result_base(task_id, index, decision, task_root, started),
        "status": "skipped",
        "returncode": None,
        "skip_reason": reason,
        "session_ref": "",
    }
    await progress.update_task(task_id, **result)
    print(f"[SKIP] {task_id}: {reason}", flush=True)
    return result


# ---------------------------------------------------------------------------
# Per-row execution
# ---------------------------------------------------------------------------

async def run_one(
    row: dict[str, Any], index: int, framework: dict[str, Any], args: argparse.Namespace,
    tasks_dir: Path, semaphore: asyncio.Semaphore, progress: Progress,
    control: BatchControl, registry: ProcessRegistry,
) -> dict[str, Any]:
    started = time.time()
    fallback_id = safe_task_id(row, index)
    task_id = fallback_id
    decision: dict[str, Any] = {}
    task_root = (tasks_dir / fallback_id).resolve()
    try:
        contract = contract_from_row(row, index, framework)
        if not str(contract.get("objective", "")).strip():
            raise ValueError("objective is required")
        task_id = safe_task_id({"task_id": contract.get("task_id")}, index)
        contract["task_id"] = task_id
        task_root = (tasks_dir / task_id).resolve()
        if tasks_dir.resolve() not in task_root.parents:
            raise ValueError(f"Task escaped tasks directory: {task_id}")
        decision = route_contract(contract, framework)
        if args.coder_mode is not None and decision["selected_route"] != "direct_api":
            decision["coder_mode"] = args.coder_mode
            decision["reasons"].append(f"Batch CLI overrode coder_mode to {args.coder_mode}.")
        setup_task(task_root, contract, decision)
        prompt = render_prompt(task_root, decision)
        write_text(task_root / "subprocess_prompt.txt", prompt)
        await progress.update_task(
            task_id,
            status="prepared",
            route=decision["selected_route"],
            coder_mode=decision.get("coder_mode"),
            workspace=str(task_root),
        )

        route = str(decision["selected_route"])
        coder_mode = str(decision.get("coder_mode", "none"))
        final_stage = "task" if route == "direct_api" or coder_mode == "api" else "research"
        final_artifact = task_root / ("output/final_report.json" if final_stage == "task" else "research_report.md")

        if args.resume and final_artifact.is_file():
            valid, validation = validate_stage(task_root, final_stage)
            if valid:
                result = {
                    **_result_base(task_id, index, decision, task_root, started),
                    "status": "resumed",
                    "returncode": 0,
                    "session_ref": load_session_ref(task_root, args.runtime),
                }
                await progress.update_task(task_id, **result)
                return result
            write_log_text(task_root / "resume_validation.log", validation)

        if args.dry_run:
            result = {
                **_result_base(task_id, index, decision, task_root, started),
                "status": "dry-run",
                "returncode": 0,
                "session_ref": "",
            }
            await progress.update_task(task_id, **result)
            return result

        if control.abort_event.is_set():
            return await skipped_result(
                task_id, index, decision, task_root, started, progress,
                str(control.abort_reason.get("message", "batch aborted")),
            )
        if not await control.wait_until_runnable():
            return await skipped_result(
                task_id, index, decision, task_root, started, progress,
                str(control.abort_reason.get("message", "batch aborted")),
            )

        queued_at = time.time()
        async with semaphore:
            if control.abort_event.is_set():
                return await skipped_result(
                    task_id, index, decision, task_root, started, progress,
                    str(control.abort_reason.get("message", "batch aborted")),
                )
            if not await control.wait_until_runnable():
                return await skipped_result(
                    task_id, index, decision, task_root, started, progress,
                    str(control.abort_reason.get("message", "batch aborted")),
                )

            queue_wait_s = round(time.time() - queued_at, 2)
            await progress.update_task(
                task_id, status="running", started_at_epoch=time.time(),
                queue_wait_s=queue_wait_s,
            )
            all_output = b""
            attempt_count = 0
            returncode = 1
            timed_out = False
            validation = "not run"
            own_global_failure = False
            session_ref = load_session_ref(task_root, args.runtime) if args.resume else ""
            research_valid = False

            if route == "direct_api":
                timeout = float(args.direct_api_timeout_sec)
                while True:
                    attempt_count += 1
                    returncode, attempt_output, timed_out = await run_process(
                        api_command(task_root), None, timeout, registry
                    )
                    write_log_bytes(task_root / f"session.attempt-{attempt_count}.log", attempt_output)
                    all_output += attempt_output
                    meta = parse_session_events(attempt_output)
                    warnings = extract_api_warnings(attempt_output, meta, plain_diagnostics=True)
                    categories = warnings["api_failure_categories"]
                    if categories:
                        action = await control.handle_global_failure(
                            task_id, categories,
                            f"{task_id} direct API failed in categories {','.join(categories)}",
                        )
                        own_global_failure = action == "abort"
                        if action == "retry":
                            continue
                    valid, validation = validate_stage(task_root, "task") if not timed_out else (False, "direct API timed out")
                    write_log_text(task_root / f"validation.attempt-{attempt_count}.log", validation)
                    if returncode == 0 and valid:
                        break
                    if timed_out or own_global_failure or attempt_count >= args.retries + 1:
                        break
                write_log_bytes(task_root / "session.log", all_output)
                final_valid = returncode == 0 and validate_stage(task_root, "task")[0]
                event_meta = parse_session_events(all_output)
                child_audit = child_agent_audit(decision, event_meta)
            else:
                # A resumed task may already have a valid research freeze but still need its API coder.
                research_valid, validation = validate_stage(task_root, "research")
                if not (args.resume and research_valid):
                    timeout = float(args.session_timeout_sec or decision["recommended_budgets"]["max_runtime_seconds"])
                    validation = "research_report.md or validated research freeze missing"
                    while True:
                        attempt_count += 1
                        active_prompt = resume_prompt(task_root, validation) if session_ref else prompt
                        command = (
                            codex_command(
                                args.model, task_root, session_ref,
                                args.ignore_user_config, args.unsafe_unattended,
                            )
                            if args.runtime == "codex"
                            else claude_command(args.model, session_ref, args.unsafe_unattended)
                        )
                        research_log_before = research_log_line_count(task_root)
                        returncode, attempt_output, timed_out = await run_process(
                            command, active_prompt, timeout, registry
                        )
                        write_log_bytes(task_root / f"session.attempt-{attempt_count}.log", attempt_output)
                        all_output += attempt_output
                        attempt_meta = parse_session_events(attempt_output)
                        session_ref = str(attempt_meta.get("session_ref") or session_ref)
                        research_warnings = extract_research_tool_warnings(
                            task_root, start_line=research_log_before
                        )
                        api_warnings = extract_api_warnings(attempt_output, attempt_meta)
                        categories = sorted(
                            set(api_warnings["api_failure_categories"])
                            | set(research_warnings["research_tool_failure_categories"])
                        )
                        if categories:
                            action = await control.handle_global_failure(
                                task_id, categories,
                                f"{task_id} agent/research tool failed in categories {','.join(categories)}",
                            )
                            own_global_failure = action == "abort"
                            if action == "retry":
                                continue
                        research_valid, validation = (
                            validate_stage(task_root, "research")
                            if not timed_out
                            else (False, "agent session timed out")
                        )
                        cumulative_meta = parse_session_events(all_output)
                        child_audit = child_agent_audit(decision, cumulative_meta)
                        child_audit_ok = child_audit["child_agent_audit_verified"] or args.child_agent_audit != "enforce"
                        if args.child_agent_audit == "warn" and not child_audit["child_agent_audit_verified"]:
                            validation += "\nChild-agent warning: " + "; ".join(child_audit["child_agent_errors"])
                        elif args.child_agent_audit == "enforce" and not child_audit["child_agent_audit_verified"]:
                            validation += "\nChild-agent failure: " + "; ".join(child_audit["child_agent_errors"])
                        write_log_text(task_root / f"validation.attempt-{attempt_count}.log", validation)
                        save_session_state(
                            task_root, args.runtime, session_ref, attempt_count,
                            returncode, timed_out, research_valid and child_audit_ok,
                        )
                        if returncode == 0 and research_valid and child_audit_ok:
                            break
                        if timed_out or own_global_failure or attempt_count >= args.retries + 1:
                            break
                    write_log_bytes(task_root / "session.log", all_output)
                else:
                    returncode = 0
                    event_meta = parse_session_events(
                        (task_root / "session.log").read_bytes() if (task_root / "session.log").is_file() else b""
                    )
                    child_audit = child_agent_audit(decision, event_meta)
                    child_audit_ok = child_audit["child_agent_audit_verified"] or args.child_agent_audit != "enforce"
                    research_valid = research_valid and child_audit_ok

                event_meta = parse_session_events(all_output or (
                    (task_root / "session.log").read_bytes() if (task_root / "session.log").is_file() else b""
                ))
                child_audit = child_agent_audit(decision, event_meta)
                child_audit_ok = child_audit["child_agent_audit_verified"] or args.child_agent_audit != "enforce"
                research_valid = research_valid and child_audit_ok

                if research_valid and coder_mode == "api" and not control.abort_event.is_set():
                    # The optional coder is deliberately one model request, not an
                    # agent or a conversational correction loop. A failed call is
                    # left for an explicit batch retry/resume.
                    coder_attempt = 1
                    coder_timeout = float(args.coder_api_timeout_sec)
                    coder_rc, coder_output, coder_timed_out = await run_process(
                        api_command(task_root, coder=True), None, coder_timeout, registry
                    )
                    write_log_bytes(task_root / "coder_api.attempt-1.log", coder_output)
                    coder_meta = parse_session_events(coder_output)
                    coder_warnings = extract_api_warnings(coder_output, coder_meta, plain_diagnostics=True)
                    categories = coder_warnings["api_failure_categories"]
                    if categories:
                        action = await control.handle_global_failure(
                            task_id, categories,
                            f"{task_id} evidence coder API failed in categories {','.join(categories)}",
                        )
                        own_global_failure = action == "abort"
                    final_valid, validation = (
                        validate_stage(task_root, "task")
                        if not coder_timed_out
                        else (False, "evidence coder API timed out")
                    )
                    write_log_text(task_root / "coder_validation.attempt-1.log", validation)
                    returncode = coder_rc
                    timed_out = coder_timed_out
                    write_log_bytes(task_root / "coder_api.log", coder_output)
                    all_output += coder_output
                elif research_valid and coder_mode == "none":
                    final_valid = True
                    returncode = 0
                else:
                    final_valid = False

            final_meta = parse_session_events(all_output)
            api_warning_info = extract_api_warnings(
                all_output, final_meta, plain_diagnostics=(route == "direct_api")
            )
            coder_log = task_root / "coder_api.log"
            if coder_log.is_file():
                coder_bytes = coder_log.read_bytes()
                api_warning_info = merge_api_warning_info(
                    api_warning_info,
                    extract_api_warnings(
                        coder_bytes, parse_session_events(coder_bytes), plain_diagnostics=True
                    ),
                )
            research_warning_info = extract_research_tool_warnings(task_root)

            if timed_out:
                status = "timeout"
            elif control.abort_event.is_set() and not own_global_failure and not final_valid:
                status = "aborted"
                validation = str(control.abort_reason.get("message", "batch aborted"))
            else:
                status = "pass" if returncode == 0 and final_valid else "fail"
            write_log_text(task_root / "validation.log", validation)
            result = {
                **_result_base(task_id, index, decision, task_root, started),
                "status": status,
                "returncode": returncode,
                "timed_out": timed_out,
                "attempt_count": attempt_count,
                "queue_wait_s": queue_wait_s,
                "session_ref": session_ref or final_meta.get("session_ref", ""),
                "turn_failed": final_meta.get("turn_failed", False),
                "error_messages": final_meta.get("error_messages", [])[:5],
                "usage_found": final_meta.get("usage_found", False),
                **final_meta.get("usage", {}),
                **child_audit,
                **api_warning_info,
                **research_warning_info,
            }
            await progress.update_task(task_id, **result)
            print(f"[{status.upper()}] {task_id} ({result['duration_s']:.1f}s)", flush=True)
            return result
    except Exception as exc:  # noqa: BLE001
        result = {
            "task_id": task_id,
            "manifest_index": index,
            "status": "fail",
            "error": str(exc),
            "workspace": str(task_root),
            "duration_s": round(time.time() - started, 2),
        }
        await progress.update_task(task_id, **result)
        return result


# ---------------------------------------------------------------------------
# Batch metadata and CLI
# ---------------------------------------------------------------------------

def retry_rows(rows: list[dict[str, Any]], results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    indices = {
        int(result["manifest_index"])
        for result in results
        if result.get("status") in {"fail", "timeout", "skipped", "aborted"}
        and isinstance(result.get("manifest_index"), int)
    }
    return [row for index, row in enumerate(rows) if index in indices]


def write_retry_manifest(path: Path, rows: list[dict[str, Any]]) -> None:
    if path.suffix.lower() == ".csv":
        fieldnames: list[str] = []
        for row in rows:
            for key in row:
                if key not in fieldnames:
                    fieldnames.append(key)
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
        write_text(path, buffer.getvalue())
    else:
        atomic_json(path, rows)


def build_summary(args: argparse.Namespace, results: list[dict[str, Any]], manifest: Path) -> dict[str, Any]:
    statuses = ("pass", "fail", "timeout", "skipped", "aborted", "dry-run", "resumed")
    counts = {status: sum(item.get("status") == status for item in results) for status in statuses}
    api_categories: dict[str, int] = {}
    for result in results:
        for category in result.get("api_warning_categories", []):
            api_categories[category] = api_categories.get(category, 0) + 1
        for category in result.get("research_tool_failure_categories", []):
            api_categories[category] = api_categories.get(category, 0) + 1
    return {
        "batch_version": BATCH_VERSION,
        "runtime": args.runtime,
        "manifest": str(manifest),
        "total": len(results),
        "passed": counts["pass"],
        "failed": counts["fail"],
        "timed_out": counts["timeout"],
        "skipped": counts["skipped"],
        "aborted": counts["aborted"],
        "dry_run": counts["dry-run"],
        "resumed": counts["resumed"],
        "api_failure_mode": args.api_failure_mode,
        "coder_mode_override": args.coder_mode,
        "unsafe_unattended": args.unsafe_unattended,
        "api_warning_categories": api_categories,
        "usage_rows_found": sum(bool(item.get("usage_found")) for item in results),
        "total_input_tokens": sum(int(item.get("input_tokens", 0)) for item in results),
        "total_cached_input_tokens": sum(int(item.get("cached_input_tokens", 0)) for item in results),
        "total_output_tokens": sum(int(item.get("output_tokens", 0)) for item in results),
    }


async def async_main(args: argparse.Namespace) -> int:
    load_env(FRAMEWORK_ROOT / ".env")
    framework = json.loads((FRAMEWORK_ROOT / "framework.json").read_text(encoding="utf-8"))
    defaults = framework.get("defaults", {})
    if args.direct_api_timeout_sec is None:
        args.direct_api_timeout_sec = float(defaults.get("direct_api_timeout_seconds", 300))
    if args.coder_api_timeout_sec is None:
        args.coder_api_timeout_sec = float(defaults.get("coder_api_timeout_seconds", 300))
    if args.progress_interval_sec is None:
        args.progress_interval_sec = float(defaults.get("progress_interval_seconds", 10))
    if args.api_failure_mode is None:
        args.api_failure_mode = str(defaults.get("api_failure_mode", "abort"))

    manifest = Path(args.manifest).expanduser().resolve()
    rows = load_manifest(manifest)
    if not rows:
        raise ValueError("Manifest contains no task rows")
    tasks_dir = Path(args.tasks_dir).expanduser()
    if not tasks_dir.is_absolute():
        tasks_dir = FRAMEWORK_ROOT / tasks_dir
    tasks_dir = tasks_dir.resolve()
    tasks_dir.mkdir(parents=True, exist_ok=True)
    task_ids = [
        safe_task_id({"task_id": contract_from_row(row, index, framework).get("task_id")}, index)
        for index, row in enumerate(rows)
    ]
    if len(task_ids) != len(set(task_ids)):
        raise ValueError("Manifest produces duplicate task IDs; make task_id values unique")

    progress = Progress(tasks_dir, task_ids)
    registry = ProcessRegistry()
    control = BatchControl(
        args.api_failure_mode,
        args.quota_pause_seconds,
        args.quota_max_pause_seconds,
        progress,
        registry,
    )
    semaphore = asyncio.Semaphore(max(1, args.max_parallel))
    stop_reporter = asyncio.Event()
    await progress.set_batch_state("dry-run" if args.dry_run else "running")
    reporter = asyncio.create_task(progress_reporter(progress, args.progress_interval_sec, stop_reporter))
    try:
        results = list(
            await asyncio.gather(
                *[
                    run_one(
                        row, index, framework, args, tasks_dir, semaphore,
                        progress, control, registry,
                    )
                    for index, row in enumerate(rows)
                ]
            )
        )
    finally:
        stop_reporter.set()
        await reporter
        await registry.kill_all()

    summary = build_summary(args, results, manifest)
    await progress.set_batch_state(
        "complete" if not (summary["failed"] or summary["timed_out"] or summary["aborted"]) else "incomplete",
        summary=summary,
    )
    atomic_json(tasks_dir / "batch_results.json", results)
    atomic_json(tasks_dir / "batch_summary.json", summary)
    pending = retry_rows(rows, results)
    retry_path = (
        Path(args.retry_manifest).expanduser().resolve()
        if args.retry_manifest
        else tasks_dir / "batch_retry.json"
    )
    if pending:
        write_retry_manifest(retry_path, pending)
        summary["retry_manifest"] = str(retry_path)
        atomic_json(tasks_dir / "batch_summary.json", summary)
    print(json.dumps(summary, indent=2), flush=True)
    return 1 if pending else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, help="CSV or JSON array; one row becomes one task")
    parser.add_argument("--runtime", choices=("codex", "claude"), default="codex")
    parser.add_argument("--tasks-dir", default="tasks")
    parser.add_argument("--max-parallel", type=int, default=4)
    parser.add_argument("--model", default="")
    parser.add_argument(
        "--coder-mode", choices=("none", "api"), default=None,
        help="Override post-research coding for local/online rows; contracts default to none",
    )
    parser.add_argument("--retries", type=int, default=0, help="Additional task attempts; agent retries resume the captured session")
    parser.add_argument("--resume", action="store_true", help="Validate completed work, then resume a captured Codex/Claude session when needed")
    parser.add_argument("--session-timeout-sec", type=float, default=0, help="Agent timeout override; 0 uses each route decision's budget")
    parser.add_argument("--direct-api-timeout-sec", type=float, default=None, help="Hard process timeout for direct_api.py")
    parser.add_argument("--coder-api-timeout-sec", type=float, default=None, help="Hard process timeout for optional api_coder.py")
    parser.add_argument("--progress-interval-sec", type=float, default=None, help="Heartbeat interval for console and batch_progress.json")
    parser.add_argument(
        "--api-failure-mode", choices=("abort", "pause", "continue"), default=None,
        help="Quota/auth policy: abort safely by default; pause retries quota but still aborts auth",
    )
    parser.add_argument("--quota-pause-seconds", type=float, default=900.0, help="Wait before retrying a quota failure in pause mode")
    parser.add_argument("--quota-max-pause-seconds", type=float, default=0.0, help="Maximum cumulative quota wait; 0 means unlimited")
    parser.add_argument(
        "--child-agent-audit", choices=("enforce", "warn", "off"), default="enforce",
        help="Require standalone local and online sessions to launch zero child agents",
    )
    parser.add_argument("--ignore-user-config", action="store_true", help="Codex only: ignore user config while retaining project config and auth")
    parser.add_argument(
        "--unsafe-unattended", action="store_true",
        help="Explicitly bypass Codex/Claude permission enforcement; use only inside an externally isolated container or VM",
    )
    parser.add_argument("--retry-manifest", help="Failed/timed-out/skipped/aborted rows; defaults to <tasks-dir>/batch_retry.json")
    parser.add_argument("--dry-run", action="store_true", help="Create contracts, routes, prompts, and batch metadata only")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    numeric_errors = (
        args.max_parallel < 1
        or args.retries < 0
        or args.session_timeout_sec < 0
        or (args.direct_api_timeout_sec is not None and args.direct_api_timeout_sec <= 0)
        or (args.coder_api_timeout_sec is not None and args.coder_api_timeout_sec <= 0)
        or (args.progress_interval_sec is not None and args.progress_interval_sec <= 0)
        or args.quota_pause_seconds <= 0
        or args.quota_max_pause_seconds < 0
    )
    if numeric_errors:
        raise SystemExit("Invalid numeric option: parallelism/intervals/timeouts must be positive; retries and maximum pause cannot be negative")
    try:
        raise SystemExit(asyncio.run(async_main(args)))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Batch setup failed: {exc}") from exc


if __name__ == "__main__":
    main()
