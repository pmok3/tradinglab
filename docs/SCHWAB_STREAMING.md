# Schwab streaming: integration and commissioning

## What is available

The OAuth/token lifecycle, price-history adapter, shared bar/quote transport and
main-chart integration are implemented and exercised with fake data. **A real
Schwab account/feed has not been commissioned.**

`SCHWAB_REGISTRATION_ENABLED` remains **False**. Saving keys or signing in does
not enable Schwab as a selectable historical provider, and Auto does not choose
an unregistered provider. The no-key foundation is not a claim that production
Schwab charts are available in this build.

Registration only reads credential presence. It never probes a server, refreshes
a token or opens a socket. The optional dependency remains the existing
`tradinglab[schwab]` extra (`websocket-client`); no new dependency is introduced.

## Configure, connect and disconnect

Use **Tools -> Configure Credentials** to save the app key, secret and registered
redirect URI, then **Tools -> Connect to Schwab** for the browser/paste-back OAuth
flow. On Windows, credentials and OAuth tokens use protected local storage.
Never put a secret or token in a screenshot, diagnostic note or issue.

A harmless save with the same effective Schwab identity preserves tokens and the
shared connection. Changing the effective app key, secret or redirect URI, or
removing the saved identity, closes its stream and clears its token cache.
Other credential layers can still win; the dialog reports provenance rather
than pretending it can delete a shell export or an external file.

OAuth disconnect deletes the application's local token cache and closes/removes
both stream capabilities for this process. It **does not claim broker-side token
revocation**. Ordinary registry refresh does not undo a deliberate disconnect.
Reconnect or a changed identity can register a fresh source. On restart,
presence-only registration can occur again, but deleted tokens still require
authorization before any feed can become live.

Already-open live heatmaps rebind after registry/OAuth changes: the old
subscription and quote book are discarded, current members are resubscribed and
missing feeds explicitly return to cached bars. Replay heatmaps are untouched.

## Chart capability and fallback

| Surface | Behavior |
| --- | --- |
| Primary 1m equity chart | Native timestamped bar events; authoritative closed-minute correction. |
| Primary 5m / 15m / 30m / 1h equity chart | Bounded resampling with conservative coverage warm-up. |
| Compare, replay, daily/weekly/monthly, other intervals | Existing nonstreaming behavior remains. |
| Quotient/scaled symbols and index aliases | Not routed through the equity stream. COMP and MOVE remain real equities. |
| Auto | Uses the existing tier-aware historical provider resolver; known cache provenance must agree. |
| Many-symbol live heatmap | Quote stream on the same connection, never per-symbol REST polling. |

These are integration capabilities, not a commissioning switch. Historical
cache keys retain the chosen provider name (`schwab` or `Auto`); stream registry
names such as `schwab-stream` are not guessed from suffixes.

The chart continues normal background polling while a subscription connects,
requires authorization, is stale/disconnected, has no usable own-symbol data,
or is warming its higher-interval coverage. A callable unsubscribe is **not**
proof that the chart is live. Typed source health and accepted data must both
be ready before polling is suppressed.

Native 1m charts also protect startup completeness: the first provisional minute
after subscribing or reconnecting cannot overwrite an existing REST candle with
its partial OHLCV/zero-volume snapshot. Polling continues until that minute's
authoritative closed bar arrives. An authoritative-only CHART stream does not
need a LEVELONE tick to publish its latest completed-minute price.

Status messages distinguish waiting for data, coverage warm-up, history
reconciliation and transport/authentication failures. Own-symbol inactivity and
a dead connection are different conditions. A quiet ticker is not proof the
socket died; the transport provides its separate heartbeat status.

## Higher-interval coverage and corrections

REST and stream aggregation share ET session boundaries: pre-market starts at
04:00, regular at 09:30, post-market at 16:00. Buckets stop at segment end:
the regular 15:30 hourly bucket ends at 16:00, not 16:30.

Only minute contributions for the current and previous target bucket are
retained. The adapter can use a bounded slice of already-cached, same-provider
1m history, but does not start another seed-history fetch pipeline. The newest
cached minute is not assumed authoritative.

The first forming minute after joining can omit earlier trades. Its timestamp
alone cannot prove complete OHLCV, even when it equals the bucket boundary.
That minute stays protected until an explicit authoritative `closed` event.
Existing REST aggregates have no covered-through-minute metadata, so replacing
one additionally requires the complete target bucket, not an observed prefix.
**Initial activation can wait until a subsequent complete bucket. Polling keeps
the actual chart updated throughout that wait.**

If a partially observed bucket is left behind, it remains reconciliation debt.
A safe newer bucket cannot cancel its required backfill. Debt clears only after
a request started after that boundary successfully returns the owed bucket and
its fresh result is applied. An earlier in-flight request arriving late, a failed
fetch, or loading existing memory/disk cache does not count. Current-bucket
minute contributions survive this reconciliation rather than restarting warm-up.

A known older correction replaces its timestamp in place, invalidates affected
indicators/series, redraws the visible slice without moving the viewport, and
persists corrected history. It cannot regress the current-price overlay.
Repeated minute corrections replace volume rather than adding it, and corrected
high/low extrema can shrink.

A newer authoritative close advances the actual live-price line and label even
without another LEVELONE tick. Same-minute authoritative final corrections can
revise that price, but an older correction cannot replace a newer provisional
minute's price or make old data appear freshly received.

An unknown/evicted minute, coverage gap or connection epoch change requires a
real coalesced historical refresh and polling fallback. No missing minute is
fabricated and no arbitrary back-in-time bar is inserted. Subscription and
connection generations reject trailing callbacks from obsolete work.

## Manual live commissioning checklist

1. Use a development build and an isolated TradingLab data directory. Keep the
   default REST gate off until an explicit commissioning change is approved.
2. Confirm app approval, redirect URI and market-data entitlements with Schwab.
   Complete OAuth, restart and confirm protected tokens load without plaintext
   secrets appearing in the environment or diagnostic bundle.
3. Verify history timestamps and sessions against a known liquid equity,
   including 1m, hourly pre/regular/post boundaries and extended-hours filtering.
4. Check login and service acknowledgements before LIVE. Test missing optional
   dependency, absent/expired authorization, rejected services and slow connect;
   Tk must remain responsive and the chart must remain polling-backed.
5. Compare native ticks and authoritative closed bars with independent observed
   data. Treat LEVELONE-derived OHLCV as provisional until corrected.
6. Exercise 5m/15m/30m/1h warm-up mid-bucket, duplicated/late corrections,
   shrinking extrema, missing minutes and reconnect. Check the persisted cache
   as well as the visible chart; warm-up must never replace a complete history
   bucket with a partial aggregate.
7. Open a live heatmap and a chart together. Confirm one shared socket and the
   union of desired symbols; close either consumer without dropping the other.
8. Disconnect, reconnect, change keys and remove credentials with both windows
   open. Verify old quote books are not displayed as live, obsolete callbacks
   cannot repaint, and socket replacements never overlap.
9. Only after those observations should a separate explicit change enable REST
   registration/Auto selection. Record limitations; do not claim these manual
   observations were covered by keyless tests.

## Developer references

Contracts live in `streaming/base.spec.md`, `streaming/registry.spec.md`,
`streaming/intraday.spec.md`, `streaming/resampler.spec.md`,
`data/stream_controller.spec.md` and the Schwab transport/auth/history specs.
Focused fake-source suites are under `tests/streaming` and
`tests/unit/test_stream_controller.py`. The canonical synthetic UI acceptance
check is `check_96_stream_readiness_and_corrections` in the streaming smoke bank.
