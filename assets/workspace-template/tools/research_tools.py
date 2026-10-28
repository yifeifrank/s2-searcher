#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen, build_opener, ProxyHandler


USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
DEFAULT_SEARCH_BACKEND = "firecrawl"
DEFAULT_RETRIEVER_BACKEND = "exa"


def _load_project_env() -> None:
    """Load the standalone pack's .env without overriding the parent process."""
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


_load_project_env()


def _build_opener():
    proxy = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or os.getenv("https_proxy") or os.getenv("http_proxy")
    if proxy:
        return build_opener(ProxyHandler({"http": proxy, "https": proxy}))
    return build_opener()


def _urlopen(request, timeout=30):
    try:
        return _OPENER.open(request, timeout=timeout)
    except HTTPError as exc:
        # Error bodies are unused; never buffer them.
        exc.close()
        raise


_OPENER = _build_opener()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def append_ndjson(path: Path, payload: dict[str, Any]) -> None:
    ensure_parent(path)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def read_ndjson(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    items: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                items.append(item)
    return items


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def normalize_url(url: str) -> str:
    parsed = urlparse(url.strip())
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc.lower()
    path = parsed.path or "/"
    return f"{scheme}://{netloc}{path}" + (f"?{parsed.query}" if parsed.query else "")


def cache_key_for_url(url: str) -> str:
    parsed = urlparse(url)
    host = slugify(parsed.netloc or "unknown")
    stem = slugify((parsed.path or "/").strip("/")) or "index"
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
    return f"{host}_{stem}_{digest}"


def strip_html(html: str) -> str:
    html = re.sub(r"(?is)<script.*?>.*?</script>", " ", html)
    html = re.sub(r"(?is)<style.*?>.*?</style>", " ", html)
    html = re.sub(r"(?is)<(br|p|div|li|tr|h[1-6])\b.*?>", "\n", html)
    html = re.sub(r"(?is)</(p|div|li|tr|h[1-6])>", "\n", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    html = unescape(html)
    html = re.sub(r"\r\n?", "\n", html)
    html = re.sub(r"\n{3,}", "\n\n", html)
    html = re.sub(r"[ \t]{2,}", " ", html)
    return html.strip()


# Maximum wire-body bytes retained before decoding or JSON parsing. Override with
# a positive integer RESEARCH_HTTP_MAX_RESPONSE_BYTES; bodies are never truncated.
DEFAULT_HTTP_MAX_RESPONSE_BYTES = 16 * 1024 * 1024
HTTP_READ_CHUNK_BYTES = 64 * 1024


class HTTPResponsePolicyError(RuntimeError):
    """A response or configuration violates the bounded HTTP policy."""


class HTTPResponseLimitError(HTTPResponsePolicyError):
    """The declared or streamed response body exceeds the configured limit."""


def _http_response_limit() -> int:
    raw = os.getenv("RESEARCH_HTTP_MAX_RESPONSE_BYTES")
    if raw is None:
        return DEFAULT_HTTP_MAX_RESPONSE_BYTES
    try:
        limit = int(raw)
    except ValueError as exc:
        raise HTTPResponsePolicyError(
            "RESEARCH_HTTP_MAX_RESPONSE_BYTES must be a positive integer"
        ) from exc
    if limit <= 0:
        raise HTTPResponsePolicyError(
            "RESEARCH_HTTP_MAX_RESPONSE_BYTES must be a positive integer"
        )
    return limit


def _read_limited_response(response, limit: int) -> bytes:
    """Read at most limit + 1 bytes, including when Content-Length is absent.

    urllib does not automatically decompress responses. Request identity encoding
    and reject an encoded response rather than decoding corrupt evidence or
    introducing an unbounded decompression path.
    """
    encoding = response.headers.get("Content-Encoding", "").strip().lower()
    if encoding not in {"", "identity"}:
        raise HTTPResponsePolicyError(
            f"Unsupported HTTP Content-Encoding {encoding!r}; identity is required"
        )
    declared = response.headers.get("Content-Length")
    if declared is not None:
        try:
            declared_bytes = int(declared)
        except (TypeError, ValueError):
            declared_bytes = -1  # An invalid header cannot bypass the stream cap.
        if declared_bytes > limit:
            raise HTTPResponseLimitError(
                f"HTTP response Content-Length {declared_bytes} exceeds "
                f"RESEARCH_HTTP_MAX_RESPONSE_BYTES={limit}"
            )
    body = bytearray()
    while True:
        chunk = response.read(min(HTTP_READ_CHUNK_BYTES, limit - len(body) + 1))
        if not chunk:
            return bytes(body)
        if len(chunk) > limit - len(body):
            raise HTTPResponseLimitError(
                f"HTTP response exceeds RESEARCH_HTTP_MAX_RESPONSE_BYTES={limit}"
            )
        body.extend(chunk)


def http_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    body = None
    limit = _http_response_limit()
    req_headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
    if headers:
        req_headers.update(headers)
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")
    request = Request(url, data=body, headers=req_headers, method=method)
    with _urlopen(request, timeout=timeout) as response:
        raw = _read_limited_response(response, limit).decode("utf-8", errors="replace")
    return json.loads(raw)


def http_text(url: str, *, timeout: int = 30, headers: dict[str, str] | None = None) -> tuple[str, str]:
    limit = _http_response_limit()
    req_headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
    if headers:
        req_headers.update(headers)
    request = Request(url, headers=req_headers)
    with _urlopen(request, timeout=timeout) as response:
        content_type = response.headers.get("Content-Type", "")
        raw_bytes = _read_limited_response(response, limit)
    text = raw_bytes.decode("utf-8", errors="replace")
    return text, content_type


def _quote_phrase(term: str) -> str:
    """Wrap multi-word terms in double quotes for exact-match search."""
    if " " in term and not (term.startswith('"') and term.endswith('"')):
        return f'"{term}"'
    return term


def build_query_from_intent(intent: dict[str, Any]) -> str:
    must_include = [str(x).strip() for x in intent.get("must_include", []) if str(x).strip()]
    any_of = [str(x).strip() for x in intent.get("any_of", []) if str(x).strip()]
    must_not_include = [str(x).strip() for x in intent.get("must_not_include", []) if str(x).strip()]
    site = str(intent.get("site", "")).strip()
    parts: list[str] = []
    parts.extend(_quote_phrase(term) for term in must_include)
    if any_of:
        parts.append("(" + " OR ".join(_quote_phrase(t) for t in any_of) + ")")
    parts.extend(f"-{_quote_phrase(term)}" for term in must_not_include)
    if site:
        parts.append(f"site:{site}")
    return " ".join(parts).strip()


@dataclass
class TaskPaths:
    task_root: Path
    framework_root: Path
    local_pages: Path
    local_index: Path
    local_log: Path
    evidence_file: Path
    global_pages: Path
    global_index: Path


def task_paths(task_root: Path) -> TaskPaths:
    # In a generated standalone pack this script lives at
    # <framework-root>/tools/research_tools.py.
    framework_root = Path(__file__).resolve().parents[1]
    local_pages = task_root / "cache" / "pages"
    local_index = task_root / "cache" / "cache_index.jsonl"
    local_log = task_root / "cache" / "research_log.ndjson"
    evidence_file = task_root / "evidence.ndjson"
    global_pages = framework_root / "tasks" / "_global_cache" / "pages"
    global_index = framework_root / "tasks" / "_global_cache" / "index.jsonl"
    local_pages.mkdir(parents=True, exist_ok=True)
    ensure_parent(local_index)
    ensure_parent(local_log)
    ensure_parent(evidence_file)
    global_pages.mkdir(parents=True, exist_ok=True)
    ensure_parent(global_index)
    return TaskPaths(
        task_root=task_root,
        framework_root=framework_root,
        local_pages=local_pages,
        local_index=local_index,
        local_log=local_log,
        evidence_file=evidence_file,
        global_pages=global_pages,
        global_index=global_index,
    )


def log_action(paths: TaskPaths, payload: dict[str, Any]) -> None:
    payload = dict(payload)
    payload.setdefault("timestamp", now_iso())
    append_ndjson(paths.local_log, payload)


def lookup_cache(index_path: Path, url: str) -> dict[str, Any] | None:
    normalized = normalize_url(url)
    for item in reversed(read_ndjson(index_path)):
        if normalize_url(str(item.get("canonical_url", ""))) == normalized:
            return item
    return None


def lookup_cache_key(index_path: Path, cache_key: str) -> dict[str, Any] | None:
    for item in reversed(read_ndjson(index_path)):
        if str(item.get("cache_key", "")) == cache_key:
            return item
    return None


def cached_markdown_path(paths: TaskPaths, cache_key: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_]+", cache_key):
        raise ValueError(f"invalid cache_key: {cache_key!r}")
    path = paths.local_pages / f"{cache_key}.md"
    try:
        path.resolve().relative_to(paths.local_pages.resolve())
    except ValueError as exc:
        raise ValueError(f"cache_key resolves outside cache/pages: {cache_key!r}") from exc
    return path


def coerce_line_number(item: dict[str, Any], field: str) -> int:
    value = item.get(field)
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer line number")
    try:
        line_number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer line number") from exc
    return line_number


def validate_archive_citation(paths: TaskPaths, item: dict[str, Any]) -> dict[str, Any]:
    citation_fields = {"cache_key", "start_line", "end_line"}
    present = citation_fields.intersection(item)
    if present != citation_fields:
        missing = ", ".join(sorted(citation_fields - present))
        raise ValueError(
            "line-based evidence citations require cache_key, start_line, and end_line "
            f"(missing: {missing})"
        )

    cache_key = str(item["cache_key"]).strip()
    if not cache_key:
        raise ValueError("cache_key must not be empty")
    start_line = coerce_line_number(item, "start_line")
    end_line = coerce_line_number(item, "end_line")
    if start_line < 1:
        raise ValueError("start_line must be >= 1")
    if end_line < start_line:
        raise ValueError("end_line must be >= start_line")

    markdown_path = cached_markdown_path(paths, cache_key)
    if not markdown_path.exists():
        raise ValueError(f"cached markdown not found for cache_key {cache_key!r}: {markdown_path}")

    lines = markdown_path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    if end_line > len(lines):
        raise ValueError(
            f"line range {start_line}-{end_line} exceeds cached markdown length "
            f"({len(lines)} lines) for cache_key {cache_key!r}"
        )

    record = lookup_cache_key(paths.local_index, cache_key)
    if record is None:
        raise ValueError(f"cache_key is not indexed in this task: {cache_key}")
    verify_cached_page(paths, record)
    item["content_kind"] = record.get("content_kind", "source_text")
    if item["content_kind"] == "summary" and any(field in item for field in ("quote", "exact_quote", "quote_fragments")):
        raise ValueError("A generated summary cannot support an original-source quotation")
    excerpt = "\n".join(lines[start_line - 1:end_line])
    page_hash = hashlib.sha256(markdown_path.read_bytes()).hexdigest()
    for field, value in (("page_sha256", page_hash), ("source_excerpt", excerpt)):
        if field in item and item[field] != value:
            raise ValueError(f"{field} differs from the cited source")
        item[field] = value
    validate_quotation(item, excerpt)
    item["cache_key"] = cache_key
    item["start_line"] = start_line
    item["end_line"] = end_line
    item.setdefault("markdown_path", f"cache/pages/{cache_key}.md")
    item.setdefault("line_count", len(lines))
    preview = " ".join(line.strip() for line in lines[start_line - 1:end_line] if line.strip())
    if preview:
        item.setdefault("citation_text_preview", preview[:500])
    if record:
        item.setdefault("url", str(record.get("canonical_url", "")))
        item.setdefault("canonical_url", str(record.get("canonical_url", "")))
        item.setdefault("retrieval_backend", str(record.get("backend", "")))
    if "labels" in item and "relevant_chunk_labels" not in item:
        item["relevant_chunk_labels"] = item["labels"]
    return item


def store_cache(
    paths: TaskPaths, canonical_url: str, markdown: str, backend: str, *,
    content_kind: str = "source_text", metadata: dict[str, Any] | None = None,
    share: bool = True,
) -> dict[str, Any]:
    if content_kind not in {"source_text", "excerpt", "summary"}:
        raise ValueError(f"Unsupported content_kind: {content_kind}")
    metadata = metadata or {}
    data = markdown.encode("utf-8")
    page_hash = hashlib.sha256(data).hexdigest()
    revision = hashlib.sha256(json.dumps(
        [canonical_url, backend, content_kind, page_hash, metadata], sort_keys=True
    ).encode("utf-8")).hexdigest()
    # Bounded, portable names with immutable source/representation identities.
    cache_key = f"{cache_key_for_url(canonical_url)[:80]}_{revision}"
    rel_path = f"cache/pages/{cache_key}.md"
    local_path = paths.task_root / rel_path
    global_path = paths.global_pages / f"{cache_key}.md"
    write_snapshot(local_path, data)
    if share:
        write_snapshot(global_path, data)
    record = {
        "canonical_url": canonical_url,
        "cache_key": cache_key,
        "markdown_path": rel_path,
        "backend": backend,
        "stored_at": now_iso(),
        "page_sha256": page_hash,
        "content_sha256": page_hash,
        "content_kind": content_kind,
        **metadata,
    }
    append_ndjson(paths.local_index, record)
    if share:
        append_ndjson(paths.global_index, {**record, "markdown_path": str(global_path)})
    return record


def materialize_global_hit(paths: TaskPaths, record: dict[str, Any]) -> dict[str, Any]:
    cache_key = str(record["cache_key"])
    source_path = paths.global_pages / f"{cache_key}.md"
    rel_path = f"cache/pages/{cache_key}.md"
    target_path = paths.task_root / rel_path
    expected = record.get("page_sha256") or record.get("content_sha256")
    if not source_path.is_file() or hashlib.sha256(source_path.read_bytes()).hexdigest() != expected:
        raise ValueError(f"Global cache snapshot is missing, unversioned, or changed: {source_path}")
    write_snapshot(target_path, source_path.read_bytes())
    local_record = dict(record)
    local_record["markdown_path"] = rel_path
    local_record["page_sha256"] = expected
    local_record.setdefault("content_kind", "source_text")
    append_ndjson(paths.local_index, local_record)
    return local_record


def search_library(
    paths: TaskPaths,
    query: str,
    num_results: int,
    *,
    materialize: bool,
) -> list[dict[str, Any]]:
    """Search the workspace cache as a small transparent source library."""
    terms = list(dict.fromkeys(
        term for term in re.findall(r"[\w-]+", query.casefold()) if len(term) > 1
    ))
    if not terms:
        raise ValueError("library search query must contain a searchable term")
    if num_results < 1:
        raise ValueError("num_results must be positive")

    newest: dict[str, dict[str, Any]] = {}
    for record in reversed(read_ndjson(paths.global_index)):
        cache_key = str(record.get("cache_key", "")).strip()
        if cache_key and cache_key not in newest:
            newest[cache_key] = record

    matches: list[tuple[int, dict[str, Any]]] = []
    query_folded = query.casefold().strip()
    for cache_key, record in newest.items():
        page = paths.global_pages / f"{cache_key}.md"
        if not page.is_file():
            continue
        text = page.read_text(encoding="utf-8-sig", errors="replace")
        folded = text.casefold()
        metadata_text = " ".join(
            str(record.get(key, ""))
            for key in ("canonical_url", "backend", "source_path")
        ).casefold()
        score = sum(min(folded.count(term), 10) for term in terms)
        score += 3 * sum(term in metadata_text for term in terms)
        if query_folded and query_folded in folded:
            score += 10
        if score <= 0:
            continue
        matched_lines: list[dict[str, Any]] = []
        for line_number, line in enumerate(text.splitlines(), 1):
            if any(term in line.casefold() for term in terms):
                matched_lines.append({"line": line_number, "text": line.strip()[:300]})
                if len(matched_lines) >= 3:
                    break
        result = {
            "cache_key": cache_key,
            "canonical_url": str(record.get("canonical_url", "")),
            "backend": str(record.get("backend", "")),
            "content_sha256": str(record.get("content_sha256", "")),
            "score": score,
            "matched_lines": matched_lines,
        }
        matches.append((score, result))

    matches.sort(key=lambda item: (-item[0], item[1]["cache_key"]))
    results = [item[1] for item in matches[:num_results]]
    if materialize:
        for result in results:
            record = newest[str(result["cache_key"])]
            local_record = materialize_global_hit(paths, record)
            result["markdown_path"] = str(local_record["markdown_path"])
    return results


def search_serper(query: str, num_results: int, *, hl: str = "", gl: str = "") -> list[dict[str, str]]:
    api_key = os.getenv("SERPER_API_KEY")
    if not api_key:
        raise RuntimeError("SERPER_API_KEY is required for backend=serper")
    payload: dict[str, Any] = {"q": query, "num": num_results}
    if hl:
        payload["hl"] = hl
    if gl:
        payload["gl"] = gl
    data = http_json(
        "https://google.serper.dev/search",
        method="POST",
        headers={"X-API-KEY": api_key},
        payload=payload,
    )
    results: list[dict[str, str]] = []
    for item in data.get("organic", [])[:num_results]:
        results.append(
            {
                "title": str(item.get("title", "")).strip(),
                "url": str(item.get("link", "")).strip(),
                "snippet": str(item.get("snippet", "")).strip(),
            }
        )
    return results


def search_exa(query: str, num_results: int) -> list[dict[str, str]]:
    api_key = os.getenv("EXA_API_KEY")
    if not api_key:
        raise RuntimeError("EXA_API_KEY is required for backend=exa")
    data = http_json(
        "https://api.exa.ai/search",
        method="POST",
        headers={"x-api-key": api_key},
        payload={"query": query, "numResults": num_results, "type": "auto"},
    )
    results: list[dict[str, str]] = []
    for item in data.get("results", [])[:num_results]:
        results.append(
            {
                "title": str(item.get("title", "")).strip(),
                "url": str(item.get("url", "")).strip(),
                "snippet": str(item.get("text", "")).strip(),
            }
        )
    return results


def search_firecrawl(query: str, num_results: int) -> list[dict[str, str]]:
    api_key = os.getenv("FIRECRAWL_API_KEY")
    if not api_key:
        raise RuntimeError("FIRECRAWL_API_KEY is required for backend=firecrawl")
    data = http_json(
        "https://api.firecrawl.dev/v2/search",
        method="POST",
        headers={"Authorization": f"Bearer {api_key}"},
        payload={"query": query, "limit": num_results, "sources": ["web"]},
    )
    payload = data.get("data") or data.get("results") or []
    if isinstance(payload, dict):
        items = payload.get("web") or []
    else:
        items = payload
    results: list[dict[str, str]] = []
    for item in items[:num_results]:
        results.append(
            {
                "title": str(item.get("title", "")).strip(),
                "url": str(item.get("url", "")).strip(),
                "snippet": str(item.get("description", "") or item.get("markdown", "")).strip(),
            }
        )
    return results


def search_ddg_html(query: str, num_results: int) -> list[dict[str, str]]:
    url = "https://html.duckduckgo.com/html/?q=" + quote(query)
    html, _ = http_text(url)
    results: list[dict[str, str]] = []
    pattern = re.compile(
        r'<a[^>]+class="result__a"[^>]+href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>(?P<tail>.*?)(?=<a[^>]+class="result__a"|$)',
        re.I | re.S,
    )
    for match in pattern.finditer(html):
        raw_url = unescape(match.group("url")).strip()
        title = strip_html(match.group("title")).strip()
        tail = match.group("tail")
        snippet_match = re.search(r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', tail, re.I | re.S)
        if snippet_match is None:
            snippet_match = re.search(r'<div[^>]+class="result__snippet"[^>]*>(.*?)</div>', tail, re.I | re.S)
        snippet = strip_html(snippet_match.group(1)).strip() if snippet_match else ""
        if raw_url.startswith("//"):
            raw_url = "https:" + raw_url
        results.append({"title": title, "url": raw_url, "snippet": snippet})
        if len(results) >= num_results:
            break
    return results


def search_bing_rss(query: str, num_results: int) -> list[dict[str, str]]:
    url = "https://www.bing.com/search?format=rss&q=" + quote(query)
    xml_text, _ = http_text(url)
    root = ET.fromstring(xml_text)
    results: list[dict[str, str]] = []
    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        snippet = (item.findtext("description") or "").strip()
        if link:
            results.append({"title": title, "url": link, "snippet": snippet})
        if len(results) >= num_results:
            break
    return results


def search_backend(query: str, num_results: int, backend: str, *, hl: str = "", gl: str = "") -> list[dict[str, str]]:
    if backend == "firecrawl":
        return search_firecrawl(query, num_results)
    if backend == "serper":
        return search_serper(query, num_results, hl=hl, gl=gl)
    if backend == "exa":
        return search_exa(query, num_results)
    if backend == "ddg_html":
        return search_ddg_html(query, num_results)
    if backend == "bing_rss":
        return search_bing_rss(query, num_results)
    raise RuntimeError(f"Unsupported search backend: {backend}")


def retrieve_firecrawl(url: str, timeout: int) -> tuple[str, str]:
    api_key = os.getenv("FIRECRAWL_API_KEY")
    if not api_key:
        raise RuntimeError("FIRECRAWL_API_KEY is required for backend=firecrawl")
    data = http_json(
        "https://api.firecrawl.dev/v2/scrape",
        method="POST",
        headers={"Authorization": f"Bearer {api_key}"},
        payload={"url": url, "formats": ["markdown"]},
        timeout=timeout,
    )
    payload = data.get("data") or data
    markdown = str(payload.get("markdown", "")).strip()
    if not markdown:
        raise RuntimeError("firecrawl response did not include markdown")
    canonical = str(payload.get("metadata", {}).get("sourceURL") or payload.get("metadata", {}).get("url") or url)
    return canonical, markdown


def retrieve_jina(url: str, timeout: int) -> tuple[str, str]:
    stripped = url.lstrip("/").removeprefix("http://").removeprefix("https://")
    scheme = "https" if url.startswith("https") else "http"
    reader_url = f"https://r.jina.ai/{scheme}://{stripped}"
    headers: dict[str, str] = {
        "X-Engine": "browser",
        "X-Return-Format": "markdown",
    }
    jina_key = os.getenv("JINA_API_KEY")
    if jina_key:
        headers["Authorization"] = f"Bearer {jina_key}"
    text, _ = http_text(reader_url, timeout=timeout, headers=headers)
    return url, text.strip()


def _retrieve_exa_once(url: str, timeout: int, api_key: str) -> tuple[str, str]:
    data = http_json(
        "https://api.exa.ai/contents",
        method="POST",
        headers={"x-api-key": api_key},
        payload={
            "urls": [url],
            "text": True,
            "livecrawlTimeout": timeout * 1000,
        },
        timeout=max(timeout, 30),
    )

    statuses = data.get("statuses") or []
    for status in statuses:
        status_id = str(status.get("id", "")).strip()
        if status_id not in {url, normalize_url(url)}:
            continue
        if str(status.get("status", "")).lower() == "error":
            error = status.get("error") or {}
            tag = str(error.get("tag", "")).strip()
            http_status = error.get("httpStatusCode")
            pieces = [part for part in [tag, str(http_status) if http_status is not None else ""] if part]
            detail = " ".join(pieces).strip() or "unknown Exa contents error"
            raise RuntimeError(f"Exa contents error for {url}: {detail}")

    results = data.get("results") or []
    if not results:
        raise RuntimeError(f"Exa contents returned no results for {url}")

    result = results[0]
    text = str(result.get("text", "")).strip()
    if not text:
        raise RuntimeError(f"Exa contents did not include source text for {url}; a summary is not source text")

    canonical = str(result.get("url") or result.get("id") or url).strip()
    title = str(result.get("title", "")).strip()
    author = str(result.get("author", "")).strip()
    published = str(result.get("publishedDate", "")).strip()
    header_lines = [f"# {title or canonical}"]
    if author:
        header_lines.append(f"Author: {author}")
    if published:
        header_lines.append(f"Published: {published}")
    header_lines.append(f"Source: {canonical}")
    markdown = "\n".join(header_lines) + "\n\n" + text
    return canonical, markdown.strip()


def retrieve_exa(url: str, timeout: int) -> tuple[str, str]:
    api_key = os.getenv("EXA_API_KEY")
    if not api_key:
        raise RuntimeError("EXA_API_KEY is required for backend=exa")
    delays_raw = os.getenv("EXA_RETRIEVE_DELAYS", "10,20")
    delays = [int(d.strip()) for d in delays_raw.split(",") if d.strip()]
    max_attempts = 1 + len(delays)
    last_error: str = ""
    attempts_made = 0
    for attempt in range(max_attempts):
        attempts_made = attempt + 1
        try:
            return _retrieve_exa_once(url, timeout, api_key)
        except HTTPResponsePolicyError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            retryable = True
            if isinstance(exc, HTTPError):
                retryable = exc.code in {408, 409, 425, 429} or exc.code >= 500
            elif re.search(r"\b(400|401|402|403|404|405|422)\b", last_error):
                retryable = False
            if retryable and attempt < len(delays):
                delay = delays[attempt]
                print(f"[exa retry] attempt {attempt + 1} failed for {url}: {last_error}; "
                      f"retrying in {delay}s", file=sys.stderr)
                time.sleep(delay)
            else:
                break
    raise RuntimeError(f"Exa retrieve failed after {attempts_made} attempts for {url}: {last_error}")


def retrieve_direct(url: str, timeout: int) -> tuple[str, str]:
    text, content_type = http_text(url, timeout=timeout)
    if "html" in content_type.lower() or "<html" in text.lower():
        text = strip_html(text)
    return url, text.strip()


def retrieve_backend(url: str, timeout: int, backend: str) -> tuple[str, str]:
    if backend == "firecrawl":
        return retrieve_firecrawl(url, timeout)
    if backend == "jina":
        return retrieve_jina(url, timeout)
    if backend == "exa":
        return retrieve_exa(url, timeout)
    if backend == "direct":
        return retrieve_direct(url, timeout)
    if backend == "serper":
        return retrieve_direct(url, timeout)
    raise RuntimeError(f"Unsupported retrieve backend: {backend}")


def backend_chain(primary: str, fallback_env: str | None) -> list[str]:
    chain = [primary]
    if fallback_env:
        chain.extend(x.strip() for x in fallback_env.split(",") if x.strip())
    seen: set[str] = set()
    unique: list[str] = []
    for item in chain:
        if item not in seen:
            unique.append(item)
            seen.add(item)
    return unique


def command_search(args: argparse.Namespace) -> int:
    paths = task_paths(Path(args.task_root))
    hl = ""
    gl = ""
    if args.search_intent:
        intent = json.loads(args.search_intent)
        query = build_query_from_intent(intent)
        hl = str(intent.get("language", "") or intent.get("hl", "")).strip()
        gl = str(intent.get("gl", "")).strip()
    else:
        query = args.query.strip()
    if not query:
        raise SystemExit("search requires --query or --search-intent")
    primary = args.backend or os.getenv("SEARCH_API_BACKEND", DEFAULT_SEARCH_BACKEND)
    backends = backend_chain(primary, os.getenv("SEARCH_API_FALLBACKS"))
    last_error = None
    for position, backend in enumerate(backends):
        try:
            results = search_backend(query, args.num_results, backend, hl=hl, gl=gl)
            payload = {
                "query": query,
                "result_count": len(results),
                "results": results,
            }
            log_action(paths, {"action": "search", "status": "success", "backend": backend, "query": query, "result_count": len(results)})
            print(json.dumps(payload, ensure_ascii=False))
            return 0
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            log_action(paths, {"action": "search", "status": "error", "backend": backend, "query": query, "error": last_error, "terminal_failure": isinstance(exc, HTTPResponsePolicyError) or position == len(backends) - 1})
            if isinstance(exc, HTTPResponsePolicyError):
                raise
    raise SystemExit(last_error or "search failed")


def command_library_search(args: argparse.Namespace) -> int:
    paths = task_paths(Path(args.task_root))
    try:
        results = search_library(
            paths,
            args.query.strip(),
            args.num_results,
            materialize=not args.no_materialize,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    log_action(
        paths,
        {
            "action": "library_search",
            "status": "success",
            "query": args.query.strip(),
            "result_count": len(results),
            "materialized": not args.no_materialize,
        },
    )
    print(
        json.dumps(
            {"query": args.query.strip(), "result_count": len(results), "results": results},
            ensure_ascii=False,
        )
    )
    return 0


def command_retrieve(args: argparse.Namespace) -> int:
    paths = task_paths(Path(args.task_root))
    url = normalize_url(args.url)
    context = str(args.log_context or "")
    local_hit = lookup_cache(paths.local_index, url)
    if local_hit and not args.force_refresh:
        verify_cached_page(paths, local_hit)
        payload = {
            "status": "success",
            "cache_hit": True,
            "source": "local",
            "backend": str(local_hit.get("backend", "")),
            "canonical_url": str(local_hit["canonical_url"]),
            "cache_key": str(local_hit["cache_key"]),
            "markdown_path": str(local_hit["markdown_path"]),
        }
        log_action(paths, {"action": "retrieve", "status": "success", "backend": payload["backend"], "url": url, "cache_hit": True, "source": "local", "context": context})
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    global_hit = lookup_cache(paths.global_index, url)
    if global_hit and not args.force_refresh:
        record = materialize_global_hit(paths, global_hit)
        payload = {
            "status": "success",
            "cache_hit": True,
            "source": "global",
            "backend": str(record.get("backend", "")),
            "canonical_url": str(record["canonical_url"]),
            "cache_key": str(record["cache_key"]),
            "markdown_path": str(record["markdown_path"]),
        }
        log_action(paths, {"action": "retrieve", "status": "success", "backend": payload["backend"], "url": url, "cache_hit": True, "source": "global", "context": context})
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    primary = args.backend or os.getenv("WEB_RETRIEVER_BACKEND", DEFAULT_RETRIEVER_BACKEND)
    fallback_value = (
        args.fallbacks
        if args.fallbacks is not None
        else os.getenv("WEB_RETRIEVER_FALLBACKS")
    )
    backends = backend_chain(primary, fallback_value)
    last_error = None
    for position, backend in enumerate(backends):
        try:
            canonical_url, markdown = retrieve_backend(url, args.timeout, backend)
            record = store_cache(paths, normalize_url(canonical_url), markdown, backend)
            payload = {
                "status": "success",
                "cache_hit": False,
                "source": "network",
                "backend": backend,
                "canonical_url": str(record["canonical_url"]),
                "cache_key": str(record["cache_key"]),
                "markdown_path": str(record["markdown_path"]),
            }
            log_action(paths, {"action": "retrieve", "status": "success", "backend": backend, "url": url, "cache_hit": False, "source": "network", "context": context})
            print(json.dumps(payload, ensure_ascii=False))
            return 0
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            log_action(paths, {"action": "retrieve", "status": "error", "backend": backend, "url": url, "error": last_error, "context": context, "terminal_failure": isinstance(exc, HTTPResponsePolicyError) or position == len(backends) - 1})
            if isinstance(exc, HTTPResponsePolicyError):
                raise
    raise SystemExit(last_error or "retrieve failed")


def command_archive(args: argparse.Namespace) -> int:
    paths = task_paths(Path(args.task_root))
    if not args.payload_json and not args.payload_path:
        raise SystemExit("archive requires --payload-json or --payload-path")
    if args.payload_json and args.payload_path:
        raise SystemExit("archive accepts only one of --payload-json or --payload-path")
    if args.payload_path:
        payload_text = Path(args.payload_path).read_text(encoding="utf-8-sig")
    else:
        payload_text = args.payload_json
    payload = json.loads(payload_text)
    items = payload if isinstance(payload, list) else [payload]
    validated = []
    for item in items:
        if not isinstance(item, dict):
            raise SystemExit("Every archived evidence item must be an object")
        item = dict(item)
        try:
            item = validate_archive_citation(paths, item)
        except ValueError as exc:
            log_action(paths, {"action": "archive", "status": "error", "error": str(exc)})
            raise SystemExit(str(exc)) from exc
        item.setdefault("archived_at", now_iso())
        validated.append(item)
    for item in validated:
        append_ndjson(paths.evidence_file, item)
    archived = len(validated)
    log_action(paths, {"action": "archive", "status": "success", "item_count": archived})
    print(json.dumps({"status": "success", "archived": archived}, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-root", required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)

    search = subparsers.add_parser("search")
    search.add_argument("--search-intent")
    search.add_argument("--query")
    search.add_argument("--num-results", type=int, default=10)
    search.add_argument("--backend")

    library_search = subparsers.add_parser("library-search")
    library_search.add_argument("--query", required=True)
    library_search.add_argument("--num-results", type=int, default=10)
    library_search.add_argument(
        "--no-materialize",
        action="store_true",
        help="List matching library sources without copying them into the task cache",
    )

    retrieve = subparsers.add_parser("retrieve")
    retrieve.add_argument("--url", required=True)
    retrieve.add_argument("--force-refresh", action="store_true")
    retrieve.add_argument("--timeout", type=int, default=30)
    retrieve.add_argument("--backend")
    retrieve.add_argument(
        "--fallbacks",
        default=None,
        help="Comma-separated retrieval fallbacks; pass an empty value to disable configured fallbacks",
    )
    retrieve.add_argument("--log-context", default="")

    archive = subparsers.add_parser("archive")
    archive.add_argument("--payload-json")
    archive.add_argument("--payload-path")
    record = subparsers.add_parser("record-search", help="Import saved native/external search results; does not search")
    record.add_argument("--query", required=True)
    record.add_argument("--results-path")
    record.add_argument("--backend", required=True)
    record.add_argument("--status", choices=("success", "error"), default="success")
    record.add_argument("--error")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.command == "search":
            return command_search(args)
        if args.command == "library-search":
            return command_library_search(args)
        if args.command == "retrieve":
            return command_retrieve(args)
        if args.command == "record-search":
            return command_record_search(args)
        if args.command == "archive":
            return command_archive(args)
        parser.error(f"unknown command: {args.command}")
    except (HTTPError, URLError, HTTPResponsePolicyError) as exc:
        raise SystemExit(str(exc)) from exc





def validate_quotation(item: dict[str, Any], excerpt: str) -> None:
    for field in ("quote", "exact_quote"):
        if field in item and (not isinstance(item[field], str) or not item[field] or item[field] not in excerpt):
            raise ValueError(f"{field} must be an exact nonempty substring of the cited source; put paraphrases in summary")
    if "quote_fragments" in item:
        fragments = item["quote_fragments"]
        if not isinstance(fragments, list) or not fragments:
            raise ValueError("quote_fragments must be a nonempty ordered list")
        cursor = 0
        for fragment in fragments:
            if not isinstance(fragment, str) or not fragment:
                raise ValueError("Each quotation fragment must be nonempty text")
            position = excerpt.find(fragment, cursor)
            if position < 0:
                raise ValueError("Quotation fragments must occur in source order without overlap")
            cursor = position + len(fragment)



def write_snapshot(path: Path, data: bytes) -> None:
    """Create once; concurrent identical materialization is harmless."""
    ensure_parent(path)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        temporary.write_bytes(data)
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != data:
                raise ValueError(f"Immutable cached snapshot differs: {path}")
    finally:
        temporary.unlink(missing_ok=True)



def verify_cached_page(paths: TaskPaths, record: dict[str, Any]) -> Path:
    path = cached_markdown_path(paths, str(record["cache_key"]))
    expected = record.get("page_sha256")
    if not expected or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError(f"Cached source is missing, unversioned, or changed: {path}")
    return path



def command_record_search(args: argparse.Namespace) -> int:
    """Import an external/native result artifact, without performing a search."""
    paths = task_paths(Path(args.task_root))
    status = getattr(args, "status", "success")
    if status == "error" and not args.results_path:
        if not getattr(args, "error", None):
            raise ValueError("A failed search import requires --error or a captured error artifact")
        log_action(paths, {"action": "search", "status": "error", "backend": args.backend,
                           "query": args.query, "error": args.error, "origin": "imported_external_result"})
        print(json.dumps({"status": "recorded_error"}))
        return 0
    if not args.results_path:
        raise ValueError("A successful search import requires --results-path")
    raw = Path(args.results_path).read_bytes()
    payload = json.loads(raw.decode("utf-8-sig"))
    results = payload.get("results") if isinstance(payload, dict) else payload
    if status == "success" and (not isinstance(results, list) or not all(isinstance(item, dict) for item in results)):
        raise ValueError("Recorded search must be a JSON results array or an object containing results")
    if status == "error":
        results = []
    digest = hashlib.sha256(raw).hexdigest()
    relative = f"cache/searches/{digest}.json"
    write_snapshot(paths.task_root / relative, raw)
    log_action(paths, {"action": "search", "status": status, "backend": args.backend,
                       "query": args.query, "result_count": len(results), "capture_path": relative,
                       "capture_sha256": digest, "origin": "imported_external_result",
                       **({"error": getattr(args, "error", None) or "See captured error artifact"} if status == "error" else {})})
    print(json.dumps({"status": "success", "capture_path": relative, "result_count": len(results)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
