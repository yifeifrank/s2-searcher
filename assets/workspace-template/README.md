# __PROJECT_NAME__

This is a standalone, general-purpose research task pack. It routes each task through one of three execution modes:

- `direct_api`: compact, self-contained inputs are sent directly to a configured API;
- `local_agent`: a tool-using Codex or Claude session examines a fixed local document collection;
- `online_agent`: a Codex or Claude session performs search, retrieval, caching, and evidence archival.

The package does not include RAG, embeddings, a vector database, or a separate synthesizer service. Local and online agents perform evidence selection through ordinary file and research tools.

Its workflow preserves the source framework's separation between evidence acquisition/selection and optional codebook coding. One standalone session plans, searches or reads locally, archives evidence, reports, and freezes the evidence inventory. Local and online sessions launch no child agents.

Local and online routes are research-first and stop at a validated frozen evidence package by default. They do not require a coder. A user may opt into `coder_mode=api`, which makes one Responses-compatible API call over `research_report.md` and the archived evidence excerpts after the research process exits.

## Work modes

- **Study mode:** use `batch.py` when one protocol or codebook applies across many manifest rows.
- **Inquiry mode:** use `inquiry.py` for one open-ended question without supplying a codebook or manifest.

Both modes use the same route, evidence, report, safety, and validation contracts. Retrieved pages are preserved in the workspace source library at `tasks/_global_cache/`; later tasks can search and materialize those sources while creating their own evidence records.

Every generated workspace contains both entry points. The instruction initially installed by the builder sets the convenient starting point; it does not remove either mode.

Run an Inquiry dry run:

```bash
python3 inquiry.py \
  "What explains variation in legislative oversight across democracies?" \
  --runtime codex \
  --dry-run
```

Remove `--dry-run` for a requested live inquiry. Add `--local-only` to prohibit web search and use only local or workspace-library sources.

## 1. Edit the Study-mode instruction contract

For structured Study work, the authoritative user-authored input is:

```text
inputs/instruction.md
```

Its prose defines the task goal, coverage, source policy, interpretation, normalization, missing values, and quality rules. Under the `## Codebook` heading, one or more fenced JSON objects jointly define output fields, nesting, types, allowed values, and formatting notes. Top-level keys must be unique across chunks.

The builder extracts that object to:

```text
inputs/codebook.json
```

This JSON file is a generated machine-readable mirror. Do not edit it independently; project and task validation fail if it differs from the embedded object. The core framework, routing logic, agents, and evidence format remain domain-neutral. Supply a stricter `schemas/user_output.schema.json` when the project needs machine validation beyond the codebook exemplar.

The former elite-biography configuration is retained only as an example under `examples/elite-biography/`.

## 2. Configure credentials

Create an environment and install the small runtime dependency set first:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

On Windows, activate with `.venv\\Scripts\\activate`.

Copy `.env.example` to `.env` and fill only the services you intend to use. Never commit `.env`. The sample configuration recommends Firecrawl for search and Exa for retrieval. Check the variables without network calls using `python3 tools/provider_smoke_check.py`, then add `--live` for one small end-to-end provider test.

For `direct_api`, set:

```text
DIRECT_API_KEY
DIRECT_API_BASE_URL
DIRECT_API_MODEL
```

The API route uses the OpenAI Python client and the Responses API interface. A compatible base URL may also be used when it implements the required endpoint.

For the optional post-research coder, set `EVIDENCE_CODER_API_KEY`, `EVIDENCE_CODER_BASE_URL`, and `EVIDENCE_CODER_MODEL`, then put `coder_mode: api` in a task contract or pass `--coder-mode api`. It is disabled by default, makes exactly one model request, and does not create a coder agent.

For `online_agent`, configure at least one search backend. The sample uses Firecrawl search with Exa search fallback, then Exa retrieval with Firecrawl fallback. Edit the backend variables when you prefer Serper or Jina. Direct HTTP retrieval remains available as an explicit option, but it is not a default because Claude's provider-domain network allow-list does not permit arbitrary destination domains.

## 3. Start from this folder

Open Codex or Claude Code inside this generated folder. No second workspace is needed. The package creates task-local folders beneath `tasks/` or the `--tasks-dir` selected for a batch.

Codex project configuration defaults to:

```toml
sandbox_mode = "workspace-write"
approval_policy = "never"

[sandbox_workspace_write]
network_access = true
```

This permits writes inside the generated workspace and allows the network calls required for online research, while attempted filesystem escalation is denied rather than pausing a non-interactive batch. It does not grant unrestricted host access.

