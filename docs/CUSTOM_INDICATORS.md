# Writing Custom Indicators

This guide walks you through creating your own custom indicators for
TradingLab. Custom indicators are standalone Python files that plug into
the same framework the built-in indicators use — they get auto-generated
dialog controls, live preview, persistence, and full scanner integration.

## Quick start

1. Open **Help → Reveal Data Folder** to find your TradingLab data
   directory.
2. Create an `indicators` subfolder if one does not exist.
3. Drop a `.py` file into that folder (see examples below).
4. Enable **Settings → Custom Indicators** and click
   **Indicators → Reload Custom**.
5. Your indicator appears in the **Add Indicator** dropdown.

The default indicator directory is:

| Platform | Path |
|----------|------|
| Windows  | `%LOCALAPPDATA%\TradingLab\indicators\` |
| macOS    | `~/Library/Application Support/TradingLab/indicators/` |
| Linux    | `~/.local/share/TradingLab/indicators/` |

---

## Indicator protocol

Every custom indicator is a Python class with a few required pieces.
TradingLab discovers your class when your plugin file calls
`register_indicator("Display Name", YourClass)`.

### Required class attributes

| Attribute | Type | Purpose |
|-----------|------|---------|
| `kind_id` | `str` | Stable identity for persistence (e.g. `"my_sma"`). Never change this after release. |
| `kind_version` | `int` | Schema version. Bump when you change `params_schema`. |
| `params_schema` | `tuple[ParamDef, ...]` | Declares the parameters the dialog auto-generates widgets for. |
| `default_style` | `dict[str, LineStyle]` | Default color/width per output key. |

### Required instance attributes

| Attribute | Type | Purpose |
|-----------|------|---------|
| `overlay` | `bool` | `True` = draw on the price axes. `False` = draw in a separate pane. |
| `name` | `str` | Display label shown on the chart (e.g. `"SMA(20)"`). |

### Required methods

```python
def __init__(self, **params):
    # Accept keyword arguments matching your params_schema.
    ...

def compute(self, candles: list) -> dict[str, np.ndarray]:
    # Return a dict mapping output names to NumPy arrays.
    # Each array must be the same length as candles.
    # Use NaN for undefined values (e.g. warmup period).
    ...
```

The faster `compute_arr(self, bars: Bars)` method is optional. If you
define it, the framework calls it instead of `compute` when `Bars` are
already available (saves a conversion step).

### ParamDef

Each entry in `params_schema` controls a dialog widget:

```python
ParamDef(name, kind, default, *, min=None, max=None, step=None,
         choices=None, description="")
```

| `kind` | Widget | Notes |
|--------|--------|-------|
| `"int"` | Spinbox | Use `min`, `max`, `step` |
| `"float"` | Spinbox | Use `min`, `max`, `step` |
| `"bool"` | Checkbox | `default` is `True` or `False` |
| `"str"` | Entry | Free-form text input |
| `"choice"` | Dropdown | Provide `choices=("A", "B", "C")` |

### LineStyle

Controls default appearance per output key:

```python
LineStyle(color="#2ca02c", width=1.4, visible=True)
```

---

## Security sandbox

Custom indicators run inside a restricted sandbox:

**Allowed imports:** `numpy`, `numpy.*`, `math`, `statistics`,
`collections`, `dataclasses`, `typing`, `functools`, `itertools`,
`operator`, `decimal`, `fractions`, `enum`.

**Blocked:** `os`, `sys`, `subprocess`, `pathlib`, `socket`, `http`,
`shutil`, and all other standard library / third-party modules.

**Blocked builtins:** `exec`, `eval`, `compile`, `open`, `__import__`
(replaced with a safe version), `globals`, `locals`, `vars`, `getattr`,
`setattr`, `delattr`, `input`, `breakpoint`.

**File size limit:** 256 KB per plugin file.

Your plugin receives `register_indicator` as a pre-injected function —
you do not need to import it.

---

## Examples

### Example 1 — Simple Moving Average (overlay, one parameter)

The simplest possible indicator. One parameter, one output line,
drawn on the price axes.

```python
import numpy as np

