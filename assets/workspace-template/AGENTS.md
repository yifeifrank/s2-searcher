# Research Task-Pack Instructions

This project is a codebook-driven research framework. Before executing a task, read `task_instruction.md`, `framework.json`, the task's `task_contract.json`, and its `route_decision.json`. Then read the combined contract:

- `instructions_path` points to `inputs/instruction.md`, whose prose defines the task and whose `## Codebook` fenced JSON object defines the output.
- `codebook_path` points to the machine-extracted JSON mirror used by validators. It must exactly equal the embedded object.

Read the complete combined Markdown, including the JSON fence. Keep every task artifact inside the supplied task workspace.

## Route discipline

`task_instruction.md` is a dispatcher. Read the selected file under `prompts/workflows/` completely and preserve its orchestration contract.

- `direct_api` is executed by `direct_api.py`; an agent session must not simulate it.
- `local_agent` uses the bounded local-document workflow and does not search the web.
- For `online_agent`, the standalone task session owns the complete online research workflow and launches no child agents.

Agent routes terminate at a validated evidence freeze and `research_report.md`. Do not launch a coder agent. If `coder_mode=api`, the launcher makes one post-freeze API request after the research process exits.

## Evidence tools

For provider-backed online work, use only the project research-tools workflow for search, retrieval, caching, and line archival. Direct browser or MCP search bypasses that evidence contract.

When `route_decision.json` names `prompts/workflows/online_agent_codex_native.md`, use Codex native web search instead. Do not call the project search, retrieve, or archive commands for those native web sources. Cite original URLs normally; the launcher captures observable search metadata and cited URLs, optionally snapshots them through a background retriever, finalizes mechanical worklog fields, and validates after the session exits.

For local work, inspect the fixed collection with `rg` and bounded reads, then use `tools/local_ingest.py` to create stable cached pages. Archive supporting cached line ranges with `tools/research_tools.py`.

## Completion

Local and provider-backed online research finish only after:

```bash
python3 validate.py research --task-root <workspace>
```

succeeds. The `direct_api` route and optional post-research API coder additionally produce `output/final_report.json`, which the launcher checks with `validate.py task`.

For the Codex-native online workflow, finish the semantic plan, worklog, and cited `research_report.md` without running the final validator yourself. The launcher owns post-session metadata capture and validation because those artifacts do not exist until the Codex JSONL stream is complete.
