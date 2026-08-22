---
name: research-tools
description: Use for every task-pack web search, webpage retrieval, cache write, research audit entry, and evidence archive operation. Always use this workflow for online evidence; do not substitute browser or MCP search that bypasses the archive.
---

# Research Tools

Use the project implementation at `tools/research_tools.py`:

```bash
python3 tools/research_tools.py --task-root <TASK_WORKSPACE> search --query "<QUERY>" --num-results 10
python3 tools/research_tools.py --task-root <TASK_WORKSPACE> search --search-intent '{"must_include":["term"]}'
python3 tools/research_tools.py --task-root <TASK_WORKSPACE> library-search --query "<TERMS>"
python3 tools/research_tools.py --task-root <TASK_WORKSPACE> retrieve --url "<URL>"
python3 tools/research_tools.py --task-root <TASK_WORKSPACE> archive --payload-path "<PAYLOAD.json>"
```

For a fixed local collection, materialize text-like files separately:

```bash
python3 tools/local_ingest.py --task-root <TASK_WORKSPACE> --path "<LOCAL_FILE>"
```

Add `--share-with-library` only when the user intends that local file to be reusable by later tasks in this workspace. Library search materializes matching sources into the active task; inspect them and archive new task-specific evidence rather than inheriting earlier conclusions.

Archive cached Markdown with `cache_key`, `start_line`, and `end_line`. Keep `--task-root` pointed at the active task workspace. The canonical task artifacts are `cache/cache_index.jsonl`, `cache/research_log.ndjson`, `cache/pages/*.md`, and `evidence.ndjson`; reusable web pages and explicitly shared local sources may come from the workspace library at `tasks/_global_cache/`.
