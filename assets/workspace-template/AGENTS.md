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

For online work, use only the project research-tools workflow for search, retrieval, caching, and archival. Direct browser or MCP search bypasses the auditable artifacts and is not a substitute.

For local work, inspect the fixed collection with `rg` and bounded reads, then use `tools/local_ingest.py` to create stable cached pages. Archive supporting cached line ranges with `tools/research_tools.py`.

## Completion

Local and online research finish only after:

```bash
python3 validate.py research --task-root <workspace>
```

succeeds. The `direct_api` route and optional post-research API coder additionally produce `output/final_report.json`, which the launcher checks with `validate.py task`.
