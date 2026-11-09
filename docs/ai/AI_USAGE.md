# AI usage disclosure

**Tools used:** Claude Code (Anthropic, desktop app) for the core pipeline; Qoder Desktop for the Django web
console, the interface redesign, the reliability work and the release.
**Full verbatim prompt history:** [PROMPT_HISTORY.md](PROMPT_HISTORY.md).

## Where AI was used

* **Web console (session 1, Qoder).** `webapp/`, `manage.py`, `docs/WEB_UI.md` and the UI tests were generated to my
  specification (Django, thin layer over `src`). The reference-column contract, the per-version download and the
  swapped-file guard were my requirements, added after I hit those failures while using the app.
* **Interface (session 2, Qoder).** The console was redesigned four times: three drafts were rejected by me before
  the accepted "Flight Deck" design, against my criterion of under 20% similarity to the reference repository I
  supplied. Templates, CSS, JavaScript, the self-hosted fonts and the regenerated documentation screenshots are
  AI-implemented to my direction.
* **Reliability (session 3, Qoder).** The rerun endpoint, startup recovery of stale `RUNNING` rows, error visibility
  in the registry and the rerun-safety tests were implemented to my specification
  (`Make reruns safe; record run status, row counts, rejects, and errors`). I then required the same scenario to hold
  in the registry itself rather than only on the run page: the result is the quick-view drawer (full error, counts,
  live polling, rerun action) and three further tests, 31 UI tests in total.
* **What the tools checked themselves:** the full test suite (72 pipeline + 31 UI = 103 passing), the sample and
  100k-row pipelines through the CLI, the benchmark, and the rendered console in a real browser at desktop and
  mobile widths. Defects found and fixed along the way: NaN-vs-None / amount-parsing inconsistency between the two
  validation engines; a test that enabled the entity check for only one version; the bottom ticker I ordered
  removed; stale documentation claims ("no UI", old test counts, the `RUNNING` restart limitation).

## What I contributed

Requirements and acceptance criteria; the choice of Django and of a thin layer over the existing engine; the
reference-file contract; four rejection rounds that set the final design bar; defect discovery from screenshots;
the rerun-safety specification; review of the verification evidence; and every release decision, including the two
authorized pushes to my GitHub repository. The detailed account is in + research and data gathering etc... work
[PROMPT_HISTORY.md → How the work was divided](PROMPT_HISTORY.md#how-the-work-was-divided).



Known limitations are listed in the README and in `docs/REQUIREMENTS_COVERAGE.md` §8; none are hidden.
