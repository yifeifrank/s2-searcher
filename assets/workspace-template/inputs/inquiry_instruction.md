# Open Inquiry Research

Answer the task contract's research question or objective. This inquiry may concern any social-science entity, event, institution, concept, or relationship. It does not require a user-defined codebook or comparable cases.

## Research requirements

- Convert the question into a small set of concrete evidence needs.
- Inspect relevant sources already preserved in the workspace library before repeating retrieval work.
- When web search is allowed, use it to fill genuine gaps and refresh sources that may be stale.
- Prefer primary and authoritative sources, supplemented by credible scholarly or journalistic sources when they add necessary context or independent corroboration.
- Retrieve and inspect complete sources rather than using search snippets as evidence.
- Preserve disagreements, scope conditions, missing information, and uncertainty.
- Stop when the question is adequately supported or remaining gaps have reached the workflow's attempt limit. Search budgets are ceilings, not targets.

## Output requirements

Write a readable, self-contained `research_report.md` that answers the question directly. Preserve every source used, archive exact supporting line ranges, cite the task's evidence records, and distinguish sourced findings from interpretation. Inquiry mode ends at the validated report and frozen evidence package; it does not perform structured API coding.

## Codebook

This internal compatibility object supplies planning cues to the current research contract. Inquiry mode expresses them in `research_report.md`; users do not need to provide or populate a structured codebook.

```json
{
  "answer": "Direct evidence-supported response to the research question.",
  "key_evidence": ["Strongest findings and their supporting evidence."],
  "conflicts_and_uncertainty": ["Material disagreements, limitations, and qualifications."],
  "open_gaps": ["Questions that remain unresolved after reasonable searching."]
}
```