Claude projects enable the Bash sandbox with fail-closed behavior, use `dontAsk`, deny child-agent launches and `.env` reads through built-in tools, and pre-allow only the bundled research-service domains. The agent may still use provider credentials supplied through its environment, so use scoped keys and a workspace that contains no unrelated sensitive data.

The launcher never bypasses runtime permission checks by default. `--unsafe-unattended` restores the runtimes' bypass flags and must be used only inside an externally isolated container or virtual machine. Network access and exposure to untrusted web content still carry prompt-injection and data-egress risk under either mode.

## 4. Create or edit a task contract

Start from one of:

```text
examples/contracts/direct_api.json
examples/contracts/local_agent.json
examples/contracts/online_agent.json
```

Then inspect routing:

```bash
python3 router.py --contract path/to/task_contract.json --stdout
```

The router writes or prints a versioned decision with route, workflow, difficulty, reasons, and budgets.

## 5. Run one task

Prepare its workspace and rendered prompt without calling a model:

```bash
python3 batch.py \
  --manifest inputs/manifest.example.csv \
  --runtime codex \
  --tasks-dir tasks/example_dry_run \
  --dry-run
```

For a real agent batch, remove `--dry-run` and choose `--runtime codex` or `--runtime claude`. Each row becomes one standalone session.

Each local or online row is one complete standalone research session and launches no child agents.

To run inside an externally isolated container or VM with runtime permissions bypassed, add `--unsafe-unattended`. The batch summary records whether this unsafe opt-in was used.

The default standalone-session allowance is deliberately generous: 40 search calls and 3,600 seconds. Override `max_search_calls` and `max_runtime_seconds` in a task contract or add those columns to a manifest when a case needs a different ceiling. These values are limits, not targets; a completed evidence plan should finish early.

The `direct_api` route runs through `direct_api.py` inside the batch process and does not raise an agent session.

## 6. Research and archive tools

Online search, retrieval, caching, and evidence archival use the preserved research-tool interface:

```bash
python3 tools/research_tools.py --task-root <task> search --query "..."
python3 tools/research_tools.py --task-root <task> search --search-intent '{"must_include":["..."]}'
python3 tools/research_tools.py --task-root <task> retrieve --url "..."
python3 tools/research_tools.py --task-root <task> archive --payload-path evidence_payload.json
python3 tools/research_tools.py --task-root <task> library-search --query "..."
```

For a fixed local collection, ingest relevant text-like files separately:

```bash
python3 tools/local_ingest.py --task-root <task> --path inputs/documents/file.md
```

Add `--share-with-library` when the user intends that local source to be discoverable by later tasks in this workspace.

Canonical artifacts are `<task>/cache/cache_index.jsonl`, `<task>/cache/research_log.ndjson`, `<task>/cache/pages/*.md`, and `<task>/evidence.ndjson`. Online retrieval and explicitly shared local documents populate the framework-level source library at `tasks/_global_cache/`. Archived line citations are validated before they are appended.

## 7. Validate

```bash
python3 validate.py project
python3 validate.py research --task-root tasks/<run>/<task_id>
python3 validate.py task --task-root tasks/<run>/<task_id>
```

Use `research` for local and online runs without coding. Use `task` for `direct_api` or an agent route that enabled the optional API coder. The task, route, evidence, online-research worklog, and final-report contracts have JSON Schemas under `schemas/`.

## Batch outputs

Each batch writes:

```text
<tasks-dir>/batch_progress.json
<tasks-dir>/batch_results.json
<tasks-dir>/batch_summary.json
<tasks-dir>/<task_id>/task_contract.json
<tasks-dir>/<task_id>/route_decision.json
<tasks-dir>/<task_id>/subprocess_prompt.txt
<tasks-dir>/<task_id>/session.log
<tasks-dir>/<task_id>/cache/
<tasks-dir>/<task_id>/evidence.ndjson
<tasks-dir>/<task_id>/research_report.md
```

`direct_api` and `coder_mode=api` additionally write `<task>/output/final_report.json`; the optional coder also records `api/coder_request.json`, `api/coder_response.json`, and `api/coder_execution.json`.

Use `--retry-manifest` to write only failed or timed-out rows for later resumption.

Inquiry tasks use the same per-task artifacts under `tasks/inquiries/<task_id>/`, plus `inquiry_progress.json` and `inquiry_result.json` instead of collection-level batch summaries.

For unattended batches, the launcher also provides periodic progress heartbeats, per-stage process timeouts, full process-tree cleanup, Codex/Claude session references for `--resume`, token/error parsing, and zero-child-agent auditing. Auth and quota failures abort safely by default. Use `--api-failure-mode pause` only when you intentionally want quota failures to pause and retry after `--quota-pause-seconds`; authentication failures still abort. The optional API coder remains exactly one model request per row even when agent retries are enabled.
