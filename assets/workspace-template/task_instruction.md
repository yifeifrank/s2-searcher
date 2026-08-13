# Task Instruction Dispatcher

You are the parent agent for one codebook-driven research task. The launcher has already created the task workspace and recorded the route. This file only dispatches execution; the complete workflow prompts live under `prompts/workflows/` and must be read in full.

## Read first

Resolve relative paths from the framework root and read:

1. `<workspace>/task_contract.json`
2. `<workspace>/route_decision.json`
3. the combined Markdown contract named by `instructions_path`, including its `## Codebook` JSON fence
4. the extracted JSON mirror named by `codebook_path`
5. the schema named by `output_schema_path`
6. `framework.json`

The prose in `instruction.md` defines the task, coverage, source rules, quality requirements, and coding guidance. Its embedded JSON codebook defines output fields, nesting, types, allowed values, and formatting. The extracted JSON file is a machine-readable mirror and must not diverge from the embedded object.

## Dispatch exactly once

- `direct_api`: do not launch an agent workflow. `direct_api.py` owns this route.
- `local_agent`: the current standalone session is the sole local research agent; read and follow `prompts/workflows/local_agent.md` completely.
- `online_agent`: the current standalone session is the complete online research agent; read and follow `prompts/workflows/online_agent.md` completely. Do not launch child agents.

Do not compress the selected workflow into an improvised summary. If a contract and route decision disagree, stop before research and report the mismatch.

## Completion gate

For `local_agent` and `online_agent`, the research session is complete only when:

```bash
python3 validate.py research --task-root <workspace>
```

succeeds. Do not launch a coder agent. If `coder_mode=api`, the launcher—not this session—makes one post-freeze API call and then applies `validate.py task`. Unknown values remain explicit; never invent facts merely to fill the codebook.
