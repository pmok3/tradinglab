# streaming/schwab.py — Spec

## Purpose
Schwab WebSocket streaming source. Implements the `streaming.base.StreamSource` protocol against Schwab's streamer API. One persistent WS connection per process, with multiplexed per-symbol subscriptions on top. Delegates bar-aggregation to the pure `streaming/schwab_aggregator.py`.

## Public API
- `class SchwabStreamSource(*, seed_lookup=None)` — main entry point.
  - `subscribe(ticker: str, interval: str, on_event: StreamCallback) -> Callable[[], None]` — adds a subscription; returns an unsubscribe callable. Returns a no-op closure (with a debug/warn log) when:
    - `interval != "1m"` (only 1-minute Schwab streaming is supported today; synthetic streaming supports broader intraday intervals);
    - `websocket-client` is not installed;
    - Schwab credentials are not configured;
    - no valid access token (refresh token expired/missing — caller should run `schwab_login`).
  - `subscribe_quotes(symbols, on_quote) -> SchwabQuoteSubscription` — the **quote axis** (see [`schwab_quotes`](schwab_quotes.spec.md)). Lives on this class because Schwab permits one streamer connection per user, so both axes must share the socket. Quote symbols join `_symbols_subscribed` (so the reconnect re-image covers them) but get **no** `_Subscription` and therefore no `MinuteBarBuilder`.
  - `seed_lookup: Optional[Callable[[str, str], Optional[float]]]` — injected lookup that returns the most-recent close from REST history; used to seed the in-progress bar. `None` from the lookup → seed 0.0.
- `fetch_streamer_info(access_token: str) -> Dict[str, Any]` — fetches Schwab's per-user `streamerInfo[0]` payload from `/userPreference`.
- `build_login_request(streamer_info, access_token, request_id=0) -> dict` — builds the `ADMIN.LOGIN` admin request (pure, testable).
- `build_subs_request(service, symbols, fields, streamer_info, request_id) -> dict` — builds a `SUBS` (initial) / `ADD` (incremental) request. Pure.
- Module constants: `USER_PREFERENCE_URL`, `LEVELONE_FIELD_IDS`, `CHART_EQUITY_FIELD_IDS`, `_BACKOFF` (reconnect schedule 1,2,4,8,16,30s capped).

## Dependencies
- Internal: `..models.Candle`, `.base.StreamCallback`, `.schwab_aggregator.{MinuteBarBuilder, chart_equity_to_candle, decode_chart_equity_content, decode_levelone_content}`. Late imports: `..data.credentials.get_credentials`, `..data.schwab_auth.get_access_token`.
- External: `websocket-client` (optional — late-imported inside `subscribe`; missing install degrades to a no-op subscriber with a warning).

