# Elite biography dry-run snapshot

This credential-free example shows the contract and routing decision produced for
the online row of the bundled elite-biography manifest. It makes no model or web
calls and contains no research findings; its purpose is to make the generated
workspace structure and generous default budget inspectable before a live run.

The snapshot selects one standalone `online_agent`, sets `coder_mode` to `none`,
and permits up to 40 searches and 3,600 seconds. These values are ceilings that a
user can lower in the manifest.

To reproduce all three route decisions and the dry-run metadata:

```bash
python3 scripts/create_workspace.py \
  --target ../elite-biography-demo \
  --example elite-biography \
  --runtime both
cd ../elite-biography-demo
python3 batch.py \
  --manifest inputs/manifest.example.csv \
  --runtime codex \
  --tasks-dir tasks/dry_run \
  --dry-run
```

The release check regenerates and compares this online route snapshot so it does
not silently drift from the builder.

