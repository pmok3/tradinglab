# Local Data (BYOD) Guide

TradingLab can read and write its own normalized OHLCV bars to CSV files
on disk. This is the **Bring Your Own Data** feature — useful for
sharing datasets between machines, backing up cache contents before a
fresh install, or feeding the app with private/proprietary bars that
none of the built-in fetchers can reach.

There are two complementary flows:

- **Import** — point the app at one or more folders ("roots"). Each
  subfolder of each root becomes a new entry in the source-selector
  combobox.
- **Export** — dump the current disk cache to a destination folder,
  cherry-picking exactly which `(source, ticker, interval)` tuples to
  include.

Round-trip is lossless when the CSV files follow the canonical schema
described below.

---

## Quick start

1. Open **Tools → Export Bars to CSV…**, pick a destination folder, and
   click **Export** to dump the disk cache to disk.
2. Copy that folder to a second machine (or just keep it as a backup).
3. On the destination machine, open **Tools → Configure Local Data…**,
   tick **Enable**, click **Add**, name the root (e.g. `share-2024-11`)
   and point it at the folder. Click **Save and Close**.
4. The toolbar source combobox now contains entries like
   `share-2024-11-yfinance`, `share-2024-11-polygon`, … one per
   subfolder. Pick one and load a ticker exactly like any other source.

---

## Directory layout

A "root" is a folder containing one or more **source subfolders**.
Each source subfolder contains flat CSV files named
`<TICKER>_<INTERVAL>.csv`.

```
<root>/
    yfinance/
        AAPL_5m.csv
        AAPL_1d.csv
        SPY_5m.csv
    polygon/
        AAPL_5m.csv
        ES_1m.csv
    alpaca/
        QQQ_1m.csv
```

Each subfolder of the root produces one combobox entry, named
`<root_name>-<subfolder>`. With the layout above and a root named
`share-2024-11`, you get:

- `share-2024-11-yfinance`
- `share-2024-11-polygon`
- `share-2024-11-alpaca`

Files inside a subfolder are flat (no nested folders). The filename
**is** the lookup key: when the toolbar requests `AAPL` at `5m` from
`share-2024-11-yfinance`, the app reads `<root>/yfinance/AAPL_5m.csv`.

The interval token is whatever string your toolbar combobox produces —
`1m`, `5m`, `15m`, `1h`, `1d`, etc. There is no built-in allow-list;
mirror the upstream source's interval names exactly.

---

## CSV schema

The schema is **strict by design**. Files that don't match are rejected
with an error in the status bar pointing back to this document.

### Canonical example

```
timestamp,open,high,low,close,volume
2024-03-15T09:30:00-04:00,172.50,172.85,172.31,172.62,1245300
2024-03-15T09:35:00-04:00,172.62,172.94,172.45,172.78,987210
2024-03-15T09:40:00-04:00,172.78,173.05,172.60,172.91,856420
```

### Rules

1. **Header row required** — exact lowercase
   `timestamp,open,high,low,close,volume`. No aliases (no `Date`,
   `Time`, `Open Price`, `vol`, etc.).
2. **Timestamps must include timezone** — ISO-8601 with an explicit
   offset (`-04:00`, `+09:30`, or `Z`). Naive timestamps like
   `2024-03-15 09:30:00` are rejected.
3. **OHLC values** — numeric, finite, non-negative.
4. **Volume** — integer; blank or `NaN` is treated as `0`.
5. **Row order** — any order. The loader sorts by timestamp ascending.
6. **Duplicate timestamps** — the first row wins; subsequent
   duplicates are dropped with a warning.

### Things that will be rejected

```
# ❌ Wrong header case
Timestamp,Open,High,Low,Close,Volume
2024-03-15T09:30:00-04:00,172.50,172.85,172.31,172.62,1245300

# ❌ Header alias
date,o,h,l,c,v
2024-03-15T09:30:00-04:00,172.50,172.85,172.31,172.62,1245300

# ❌ Naive timestamp (no tz offset)
timestamp,open,high,low,close,volume
2024-03-15 09:30:00,172.50,172.85,172.31,172.62,1245300

# ❌ Negative price
timestamp,open,high,low,close,volume
2024-03-15T09:30:00-04:00,-172.50,172.85,172.31,172.62,1245300
```

---

## Configuring import roots

**Tools → Configure Local Data…** opens the dialog.

| Control | Purpose |
|---|---|
| **Enable** checkbox | Master switch for BYOD discovery on app start. When off, the registered local sources do not appear in the combobox. |
| **Add** | Prompt for a root name + folder, then add a row. |
| **Edit** | Change the name/path of the selected row. |
| **Remove** | Delete the selected row. |
| **Save and Close** | Persist settings, re-discover sources, refresh the toolbar combobox. |
| **Cancel** | Discard pending edits without changing anything. |

