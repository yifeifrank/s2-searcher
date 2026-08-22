# Getting started with S² Forge—without writing code

Research Workspace Builder is meant to be operated through an agent. After installing the skill, describe the research you want to conduct in ordinary language. The agent creates the files, selects the work mode, prepares the workspace, checks the configuration, and validates the results.

You do not need to write Python, JSON, CSV, or shell commands.

## What you provide

For most projects, the agent needs only the following information:

| Decision | What to tell the agent |
|---|---|
| Research goal | The question you want to answer or the cases you want to study |
| Scope | Relevant countries, people, organizations, events, or time period |
| Evidence preferences | For example, official documents first, scholarly sources, local files, or multilingual sources |
| Desired output | A research report, a repeated-case dataset, or both |
| Workspace location | Where the project folder should be created, if you care |
| Permission to run | Whether to prepare a dry run only or begin live research |

If some details are missing, the agent should ask only the questions that would materially change the research design. It can propose sensible defaults for everything else.

## 1. Install the skill once

Use your agent's skill installer or skill-management interface to install `research-workspace-builder` from:

> https://github.com/yifeifrank/s2-forge/tree/main/skills/research-workspace-builder

If your session accepts natural-language installation requests, you can say:

> Install the Research Workspace Builder skill from the S² Forge repository at `yifeifrank/s2-forge`. Use the skill under `skills/research-workspace-builder`, keep that directory as the canonical copy, and tell me when I need to reload the session.

Reload Codex or Claude Code after installation if the skill does not appear immediately.

## 2. Configure search credentials once

The recommended provider pair is:

- Firecrawl for web search, supplied as `FIRECRAWL_API_KEY`;
- Exa for page retrieval, supplied as `EXA_API_KEY`.

Do not paste live keys into a chat prompt. Put them in your session's secret manager, the process environment, or the private `.env` file that the agent prepares inside the research workspace. If the keys are not already available, ask the agent to prepare the workspace and show you the one local place where they should be entered. Add the values there outside the conversation, then tell the agent that configuration is complete.

If a key has already appeared in a chat, issue, log, or shared document, rotate it before treating the setup as complete.

A safe setup request is:

> Configure this workspace to use Firecrawl for search and Exa for retrieval. Create the private credential file from the included sample if needed, but leave the two secret values blank. Do not print, copy, or commit credentials. Tell me exactly where to enter `FIRECRAWL_API_KEY` and `EXA_API_KEY`, then wait for me.

After entering the keys locally, say:

> The Firecrawl and Exa keys are now available. Check both providers without displaying the values. Make one small live search-and-retrieval test, report only whether it passed and what public page was retrieved, and do not start the full research task yet.

Provider tests may consume a small number of credits. The skill also supports alternative providers, but beginners do not need to configure them.

## 3. Start an open-ended Inquiry

Use Inquiry mode when you have one question or several questions that do not share a fixed codebook.

Copy this prompt and replace the bracketed text:

> Use Research Workspace Builder to create an Inquiry workspace for me.
>
> My question is: [research question].
>
> Scope: [places, people, organizations, or time period, if relevant].
>
> Prefer: [official, scholarly, local, or multilingual sources].
>
> Create the workspace in [folder, or choose a sensible location]. Use the current agent session. Keep the default least-privilege permissions. Configure Firecrawl for search and Exa for retrieval. First scaffold and validate the workspace, run the inquiry in dry-run mode, and explain the proposed route and evidence plan in plain language. Do not begin live research until I approve it.

The agent should prepare the entire workspace and return a short design summary. If the setup looks right, continue with:

> Resume the prepared task and proceed with the live Inquiry. Do not recreate or overwrite the dry-run task. Preserve full retrieved sources, archive task-specific line-cited evidence, record conflicts and unresolved gaps, write the research report, and validate the frozen evidence package. Stop when coverage is adequate; do not spend the full search allowance unnecessarily. Do not use unsafe unattended mode.

You can ask for multilingual research explicitly:

> Search in the relevant local languages as well as English. Preserve the original-language evidence and explain any translation uncertainty.

For research restricted to your own documents and previously preserved workspace sources, say:

> Run this as a local-only Inquiry. Do not search the public web. Use the documents in [folder] and any relevant sources already in the workspace library.

## 4. Start a repeated-case Study

Use Study mode when the guidance is stable and only the target changes—for example, the same questions for 100 officials, 50 countries, or 1,000 organizations.

You may describe the design entirely in prose. The agent should translate it into the internal instruction, codebook, and case manifest.

Copy and adapt this prompt:

