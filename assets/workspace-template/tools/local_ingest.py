#!/usr/bin/env python3
"""Materialize one local text document as a stable task-local cached page."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from research_tools import (
    append_ndjson,
    log_action,
    lookup_cache,
    lookup_cache_key,
    now_iso,
    slugify,
    task_paths,
)


TEXT_SUFFIXES = {
    "",
    ".csv",
    ".htm",
    ".html",
    ".js",
    ".json",
    ".jsonl",
    ".markdown",
    ".md",
    ".ndjson",
    ".py",
    ".r",
    ".rst",
    ".tex",
    ".toml",
    ".ts",
    ".tsv",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}


def share_with_library(paths, record: dict, markdown: str) -> bool:
    cache_key = str(record["cache_key"])
    global_path = paths.global_pages / f"{cache_key}.md"
    global_path.write_text(markdown, encoding="utf-8")
    if lookup_cache_key(paths.global_index, cache_key) is not None:
        return True
    library_record = dict(record)
    library_record["markdown_path"] = str(global_path)
    library_record["shared_from"] = "local_ingest"
    library_record["content_sha256"] = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
    append_ndjson(paths.global_index, library_record)
    return True


def command_ingest(args: argparse.Namespace) -> int:
    source = Path(args.path).expanduser().resolve()
    if not source.is_file():
        raise SystemExit(f"local source is not a file: {source}")
    if source.suffix.lower() not in TEXT_SUFFIXES:
        allowed = ", ".join(sorted(suffix or "<no suffix>" for suffix in TEXT_SUFFIXES))
        raise SystemExit(f"unsupported local text suffix {source.suffix!r}; allowed: {allowed}")

    raw = source.read_bytes()
    if b"\x00" in raw:
        raise SystemExit(f"local source appears to be binary: {source}")
    text = raw.decode("utf-8-sig", errors="replace")
    canonical_url = source.as_uri()
    paths = task_paths(Path(args.task_root).resolve())

    existing = lookup_cache(paths.local_index, canonical_url)
    if existing and not args.force_refresh:
        cached_path = paths.task_root / str(existing.get("markdown_path", ""))
        if cached_path.is_file():
            shared = share_with_library(
                paths,
                existing,
                cached_path.read_text(encoding="utf-8-sig", errors="replace"),
            ) if args.share_with_library else False
            payload = {
                "status": "success",
                "cache_hit": True,
                "source": "local_document",
                "backend": "local_ingest",
                "canonical_url": canonical_url,
                "cache_key": str(existing["cache_key"]),
                "markdown_path": str(existing["markdown_path"]),
                "shared_with_library": shared,
            }
            log_action(paths, {"action": "ingest", **payload, "path": str(source)})
            print(json.dumps(payload, ensure_ascii=False))
            return 0

    digest = hashlib.sha1(canonical_url.encode("utf-8")).hexdigest()[:8]
    stem = slugify(source.stem) or "document"
    cache_key = f"local_{stem}_{digest}"
    rel_path = f"cache/pages/{cache_key}.md"
    cached_path = paths.task_root / rel_path
    cached_path.parent.mkdir(parents=True, exist_ok=True)
    title = (args.title or source.name).replace("\r", " ").replace("\n", " ").strip()
    markdown = f"# {title}\n\nSource: `{source}`\n\n{text.rstrip()}\n"
    cached_path.write_text(markdown, encoding="utf-8")

    record = {
        "canonical_url": canonical_url,
        "cache_key": cache_key,
        "markdown_path": rel_path,
        "backend": "local_ingest",
        "source_path": str(source),
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "content_sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
        "stored_at": now_iso(),
    }
    append_ndjson(paths.local_index, record)
    shared = share_with_library(paths, record, markdown) if args.share_with_library else False
    log_action(
        paths,
        {
            "action": "ingest",
            "status": "success",
            "backend": "local_ingest",
            "path": str(source),
            "cache_key": cache_key,
            "cache_hit": False,
            "shared_with_library": shared,
        },
    )
    print(
        json.dumps(
            {
                "status": "success",
                "cache_hit": False,
                "source": "local_document",
                "backend": "local_ingest",
                "canonical_url": canonical_url,
                "cache_key": cache_key,
                "markdown_path": rel_path,
                "shared_with_library": shared,
            },
            ensure_ascii=False,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-root", required=True)
    parser.add_argument("--path", required=True)
    parser.add_argument("--title")
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument(
        "--share-with-library",
        action="store_true",
        help="Copy this ingested source into the workspace library for later tasks",
    )
    return parser


if __name__ == "__main__":
    sys.exit(command_ingest(build_parser().parse_args()))