### Root naming rules

Root names must be alphanumerics + underscores only — no hyphens, no
spaces, no dots. Hyphens are reserved as the separator between root
name and subfolder name in the combobox entry.

Good: `share_2024_11`, `archive`, `prop_data`.
Bad: `share-2024-11`, `my data`, `archive.v2`.

### What happens on save

1. Settings are written to `<app_data>/settings.json` under the
   `local_data` key.
2. The data-source registry is rebuilt: built-in sources first, then
   each enabled root's subfolders. Any source name registered by a
   built-in is **not** overridden (built-in always wins).
3. Each newly-registered BYOD source is **opted out of the disk
   cache** so CSV files on disk remain the source of truth — no stale
   pickle copies accumulate alongside your CSVs.
4. The toolbar combobox refreshes immediately.

Built-in source names you can't shadow:
`yfinance`, `synthetic`, `synthetic-stream`, `schwab`, `alpaca`,
`polygon`.

---

## Exporting cache to CSV

**Tools → Export Bars to CSV…** opens the export dialog.

The dialog shows every `(source, ticker, interval)` currently in the
disk cache as a checkbox row, grouped by source. By default everything
is checked.

| Control | Purpose |
|---|---|
| **Destination** | Folder picker. Files write to `<destination>/<SOURCE>/<TICKER>_<INTERVAL>.csv`. |
| **Select All** / **Select None** | Toggle every row at once. |
| Row checkbox | Click the leftmost cell of a row to toggle it. |
| **Export** | Write selected rows to the destination. Writes are atomic (temp file → `os.replace`), so a crash mid-export will not leave a half-written CSV. |
| **Cancel** | Close the dialog without writing anything. |

Existing files at the destination are **overwritten** without prompt.
Pick a fresh folder if you don't want to clobber prior exports.

---

## Round-trip workflow

A common pattern is sharing your local cache between machines:

1. On the **source** machine, open **Tools → Export Bars to CSV…**,
   pick `D:\share\2024-11`, select what to share, click **Export**.
2. Copy `D:\share\2024-11` to the **destination** machine (USB,
   OneDrive, wherever).
3. On the destination, open **Tools → Configure Local Data…**, click
   **Add**, name it `share_2024_11`, point at the folder, **Save and
   Close**.
4. The toolbar now exposes `share_2024_11-yfinance`,
   `share_2024_11-polygon`, etc. — load any ticker exactly like any
   other source.

The data round-trips losslessly: the same OHLCV values that produced
the export show up on the destination chart.

---

## Errors and what they mean

All BYOD errors surface in the status bar prefixed with `local:` and
the failing `<ticker>/<interval>`. Common ones:

| Status message | Cause | Fix |
|---|---|---|
| `local: AAPL/5m: file not found` | No `AAPL_5m.csv` in the resolved source folder. | Check the filename — case matters on Linux/macOS. |
| `local: AAPL/5m: header row missing or mismatched` | First row is not exactly `timestamp,open,high,low,close,volume`. | Fix the header. Lowercase, no aliases. |
| `local: AAPL/5m: timestamp at row N is not tz-aware ISO-8601` | A row's timestamp lacks an offset, or isn't parseable. | Re-emit with `-04:00` / `+00:00` / `Z`. |
| `local: AAPL/5m: ohlc value at row N is invalid` | Negative, non-numeric, infinite, or NaN price. | Clean the source data. |
| `Configure Local Data: root name contains illegal characters` | Hyphens, spaces, or punctuation in the name field. | Use alphanumerics + underscores only. |

---

## Limitations

These are intentional simplifications, not bugs:

- **No live updates.** Files are read on app start (or on save in the
  Configure dialog). Edit a CSV mid-session and you have to restart
  TradingLab to pick up the change.
- **No schema migration.** Only the strict canonical schema is
  accepted. Convert from your tool of choice (yfinance, pandas, R,
  etc.) before pointing TradingLab at the folder.
- **CSV-only.** No Parquet, no JSON, no databases. CSV is plain-text
  and trivially diffable, which matches the file-shipping use case.
- **No corporate-action adjustment.** We round-trip what the upstream
  source produced. If your CSV is unadjusted, the chart is unadjusted.
- **Immutable within a session.** Local sources participate in the
  in-memory LRU cache but bypass the on-disk pickle cache so your CSVs
  remain the only persistent storage.

---

## Related

- `docs/SPEC_INDEX.md` — full module/spec index, including
  `data/local_source.spec.md`, `data/local_export.spec.md`,
  `gui/local_data_dialog.spec.md`, `gui/export_cache_dialog.spec.md`.
- `docs/ONBOARDING.md` — onboarding overview and tour of built-in
  sources.