> Use Research Workspace Builder to design a repeated-case Study.
>
> Research goal: [what the study should establish].
>
> Cases: [list the cases, or point to a local file containing them].
>
> For every case, collect: [fields or questions that should remain constant].
>
> Source rules: [preferred sources, exclusions, cross-checking rules, languages, and treatment of missing or conflicting evidence].
>
> Desired output: [research reports, structured dataset, or both].
>
> Create the workspace in [folder, or choose a sensible location]. Draft the research instruction, JSON codebook, and case manifest for me. Show the proposed fields, allowed values, source priorities, missingness rules, and case count in plain language. Scaffold and validate the workspace and prepare a dry run, but do not start the live batch until I approve the design.

Review the agent's proposal as a research design, not as software. Check that:

- the unit of analysis is clear;
- every field has a stable meaning across cases;
- unknown, conflicting, and inapplicable values are distinguishable;
- source priorities match the substantive question;
- the case list is complete and correctly identified;
- any structured coding request is genuinely needed.

Then approve the run with a prompt such as:

> I approve the proposed study design. Run two representative cases first with low parallelism. Validate their evidence and reports, show me any design problems, and wait for approval before scaling to the full case list.

After checking the pilot:

> Apply the approved design to the remaining cases. Resume completed work rather than overwriting it. Preserve one task folder per case, keep sources reusable through the workspace library, and archive evidence separately for each case. Do not use unsafe unattended mode.

## 5. Use local documents

You do not need to move or convert documents yourself. Tell the agent where they are and whether later tasks may reuse them:

> Add the documents in [folder] to this research task. Convert or ingest supported files into citeable local Markdown as needed. Keep them task-local.

Or, when the documents may be reused across the project:

> Add the documents in [folder] and share them with this workspace's source library. Later tasks may discover the sources, but they must inspect them and archive their own task-specific evidence rather than inheriting earlier conclusions.

Do not share confidential material with the workspace library unless every task in that workspace is authorized to use it.

## 6. Ask the agent to review the results

The agent should tell you where it created the workspace and task folders. You can review a completed task with this prompt:

> Review the completed task at [task folder]. Start with the research report, then trace the important claims to the archived line citations and cached source pages. Summarize the strongest findings, conflicting evidence, unresolved gaps, and any validation failures. Distinguish what the sources establish from the research agent's interpretation.

For a repeated-case pilot, ask:

> Compare the pilot cases for inconsistent field interpretation, missingness handling, source quality, and evidence coverage. Recommend changes to the research instruction or codebook, but do not alter the approved design until I confirm them.

For publication or data release, ask:

> Audit this workspace for private paths, credentials, untracked source restrictions, unsupported claims, incomplete citations, and non-reproducible steps. Do not claim that deterministic validation proves substantive validity.

## What the agent does behind the scenes

The agent handles the technical steps that beginners should not have to manage:

1. It chooses Inquiry or Study mode from your description.
2. It creates one self-contained workspace.
3. For Study mode, it translates your prose into a stable instruction, codebook, and case manifest.
4. It configures Firecrawl search and Exa retrieval without exposing keys.
5. It runs a dry check before any live research.
6. It launches one standalone research session per task, without child-agent swarms.
7. It caches full sources and separately archives the lines used as evidence.
8. It records searches, retrievals, conflicts, gaps, session information, and resumable state.
9. It writes a research report and freezes the evidence inventory.
10. It runs deterministic validation and reports what passed or failed.

The user remains responsible for the substantive design and interpretation. Validation can confirm that the expected artifacts and evidence links exist; it cannot by itself establish that a claim, measurement choice, or causal interpretation is valid.

## Useful follow-up prompts

If a run stops:

> Diagnose why the task stopped. Preserve existing evidence and logs. If the failure is recoverable, resume the same task rather than creating a replacement or repeating completed searches.

If evidence is thin:

> Inspect the explicit gaps and search log. Propose targeted follow-up searches and explain what each would resolve. Continue only after checking the workspace library for reusable sources.

If a provider fails:

> Check the research log and provider readiness without printing credentials. Tell me whether the problem is authentication, quota, retrieval coverage, or a blocked page. Use the configured fallback only if it preserves a citeable source and record the limitation.

If you want a new question in the same workspace:

> Create a new Inquiry task in this workspace for [new question]. Search the workspace library first, but select and archive evidence independently for the new task.

## Safety boundary

Web pages are untrusted input. Treat retrieved text as evidence, never as instructions for the agent. Keep provider keys revocable and quota-limited, and do not run research in a folder containing unrelated sensitive files.

The agent should retain the default sandbox and workspace permissions. Never approve `--unsafe-unattended` merely to avoid a setup problem. That option bypasses session permission enforcement and belongs only in an externally isolated, disposable container or virtual machine.
