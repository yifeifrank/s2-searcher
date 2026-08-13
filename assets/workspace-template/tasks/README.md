# Task workspaces

`batch.py` creates one subfolder per manifest row here (or beneath the directory passed with `--tasks-dir`). Every task owns its contracts, prompt, logs, cache, evidence archive, and research report. Direct coding and explicitly enabled API coding additionally create a structured final output. `_global_cache/` is reserved for reusable fetched web pages and is never treated as a task.
