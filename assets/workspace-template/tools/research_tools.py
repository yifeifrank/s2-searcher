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
    return _OPENER.open(request, timeout=timeout)


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


def http_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    body = None
    req_headers = {"User-Agent": USER_AGENT}
    if headers:
        req_headers.update(headers)
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")
    request = Request(url, data=body, headers=req_headers, method=method)
    with _urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="replace")
    return json.loads(raw)


def http_text(url: str, *, timeout: int = 30, headers: dict[str, str] | None = None) -> tuple[str, str]:
    req_headers = {"User-Agent": USER_AGENT}
    if headers:
        req_headers.update(headers)
    request = Request(url, headers=req_headers)
    with _urlopen(request, timeout=timeout) as response:
        content_type = response.headers.get("Content-Type", "")
        raw_bytes = response.read()
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
    for item in read_ndjson(index_path):
        if normalize_url(str(item.get("canonical_url", ""))) == normalized:
            return item
    return None


def lookup_cache_key(index_path: Path, cache_key: str) -> dict[str, Any] | None:
    for item in read_ndjson(index_path):
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
    if not present:
        return item
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

    record = lookup_cache_key(paths.local_index, cache_key) or lookup_cache_key(paths.global_index, cache_key)
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


def store_cache(paths: TaskPaths, canonical_url: str, markdown: str, backend: str) -> dict[str, Any]:
    cache_key = cache_key_for_url(canonical_url)
    rel_path = f"cache/pages/{cache_key}.md"
    local_path = paths.task_root / rel_path
    global_path = paths.global_pages / f"{cache_key}.md"
    ensure_parent(local_path)
    local_path.write_text(markdown, encoding="utf-8")
    global_path.write_text(markdown, encoding="utf-8")
    record = {
        "canonical_url": canonical_url,
        "cache_key": cache_key,
        "markdown_path": rel_path,
        "backend": backend,
        "stored_at": now_iso(),
    }
    append_ndjson(paths.local_index, record)
    append_ndjson(paths.global_index, {**record, "markdown_path": str(global_path)})
    return record


def materialize_global_hit(paths: TaskPaths, record: dict[str, Any]) -> dict[str, Any]:
    cache_key = str(record["cache_key"])
    source_path = paths.global_pages / f"{cache_key}.md"
    rel_path = f"cache/pages/{cache_key}.md"
    target_path = paths.task_root / rel_path
    if source_path.exists() and not target_path.exists():
        ensure_parent(target_path)
        shutil.copyfile(source_path, target_path)
    local_record = dict(record)
    local_record["markdown_path"] = rel_path
    append_ndjson(paths.local_index, local_record)
    return local_record


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
        "https://api.firecrawl.dev/v1/search",
        method="POST",
        headers={"Authorization": f"Bearer {api_key}"},
        payload={"query": query, "limit": num_results},
    )
    items = data.get("data") or data.get("results") or []
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
        "https://api.firecrawl.dev/v1/scrape",
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
        summary = result.get("summary")
        if isinstance(summary, str):
            text = summary.strip()
        elif isinstance(summary, dict):
            text = str(summary.get("text", "")).strip()
    if not text:
        raise RuntimeError(f"Exa contents did not include text for {url}")

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
    for attempt in range(max_attempts):
        try:
            return _retrieve_exa_once(url, timeout, api_key)
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            if attempt < len(delays):
                delay = delays[attempt]
                print(f"[exa retry] attempt {attempt + 1} failed for {url}: {last_error}; "
                      f"retrying in {delay}s", file=sys.stderr)
                time.sleep(delay)
    raise RuntimeError(f"Exa retrieve failed after {max_attempts} attempts for {url}: {last_error}")


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
    primary = args.backend or os.getenv("SEARCH_API_BACKEND", "serper")
    backends = backend_chain(primary, os.getenv("SEARCH_API_FALLBACKS"))
    last_error = None
    for backend in backends:
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
            log_action(paths, {"action": "search", "status": "error", "backend": backend, "query": query, "error": last_error})
    raise SystemExit(last_error or "search failed")


def command_retrieve(args: argparse.Namespace) -> int:
    paths = task_paths(Path(args.task_root))
    url = normalize_url(args.url)
    local_hit = lookup_cache(paths.local_index, url)
    if local_hit and not args.force_refresh:
        payload = {
            "status": "success",
            "cache_hit": True,
            "source": "local",
            "backend": str(local_hit.get("backend", "")),
            "canonical_url": str(local_hit["canonical_url"]),
            "cache_key": str(local_hit["cache_key"]),
            "markdown_path": str(local_hit["markdown_path"]),
        }
        log_action(paths, {"action": "retrieve", "status": "success", "backend": payload["backend"], "url": url, "cache_hit": True, "source": "local"})
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
        log_action(paths, {"action": "retrieve", "status": "success", "backend": payload["backend"], "url": url, "cache_hit": True, "source": "global"})
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    primary = args.backend or os.getenv("WEB_RETRIEVER_BACKEND", "jina")
    backends = backend_chain(primary, os.getenv("WEB_RETRIEVER_FALLBACKS"))
    last_error = None
    for backend in backends:
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
            log_action(paths, {"action": "retrieve", "status": "success", "backend": backend, "url": url, "cache_hit": False, "source": "network"})
            print(json.dumps(payload, ensure_ascii=False))
            return 0
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            log_action(paths, {"action": "retrieve", "status": "error", "backend": backend, "url": url, "error": last_error})
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
    archived = 0
    for item in items:
        if isinstance(item, dict):
            item = dict(item)
            try:
                item = validate_archive_citation(paths, item)
            except ValueError as exc:
                log_action(paths, {"action": "archive", "status": "error", "error": str(exc)})
                raise SystemExit(str(exc)) from exc
            item.setdefault("archived_at", now_iso())
            append_ndjson(paths.evidence_file, item)
            archived += 1
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

    retrieve = subparsers.add_parser("retrieve")
    retrieve.add_argument("--url", required=True)
    retrieve.add_argument("--force-refresh", action="store_true")
    retrieve.add_argument("--timeout", type=int, default=30)
    retrieve.add_argument("--backend")

    archive = subparsers.add_parser("archive")
    archive.add_argument("--payload-json")
    archive.add_argument("--payload-path")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.command == "search":
            return command_search(args)
        if args.command == "retrieve":
            return command_retrieve(args)
        if args.command == "archive":
            return command_archive(args)
        parser.error(f"unknown command: {args.command}")
    except (HTTPError, URLError) as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    sys.exit(main())
