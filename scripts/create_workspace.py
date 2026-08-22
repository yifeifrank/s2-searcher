#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


SUPPORTED_EXAMPLES = {"elite-biography"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a standalone, codebook-driven research task-pack subfolder"
    )
    parser.add_argument("--target", required=True, help="Subfolder to create")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--instruction",
        help="Combined Markdown instruction containing ## Codebook fenced JSON object(s)",
    )
    source.add_argument("--codebook", help="Compatibility input: standalone JSON codebook")
    source.add_argument(
        "--inquiry",
        action="store_true",
        help="Create a general inquiry workspace without a user-supplied codebook",
    )
    source.add_argument("--example", choices=sorted(SUPPORTED_EXAMPLES))
    parser.add_argument(
        "--instructions",
        help="Compatibility input: separate prose Markdown used only with --codebook",
    )
    parser.add_argument("--output-schema", help="Optional user-supplied JSON Schema")
    parser.add_argument(
        "--runtime",
        choices=("codex", "claude", "both"),
        default="both",
    )
    parser.add_argument(
        "--default-route",
        choices=("auto", "direct_api", "local_agent", "online_agent"),
        default="auto",
    )
    parser.add_argument("--project-name", default="")
    return parser.parse_args()


def load_json_object(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"Invalid {label} JSON at {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"{label} must be a JSON object: {path}")
    return value


def ensure_empty_target(target: Path) -> None:
    if target.exists() and any(target.iterdir()):
        raise SystemExit(
            f"Target already contains files: {target}. Choose a new subfolder; "
            "the builder does not overwrite projects implicitly."
        )
    target.parent.mkdir(parents=True, exist_ok=True)


def render_text_files(target: Path, project_name: str) -> None:
    replacements = {"__PROJECT_NAME__": project_name}
    for path in target.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {
            ".md",
            ".json",
            ".toml",
            ".yaml",
            ".yml",
            ".txt",
            ".example",
        }:
            continue
        text = path.read_text(encoding="utf-8")
        updated = text
        for old, new in replacements.items():
            updated = updated.replace(old, new)
        if updated != text:
            path.write_text(updated, encoding="utf-8")


def remove_unused_runtime(target: Path, runtime: str) -> None:
    if runtime == "codex":
        shutil.rmtree(target / ".claude", ignore_errors=True)
    elif runtime == "claude":
        shutil.rmtree(target / ".codex", ignore_errors=True)
        shutil.rmtree(target / ".agents", ignore_errors=True)


def configure_project(
    target: Path,
    project_name: str,
    runtime: str,
    default_route: str,
    output_schema_path: str,
) -> None:
    config_path = target / "framework.json"
    config = load_json_object(config_path, "framework configuration")
    config["project_name"] = project_name
    config["runtime_support"] = runtime
    config["default_route"] = default_route
    config["output_schema_path"] = output_schema_path
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def validate_created_project(target: Path) -> None:
    completed = subprocess.run(
        [sys.executable, "validate.py", "project"],
        cwd=target,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if completed.returncode != 0:
        raise SystemExit(
            "Project was created but validation failed:\n" + completed.stdout
        )
    print(completed.stdout.rstrip())


def main() -> None:
    args = parse_args()
    skill_root = Path(__file__).resolve().parents[1]
    template = skill_root / "assets" / "workspace-template"
    if not template.is_dir():
        raise SystemExit(f"Missing workspace template: {template}")

    sys.path.insert(0, str(template / "tools"))
    from instruction_contract import (  # noqa: PLC0415
        compose_instruction,
        extract_codebook,
        instruction_prose,
    )

    if args.instructions and not args.codebook:
        raise SystemExit("--instructions is a compatibility option and requires --codebook")

    target = Path(args.target).expanduser().resolve()
    ensure_empty_target(target)

    if args.instruction:
        instruction_source = Path(args.instruction).expanduser().resolve()
        if not instruction_source.is_file():
            raise SystemExit(f"Combined instruction file not found: {instruction_source}")
        instruction_text = instruction_source.read_text(encoding="utf-8-sig")
        try:
            codebook = extract_codebook(instruction_text)
        except ValueError as exc:
            raise SystemExit(f"Invalid combined instruction at {instruction_source}: {exc}") from exc
    elif args.inquiry:
        instruction_source = template / "inputs" / "inquiry_instruction.md"
        instruction_text = instruction_source.read_text(encoding="utf-8-sig")
        try:
            codebook = extract_codebook(instruction_text)
        except ValueError as exc:
            raise SystemExit(f"Invalid bundled inquiry instruction: {exc}") from exc
    elif args.example:
        instruction_source = (
            skill_root
            / "assets"
            / "workspace-template"
            / "examples"
            / str(args.example)
            / "instruction.md"
        )
        instruction_text = instruction_source.read_text(encoding="utf-8-sig")
        try:
            codebook = extract_codebook(instruction_text)
        except ValueError as exc:
            raise SystemExit(f"Invalid bundled example instruction: {exc}") from exc
    else:
        codebook_source = Path(str(args.codebook)).expanduser().resolve()
        codebook = load_json_object(codebook_source, "codebook")
        if args.instructions:
            instructions_source = Path(args.instructions).expanduser().resolve()
            if not instructions_source.is_file():
                raise SystemExit(f"Instruction file not found: {instructions_source}")
            prose = instructions_source.read_text(encoding="utf-8-sig")
        else:
            generic_instruction = (template / "inputs" / "instruction.md").read_text(
                encoding="utf-8-sig"
            )
            prose = instruction_prose(generic_instruction)
        try:
            instruction_text = compose_instruction(prose, codebook)
        except ValueError as exc:
            raise SystemExit(f"Cannot combine separate instruction/codebook inputs: {exc}") from exc

    shutil.copytree(template, target, dirs_exist_ok=True)
    (target / "inputs" / "instruction.md").write_text(
        instruction_text.rstrip() + "\n", encoding="utf-8"
    )
    (target / "inputs" / "codebook.json").write_text(
        json.dumps(codebook, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    output_schema_relpath = "schemas/final_report.schema.json"
    if args.output_schema:
        schema_source = Path(args.output_schema).expanduser().resolve()
        load_json_object(schema_source, "output schema")
        output_schema_relpath = "schemas/user_output.schema.json"
        shutil.copy2(schema_source, target / output_schema_relpath)

    project_name = args.project_name.strip() or target.name
    render_text_files(target, project_name)
    remove_unused_runtime(target, args.runtime)
    configure_project(
        target=target,
        project_name=project_name,
        runtime=args.runtime,
        default_route=args.default_route,
        output_schema_path=output_schema_relpath,
    )
    validate_created_project(target)

    print(f"Created research task pack: {target}")
    print(f"Combined instruction: {target / 'inputs' / 'instruction.md'}")
    print(f"Extracted codebook mirror: {target / 'inputs' / 'codebook.json'}")
    print(f"Runtime support: {args.runtime}")
    print(f"Default route: {args.default_route}")
    if args.inquiry:
        print('Inquiry mode: run python3 inquiry.py "<question>" --runtime codex|claude')
    print("Start Codex or Claude from inside that folder so project agents load.")


if __name__ == "__main__":
    main()
