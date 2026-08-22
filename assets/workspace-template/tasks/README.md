# Task workspaces

`batch.py` creates one Study-mode subfolder per manifest row, while `inquiry.py` creates one Inquiry task under `tasks/inquiries/` by default. Every task owns its contracts, prompt, logs, cache, evidence archive, and research report. Direct coding and explicitly enabled API coding additionally create a structured final output.

`_global_cache/` is the workspace source library. Retrieved web pages are added automatically; local documents are added only with `tools/local_ingest.py --share-with-library`. Library sources may be materialized into later task caches, but every task must archive its own evidence lines.
