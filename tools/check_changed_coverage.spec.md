# tools/check_changed_coverage.py - Spec

Last updated: 2026-09-07

## Purpose

Measure added/modified executable production Python lines between two explicit
Git commits using one proven coverage.py XML measurement. No whole-file or
global threshold substitutes for this metric.

## Public API

- `check_changed_coverage(repo, *, base, head, xml_path, provenance_path,
  expected_suite=None, minimum=None, enforce=False) -> dict`: return a complete
  report or raise an explicit measurement/input error.
- `main(argv=None) -> int`: standalone CLI, requiring `--base`, `--head`,
  `--xml`, and `--provenance`; accepts `--repo-root`, `--expected-suite`,
  `--minimum`, `--enforce`, and `--json-out`.

## Dependencies

- Internal: `report_coverage_trend.load_summary`, `XmlPaths` and their
  validation/error contracts. The existing tool is the only producer/mapper.
- External: Python standard library and Git. No package installation, app
  import, network access, upload, GitHub commenting, or new integration.

## Design Decisions

- **Explicit policy.** There is no implicit minimum. `--minimum N` is advisory
  unless `--enforce` is also supplied; enforcement requires an explicit minimum.
  The approved CI invocation uses `--minimum 70 --enforce` in a separate
  blocking consumer job. The existing producer remains informational.
- **Immutable snapshots.** Resolve base/head to commits and use their Git
  blobs, not working-tree contents. Compare endpoints directly. Missing or
  all-zero bases are errors; never substitute `HEAD^` or a merge base.
- **One proven scope.** Consume the producer's schema-v1 coverage-summary.json.
  Validate XML SHA256, measured commit/source tree, clean/fresh provenance,
  successful test exit, scope integrity, exact optional suite label, and the
  whole pyproject.toml Git-blob hash at head. Require the producer's
  `xml_path_policy=checkout-tracked-v1` confinement capture; older unchecked
  summaries are unavailable, not successes. No second capture framework.
- **Authoritative executable lines.** Intersect new-side zero-context hunk
  lines with XML class/lines records; hits greater than zero mean covered.
  XML omissions inside a present file are non-executable/excluded, not misses.
  A changed file missing from the measurement is an error, not an exclusion.
- **Renames and scope boundaries.** Use NUL-delimited rename metadata and
  blob-to-blob diffs rather than parsing quoted patch filenames. Pure internal
  renames contribute no lines; edited renames use their destination. New files
  and files moved into production contribute all new-side lines. Deletions
  and moves out of production contribute none.
- **Portable paths.** Resolve relative XML filenames only through their
  declared XML source roots, and absolute filenames inside the recorded
  producer checkout; support Windows drives/UNC and POSIX independently of
  the consumer OS. Require unique Git-path resolution; no prefix fallback.
  Reject traversal, control characters, outside roots, ambiguous mappings,
  duplicate classes and case collisions for Windows-produced measurements.
- **Strict measurement data.** Reject malformed XML, duplicate/invalid line
  numbers, invalid hits, out-of-source line numbers, missing lines containers,
  mismatched XML/summary statement counts and incomplete provenance. An empty
  lines container is valid. Unsupported binary/symlink Python files are errors.
- **Exact comparison.** Compare integer counts to an exact rational minimum.
  Display rounding cannot turn a shortfall into a pass.
- **No vacuous score.** Empty/deleted/renamed/comment-only/excluded-only diffs
  report `not_applicable`, null percentage and null threshold result. Input
  validation still applies. Never label them 100% covered.

## Invariants

1. Production scope is head-side `src/tradinglab/**/*.py` only.
2. Unchanged executable lines never enter the changed-line denominator.
3. Coverage from different test scopes is never merged. The required CI
   invocation pins `--expected-suite unit-scanner-logic-oracles-v1`.
4. Exit 0 means a valid result, advisory shortfall, or explicit N/A.
5. Exit 1 means a valid nonempty measurement below an enforced minimum.
6. Exit 2 means invalid usage, Git, XML, provenance, paths or output. Report-only
   mode never hides measurement errors.
7. Optional UTF-8 JSON output has schema version 1, metric
   `changed_executable_lines`, measured/N/A/error status, counts, missed
   locations, nullable percentage/threshold result and measurement scope.
8. Global/file line-rate, branch-rate and combined percentages never determine
   the score. Changed-line branch coverage is explicitly not measured.

## Testing

`tests/unit/test_changed_coverage.py` covers temporary Git histories, scope
boundaries, Windows/XML paths, exclusions, malformed/stale/missing measurements,
exact threshold boundaries, CLI exits and real coverage.py XML generation.
Coordinator-owned `test_changed_coverage_workflow.py` pins CI wiring separately.

## Known limitations / Future work

- Local raw coverage XML alone is not supported gate evidence. The shared
  producer requires genuine Actions run context; it has no local capture mode.
  A local consumer may use matching CI-downloaded XML/provenance and the
  corresponding Git head. Do not synthesize hosted run identity outside tests.
- A changed multiline continuation line not listed by coverage.py does not
  count. The checker does not guess a parent statement using a second parser.
- Provenance protects against accidental stale/incomplete measurements, not
  forged metadata or malicious test processes.
- A push-to-main CI failure occurs after landing, not before a PR merge. No
  pre-push hook is installed. Scheduled/manual comparisons need an explicit
  base; the automatic gate runs only on push/PR events.
- Coverage is execution evidence, not proof of meaningful assertions. Existing
  tests, smoke, oracle, performance and spec gates remain independent.

## Recent history

- 2026-09-07: Add configurable changed-executable-line checking and explicit
  70% enforcement contract using the shared coverage measurement provenance.
