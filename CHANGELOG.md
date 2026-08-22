# Changelog

All notable changes are documented here. This project follows semantic versioning after the initial preview.

## [Unreleased]

- Added Inquiry mode as a single-question entry point over the existing standalone task engine; the current manifest path is documented as Study mode.
- Exposed `tasks/_global_cache/` as a searchable workspace source library and added opt-in sharing for ingested local documents.
- Added an agent-first, no-code beginner guide, a credential-safe provider smoke check, and a recommended Firecrawl-search/Exa-retrieval sample configuration.
- Updated Firecrawl search and scrape calls to the current v2 endpoints while retaining response compatibility with the earlier search shape.
- Adopted session terminology in user-facing documentation and clarified the repository's evidence-workspace description.

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
