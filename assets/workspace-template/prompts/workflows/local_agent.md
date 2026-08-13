# Workflow: Direct Local Research Agent

Use this workflow when the route decision selects `local_agent` for a fixed local collection.

1. Read the task contract, route decision, combined `inputs/instruction.md` or `instructions_path` (including its embedded codebook), extracted codebook mirror, and output schema.
2. Create `<workspace>/plan.md` from the combined prose and codebook coverage targets.
3. Resolve contract document paths relative to the framework root unless already absolute.
4. Use `rg`, bounded reads, and ordinary local file tools to inspect the fixed collection. Do not use external search.
5. Materialize relevant text-like sources with:

   ```bash
   python3 tools/local_ingest.py --task-root <workspace> --path <local-file>
   ```

6. Inspect cached Markdown and archive source-linked line ranges through `tools/research_tools.py archive`.
7. Track conflicts, missing fields, and identity or case ambiguity.
8. Write `<workspace>/research_report.md` with `# Research Report`, `## Summary`, `## Evidence`, `## Conflicts and Uncertainty`, and `## Open Gaps`. Cite cache keys or evidence records and keep missing information explicit.
9. Freeze `cache/pages/*.md`, `cache/cache_index.jsonl`, `cache/research_log.ndjson`, `evidence.ndjson`, and `research_report.md`.
10. Run `python3 validate.py research --task-root <workspace>` and correct failures before completion.

You are the only agent in this route. Do not delegate to a coder or another research agent. Do not introduce a RAG, vector, or standalone synthesizer stage. Iterative local inspection and evidence selection are activities inside this route. If `coder_mode=api`, the launcher may make one separate API call after your research freeze; that is outside your work.
