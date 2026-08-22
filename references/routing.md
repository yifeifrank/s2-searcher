# Routing

The framework exposes three execution routes. Route selection describes how evidence is acquired and processed; it does not introduce an agent hierarchy or a separate synthesis service.

## Routes

### `direct_api`

Use when all required evidence is already known, compact, self-contained, and suitable for a single API request. The API worker reads the task contract, codebook, optional output schema, and named documents. It records the request, raw response metadata, execution route, model, retry count, and final JSON.

### `local_agent`

Use for a fixed collection that benefits from iterative file discovery and close reading. One standalone session uses local tools such as `rg`, bounded file reads, workspace-library search, and `tools/local_ingest.py` to materialize citeable Markdown. It archives line-based evidence, writes `research_report.md`, and freezes the inventory.

### `online_agent`

Use when sources may need to be discovered externally. One standalone session owns planning, workspace-library lookup, gap tracking, web search, retrieval, archival, the research report, and evidence freeze. It materializes reusable sources into its task cache so audit logs and evidence remain task-local. It does not launch child agents.

Both agent routes default to `coder_mode=none`. When explicitly enabled, `coder_mode=api` makes one post-freeze API call outside the agent session.

The standalone-session defaults are deliberately generous: 40 search calls and 3,600 seconds. A task contract or manifest row may set lower or higher values. The research agent treats these values as ceilings and stops when its evidence-derived coverage todos are terminal; it does not spend the allowance merely because it is available.

## Deterministic rules

An explicit task-contract route wins. Otherwise:

1. Route to `online_agent` when external search is required, document scope is `open_web`, or no local documents are supplied and the objective requires factual evidence.
2. Route to `local_agent` for a fixed collection, a large or noisy corpus, multiple documents requiring iterative inspection, or compound output over dispersed local evidence.
3. Route to `direct_api` for compact inline or single-document inputs that do not require iterative tool use.

## Difficulty profile

Difficulty tunes budgets and planning depth; it does not change the agent architecture.

- `light`: compact corpus, low noise, simple output.
- `standard`: ordinary structured extraction.
- `intensive`: large/noisy local corpus or demanding online coverage.
- `extreme`: several compounding risks such as high identity ambiguity, multilingual discovery, conflicting evidence, compound output, and high source dispersion.

## Audit record

Every decision is written to `route_decision.json` with:

- router version;
- selected route;
- authoritative workflow prompt;
- difficulty profile;
- normalized input factors;
- decision reasons;
- explicit overrides;
- recommended budgets and permission mode.