class SimpleSMA:
    kind_id = "custom_sma"
    kind_version = 1
    params_schema = (
        ParamDef("length", "int", default=20, min=2, max=500, step=1,
                 description="Length"),
    )
    default_style = {
        "sma": LineStyle(color="#1f77b4", width=1.4),
    }
    overlay = True

    def __init__(self, length=20):
        self.length = int(length)
        self.name = f"My SMA({self.length})"

    def compute(self, candles):
        closes = np.array([c.close for c in candles], dtype=np.float64)
        n = len(closes)
        out = np.full(n, np.nan)
        if n < self.length:
            return {"sma": out}
        cs = np.cumsum(closes)
        cs = np.insert(cs, 0, 0.0)
        L = self.length
        out[L - 1:] = (cs[L:] - cs[:n - L + 1]) / L
        return {"sma": out}

register_indicator("My SMA", SimpleSMA)
```

**Key points:**
- `overlay = True` places the line on the price chart.
- `kind_id` must be unique across all indicators (built-in and custom).
- The output dict key (`"sma"`) must match a key in `default_style`.
- The output array must be the same length as `candles`.

---

### Example 2 — Momentum oscillator (separate pane, reference lines)

A rate-of-change oscillator drawn in its own pane below the chart,
with a horizontal zero line.

```python
import numpy as np

class Momentum:
    kind_id = "custom_momentum"
    kind_version = 1
    params_schema = (
        ParamDef("length", "int", default=10, min=1, max=500, step=1,
                 description="Lookback"),
    )
    default_style = {
        "mom": LineStyle(color="#ff7f0e", width=1.4),
    }
    reference_levels = (0.0,)
    overlay = False

    def __init__(self, length=10):
        self.length = int(length)
        self.name = f"Momentum({self.length})"

    def compute(self, candles):
        closes = np.array([c.close for c in candles], dtype=np.float64)
        n = len(closes)
        out = np.full(n, np.nan)
        L = self.length
        if n > L:
            out[L:] = closes[L:] - closes[:n - L]
        return {"mom": out}

register_indicator("Momentum", Momentum)
```

**Key points:**
- `overlay = False` creates a dedicated pane below the chart.
- `reference_levels = (0.0,)` draws a horizontal guide line at zero.

---

### Example 3 — Multi-output indicator (Bollinger-style bands)

An indicator that outputs multiple lines sharing the same axes.

```python
import numpy as np

class DonchianChannels:
    kind_id = "custom_donchian"
    kind_version = 1
    params_schema = (
        ParamDef("length", "int", default=20, min=2, max=500, step=1,
                 description="Length"),
    )
    default_style = {
        "upper":  LineStyle(color="#2ca02c", width=1.0),
        "lower":  LineStyle(color="#d62728", width=1.0),
        "middle": LineStyle(color="#7f7f7f", width=1.0),
    }
    overlay = True

    def __init__(self, length=20):
        self.length = int(length)
        self.name = f"Donchian({self.length})"

    def compute(self, candles):
        highs = np.array([c.high for c in candles], dtype=np.float64)
        lows = np.array([c.low for c in candles], dtype=np.float64)
        n = len(candles)
        upper = np.full(n, np.nan)
        lower = np.full(n, np.nan)
        middle = np.full(n, np.nan)
        L = self.length
        if n >= L:
            from numpy.lib.stride_tricks import sliding_window_view
            upper[L - 1:] = sliding_window_view(highs, L).max(axis=1)
            lower[L - 1:] = sliding_window_view(lows, L).min(axis=1)
            middle[L - 1:] = (upper[L - 1:] + lower[L - 1:]) / 2.0
        return {"upper": upper, "lower": lower, "middle": middle}

register_indicator("Donchian Channels", DonchianChannels)
```

**Key points:**
- Multiple output keys — each gets its own color swatch in the dialog.
- Uses `sliding_window_view` from NumPy for efficient rolling max/min.
- All three arrays must be the same length as `candles`.

---

### Example 4 — Choice parameter (selectable moving average type)

An indicator with a dropdown parameter so the user can pick the
smoothing method.

```python
import numpy as np

