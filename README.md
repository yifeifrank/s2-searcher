# Research Workspace Builder

Research Workspace Builder is a Codex and Claude Code skill that creates self-contained, auditable research workspaces. Each task uses one standalone session to inspect local documents or search the web, cache full source material, archive line-cited evidence, write a research report, and validate the frozen evidence package.

This subskill is an initial `v0.1.0` friend preview by Yifei Zhu. Its conceptual framework is described in the publicly available preprint [“Agentic Framework for Political Biography Extraction”](https://arxiv.org/abs/2603.18010), which remains under review.

## Two work modes, one evidence system

- **Study mode** uses the existing CSV/JSON manifest launcher when guidance is stable and targets vary.
- **Inquiry mode** takes one open-ended question without requiring a user codebook or manifest.

Both modes preserve task-local evidence and reuse full sources through the workspace library at `tasks/_global_cache/`. Reusing a source never reuses an earlier conclusion automatically: each task inspects the source and archives its own supporting line ranges.

Every generated workspace includes both entry points. Inquiry-first scaffolding only removes the initial codebook requirement; it does not create a separate or reduced research architecture.

## What it builds

```text
open question | instruction + embedded JSON codebook
                       |
                       v
        direct API | local agent | online agent
                       |
                       v
       cache + evidence.ndjson + research report
                       |
                       v
              deterministic validation
```

The online route uses one persistent research session. It does not create a supervisor, searcher children, or a coder child. Optional structured coding is a separate, single API call after evidence is frozen.

## Safety model

The default is designed to be useful without granting unrestricted host access:

- Codex runs with `workspace-write`, outbound network access, and no approval escalation. It can write inside the generated project; out-of-scope filesystem writes are denied.
- Claude Code enables its Bash sandbox, fails closed when sandboxing is unavailable, uses `dontAsk`, blocks child agents and `.env` reads through built-in tools, and pre-allows the bundled research-service domains.
- The batch launcher does not use either session's permission-bypass flag by default.

Online research still processes untrusted content and uses network credentials. `workspace-write` constrains writes but should not be treated as a complete host read boundary. Use provider-scoped keys, avoid sensitive workspaces, and inspect outputs before relying on them. The explicit `--unsafe-unattended` option disables session permission enforcement and is intended only for externally isolated containers or virtual machines.

For Codex configuration details, see the official [configuration reference](https://developers.openai.com/codex/config-file/config-reference/). See the [least-privilege deployment guide](docs/least-privilege.md) and the complete [threat model](SECURITY.md).

## Requirements

- Python 3.10+
- Codex CLI and/or Claude Code
- At least one configured search backend for online research

Install Python dependencies:

```bash
python3 -m pip install -r assets/workspace-template/requirements.txt
```

## Install the skill

Clone the S2 Forge collection, then copy or symlink this subskill into your agent's skill directory:

```bash
git clone https://github.com/yifeifrank/s2-forge.git
ln -s /absolute/path/to/s2-forge/skills/research-workspace-builder \
  ~/.codex/skills/research-workspace-builder
```

For Claude Code, install the same directory under `~/.claude/skills/research-workspace-builder`. Reload the session after installation.

## Start with one prompt

After installing the skill, a beginner can ask an agent:

> Use Research Workspace Builder to create an Inquiry workspace for this question: What explains variation in legislative oversight across democracies? Use Firecrawl for search and Exa for retrieval, preserve full sources and line-cited evidence, keep safe permissions, and run a dry check before asking me to approve live research.

For a repeated-case project, describe the research goal, cases, fields, and source rules in prose. The agent should draft the instruction, codebook, and manifest, explain them in plain language, and wait for approval before a live batch. The [no-code getting-started guide](docs/getting-started.md) provides copyable prompts for Inquiry, Study, local documents, pilots, review, and resumption.

## Manual CLI example

Create an Inquiry-first workspace and prepare one question without model or web calls:

```bash
python3 scripts/create_workspace.py \
  --target ../open-inquiry \
  --inquiry \
  --runtime both
cd ../open-inquiry
python3 inquiry.py \
  "What explains variation in legislative oversight across democracies?" \
  --runtime codex \
  --dry-run
```

Remove `--dry-run` for a requested live inquiry after configuring a search backend. Add `--local-only` to use only local and previously preserved workspace sources.

For a Study-mode demonstration, create the bundled elite-biography example:

```bash
python3 scripts/create_workspace.py \
  --target ../elite-biography-demo \
  --example elite-biography \
  --runtime both
```

Inspect all three routes without making model or web calls:

```bash
cd ../elite-biography-demo
python3 batch.py \
  --manifest inputs/manifest.example.csv \
  --runtime codex \
  --tasks-dir tasks/dry_run \
  --dry-run
```

Copy `.env.example` to `.env`, configure only the providers you use, and remove `--dry-run` for a live batch. The sample configuration uses Firecrawl for search and Exa for retrieval, with cross-provider fallbacks. The online defaults—40 searches and 3,600 seconds—are generous ceilings, not targets, and can be changed per task or manifest row.

A reproducible, credential-free contract and route snapshot is included under [examples/elite-biography-dry-run](examples/elite-biography-dry-run/README.md).

## Manual CLI: use your own task

Agent-first users can describe the research design in ordinary language and let the skill create these inputs. For manual use, put prose requirements and a fenced JSON object beneath `## Codebook` in one Markdown file, then run:

```bash
python3 scripts/create_workspace.py \
  --target ../my-research-project \
  --instruction /path/to/instruction.md \
  --runtime both
```

The detailed generated-workspace guide lives at [assets/workspace-template/README.md](assets/workspace-template/README.md).

## Tests and continuous integration

Run the same release checks used by GitHub Actions:

```bash
python3 scripts/release_check.py
```

“CI” means GitHub automatically runs these checks for every proposed change. The workflow tests supported Python versions, validates schemas and prompts, scaffolds fresh Study and Inquiry workspaces, checks all three route decisions, verifies source-library reuse, and completes dry runs without credentials or paid services.

Build a deterministic archive to send directly to a friend:

```bash
python3 scripts/package_release.py --output-dir dist
```

The generated `dist/research-workspace-builder.skill` excludes Git metadata,
credential files, bytecode, and caches. CI also inspects this archive.

## Citation

Use [CITATION.cff](CITATION.cff) to cite the versioned software release. For the research design and empirical framework, cite:

> Yifei Zhu, Songpo Yang, Jiangnan Zhu, and Junyan Jiang. “Agentic Framework for Political Biography Extraction.” arXiv:2603.18010, 2026. [https://doi.org/10.48550/arXiv.2603.18010](https://doi.org/10.48550/arXiv.2603.18010).

The article is an arXiv preprint and remains under review; publication metadata can be updated after acceptance without rewriting the software release history.

## License

Licensed under Apache-2.0. See [LICENSE](LICENSE).
