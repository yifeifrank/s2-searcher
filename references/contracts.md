# Contracts

The package uses explicit contracts so runs remain reproducible across Codex, Claude, and direct API execution.

## Combined user input

Normal scaffolding accepts one `instruction.md`. Its prose governs research scope, coverage, source policy, interpretation, normalization, missing values, and quality. A `## Codebook` section contains one or more fenced JSON objects jointly governing output field names, nesting, types, allowed values, and field-local formatting notes. Top-level keys must be unique across chunks.

The builder extracts that embedded object to `inputs/codebook.json`. This is a machine-readable mirror rather than a second authoring surface; project and task validation require exact equality. The compatibility CLI accepts `--codebook` plus optional `--instructions` and composes the same combined file.

An optional output JSON Schema can impose stricter machine validation on `final_output`. Without one, the generated generic final-report schema validates the common wrapper when direct coding or the optional API coder is used.

## Task contract

Each task contract records:

- stable task ID and objective;
- combined-instruction, extracted-codebook-mirror, and output-schema paths;
- input documents and document scope;
- external-search requirement;
- route override;
- optional post-research `coder_mode` (`none` by default or one `api` call);
- output-complexity and evidence-risk factors;
- search, session-time, and retry budgets;
- arbitrary task metadata.

Study mode creates these contracts from manifest rows. Inquiry mode creates one contract from a free-form question, records `metadata.work_mode=inquiry`, disables structured coding, and references the bundled generic inquiry instruction/codebook as a compatibility layer. Both modes terminate in the same task-local evidence and report contracts.

## Process contract

Agent routes preserve:

- rendered prompt;
- route decision;
- plan and optional worklog;
- cached or ingested pages;
- `cache/cache_index.jsonl` and `cache/research_log.ndjson`;
- `evidence.ndjson` with line citations;
- `research_report.md` as the terminal research synthesis;
- session log and agent-session identifiers.

Retrieved web pages are also copied into the framework-level workspace library under `tasks/_global_cache/`. A later task may materialize a matching library source into its own cache, but it must create its own evidence records and report citations.

Machine schemas cover the task contract, route decision, evidence records, online-research worklog, and optional final report.

## Output contract

Local and online routes normally terminate with `research_report.md`, the frozen cache, `evidence.ndjson`, and the research log. They do not require structured coding.

When `direct_api` or optional `coder_mode=api` is used, `output/final_report.json` contains:

- `task_id`;
- `status`;
- `summary`;
- `execution_route`;
- `final_output` following the user codebook;
- `open_gaps`;
- `evidence_file`.

The optional API coder is one independent model request over the research report and materialized archived evidence excerpts. It is not an agent, does not search, and does not run a correction loop.

Never copy credentials into contracts, prompts, request archives, or result files.
