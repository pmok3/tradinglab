"""Synthetic (offline, deterministic) quote source.

The counterpart to :mod:`streaming.synthetic` for the quote axis, and
the reason the live heatmap path is testable on a machine with no
brokerage credentials at all.

Two properties make it useful beyond tests:

* **One thread for the whole subscription**, not one per symbol. That is
  the defining shape of a quote source — the real adapters multiplex
  hundreds of symbols over a single connection — so exercising the
  consumer against a per-symbol fan-out would validate the wrong
  concurrency model.
* **It emits partial updates.** The first message for a symbol carries a
  full picture (last, previous close, day volume); every message after
  it carries only the fields that moved. Real vendors behave this way,
  and a consumer that quietly assumed full records would pass a test
  built on full records and then blank out in production.

Prices are a per-symbol seeded random walk, so a given symbol always
draws the same series.

See ``streaming/synthetic_quotes.spec.md``.
"""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Iterable, Sequence

from .quotes import Quote, QuoteCallback


def base_price(symbol: str) -> float:
    """Deterministic starting price for ``symbol``.

    Matches ``streaming.synthetic``'s convention so a symbol looks
    consistent across both axes.
    """
    rng = random.Random(hash((symbol, "quote")) & 0xFFFFFFFF)
    return 50.0 + rng.random() * 450.0


def synthetic_quote(symbol: str, step: int, *, now: float | None = None) -> Quote:
    """The ``step``-th quote for ``symbol``. Pure and deterministic.

    ``step == 0`` is the subscribe-time snapshot and populates every
    field; later steps report only ``last``/``day_volume``/``ts``, which
    is what forces consumers through the field-wise merge path.
    """
    ts = time.time() if now is None else now
    start = base_price(symbol)
    rng = random.Random(hash((symbol, "quote", step)) & 0xFFFFFFFF)
    drift = (rng.random() - 0.5) * 0.04
    last = round(max(0.01, start * (1.0 + drift)), 4)
    if step == 0:
        prev_close = round(max(0.01, start * (1.0 + (rng.random() - 0.5) * 0.02)), 4)
        return Quote(
            symbol=symbol,
            last=last,
            prev_close=prev_close,
            day_volume=float(rng.randint(100_000, 20_000_000)),
            day_high=round(last * 1.01, 4),
            day_low=round(last * 0.99, 4),
            ts=ts,
        )
    return Quote(
        symbol=symbol,
        last=last,
        day_volume=float(rng.randint(100_000, 20_000_000)),
        ts=ts,
    )


class _SyntheticSubscription:
    """One driver thread walking the whole symbol set."""

    def __init__(
        self,
        symbols: Sequence[str],
        on_quote: QuoteCallback,
        *,
        tick_period: float,
    ) -> None:
        self._on_quote = on_quote
        self._tick_period = max(0.001, float(tick_period))
        self._lock = threading.Lock()
        self._symbols = [s.strip().upper() for s in symbols if (s or "").strip()]
        self._seen: set[str] = set()
        self._step = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="synthetic-quotes", daemon=True
        )
        self._thread.start()

    def set_symbols(self, symbols: Iterable[str]) -> None:
        cleaned = [s.strip().upper() for s in symbols if (s or "").strip()]
        with self._lock:
            self._symbols = cleaned
            # A symbol that left and came back must re-send its full
            # snapshot; otherwise the consumer would merge price-only
            # updates onto nothing and never recover ``prev_close``.
            self._seen &= set(cleaned)

    def close(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                symbols = list(self._symbols)
                step = self._step
                self._step += 1
            for sym in symbols:
                if self._stop.is_set():
                    return
                with self._lock:
                    first = sym not in self._seen
                    self._seen.add(sym)
                try:
                    self._on_quote(synthetic_quote(sym, 0 if first else step))
                except Exception:  # noqa: BLE001 - a bad subscriber must not kill the source
                    pass
            self._stop.wait(self._tick_period)


class SyntheticQuoteSource:
    """Deterministic offline quote source."""

    def __init__(self, tick_period: float = 0.5) -> None:
        self._tick_period = tick_period

    def subscribe_quotes(
        self, symbols: Sequence[str], on_quote: QuoteCallback
    ) -> _SyntheticSubscription:
        return _SyntheticSubscription(
            symbols, on_quote, tick_period=self._tick_period
        )


__all__ = ("SyntheticQuoteSource", "base_price", "synthetic_quote")