class SmoothROC:
    """Rate of Change smoothed by a user-selectable moving average."""

    kind_id = "custom_smooth_roc"
    kind_version = 1
    params_schema = (
        ParamDef("roc_length", "int", default=12, min=1, max=500, step=1,
                 description="ROC period"),
        ParamDef("smooth_length", "int", default=3, min=1, max=100, step=1,
                 description="Smooth period"),
        ParamDef("ma_type", "choice", default="SMA",
                 choices=("SMA", "EMA"),
                 description="Smoothing"),
    )
    default_style = {
        "sroc": LineStyle(color="#9467bd", width=1.4),
    }
    reference_levels = (0.0,)
    overlay = False

    def __init__(self, roc_length=12, smooth_length=3, ma_type="SMA"):
        self.roc_length = int(roc_length)
        self.smooth_length = int(smooth_length)
        self.ma_type = str(ma_type)
        self.name = (f"SmoothROC({self.roc_length},"
                     f"{self.smooth_length},{self.ma_type})")

    def compute(self, candles):
        closes = np.array([c.close for c in candles], dtype=np.float64)
        n = len(closes)
        roc = np.full(n, np.nan)
        L = self.roc_length
        if n > L:
            prev = closes[:n - L]
            with np.errstate(divide="ignore", invalid="ignore"):
                roc[L:] = np.where(prev != 0, (closes[L:] - prev) / prev * 100, 0.0)
        # Apply smoothing
        result = self._smooth(roc, self.smooth_length)
        return {"sroc": result}

    def _smooth(self, arr, length):
        """Simple SMA or EMA over an array with NaN handling."""
        out = np.full_like(arr, np.nan)
        if length < 1:
            return arr.copy()
        # Find first finite value
        finite_mask = np.isfinite(arr)
        indices = np.flatnonzero(finite_mask)
        if indices.size == 0:
            return out
        first = int(indices[0])
        cleaned = np.where(finite_mask, arr, 0.0)
        if self.ma_type == "EMA":
            alpha = 2.0 / (length + 1.0)
            seed_end = first + length
            if seed_end > arr.size:
                return out
            seed = float(cleaned[first:seed_end].mean())
            out[seed_end - 1] = seed
            prev = seed
            for i in range(seed_end, arr.size):
                v = float(cleaned[i])
                prev = alpha * v + (1.0 - alpha) * prev
                out[i] = prev
        else:
            cs = np.cumsum(cleaned)
            cs = np.insert(cs, 0, 0.0)
            seed_end = first + length
            if seed_end > arr.size:
                return out
            for i in range(seed_end - 1, arr.size):
                out[i] = (cs[i + 1] - cs[i + 1 - length]) / length
        return out

register_indicator("Smooth ROC", SmoothROC)
```

**Key points:**
- The `"choice"` param kind renders as a dropdown in the dialog.
- The `choices` tuple defines the available options.
- The `__init__` signature must accept all `params_schema` names as kwargs.

---

### Example 5 — Volume-based indicator with boolean toggle

A volume z-score indicator with a toggle for cumulative mode.

```python
import numpy as np

class VolumeZScore:
    """Z-score of volume relative to a rolling lookback window."""

    kind_id = "custom_vol_zscore"
    kind_version = 1
    params_schema = (
        ParamDef("length", "int", default=20, min=5, max=500, step=1,
                 description="Lookback"),
        ParamDef("cumulative", "bool", default=False,
                 description="Cumulative"),
    )
    default_style = {
        "zscore": LineStyle(color="#17becf", width=1.4),
    }
    reference_levels = (-2.0, 0.0, 2.0)
    overlay = False

    def __init__(self, length=20, cumulative=False):
        self.length = int(length)
        self.cumulative = bool(cumulative)
        mode = "cum" if self.cumulative else str(self.length)
        self.name = f"VolZ({mode})"

    def compute(self, candles):
        vol = np.array([c.volume for c in candles], dtype=np.float64)
        n = len(vol)
        out = np.full(n, np.nan)
        if self.cumulative:
            for i in range(1, n):
                window = vol[:i + 1]
                mu = window.mean()
                sigma = window.std()
                if sigma > 0:
                    out[i] = (vol[i] - mu) / sigma
        else:
            L = self.length
            if n >= L:
                from numpy.lib.stride_tricks import sliding_window_view
                windows = sliding_window_view(vol, L)
                mu = windows.mean(axis=1)
                sigma = windows.std(axis=1)
                with np.errstate(divide="ignore", invalid="ignore"):
                    out[L - 1:] = np.where(
                        sigma > 0, (vol[L - 1:] - mu) / sigma, 0.0)
        return {"zscore": out}

