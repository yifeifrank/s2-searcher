# Changelog

All notable changes are documented here. This project follows semantic versioning after the initial preview.

## [Unreleased]

- Added a Codex-native online workflow that retains the full research strategy while the launcher automatically captures observable search actions, queries, cited URLs, and optional post-turn source snapshots; the original provider-backed line-citation workflow remains available.
- Hardened retrieval handling so native snapshots remain best effort, recovered provider fallbacks do not trigger false batch aborts, and permanent Exa client/authentication errors are not retried.
- Simplified beginner prompts so users provide only the research question, cases, and materially relevant preferences; built-in preparation, safety, evidence, and validation requirements now remain implicit.
- Clarified that an initial research request authorizes preparation and a dry run only; live agents, searches, and provider calls require separate explicit approval.
- Renamed Research Workspace Builder to S² Searcher, including the canonical repository, skill ID, installation paths, and deterministic release archive.
- Added Inquiry mode as a single-question entry point over the existing standalone task engine; the current manifest path is documented as Study mode.
- Exposed `tasks/_global_cache/` as a searchable workspace source library and added opt-in sharing for ingested local documents.
- Added an agent-first, no-code beginner guide, a credential-safe provider smoke check, and a recommended Firecrawl-search/Exa-retrieval sample configuration.
- Updated Firecrawl search and scrape calls to the current v2 endpoints while retaining response compatibility with the earlier search shape.
- Adopted session terminology in user-facing documentation and clarified the repository's evidence-workspace description.
- Established this repository as the skill's canonical standalone source and retained S² Forge as its collection catalog.

## [0.1.0] - 2026-08-12

Initial friend-preview release.

- Added direct API, standalone local-agent, and standalone online-agent routes.
- Added auditable search, retrieval, caching, line-cited evidence archival, and validation.
- Added resilient CSV/JSON batch execution with heartbeat, resume, retries, process-tree cleanup, quota/auth handling, and zero-child-agent auditing.
- Added optional single-call API coding after evidence freeze.
- Adopted generous, user-overridable online budgets.
- Adopted least-privilege defaults and an explicit unsafe-isolation opt-in.
- Added public documentation, CI, software citation metadata, and a validated offline example.
- Identified Yifei Zhu as software author and linked the four-author arXiv preprint that describes the research framework.
