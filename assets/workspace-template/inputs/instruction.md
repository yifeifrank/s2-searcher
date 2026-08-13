# Research and Coding Instruction

This Markdown file is the authoritative task contract. Its prose governs research scope, source use, interpretation, normalization, missing values, and quality control. The fenced JSON object under `## Codebook` governs output fields, nesting, types, allowed values, and field-local formatting.

## Research goal

Use the task contract's objective, subject metadata, and permitted evidence environment to populate every applicable codebook field with source-supported information.

## Required information

- Treat every codebook field as a coverage target unless these instructions mark it optional or not applicable.
- Preserve distinct events, relations, observations, or records as separate entries when the codebook uses arrays.
- Keep unknown, contradicted, and not-applicable values explicit according to project rules; never manufacture a value merely to fill the codebook.

## Research requirements

- Confirm that evidence belongs to the task's intended subject, entity, case, or unit of analysis.
- For online work, prioritize primary and authoritative sources, then use credible secondary sources to fill genuine gaps.
- Use alternate terminology, names, identifiers, languages, and date forms when relevant.
- Preserve the complete retrieved or ingested source in the cache and archive focused line ranges with enough surrounding context to support auditing.

## Quality requirements

- Optimize for accuracy, completeness, provenance, and faithful uncertainty.
- Resolve identity ambiguity before combining evidence.
- Preserve conflicts and qualifiers instead of silently choosing convenient values.
- Do not collapse simultaneous or distinct records merely to shorten the output.
- Every populated field must be traceable to task-local evidence.

## Coding expectations

- Follow the embedded codebook exactly for field names, nesting, types, allowed values, and field-local formatting notes.
- Apply the prose instructions for coverage, source use, interpretation, normalization, missing values, and quality control.
- Follow the optional output JSON Schema when one is supplied.

## Local documents

Place fixed source material under `inputs/documents/` or reference other paths from a task contract or manifest. Relative paths resolve from the framework root.

## Codebook

The following fenced JSON object is the machine-extracted output contract. Large codebooks may use additional fenced JSON objects in this section; top-level keys must remain unique across chunks.

```json
{
  "replace_me": "Replace this object with the project codebook."
}
```