register_indicator("Volume Z-Score", VolumeZScore)
```

**Key points:**
- The `"bool"` param kind renders as a checkbox.
- The rolling path uses vectorized NumPy; the cumulative path uses a
  loop (expanding window is inherently sequential).
- `reference_levels` draws three guide lines in the pane.

---

### Example 6 — Interval-restricted indicator

An indicator that only makes sense on intraday charts.

```python
import numpy as np

class IntradayRange:
    """Cumulative intraday range as a percentage of the open."""

    kind_id = "custom_intraday_range"
    kind_version = 1
    params_schema = ()
    default_style = {
        "range_pct": LineStyle(color="#e377c2", width=1.4),
    }
    available_intervals = ("1m", "2m", "5m", "15m", "30m", "1h")
    overlay = False

    def __init__(self):
        self.name = "Intraday Range %"

    def compute(self, candles):
        n = len(candles)
        out = np.full(n, np.nan)
        if n == 0:
            return {"range_pct": out}
        opens = np.array([c.open for c in candles], dtype=np.float64)
        highs = np.array([c.high for c in candles], dtype=np.float64)
        lows = np.array([c.low for c in candles], dtype=np.float64)
        # Use the first bar's open as the session anchor
        session_open = opens[0]
        if session_open > 0:
            session_high = np.maximum.accumulate(highs)
            session_low = np.minimum.accumulate(lows)
            out[:] = (session_high - session_low) / session_open * 100.0
        return {"range_pct": out}

register_indicator("Intraday Range %", IntradayRange)
```

**Key points:**
- `available_intervals` restricts which chart intervals show this
  indicator. It is automatically hidden on daily/weekly/monthly charts.
- `params_schema = ()` means no configurable parameters — the dialog
  shows just the kind dropdown and scope checkboxes.

---

## Candle object reference

Each candle in the `candles` list has these attributes:

| Attribute | Type | Description |
|-----------|------|-------------|
| `date` | `datetime` | Bar timestamp |
| `open` | `float` | Open price |
| `high` | `float` | High price |
| `low` | `float` | Low price |
| `close` | `float` | Close price |
| `volume` | `float` | Volume |
| `session` | `str` | `"regular"`, `"pre"`, or `"post"` |
| `is_gap` | `bool` | `True` for gap-placeholder candles (NaN OHLC) |

**Tip:** For better performance, extract arrays once at the top of
`compute` rather than accessing attributes in a loop:

```python
closes = np.array([c.close for c in candles], dtype=np.float64)
```

Or implement `compute_arr(self, bars)` which receives pre-extracted
NumPy arrays via the `Bars` object (`bars.open`, `bars.high`,
`bars.low`, `bars.close`, `bars.volume`).

---

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| Indicator doesn't appear | `register_indicator` not called | Add it at the bottom of your file |
| `ImportError` on import | Module not in the allowlist | Use only allowed imports (see Security sandbox) |
| `NameError: exec` | Blocked builtin | `exec`/`eval` are blocked for security |
| Dialog shows wrong widget | `kind` mismatch in `ParamDef` | Use `"int"`, `"float"`, `"bool"`, `"str"`, or `"choice"` |
| Line not visible | Output key not in `default_style` | Keys in `compute` return dict must match `default_style` keys |
| Chart crashes on load | `compute` returns wrong-length array | Ensure every output array has `len(candles)` elements |
| Indicator not on some intervals | `available_intervals` set | Remove it to show on all intervals, or add the missing ones |

## Tips

- **Start simple.** Copy Example 1, change the `kind_id`, rename the
  class and `register_indicator` call, and iterate from there.
- **Use NaN for warmup.** The first `length - 1` bars of any windowed
  indicator should be `np.nan` — the renderer skips NaN gracefully.
- **Test locally.** You can test your `compute` function outside
  TradingLab by constructing candle-like objects and calling it
  directly.
- **Check the status bar.** After Reload Custom, the status bar shows
  which files loaded successfully and which errored.
- **Keep `kind_id` stable.** Once you save a config with your indicator,
  changing `kind_id` orphans the saved config (it becomes an "Unknown
  indicator" placeholder).
