"""Small, deterministic input identities and completion manifests.

These check accidental reuse and byte changes; they are not an isolation boundary
or evidence of scientific correctness.
"""
from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any
from contextlib import contextmanager


@contextmanager
def batch_directory_lock(root: Path):
    """Prevent two controllers from writing the same task/output namespace."""
    handle = (root / ".launcher.lock").open("a+b")
    try:
        if os.name == "nt":
            import msvcrt
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise ValueError("Another launcher owns this tasks directory") from exc
    try:
        yield
    finally:
        # Closing also releases the OS lock after interruption or process exit.
        handle.close()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_input(value: str, framework_root: Path) -> Path:
    path = Path(value).expanduser()
    path = (path if path.is_absolute() else framework_root / path).resolve()
    if not path.exists():
        raise ValueError(f"Required input is missing: {path}")
    return path


def source_identity(path: Path, task_root: Path) -> dict[str, Any]:
    if path == task_root or path in task_root.parents:
        raise ValueError(f"Source collection contains the task workspace: {path}")
    if path.is_file():
        return {"path": str(path), "sha256": sha256(path)}
    if not path.is_dir():
        raise ValueError(f"Unsupported input: {path}")
    files: dict[str, str] = {}
    for child in sorted(path.rglob("*")):
        if child.is_symlink() and child.is_dir():
            raise ValueError(f"List directory symlinks as explicit source roots: {child}")
        if child.is_file():
            files[child.relative_to(path).as_posix()] = sha256(child)
    return {"path": str(path), "files": files}


def prepare_identity(
    contract: dict[str, Any], decision: dict[str, Any], framework_root: Path,
    task_root: Path, execution: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    resolved = deepcopy(contract)
    inputs: dict[str, Any] = {}
    for field in ("instructions_path", "codebook_path", "output_schema_path"):
        path = resolve_input(str(resolved.get(field, "")), framework_root)
        if not path.is_file():
            raise ValueError(f"{field} must name a file: {path}")
        resolved[field] = str(path)
        inputs[field] = {"path": str(path), "sha256": sha256(path)}
    documents = []
    for raw in resolved.get("input", {}).get("documents", []):
        path = resolve_input(str(raw), framework_root)
        inputs.setdefault("documents", []).append(source_identity(path, task_root))
        documents.append(str(path))
    resolved.setdefault("input", {})["documents"] = documents
    controls = {}
    for pattern in ("*.py", "tools/*.py", "schemas/*.json", "prompts/**/*.md",
                    ".agents/skills/**/SKILL.md", ".claude/skills/**/SKILL.md"):
        for path in sorted(framework_root.glob(pattern)):
            controls[path.relative_to(framework_root).as_posix()] = sha256(path)
    for relative in ("AGENTS.md", "CLAUDE.md", "task_instruction.md", "framework.json",
                     ".codex/config.toml", ".claude/settings.json"):
        path = framework_root / relative
        if path.is_file():
            controls[relative] = sha256(path)
    stable_contract = deepcopy(resolved)
    # Retry manifests may change row positions without changing the task.
    stable_contract.get("metadata", {}).pop("manifest_index", None)
    identity = {"version": "research-input-v1", "contract": stable_contract,
                "route": decision, "inputs": inputs, "controls": controls,
                "execution": execution or {}}
    return resolved, identity


def completion_files(task_root: Path) -> dict[str, str]:
    files = {}
    for path in sorted(task_root.rglob("*")):
        if path.name == "completion_manifest.json" or not path.is_file():
            continue
        if not path.resolve().is_relative_to(task_root.resolve()):
            raise ValueError(f"Task artifact escapes its workspace: {path}")
        files[path.relative_to(task_root).as_posix()] = sha256(path)
    return files


def seal_completion(task_root: Path) -> None:
    record = {"version": "research-completion-v1", "review_status": "unreviewed",
              "scope": "task files at successful launcher completion; excludes external native sessions",
              "files": completion_files(task_root)}
    with (task_root / "completion_manifest.json").open("x", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def verify_completion(task_root: Path) -> None:
    path = task_root / "completion_manifest.json"
    if not path.is_file():
        raise ValueError("Completed output has no launcher completion manifest; use a fresh task directory")
    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("files") != completion_files(task_root):
        raise ValueError("Completed task bytes differ from the completion manifest")
