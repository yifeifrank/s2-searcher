---
name: research-workspace-builder
description: Create a standalone research workspace from an ordinary-language question or repeated-case study description, with auditable local and online evidence, routing, batching, caching, archiving, and validation. Use Study mode for one protocol applied across many targets, or Inquiry mode for one substantive question without a user-defined codebook or manifest. Use when the user asks an agent to scaffold, run, migrate, or standardize reusable research, preserve inspectable sources and line-cited evidence, or turn a prose study design into a codebook and case manifest. Do not use for simple fact lookups that do not need an evidence archive.
---

# Research Workspace Builder

Requirements: Python 3.10+, Codex CLI and/or Claude Code, and at least one configured search backend for online research.

Create a self-contained research task-pack inside a subfolder chosen by the user. The generated package must remain general-purpose: the user's question or codebook defines the domain, while agent prompts, routing, evidence storage, and execution mechanics remain generic.

Read [design-lineage.md](references/design-lineage.md) when explaining how the generated workflow relates to the source article or when changing its architecture.
Read [getting-started.md](docs/getting-started.md) when a user needs beginner-oriented setup, provider configuration, input examples, or artifact interpretation.

## Work modes

- **Study mode** is the existing manifest-driven path: one instruction/codebook is applied repeatedly while targets vary. Use `batch.py` and preserve one standalone task folder per row.
- **Inquiry mode** accepts one free-form substantive question through `inquiry.py`. It uses an internal generic coverage contract, disables structured coding, and preserves the same report, cached sources, evidence lines, worklog, and validation artifacts as Study-mode agent tasks.

Both modes share `tasks/_global_cache/` as a workspace source library. A later task may search and materialize earlier sources, but must inspect them and create its own task-specific evidence records. Local documents enter the shared library only through the explicit `--share-with-library` option.

Every generated workspace retains both entry points. `--inquiry` only supplies a useful initial contract when the user has no codebook yet; it does not remove Study mode.

## Agent-first interface

Accept a beginner's ordinary-language research description as sufficient input. Carry out scaffolding, file creation, provider selection, dry runs, and validation instead of returning terminal instructions for the user to execute.

For Inquiry mode, turn the user's question and scope into the existing one-question entry point. For Study mode, draft the combined Markdown instruction, embedded JSON codebook, and CSV or JSON manifest from the user's prose. Summarize the proposed unit of analysis, fields, allowed values, missingness rules, source priorities, and case count for substantive approval before a live batch.

Ask only for missing choices that would materially change the result. Infer the current runtime when possible and choose a sensible workspace location when the user has no preference. Run a dry check before live research. When the user approves the prepared task, resume that same task for the live run instead of recreating or overwriting it. Launch live model or provider calls only when the user authorizes an operational run.

Never ask a user to paste provider keys into a chat prompt. Configure Firecrawl search with Exa fallback and Exa retrieval with Firecrawl fallback by default; do not add direct HTTP as a default because Claude's network allow-list does not permit arbitrary destination domains. Create credential placeholders when needed, and tell the user where to provision `FIRECRAWL_API_KEY` and `EXA_API_KEY` through a local secret mechanism. Check availability without printing, logging, or copying credential values.

## Study-mode input

Prefer one Markdown file containing prose instructions and a `## Codebook` section with one or more fenced JSON objects. The user may supply it, or the agent may create it from an approved ordinary-language study design. The prose defines research/coding requirements; the JSON chunks jointly define output fields and format. Top-level keys must be unique across chunks. The builder extracts their merged object to `inputs/codebook.json`, and validation requires exact equality.

For compatibility, accept a standalone JSON codebook with optional separate prose Markdown; the builder combines them into the same `inputs/instruction.md` contract. Accept the bundled elite-biography example only when explicitly requested.

Determine:

- target subfolder;
- combined instruction Markdown path, or compatibility codebook plus optional prose paths;
- runtime: `codex`, `claude`, or `both`;
- optional output JSON Schema;
- optional default route override.

If the target already contains files, stop and ask whether the user wants a different target. Do not overwrite an existing project implicitly.

## Create the package

For an Inquiry-first workspace that does not require a user codebook, run:

```bash
python3 <skill-root>/scripts/create_workspace.py \
  --target <target-subfolder> \
  --inquiry \
  --runtime both
```

Then prepare one task without model or web calls:

```bash
cd <target-subfolder>
python3 inquiry.py "<substantive research question>" \
  --runtime codex \
  --dry-run
```

Remove `--dry-run` only when the user requests a live run. Use `--local-only` when the inquiry must rely exclusively on local and workspace-library sources.

For a Study-mode workspace, run:

```bash
python3 <skill-root>/scripts/create_workspace.py \
  --target <target-subfolder> \
  --instruction <instruction.md> \
  --runtime both
```

Optional arguments:

```bash
--output-schema <schema.json>
--default-route auto|direct_api|local_agent|online_agent
--project-name <name>
```

Compatibility form:

```bash
python3 <skill-root>/scripts/create_workspace.py \
  --target <target-subfolder> \
  --codebook <codebook.json> \
  --instructions <prose.md> \
  --runtime both
```

