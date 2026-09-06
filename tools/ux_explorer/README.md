# TradingLab UX Explorer

This harness coordinates screenshot-driven exploratory testing of the frozen
Windows application. It is deliberately separate from `tests/smoke`: explorers
must operate through visible windows, mouse and keyboard input, not imports or
private application state.

## Safety model

- The project extension launches only the current checkout's verified
  `dist\TradingLab\TradingLab.exe`.
- Foreground input mode is the default for full sweeps: it uses guarded
  `SendInput` after a verified foreground handoff to the selected TradingLab
  HWND/PID. The target point must hit-test back to that process/root. Native
  menu popups must belong to TradingLab's active foreground menu loop. If
  handoff fails or focus changes during an action, input fails closed.
- Message input mode remains an explicit, limited option for scoped probes that
  should use process-addressed Win32 messages instead of foreground input.
- Screenshots are restricted to `PrintWindow` captures of windows owned by the
  TradingLab process; the driver never captures the desktop.
- Every run uses a fresh empty `TRADINGLAB_DATA_DIR` under `_ux_explorer\` and
  a sanitized environment built from a small system allowlist.
- A named Windows mutex permits one desktop-driving run per interactive
  Windows session at a time. Campaign edits use OS file locks.
- Real credentials are forbidden. Closing uses `WM_CLOSE`, never a process kill.
- Traces, screenshots and findings remain under the gitignored
  `_ux_explorer\` directory.

The driver is an input guard, **not an operating-system sandbox**. Do not open
external applications, submit real credentials, or select file destinations
outside the isolated run directory. Screenshot rendering uses `PrintWindow`;
occlusion and compositor-only artifacts still need human confirmation.

## Campaign workflow

```powershell
python -m tools.ux_explorer validate
python -m tools.ux_explorer init --limit 5
python -m tools.ux_explorer claim <campaign-directory>
python -m tools.ux_explorer prompt <campaign-directory> `
  --exe C:\path\to\dist\TradingLab\TradingLab.exe
python -m tools.ux_explorer complete <campaign-directory> `
  --scenario ux-startup-shell --lease <lease-id> --outcome passed `
  --run-dir _ux_explorer\<run-id> `
  --visited-surface TL.TOOLBAR --visited-surface TL.TABS
python -m tools.ux_explorer summary <campaign-directory>
python -m tools.ux_explorer report <campaign-directory>
```

Give the generated prompt to one Copilot sub-agent using the current worktree
and its loaded UX tools. The coordinator runs missions sequentially and records
completion using the returned lease and run directory. Separate worktree
sessions need their own build; pointing at another checkout's executable is
deliberately rejected. The explorer must record findings before source
inspection; fixes belong in a separate coding turn.

Campaigns are randomized by default. Their seed and pinned catalog digest are
stored for provenance, but the explorer's choices remain open-ended and based
on visible application behavior. A claimed scenario receives a lease lasting
its declared time budget plus 15 minutes; abandoned child sessions are
automatically released after that interval. Exploration prompts cap automatic
runs at 400 UX-tool actions and 45 minutes unless a scenario budget is lower.
Explorers should choose the default foreground input mode for broad coverage and
report an honest blocked result if Windows remains locked or foreground focus
cannot be secured.

Completion is intentionally evidence-based:

- `passed` and `findings` outcomes require a UX run directory containing the
  native extension's `manifest.json`, `trace.jsonl`, and completed `summary.json`
  with matching campaign/scenario identity and `stopped=true`.
- `findings` also requires valid `findings.jsonl`; malformed lines are errors.
- Coverage is never inferred from assigned scenario surfaces. Pass one
  `--visited-surface` flag for each catalog surface actually reached through
  visible GUI interaction and screenshots; visited coverage requires a run
  directory.
- `blocked` and `aborted` can be completed without run evidence, but they do
  not add visited coverage. For example, a blocked keyboard-focus scenario is
  not counted visited merely because the scenario assigned keyboard surfaces.

The `report` command prints Markdown by default (or writes `--format html` /
`--output <path>`). It lists visited and unvisited selected surfaces, pending
and blocked scenarios, run evidence, finding IDs, and screenshot paths. The
51 committed surfaces are a planning catalog, not a claim that every possible
TradingLab UI state exists or was exhaustively covered.

## Current limitations

This is an agent-driven campaign planner. It creates missions, leases work, and
records evidence, but it is not a standalone autonomous bot unless a Copilot
session runs the generated prompt and calls the UX tools.

The current native driver uses guarded foreground `SendInput` by default, with
process-addressed Win32 message input available only as an explicit limited
mode. It supports pointer move and drag exploration through
`tradinglab_ux_pointer`, but it is still not full hardware mouse/keyboard
fidelity and may miss issues that depend on the real desktop compositor or
low-level input stack. In message mode, modifier chords are rejected because
`WM_KEYDOWN` does not faithfully affect another thread's modifier state in
message mode. Reports therefore use only recorded GUI evidence from the
extension, never application state or backend trading-test assertions.
