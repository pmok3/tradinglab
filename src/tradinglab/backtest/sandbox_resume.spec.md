# `sandbox_resume.py` — last-session metadata persistence (Feature B)

## Purpose

When a sandbox session is active and the user closes the app cleanly,
drop a small JSON file under `app_data_dir()` so the **next** launch
can ask "Resume previous sandbox session?". NOT a full session
save/load — that lives in `backtest/persistence.py`.

## Public API

```python
RESUME_FILE_FORMAT  = "tradinglab-sandbox-resume"
RESUME_FILE_VERSION = 1
RESUME_FILE_NAME    = "sandbox_last.json"

@dataclass(frozen=True)
class SandboxResumeMetadata:
    saved_at: str
    session_id: str
    ticker: str
    interval: str
    bars_processed: int
    engine_version: str
    spec_dict: Dict[str, Any]

    def to_dict() -> Dict[str, Any]
    @classmethod
    def from_dict(cls, payload) -> "SandboxResumeMetadata"
    def short_description() -> str

def resume_metadata_path() -> Path
def write_resume_metadata(meta) -> None
def read_resume_metadata() -> Optional[SandboxResumeMetadata]
def clear_resume_metadata() -> None
def build_metadata_from_session(*, session_id, ticker, interval, bars_processed, spec_dict, engine_version=None, saved_at=None) -> SandboxResumeMetadata
def now_iso() -> str
```

## On-disk format

```json
{
  "format":          "tradinglab-sandbox-resume",
  "version":         1,
  "saved_at":        "2026-04-30T12:34:56",
  "session_id":      "sandbox-aapl-5m-...",
  "ticker":          "AAPL",
  "interval":        "5m",
  "bars_processed":  14,
  "engine_version":  "sandbox-1d",
  "spec":            { …SessionSpec.to_dict()… }
}
```

Location: `<app_data_dir>/sandbox_last.json` — next to `settings.json`,
**not** inside `cache/` (so clearing the candle cache doesn't wipe
resume state).

## Invariants

- All writes are atomic (tempfile + `os.replace`).
- Reads are tolerant: corrupt file / missing keys / version mismatch
  → `None`. Corrupt file is **not** auto-deleted (preserved for future
  migration inspection).
- Engine-version mismatch → returns `None`; file preserved on disk.
- `clear_resume_metadata()` is idempotent.

## Wiring (in `app.py`)

```python
# ChartApp._on_close, BEFORE self.destroy():
if self._sandbox is not None and self._sandbox.active:
    try:
        from .backtest.sandbox_resume import (
            build_metadata_from_session, write_resume_metadata,
        )
        spec = self._sandbox.engine.spec
        meta = build_metadata_from_session(
            session_id=self._sandbox.session_id or "",
            ticker=(spec.tickers[0] if spec.tickers else ""),
            interval=getattr(self._sandbox, "interval", ""),
            bars_processed=int(getattr(
                self._sandbox.engine, "bars_processed", 0) or 0),
            spec_dict=spec.to_dict(),
        )
        write_resume_metadata(meta)
    except Exception:  # noqa: BLE001
        pass

# ChartApp.__init__, AFTER _load_data:
self.after_idle(self._maybe_prompt_sandbox_resume)
```
