"""Tests for the SEC EDGAR shares provider and the shares-source registry.

Two things are pinned here.

**EDGAR parsing.** The provider exists because a price vendor's
fundamentals feed could not be trusted: yfinance's `get_shares_full`
interleaves as-reported and already-split-adjusted values, sometimes
several for one date and sometimes double-adjusted, and it never says
when a number became public. EDGAR gives one clean fact per filing with
both `end` (the date it describes) and `filed` (when it became public).
The parser must preserve that, prefer amendments over the filings they
supersede, and never raise on a malformed payload.

**The indirection.** The concrete vendor is not hardcoded anywhere: it
is registered under a name and selected by the `shares_data_source`
tunable, resolved by a higher-level caller. An unknown name must degrade
to "knows nothing" rather than silently substituting a different source.

Every network call is injected, so nothing here touches the SEC.
"""

from __future__ import annotations

import datetime as _dt

import pytest

from tradinglab.data import edgar_shares as E
from tradinglab.data import shares_sources as S


def _epoch(y: int, m: int, d: int) -> int:
    return int(_dt.datetime(y, m, d, tzinfo=_dt.timezone.utc).timestamp())


# A trimmed but faithful companyconcept payload (shape copied from the
# live API: units -> "shares" -> list of facts).
_PAYLOAD = {
    "cik": 320193,
    "units": {
        "shares": [
            {"end": "2020-04-17", "filed": "2020-05-01", "val": 4334335000,
             "form": "10-Q"},
            {"end": "2020-07-17", "filed": "2020-07-31", "val": 4275634000,
             "form": "10-Q"},
            {"end": "2020-10-16", "filed": "2020-10-30", "val": 17001802000,
             "form": "10-K"},
        ]
    },
}


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_parses_facts_with_both_dates() -> None:
    facts = E.parse_company_concept(_PAYLOAD)
    assert [f.shares for f in facts] == [4334335000.0, 4275634000.0, 17001802000.0]
    assert facts[0].as_of_ts == _epoch(2020, 4, 17)
    assert facts[0].filed_ts == _epoch(2020, 5, 1)
    assert facts[0].filed_ts > facts[0].as_of_ts, (
        "a filing is always after the date it describes — that gap is the "
        "look-ahead window the filed date exists to close"
    )


def test_facts_are_ascending_by_as_of() -> None:
    shuffled = {"units": {"shares": list(reversed(_PAYLOAD["units"]["shares"]))}}
    facts = E.parse_company_concept(shuffled)
    assert [f.as_of_ts for f in facts] == sorted(f.as_of_ts for f in facts)


def test_amendment_supersedes_the_original_filing() -> None:
    """A 10-K/A restates the same period; only the latest filing counts."""
    payload = {"units": {"shares": [
        {"end": "2020-02-07", "filed": "2020-02-13", "val": 181000000},
        {"end": "2020-02-07", "filed": "2020-04-28", "val": 181300000},
    ]}}
    facts = E.parse_company_concept(payload)
    assert len(facts) == 1
    assert facts[0].shares == 181300000.0
    assert facts[0].filed_ts == _epoch(2020, 4, 28)


@pytest.mark.parametrize("payload", [
    None, {}, {"units": None}, {"units": {}}, {"units": {"shares": None}},
    {"units": {"shares": [None, 5, "x"]}}, "not-a-dict", [],
])
def test_malformed_payloads_yield_nothing_and_never_raise(payload) -> None:
    assert E.parse_company_concept(payload) == []


def test_bad_rows_are_skipped_individually() -> None:
    payload = {"units": {"shares": [
        {"end": "2020-04-17", "filed": "2020-05-01", "val": 100},
        {"end": "nonsense", "filed": "2020-05-01", "val": 200},
        {"end": "2020-07-17", "filed": None, "val": 300},
        {"end": "2020-07-17", "filed": "2020-07-31", "val": 0},       # nonpositive
        {"end": "2020-10-16", "filed": "2020-10-30", "val": "abc"},   # unparseable
        {"end": "2021-01-15", "filed": "2021-01-29", "val": 400},
    ]}}
    assert [f.shares for f in E.parse_company_concept(payload)] == [100.0, 400.0]


def test_ticker_map_parses_and_dot_munges() -> None:
    payload = {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple"},
               "1": {"cik_str": 1067983, "ticker": "BRK.B", "title": "Berkshire"},
               "2": {"cik_str": None, "ticker": "BAD", "title": "Bad"}}
    m = E.parse_ticker_map(payload)
    assert m["AAPL"] == 320193
    assert m["BRK-B"] == 1067983, "must match the app's dash form"
    assert "BAD" not in m
    assert E.parse_ticker_map(None) == {}


# ---------------------------------------------------------------------------
# Fetcher wiring
# ---------------------------------------------------------------------------


def test_fetcher_uses_the_injected_cik_and_never_looks_one_up() -> None:
    urls: list[str] = []

    def fake(url: str):
        urls.append(url)
        return _PAYLOAD

    f = E.EdgarSharesFetcher(url_fetcher=fake, cik_lookup=lambda _s: 320193)
    facts = f("AAPL")
    assert len(facts) == 3
    assert len(urls) == 1, "a shipped CIK must not cost a ticker lookup"
    assert "CIK0000320193" in urls[0]


