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

## The five rules worth repeating here

If you read nothing else before touching code:

1. **Specs are part of the change.** Every `.py` under `src/tradinglab/` has a
   colocated `.spec.md`. Change behaviour, update the spec in the same commit.
   `tests/unit/test_codebase_invariants.py` gates that a spec exists; whether
   it is *true* is on you. (§2, §7.30)
2. **No pull requests.** pmok3 is the only contributor; work lands directly on
   `main`. Because there is no PR gate, validate before you push:
   `ruff check src tests` and `pytest tests/unit tests/data -q`. (§9)
3. **Surgical diffs.** This is mature, production-ish tooling. Prefer the
   smallest correct change; never do stylistic rewrites; no new dependencies
   without discussion. (§9)
4. **Windows + PowerShell.** Backslash paths, fresh process per shell call,
   `gh` needs git on `PATH`. (§0, §3)
5. **Read §7 before debugging something weird.** Thirty-five documented
   landmines, each pointing at the spec and the test that pin it.

Everything else — project overview, layout, commands, smoke-test rules, CI,
landmines, build/release flow, conventions, cheatsheet — is in
[`AGENTS.md`](AGENTS.md).