## Design Decisions
- **Singleton connection** lifecycle: dormant until first `subscribe` / `subscribe_quotes`; shut down when the last subscription of **either** axis drops. The `_Connection` owns two daemon threads — a recv loop and a 1-second clock thread that forces minute rollovers for quiet symbols.
- **The wire symbol set is the union of both axes** (`_refresh_symbols_locked`). Dropping the last *bar* subscriber for a symbol must not `UNSUBS` it while the quote axis still wants it, and vice versa.
- **A symbol newly entering the QUOTE set gets an ADD even when it is already on the wire for bars.** Schwab sends a full image only in response to SUBS/ADD and change-only deltas after that, so suppressing the re-image leaves the `QuoteBook`'s first entry for that symbol a delta — and `prev_close` never arrives. The symptom is that the symbol the user is actively charting is the one tile that cannot compute a percent. A duplicate ADD is harmless; it just re-images.
- **Symbol changes are batched.** `add_symbols` / `remove_symbols` take a list and send one message per service. The per-symbol loop was 500 blocking socket writes on window open at S&P scale and 1000 on close, all on the Tk thread. `add_symbol` / `remove_symbol` remain as single-symbol shims for the bar path.
- **The connection is opened OUTSIDE `_lock`** (`_open_connection`). `get_access_token` can make a synchronous HTTPS round trip to Schwab's `/oauth/token`, and the quote path reaches it from the Tk thread; holding the lock across it would freeze the UI *and* stall the socket thread's dispatch and clock thread for the duration. The double-check on re-acquire prevents two connections, which matters because Schwab permits exactly one.
- **Teardown never dials out.** `_drop_quote_subscription` decides `idle` under the lock and, when idle, shuts the connection down directly instead of reconciling first. Reconciling would (a) attempt to *open* a connection purely to close it — potentially blocking on a token refresh — and (b) emit ~1000 pointless UNSUBS messages for a socket that is about to close.
- **LEVELONE is dispatched to the quote axis first**, before the bar aggregators: a quote delivery is a dict write per subscriber, whereas the bar path runs an aggregator per subscription, and a slow or raising bar subscriber must not delay the snapshot the UI samples.
- **`LEVELONE_FIELD_IDS` carries the union of both axes' fields** (adding 10 / 11 / 12 for the quote axis). One subscription request serves both, and the bar builder ignores decoded keys it does not read.
- **LEVELONE drives in-progress bars, CHART_EQUITY corrects sealed bars**: the aggregator builds 1-min bars from sub-minute LEVELONE ticks. Schwab's authoritative CHART_EQUITY arrives 5–30s after the minute closes; we re-emit it as a `("tick", Candle)` so the BarsBuffer's match-by-timestamp overwrites the prior synthesized bar. **No separate "correction" event kind.**
- **Per-subscriber `_Subscription` instances** — multiple subscribers for the same symbol get their own `MinuteBarBuilder` so late subscribers see a fresh in-progress bar seeded with their own most-recent close.
- **Reconnect with exponential backoff** (`1,2,4,8,16,30` seconds, capped). Resubscribes all symbols on reconnect; `_request_id` resets to send `SUBS` (not `ADD`) on the fresh socket.
- **Late `import websocket`**: keeps `streaming/schwab.py` importable even when the optional `[schwab]` extra isn't installed; subscribe returns a no-op closure with a warning.
- **`ADD` for new symbols on an existing connection**, `SUBS` only for the initial subscription. Avoids re-sending the full keylist on every new symbol.
- **`UNSUBS` is per-service**: when the last subscriber for a symbol drops, we send `UNSUBS` for both `LEVELONE_EQUITIES` and `CHART_EQUITY`.
- **Callbacks invoked outside the connection lock** to avoid deadlocks with consumer-side locks (e.g. the BarsBuffer mutator on the Tk thread). `_safe_invoke` wraps a subscriber callback so a raised exception only logs.
- **Shared `credentialed_opener()`** (security audit I4 / M5). The
  REST sidecar call to `/userPreference` (`fetch_streamer_info`)
  routes through `data._http.credentialed_opener` so cross-host 30x
  redirects strip the `Authorization: Bearer …` header before
  forwarding. Response read bounded by `MAX_RESPONSE_BYTES` (8 MB);
  real streamer-info payloads are ~2 KB.

## Invariants
- `subscribe(...)` never raises — degraded paths return a no-op closure.
- The connection's recv thread and clock thread are daemon threads (process exit terminates them).
- A subscription's `MinuteBarBuilder` is private to its `_Subscription`; no cross-subscriber state-sharing.
- After `unsubscribe()`, `alive=False` is checked on every dispatch. A callback already past the alive check may still complete, matching `streaming/base.py`'s single trailing-callback contract.

## Testing
- Pure helpers `build_login_request`, `build_subs_request`, `_is_login_ok`, `decode_levelone_content`, and `chart_equity_to_candle` are unit-tested with no I/O in `tests/unit/test_schwab_streaming.py`.
- WS / network path is `# pragma: no cover` — exercised by manual integration. The `MinuteBarBuilder` state machine is unit-tested via `schwab_aggregator.spec`'s coverage.
