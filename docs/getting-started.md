# Getting started with S² Searcher—without writing code

You usually need only one sentence. Describe the research you want, and S² Searcher handles the workspace, work mode, safe setup, evidence preservation, dry run, and validation.

You do not need to write Python, JSON, CSV, shell commands, a codebook, or a manifest.

## 1. Install the skill once

Tell your agent:

> Install S² Searcher from `https://github.com/yifeifrank/s2-searcher` and tell me when it is ready.

Reload Codex or Claude Code if the skill does not appear immediately.

## 2. Ask for the research

Choose the closest starting point. The labels below explain the modes; you do not need to use them in your prompt.

### One question

> Use S² Searcher to investigate: [your question].

This becomes an Inquiry: one open-ended question with its own report and evidence package.

### The same questions for many cases

> Use S² Searcher to study [the cases, or a file containing them]. For every case, find [the fields or questions that stay the same].

This becomes a Study: shared guidance applied consistently across multiple targets.

### Your own documents

> Use S² Searcher to answer [your question] using only the documents in [folder].

This keeps the task local and does not search the public web.

These short prompts are enough. The agent should ask a follow-up only when a missing choice would materially change the research.

Your first request prepares and checks the task only. It does not start live agents, web searches, or paid provider calls.

## Optional details

Add any of these when they matter:

- scope, such as a country, organization, or time period;
- preferred evidence, such as official, scholarly, local, or multilingual sources;
- desired output, such as a report, dataset, or both;
- a workspace location, if you care where the files live.

For example:

> Use S² Searcher to investigate why legislative oversight varies across democracies. Focus on national parliaments since 1990 and prefer official and scholarly sources.

Everything after the first sentence is optional.

## 3. Review the prepared task

Before live research, the agent prepares the workspace, chooses Inquiry or Study mode, records the design, validates the setup, and runs a dry check. For a Study, it also translates your description into consistent fields and a case list.

The agent should explain any consequential assumptions in plain language. You review the research design rather than the software configuration.

When the preparation looks right, say:

> The setup looks right. Start the live research.

For a large Study, begin with a small pilot:

> Run two representative cases first and show me any design problems before continuing.

After reviewing the pilot:

> The pilot looks good. Continue with the remaining cases.

S² Searcher resumes the prepared tasks instead of overwriting them.

## Online research credentials

Online research normally uses Firecrawl for search and Exa for retrieval. If credentials are missing, the agent should prepare a private local credential file and tell you where to enter `FIRECRAWL_API_KEY` and `EXA_API_KEY`.

Never paste live keys into a chat. Enter them through a local secret manager, process environment, or private `.env` file outside the conversation. If a key has appeared in a chat, issue, log, or shared document, rotate it.

After entering the keys locally, say:

> The search credentials are available. Check them without displaying their values.

Provider checks may consume a small number of credits.

## Review or resume work

To review a completed task:

> Review the completed S² Searcher task in [folder]. Explain the findings, strongest evidence, conflicts, unresolved gaps, and validation problems.

To resume interrupted work:

> Resume the existing S² Searcher task in [folder]. Preserve completed work and do not repeat successful searches.

To ask another question in the same workspace:

> Investigate this new question in the same workspace: [question].

Earlier sources may be reused, but each task selects and archives its own evidence rather than inheriting earlier conclusions.

## What S² Searcher handles automatically

The skill:

1. chooses Inquiry or Study mode;
2. creates one self-contained workspace;
3. applies least-privilege permissions and prepares credentials without exposing them;
4. runs a dry check before live research;
5. preserves full sources, line-cited evidence, conflicts, gaps, logs, and resumable state;
6. writes a research report and validates the frozen evidence package.

The user remains responsible for the substantive question, authorization to use sources or private documents, and interpretation of the results. Deterministic validation confirms that expected artifacts and evidence links exist; it does not by itself establish substantive or causal validity.

## Safety boundary

Web pages are untrusted input. Treat retrieved text as evidence, never as instructions for the agent. Keep provider keys revocable and quota-limited, and do not run research in a folder containing unrelated sensitive files.

Keep the default sandbox and workspace permissions. The `--unsafe-unattended` option bypasses session permission enforcement and belongs only in an externally isolated, disposable container or virtual machine.
