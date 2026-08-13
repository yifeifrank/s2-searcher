#!/usr/bin/env python3
"""Run credential-free checks used locally and by GitHub Actions."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "assets" / "workspace-template"


def run(*command: str, cwd: Path = ROOT) -> str:
    completed = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if completed.returncode != 0:
        raise SystemExit(
            f"Release check failed ({' '.join(command)}):\n{completed.stdout}"
        )
    return completed.stdout


def scan_public_tree() -> None:
    secret_patterns = (
        re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
        re.compile(r"fc-[A-Za-z0-9_-]{20,}"),
        re.compile(r"[A-Za-z0-9_-]{32,}\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}"),
    )
    private_markers = (
        "/home/" + "frank",
        "/mnt/e/" + "AI",
        "launch_batch_" + "codex_private",
    )
    errors: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or ".git" in path.parts or "__pycache__" in path.parts:
            continue
        if path.name == ".env":
            errors.append(f"Tracked credential file: {path.relative_to(ROOT)}")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if any(pattern.search(text) for pattern in secret_patterns):
            errors.append(f"Credential-shaped token: {path.relative_to(ROOT)}")
        if any(marker in text for marker in private_markers):
            errors.append(f"Private path or wrapper reference: {path.relative_to(ROOT)}")
    if errors:
        raise SystemExit("Public-tree scan failed:\n- " + "\n- ".join(errors))


def check_release_metadata() -> None:
    for relative in (
        "README.md", "LICENSE", "CITATION.cff", "CHANGELOG.md", "SECURITY.md",
        ".github/workflows/ci.yml",
    ):
        if not (ROOT / relative).is_file():
            raise SystemExit(f"Missing release file: {relative}")
    citation = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
    for marker in (
        "cff-version: 1.2.0",
        "version: 0.1.0",
        "type: software",
        'given-names: "Yifei"',
        'doi: "10.48550/arXiv.2603.18010"',
    ):
        if marker not in citation:
            raise SystemExit(f"CITATION.cff missing marker: {marker}")
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    if not skill.startswith("---\nname: research-workspace-builder\n"):
        raise SystemExit("SKILL.md frontmatter is missing or malformed")


def check_example_snapshot() -> None:
    example = ROOT / "examples" / "elite-biography-dry-run"
    contract_path = example / "task_contract.json"
    expected_path = example / "route_decision.json"
    if not (example / "README.md").is_file():
        raise SystemExit("Missing example README")
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    actual = json.loads(run(
        sys.executable,
        "router.py",
        "--contract", str(contract_path),
        "--stdout",
        cwd=TEMPLATE,
    ))
    if actual != expected:
        raise SystemExit("Tracked elite-biography route snapshot is stale")


def validate_fresh_workspace() -> None:
    with tempfile.TemporaryDirectory(prefix="research-workspace-release-") as temporary:
        workspace = Path(temporary) / "workspace"
        run(
            sys.executable,
            str(ROOT / "scripts/create_workspace.py"),
            "--target", str(workspace),
            "--example", "elite-biography",
            "--runtime", "both",
            "--project-name", "release-check",
        )
        for contract in ("direct_api", "local_agent", "online_agent"):
            output = run(
                sys.executable,
                "router.py",
                "--contract", f"examples/contracts/{contract}.json",
                "--stdout",
                cwd=workspace,
            )
            decision = json.loads(output)
            if decision["selected_route"] != contract:
                raise SystemExit(
                    f"Route mismatch for {contract}: {decision['selected_route']}"
                )
        tasks = workspace / "tasks" / "dry_run"
        run(
            sys.executable,
            "batch.py",
            "--manifest", "inputs/manifest.example.csv",
            "--runtime", "codex",
            "--tasks-dir", str(tasks),
            "--dry-run",
            cwd=workspace,
        )
        summary = json.loads((tasks / "batch_summary.json").read_text(encoding="utf-8"))
        if summary.get("dry_run") != 3 or summary.get("failed") != 0:
            raise SystemExit(f"Unexpected dry-run summary: {summary}")
        if summary.get("unsafe_unattended") is not False:
            raise SystemExit("Dry run did not record the safe permission default")


def validate_release_archive() -> None:
    with tempfile.TemporaryDirectory(prefix="research-workspace-package-") as temporary:
        output = run(
            sys.executable,
            str(ROOT / "scripts/package_release.py"),
            "--output-dir", temporary,
        ).strip()
        archive_path = Path(output)
        with zipfile.ZipFile(archive_path) as archive:
            names = archive.namelist()
        required = "research-workspace-builder/SKILL.md"
        if required not in names:
            raise SystemExit(f"Release archive missing {required}")
        def forbidden(name: str) -> bool:
            parts = Path(name).parts
            leaf = parts[-1]
            credential = leaf == ".env" or (
                leaf.startswith(".env.") and leaf != ".env.example"
            )
            return (
                ".git" in parts
                or "__pycache__" in parts
                or credential
                or leaf.endswith(".pyc")
            )
        if any(forbidden(name) for name in names):
            raise SystemExit("Release archive contains excluded files")


def main() -> None:
    check_release_metadata()
    scan_public_tree()
    check_example_snapshot()
    run(sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v", cwd=TEMPLATE)
    run(sys.executable, "validate.py", "project", cwd=TEMPLATE)
    validate_fresh_workspace()
    validate_release_archive()
    print("OK: metadata, safety scan, tests, validation, scaffold, routes, dry run, and package")


if __name__ == "__main__":
    main()
