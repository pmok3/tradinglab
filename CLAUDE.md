# CLAUDE.md

**The agent guide for this repo is [`AGENTS.md`](AGENTS.md). Read that.**

This file used to be a byte-for-byte copy of it. Both are loaded into every
session, so the duplicate doubled the standing context cost — and, exactly as
the repo's own DRY landmines predict (`AGENTS.md` §7.34), the two copies drifted
apart the moment anyone updated only one. There is now one canonical file.

## Section numbering is unchanged

Roughly a hundred docstrings, tests, docs and CI comments cite this guide as
`CLAUDE.md §7.x`. **Those references are still valid** — the numbering in
`AGENTS.md` is identical, so `CLAUDE.md §7.19` means `AGENTS.md §7.19`. Never
renumber a section in `AGENTS.md`; add new landmines at the end.

## Core rules worth repeating here

If you read nothing else before touching code:

1. **Specs are part of the change.** Every `.py` under `src/tradinglab/` has a
   colocated `.spec.md`. Every changed module needs its spec in the same commit;
   CI/release enforce this with `tools/check_spec_freshness.py`. Update the
   behavior contract and `Last updated` date, or update only the date for a
   non-behavioral code change. (§2, §7.30)
2. **No pull requests.** pmok3 is the only contributor; work lands directly on
   `main`. Because there is no PR gate, validate before you push:
   `ruff check src tests` and `pytest tests/unit tests/data -q`. (§9)
3. **Surgical diffs.** This is mature, production-ish tooling. Prefer the
   smallest correct change; never do stylistic rewrites; no new dependencies
   without discussion. (§9)
4. **Windows + PowerShell.** Backslash paths, fresh process per shell call,
   `gh` needs git on `PATH`. (§0, §3)
5. **Read §7 before debugging something weird.** The documented landmines
   point at the specs and tests that pin them; existing section numbers stay fixed.
6. **Coverage improvements follow the canonical methodology.** Read
   `AGENTS.md` §7.39 for comparable baselines, behavioral regressions, parallel
   ownership, fixture isolation and end-to-end CI evidence. §6 defines the
   **70% changed-executable-line gate**; whole-suite coverage stays informational.
   Keep statement and branch metrics separate, and never invent CI provenance
   for a local report.

Everything else — project overview, layout, commands, smoke-test rules, CI,
landmines, build/release flow, conventions, cheatsheet — is in
[`AGENTS.md`](AGENTS.md).
