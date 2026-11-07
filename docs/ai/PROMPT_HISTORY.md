# Prompt history

Every prompt sent to an AI tool for this project, in order. (AI responses are optional per the submission FAQ and are not reproduced.)

## Tool: Claude Code (Anthropic), desktop app

| # | Prompt (verbatim) | Attachments / context |
|---|---|---|
| 1 | `implement it` (preceded by the path of the challenge folder `sodarshan`) | The four supplied files: the "72 Hours Code Challenge" brief (PDF), the Submission Guidelines (PDF), the Candidate FAQ (PDF) and `YottaFlex_72_Hour_Challenge_Implementation_Guide.docx`. |
| 2 | `hay check the problem statement and this and everything and let me know what else left to do` (plus the path of this project folder) | The same four files and this repository. Result: a gap analysis (git repo, disclosure files, clean-environment test run, submission steps). |
| 3 | `bruh refine the codebase and include everything that he needs to pass this text in a single repo zip and give me along with the ai prompt` | This repository. Result: final refinement pass, clean-environment verification, and a zip plus a hand-off prompt for the AI tool used to push to GitHub. |
| 4 | `ahh does it 100% satisfies everything from the problem statemebts and those pdf's ??` | Gap analysis against the brief. |
| 5 | `ha fill the gaps and give me refined finel version` | Added the changed-column-type tests, a `recover` command for stuck runs, and refreshed docs/evidence. |

Everything else (architecture, schema, rules, tests, documentation) was produced by the tool from those files.

## Tool: Qoder Desktop (this laptop) — Django web UI, follow-up fixes, verification and push

| # | Prompt (verbatim) | Attachments / context |
|---|---|---|
| 1 | `for this please add the ui and ux with using django and please make it for the give me carefully because it is my dream please make it correct an sutable to that zip` | `reference-code-pipeline.zip`. Result: the Django web console (`webapp/`, `manage.py`, `docs/WEB_UI.md`, `docs/ui_screenshots/`, 21→23 UI tests), a thin layer over `src/`. |
| 2 | `in this why it was coming like that error is coming please explain` | Screenshot of `REFERENCE_INVALID` on the load page; a non-reference CSV had been uploaded. Explanation only. |
| 3 | `Columns: product_code, description, status, effective_from, effective_to. in the perticularly mention about it should it should must taken this only` | Result: mandatory-columns help text; loader now also rejects unexpected reference columns; regression test added. |
| 4 | `please give me download button to dowload the reference_codes_v1.csv` | Result: per-version Download CSV buttons (`/reference/<version>/download/`) and test. |
| 5 | `why it was coming like this please tell where is the problem` | Screenshot of a `SCHEMA_MISMATCH` failed run (reference CSV uploaded into the incoming-records slot). Result: explanation plus a form guard that detects swapped files before a run starts. |
| 6 | `i want my code in the zip file path to download` | Result: source-only zip of the project in Downloads. |
| 7 | `https://github.com/sodarsanvenkatasai/refpipe in this laptop github was add this repo will be access you are not` | Access check only; push deferred pending instruction. |
| 8 | `proceed with this` | `reference-code-pipeline-final.zip` + `PUSH_PROMPT.md`. Result: clean-venv verification, this prompt-history append, meaningful commits and push to the candidate's repo. |

The Qoder session transcript is stored locally by the app and was not exported; the prompts above are quoted verbatim.
No code in this final zip was rewritten by the push step; the only file changed by it is this history append.

> **Candidate to complete before submitting:** add here any other prompts you send to any AI tool while reviewing,
> changing or extending this repository (including the AI tool that pushes this repo to GitHub), and note any tool
> that cannot export its history.
