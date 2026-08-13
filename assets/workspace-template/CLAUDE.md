# Research Task-Pack Instructions

This project is a codebook-driven research framework. Before executing a task, read `task_instruction.md`, `framework.json`, the task's `task_contract.json`, and its `route_decision.json`. Then read the combined contract:

- `instructions_path` points to `inputs/instruction.md`; its prose defines the task and its `## Codebook` JSON fence defines the output contract.
- `codebook_path` is an extracted machine-readable mirror and must exactly match that embedded object.

Read the complete combined Markdown. Write all task artifacts inside the supplied task workspace.

`task_instruction.md` dispatches to a complete prompt under `prompts/workflows/`. Read and follow the selected workflow without omitting its planning, evidence-freeze, handoff, or verification requirements.

- `direct_api` is handled by `direct_api.py`, not by a Claude session.
- `local_agent` uses the fixed local collection and must not add web evidence.
- For `online_agent`, the standalone Claude session owns the complete online research workflow and launches no subagent.

For online research, use the project `research-tools` skill exclusively so every search, retrieval, cached page, and citation is auditable. For local documents, use `rg` and bounded reads, ingest relevant text with `tools/local_ingest.py`, and archive exact cached line ranges.

Local and online research must write `research_report.md` and freeze the evidence inventory. Finish only after:

```bash
python3 validate.py research --task-root <workspace>
```

succeeds. Do not launch a coder subagent. Optional `coder_mode=api` execution belongs to the batch launcher after the research session exits.
