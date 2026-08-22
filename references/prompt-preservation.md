# Prompt Adaptation

The active workflows under `prompts/workflows/` preserve the source framework's planning, search refinement, three-attempt gap rule, evidence archival, path safety, and evidence-freeze discipline. Adaptations are:

1. replacing biography-specific subjects and categories with the combined user instruction and embedded codebook;
2. storing prose and codebook together in `instruction.md`, with an exact extracted JSON mirror for validation;
3. replacing private wrapper paths with standalone `tools/research_tools.py` paths;
4. replacing repository-specific validator calls with `validate.py research|task`;
5. assigning the complete local or online research loop to one standalone session with no child agents;
6. terminating agent routes at `research_report.md` plus a validated frozen evidence package;
7. replacing mandatory agent-based coding and correction loops with an optional, explicitly configured, single API call after the research session;
8. retaining the provider-backed workflow for Codex and Claude while adding a Codex-native acquisition prompt that preserves the same search strategy and delegates only mechanical event/URL capture, optional snapshots, inventory counts, and validation to the launcher;
9. omitting experiment-only telemetry requirements that are not portable across runtimes.
10. checking the workspace source library before new discovery while retaining task-specific inspection, archival, and evidence freeze.

The native path does not weaken planning, query refinement, source hierarchy, identity checking, corroboration, conflict handling, or the three-attempt gap rule. It changes the evidence level honestly: observable URLs are traceable and optional snapshots are preserved, but the system does not fabricate line-addressable citations for native context the launcher cannot see.

The dated pre-simplification backup retains the former prompts and historical source snapshots. Active workflow changes should preserve the substantive research and verification requirements above.