def test_fetcher_falls_back_to_the_sec_ticker_map() -> None:
    calls: list[str] = []

    def fake(url: str):
        calls.append(url)
        if "company_tickers" in url:
            return {"0": {"cik_str": 1318605, "ticker": "TSLA"}}
        return _PAYLOAD

    f = E.EdgarSharesFetcher(url_fetcher=fake, cik_lookup=lambda _s: None)
    assert len(f("TSLA")) == 3
    assert any("company_tickers" in u for u in calls)
    # the map is cached in-process
    f("TSLA")
    assert sum("company_tickers" in u for u in calls) == 1


def test_unknown_symbol_returns_nothing() -> None:
    f = E.EdgarSharesFetcher(
        url_fetcher=lambda _u: {}, cik_lookup=lambda _s: None
    )
    assert f("NOSUCH") == []


def test_network_failure_degrades_to_empty() -> None:
    def boom(_url: str):
        raise OSError("sec unreachable")

    f = E.EdgarSharesFetcher(url_fetcher=boom, cik_lookup=lambda _s: 320193)
    assert f("AAPL") == [], "an outage must not raise into the render path"


def test_rate_limiter_keeps_requests_inside_the_sec_budget() -> None:
    """Throttling lives at the fetcher, so concurrency can't outrun it.

    SEC asks for <=10 req/s; priming a 500-name universe on a worker
    pool would otherwise burst well past that.
    """
    import threading
    import time

    E._next_allowed_at = 0.0
    stamps: list[float] = []
    lock = threading.Lock()

    def hit() -> None:
        E._throttle()
        with lock:
            stamps.append(time.monotonic())

    threads = [threading.Thread(target=hit) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    stamps.sort()
    span = stamps[-1] - stamps[0]
    rate = (len(stamps) - 1) / span if span > 0 else float("inf")
    assert rate <= E.MAX_REQUESTS_PER_SECOND * 1.35, (
        f"observed {rate:.1f} req/s against a {E.MAX_REQUESTS_PER_SECOND} cap"
    )


def test_user_agent_identifies_the_app_and_a_contact() -> None:
    """SEC requires a contact address; their WAF 403s without one.

    It also rejects any agent containing ``github.com``, which rules out
    a repo URL or a noreply address — hence a real mailbox, overridable
    via the ``sec_user_agent`` tunable.
    """
    ua = E.USER_AGENT
    assert "tradinglab" in ua.lower()
    assert "@" in ua, "SEC rejects a UA with no contact address"
    assert "github.com" not in ua.lower(), "SEC's WAF 403s these"


def test_user_agent_is_overridable(monkeypatch) -> None:
    import tradinglab.defaults as _defaults

    monkeypatch.setattr(_defaults, "get", lambda k: "Me me@example.com")
    assert E.user_agent() == "Me me@example.com"
    monkeypatch.setattr(_defaults, "get", lambda k: "")
    assert E.user_agent() == E.USER_AGENT


def test_user_agent_survives_broken_settings(monkeypatch) -> None:
    import tradinglab.defaults as _defaults

    def boom(_k):
        raise RuntimeError("settings unreadable")

    monkeypatch.setattr(_defaults, "get", boom)
    assert E.user_agent() == E.USER_AGENT


# ---------------------------------------------------------------------------
# Registry indirection
# ---------------------------------------------------------------------------


def test_edgar_is_registered_by_the_data_package() -> None:
    import tradinglab.data  # noqa: F401  (import registers built-ins)

    assert "edgar" in S.available_shares_sources()


def test_resolution_defaults_to_edgar() -> None:
    import tradinglab.data  # noqa: F401

    name, fetcher = S.resolve_shares_fetcher()
    assert name == "edgar"
    assert callable(fetcher)


def test_an_unknown_source_disables_sizing_rather_than_substituting() -> None:
    name, fetcher = S.resolve_shares_fetcher("no-such-provider")
    assert name == "no-such-provider"
    assert fetcher is S.null_shares_fetcher
    assert fetcher("AAPL") == [], "must know nothing, not fall back to a vendor"


def test_a_broken_factory_does_not_break_session_start() -> None:
    def explode(**_kw):
        raise RuntimeError("provider misconfigured")

    S.register_shares_source("boom-test", explode)
    try:
        name, fetcher = S.resolve_shares_fetcher("boom-test")
        assert fetcher is S.null_shares_fetcher
        assert name == "boom-test"
    finally:
        S.unregister_shares_source("boom-test")


def test_registration_round_trip() -> None:
    sentinel = [S.SharesFact(1, 2, 3.0)]
    S.register_shares_source("stub-test", lambda **_kw: (lambda _s: sentinel))
    try:
        assert "stub-test" in S.available_shares_sources()
        _n, f = S.resolve_shares_fetcher("stub-test")
        assert f("ANY") is sentinel
    finally:
        assert S.unregister_shares_source("stub-test") is True
    assert "stub-test" not in S.available_shares_sources()


def test_kwargs_reach_the_factory() -> None:
    seen: dict = {}

    def factory(**kwargs):
        seen.update(kwargs)
        return S.null_shares_fetcher

    S.register_shares_source("kw-test", factory)
    try:
        S.resolve_shares_fetcher("kw-test", cik_lookup=len)
        assert seen.get("cik_lookup") is len
    finally:
        S.unregister_shares_source("kw-test")