To create the bundled demonstration instead of supplying a combined instruction:

```bash
python3 <skill-root>/scripts/create_workspace.py \
  --target <target-subfolder> \
  --example elite-biography \
  --runtime both
```

The script copies the template, installs the selected runtime definitions, writes the combined instruction, extracts its codebook mirror, configures the project, and validates their equality.

## Routing contract

Preserve exactly three execution routes:

1. `direct_api`: compact, self-contained evidence can be sent directly to a configured Responses-compatible API.
2. `local_agent`: evidence is already in a fixed local collection and requires iterative `rg`, file reading, ingestion, or evidence selection.
3. `online_agent`: evidence must be discovered through search and retrieval.

Do not invent a RAG, embedding, vector-database, or separate synthesizer route. Evidence selection is an activity performed by the local or online agent.

For `online_agent`, the standalone task session owns the complete planning, search, retrieval, archival, reconciliation, reporting, and evidence-freeze loop. It must not launch child agents. Difficulty changes budgets and planning depth, not agent architecture.

Default to a generous ceiling of 40 search calls and 3,600 seconds for a standalone research session. These are overridable per task or manifest row and are not consumption targets: finish early when coverage is complete, while leaving enough headroom for difficult cases.

Local and online routes terminate at a validated `research_report.md` and frozen evidence package by default. Do not require or launch a coder agent. Structured coding is optional: `coder_mode=api` allows the launcher to make one Responses-compatible model call over the research report and materialized archived evidence after the research process exits. The default is `coder_mode=none`.

Read [routing.md](references/routing.md) when explaining or modifying route selection.

## Generated-folder boundary

Create one project subfolder and operate from it. Do not create a second external workspace. Inside the generated project, each task or manifest row receives a task-local directory under `tasks/` or the chosen run directory.

The generated package includes:

- combined user instruction, extracted codebook mirror, and optional output schema;
- task and routing contracts;
- `direct_api`, `local_agent`, and `online_agent` workflows;
- standalone Codex and Claude project instructions;
- the preserved research-tools implementation for online search, retrieval, caching, and archival, plus a separate local-document ingester;
- a one-question `inquiry.py` entry point and cross-runtime `batch.py` launcher over the same execution core;
- progress, retry, prompt, session, provenance, research-report, and validation artifacts;
- an optional one-call API coder that is disabled by default and is not an agent;
- `.env.example` without credentials;
- the elite-biography setup only under `examples/`.

## Permissions

The generated Codex project defaults to `workspace-write` with network access and `approval_policy = "never"`. This lets the standalone session write task artifacts and call online research services while denying filesystem escalation without pausing a non-interactive batch. Claude projects enable the Bash sandbox, fail closed when it is unavailable, restrict network access to the bundled research backends, and use `dontAsk` so unapproved actions are denied rather than paused.

The launchers must not bypass these protections by default. `--unsafe-unattended` is the only opt-in bypass and is appropriate only inside an externally isolated container or virtual machine. Make this distinction prominent in generated and repository documentation. Permission settings do not supply credentials or eliminate prompt-injection and data-egress risks; recommend provider-scoped keys and non-sensitive workspaces.

## Cross-runtime consistency

Treat `prompts/workflows/*.md` as the research behavioral source of truth. The workflow files preserve the complete search, retrieval, archival, gap, and freeze contracts; `task_instruction.md` only dispatches to them. The standalone Codex or Claude session must execute the selected workflow itself and must not launch child agents. The optional API coding prompt is separate under `prompts/coder/` and must not be registered as an agent.

Read [agent-packaging.md](references/agent-packaging.md) when changing runtime packaging.
Read [prompt-preservation.md](references/prompt-preservation.md) before modifying a workflow prompt.

## Verification

After scaffolding:

1. Run `python3 validate.py project` from the generated folder.
2. Run `python3 router.py --contract examples/contracts/direct_api.json --stdout`.
3. Run `python3 router.py --contract examples/contracts/local_agent.json --stdout`.
4. Run `python3 router.py --contract examples/contracts/online_agent.json --stdout`.
5. Run a dry batch without model or search calls:

   ```bash
   python3 batch.py \
     --manifest inputs/manifest.example.csv \
     --runtime codex \
     --tasks-dir tasks/dry_run \
     --dry-run
   ```

6. Run an Inquiry dry run without model or search calls:

   ```bash
   python3 inquiry.py "How does existing evidence bear on this question?" \
     --runtime codex \
     --task-id inquiry-check \
     --dry-run
   ```

Do not launch live API, Codex, Claude, or web-search work unless the user asks for an operational run.

## Handoff

Tell the user:

- where the standalone skill lives;
- where the generated project lives, if created;
- whether Study or Inquiry mode was used;
- that Study scaffolding prefers one Markdown instruction with an embedded JSON codebook;
- how to install the skill into `$CODEX_HOME/skills/` (normally `$HOME/.codex/skills/`) for Codex;
- how to install the same directory into `$HOME/.claude/skills/` for Claude Code;
- that a new or reloaded session may be needed before newly generated project agents are visible;
- which validation and dry-run checks passed.
