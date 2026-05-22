"""yfinance-backed historical data source."""

from __future__ import annotations

from ..constants import INTERVAL_PERIODS, is_intraday
from ..models import Candle
from .normalize import candles_from_dataframe


def fetch_live_data(ticker: str = "AMD", interval: str = "1d") -> list[Candle] | None:
    """Fetch OHLCV history for ``ticker`` at ``interval`` via yfinance.

    For intraday intervals we request ``prepost=True`` so pre- and
    post-market bars are included; session tagging is delegated to
    :func:`candles_from_dataframe` which classifies each bar's
    hour/minute against US Eastern exchange hours.

    Uses the vectorized ``candles_from_dataframe`` normalizer rather
    than ``df.iterrows()``: on typical intraday fetches (~5k bars) this
    is 5–20× faster because iterrows materializes a fresh ``Series``
    per row. The normalizer also stashes the extracted numpy arrays so
    the subsequent ``_SeriesArrays`` build skips a redundant extraction
    pass.

    Returns ``None`` on any failure (import error, network, empty frame).
    """
    try:
        import yfinance as yf  # type: ignore
    except ImportError:
        return None
    period = INTERVAL_PERIODS.get(interval, "2y")
    intraday = is_intraday(interval)
    try:
        df = yf.Ticker(ticker).history(
            period=period, interval=interval, prepost=intraday,
        )
        if df.empty:
            return None
        return candles_from_dataframe(df, interval=interval)
    except Exception as e:  # noqa: BLE001
        # Stateless module — no _status here. Caller's higher level will
        # log via _status when handling the None return.
        print(f"Live fetch failed: {e}")
        return None
