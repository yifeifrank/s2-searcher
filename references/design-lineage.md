# Design Lineage and Modernization

The generated package generalizes the workflow described by Yifei Zhu, Songpo Yang, Jiangnan Zhu, and Junyan Jiang in [“Agentic Framework for Political Biography Extraction”](https://arxiv.org/abs/2603.18010) (arXiv:2603.18010; DOI: 10.48550/arXiv.2603.18010) without embedding its political-biography domain assumptions.

## Preserved principles

- Separate evidence acquisition and selection from terminal structured coding.
- Use iterative reason--act cycles: identify a gap, search or inspect, retrieve, archive, and update state.
- Cache the complete retrieved or ingested source while separately archiving focused, source-linked line ranges.
- Keep the codebook fixed; when an evaluation enables downstream coding, keep that configured single-call coder fixed so evidence strategies remain comparable.
- Preserve explicit input, process, and output contracts, including unresolved gaps and tool-use records.
- Run one bounded task per manifest row and retain validation, retry, provenance, and session artifacts.
- Use multilingual search and identity anchors when the evidence environment demands them.

## Deliberate modernization

- The domain-specific elite instruction/codebook is an opt-in example; normal scaffolding uses a user combined instruction contract.
- There are exactly three execution routes: direct API, local agent, and online agent.
- There is no RAG, vector store, embedding service, or standalone synthesizer system. Evidence selection happens inside the local or online route.
- One standalone online session owns the complete research loop for every online case. Difficulty affects budgets and planning depth, not agent architecture.
- Codex and Claude definitions project from common role prompts and use the same research-tools implementation and artifact contracts.
- Active research prompts preserve the source framework's search, retrieval, archival, gap, and freeze discipline while removing orchestration layers and the mandatory coder agent.
- Local and online routes terminate at a research report and frozen evidence by default. Optional coding is one launcher-owned API request over those artifacts, enabled explicitly after research.
- Prose instructions and the JSON codebook share one authoritative Markdown contract; a deterministic extracted JSON mirror supports routing and validation without creating a second user-maintained source.

The pre-simplification skill backup retains the earlier source snapshots. See `prompt-preservation.md` for the adaptation ledger.

The term “coding” means mapping frozen evidence into the user’s structured codebook. It does not mean writing software.
