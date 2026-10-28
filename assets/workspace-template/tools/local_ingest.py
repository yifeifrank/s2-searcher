#!/usr/bin/env python3
"""Materialize one local text document as a stable task-local cached page."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from research_tools import log_action, lookup_cache, normalize_url, store_cache, task_paths, verify_cached_page
from urllib.parse import urlparse


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
    source_url = getattr(args, "source_url", None)
    if source_url and urlparse(source_url).scheme not in {"http", "https"}:
        raise SystemExit("--source-url must be an http(s) URL")
    canonical_url = normalize_url(source_url) if source_url else source.as_uri()
    paths = task_paths(Path(args.task_root).resolve())
    existing = lookup_cache(paths.local_index, canonical_url)
    title = (args.title or source.name).replace("\r", " ").replace("\n", " ").strip()
    content_kind = getattr(args, "content_kind", "source_text")
    backend = (getattr(args, "capture_backend", None) or "native_capture") if source_url else "local_ingest"
    markdown = f"# {title}\n\nSource: {canonical_url}\nContent kind: {content_kind}\n\n{text}"
    record = store_cache(paths, canonical_url, markdown, backend, content_kind=content_kind,
                         metadata={"source_path": str(source), "source_sha256": hashlib.sha256(raw).hexdigest()},
                         share=bool(getattr(args, "share_with_library", False)))
    cache_key = record["cache_key"]
    rel_path = record["markdown_path"]
    cache_hit = bool(existing and existing["cache_key"] == cache_key and not args.force_refresh)
    verify_cached_page(paths, record)
    log_action(
        paths,
        {
            "action": "ingest",
            "status": "success",
            "backend": backend,
            "path": str(source),
            "cache_key": cache_key,
            "cache_hit": cache_hit,
        },
    )
    print(
        json.dumps(
            {
                "status": "success",
                "cache_hit": cache_hit,
                "shared_with_library": bool(getattr(args, "share_with_library", False)),
                "source": "external_capture" if source_url else "local_document",
                "backend": backend,
                "content_kind": content_kind,
                "canonical_url": canonical_url,
                "cache_key": cache_key,
                "markdown_path": rel_path,
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
    parser.add_argument("--source-url", help="Origin URL of a capture saved by a native or external tool")
    parser.add_argument("--capture-backend", help="Name of the tool that actually produced the capture")
    parser.add_argument("--content-kind", choices=("source_text", "excerpt", "summary"), default="source_text")
    parser.add_argument("--share-with-library", action="store_true", help="Share this local capture with the workspace source library")
    return parser


if __name__ == "__main__":
    sys.exit(command_ingest(build_parser().parse_args()))
