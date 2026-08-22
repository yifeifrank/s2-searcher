# Runtime Packaging

The generated package supports Codex and Claude as standalone research sessions. It does not register child-agent roles.

## Shared workflow source

Reusable behavior lives in `prompts/workflows/`:

- `local_agent.md`
- `online_agent.md`

The optional API coding prompt lives at `prompts/coder/api_coder.md`. It is invoked by the launcher after evidence freeze and is not an agent definition.

`task_instruction.md` dispatches to one workflow file and must not replace that workflow with a shortened restatement.

## Codex

`.codex/config.toml` contains project sandbox and approval defaults only. The standalone Codex session reads `AGENTS.md`, the dispatcher, and the selected workflow. No `[agents.*]` entries are installed.

## Claude Code

`CLAUDE.md` gives the standalone Claude session the same project rules. No `.claude/agents/` definitions are installed.

## Skills and tools

The generated project exposes the research-tools workflow in:

- `.agents/skills/research-tools/` for Codex;
- `.claude/skills/research-tools/` for Claude Code.

Both wrappers call the same `tools/research_tools.py` implementation. Local fixed-corpus materialization uses `tools/local_ingest.py`, after which the same archive command validates citations. Never fork runtime-specific tool implementations silently.

## Execution ownership

- `direct_api`: no agent session.
- `local_agent`: one standalone local research session, zero child agents.
- `online_agent`: one standalone online research session, zero child agents.
- optional `coder_mode=api`: after a successful freeze, the launcher makes one API model call; it is not part of an agent tree.

`inquiry.py` is a one-task entry point over the same launcher implementation. It does not define or spawn an additional agent role. `batch.py` remains the Study-mode entry point for repeated manifest rows.
