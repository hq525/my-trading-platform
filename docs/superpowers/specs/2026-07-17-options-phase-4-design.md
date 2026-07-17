# Phase 4: Options Paper Trading — Design

**Date:** 2026-07-17
**Status:** Approved by user (brainstorming session); hardened by adversarial design review

## Purpose

Add long-only options trading (buy calls/puts, sell to close) to the **paper** section of the platform. Users browse real option chains for a stock underlying, buy contracts with realistic spread-crossing fills, and positions either close manually or cash-settle at intrinsic value at expiry.

**Chosen approach:** a fourth execution pipeline (Approach A) — a dedicated `OptionsSimAdapter` and a dedicated Alpaca options market-data provider, partitioned from the stock/crypto sims by a new `is_option_symbol` predicate, following the owns-order pattern established in Phases 2–3.

**Rejected alternatives:**
- *Extend the stock SimAdapter with option branches* — entangles two fill policies and two data providers in one adapter; every stock-fill change would risk options and vice versa.
- *Delegate paper options to Alpaca's own paper execution* — injects broker state into the local paper ledger (cash reservations, instant fills, offline use all break) and contradicts the chosen local fill model.

## Requirements (user decisions)

1. **Scope: paper manual only.** Strategies, live, and replay do not trade options this phase; each is explicitly fenced (see Isolation).
2. **Data source: Alpaca's free "indicative" options feed** on the existing `PT_ALPACA_KEY_ID`/`PT_ALPACA_SECRET_KEY` credentials. Single provider, no failover.
3. **Fill model: cross the spread.** Market buys fill at ask, market sells at bid; limit buys fill at ask once ask ≤ limit, limit sells at bid once bid ≥ limit.
4. **Expiry: cash-settle at intrinsic value** of the underlying after the close on expiry day, via a system sell fill; OTM contracts fill at $0 (expire worthless).
5. **UI: dedicated Options page** in the Paper nav — underlying search → expiration picker → Calls/Puts chain with bid/ask, last, open interest, IV, delta, theta → click a row to load the order ticket. (Volume is not shown: no free upstream source carries it — see Honest Limitations.)
6. **Long-only** (existing platform invariant): buy to open, sell to close, never short. Whole contracts only.

## Symbology

Contracts are identified everywhere by **compact OCC symbols**: `ROOT + YYMMDD + C|P + strike×1000 zero-padded to 8 digits`, e.g. `SPY260821C00625000` = SPY 2026-08-21 $625 call.

- No dash → cannot collide with the crypto heuristic (`"-" in symbol`).
- Unique per contract → the frozen `(account_id, symbol)` uniqueness on positions works unchanged. **No schema changes in this phase**; expiry/strike/right are always derived by parsing the symbol.
- This is Alpaca's native symbology — symbols flow verbatim to the data API.

`app/assets.py` gains:

- `is_option_symbol(symbol)` — matches `^[A-Z]{1,6}\d{6}[CP]\d{8}$` **and** the six digits parse as a valid date. Classification order at every call site: **option → crypto → stock**.
- `parse_occ(symbol) -> OccContract(underlying, expiry: date, right: "call"|"put", strike: Decimal)` — raises `ValueError` on non-option symbols. Strike = the 8 digits / 1000.
- `contract_multiplier(symbol) -> Decimal` — `100` for option symbols, `1` otherwise. This is the **single source of the ×100** threaded through every notional site (enumerated in Engine below).

## Market Data

New provider `app/marketdata/alpaca_options.py` (`AlpacaOptionsData`) with its own `_get` helper — the stock provider's error mapper is hardcoded to the `/v2/stocks/{symbol}/…` URL shape and must not be reused. It talks to **two hosts with the same `APCA-API-KEY-ID`/`APCA-API-SECRET-KEY` headers** (the market-data key pair):

- **Data host** `https://data.alpaca.markets` for snapshots, always passing `feed=indicative`. New setting `PT_ALPACA_OPTIONS_FEED` (default `"indicative"`) makes a paid OPRA feed a config switch later.
- **Contracts host** — new setting `PT_ALPACA_CONTRACTS_BASE` (default `https://paper-api.alpaca.markets`) for contract discovery. This is the trading-API host (separate 200 req/min bucket from the data API).

**Upstream endpoints:**

| Need | Endpoint | Notes |
|---|---|---|
| Expirations + open interest | `GET {contracts_base}/v2/options/contracts?underlying_symbols={u}&expiration_date_gte={today}&expiration_date_lte={today+2 years}&limit=10000` | **`expiration_date_lte` MUST be passed explicitly** — Alpaca defaults it to the upcoming weekend, which would silently truncate the expirations list to the current week. A provider test asserts the param is always sent. Rows carry `expiration_date` and `open_interest`. |
| Chain for one expiry | `GET https://data.alpaca.markets/v1beta1/options/snapshots/{u}?expiration_date={e}&feed=indicative&limit=1000` | Per contract: `latestQuote` (bid/ask), `latestTrade` (last), `impliedVolatility`, `greeks` (delta/gamma/theta/vega/rho) — all present on the free indicative feed. **No volume exists in snapshots or contracts rows**; the chain does not carry volume. Strike/right parsed from the OCC symbol. Merged with the contracts rows for open interest. |
| Single-contract quote | `GET https://data.alpaca.markets/v1beta1/options/snapshots?symbols={occ}&feed=indicative` | Used for fills, ticket, valuation. |

**Pagination (identical on both endpoints):** pass the `page_token` query param, read `next_page_token` from the response body.

**Quote shape.** The frozen `Quote` dataclass gains optional `bid: Decimal | None = None` and `ask: Decimal | None = None` (all existing constructor sites unchanged). For options: `bid`/`ask` populate from the latest quote when the side is > 0; `price` = mid `(bid+ask)/2` when both sides exist, else last trade, else the provider raises `MarketDataError("no quote for contract")`. A contract absent from the snapshots response raises `UnknownSymbolError`.

**Bars:** the options service implements `get_bars` by raising `MarketDataError("bars not available for option contracts")`. This keeps `GET /api/market/bars/{occ}` (reachable via `/trade?symbol=` links or typed input) and strategy `ctx.get_bars(occ)` on the standard error path — a 4xx/503, never a 500.

**Caching (service layer):** expirations + open-interest rows 15-minute TTL keyed by underlying; chain 30-second TTL keyed `(underlying, expiry)`; single-contract quotes reuse the existing 30-second quote cache keyed by symbol. At most two upstream calls per chain render (snapshots + cached contracts) — never per-contract fan-out. Well inside the 200 req/min limits of both buckets.

**Routing — all four copies change in the same commit.** `AppDeps` gains `options_market_data`, `options_engine`, and `options_execution` fields, **all defaulting to `None`** (so bare `AppDeps` constructions in existing tests keep working; jobs None-guard them like `live_execution`). The option branch (classification order option → crypto → stock) is added to:
1. `AppDeps.execution_for_symbol` (order placement/cancel routing),
2. `AppDeps.market_data_for_symbol` (quotes/bars/valuation routing),
3. the `execution_for_symbol` closure in `build_deps` passed to `StrategyRunner`,
4. the `market_data_for_symbol` closure in `build_deps` passed to `StrategyRunner`.

Placement routing is the critical one: without it, an OCC order reaches the **stock** sim, and yfinance happens to resolve OCC-format tickers — the order would fill at the raw premium with no ×100, no spread crossing, and no expiry guard. A routing test pins: `POST /accounts/{id}/orders` with an OCC symbol reaches `OptionsSimAdapter`, never the stock sim.

## Engine

`TradingEngine` changes are multiplier-centric; all money math stays `Decimal`:

- **Notional** = `price × qty × contract_multiplier(symbol)` at **every** notional site, exhaustively: (1) buy reservation at placement; (2) `apply_fill` cash debit/credit; (3) realized P&L `(price − avg_cost) × qty × multiplier − commission`; (4) the at-fill cash recheck in the adapter; (5) `valuation.position_values` — **both** `market_value` and `unrealized_pnl` — which propagates to `account_equity`, equity snapshots, and the accounts API positions payload. `avg_cost` and `last_price` stay **per-share** (per-unit premium), matching the quote convention — only cash/valuation math multiplies.
- **Buy reservation price:** market orders reserve at `quote.ask` when present, else `quote.price`; limit orders reserve at the limit price.
- **`apply_fill` gains an optional `commission: Decimal | None = None` parameter** — `None` means `account.commission` (all existing callers unchanged); the settlement job passes `Decimal("0")`. Without this, OTM $0 settlements would *debit* the account by the commission.
- **Whole contracts:** OCC symbols already fall into the existing non-crypto whole-number qty branch; a test pins this.
- **Placement guard (user/API placement only — the settlement job bypasses `place_order` entirely):** reject with reason `contract expired` when the parsed expiry is before today's America/New_York date, **or** when expiry is today and NY time is at/past 16:00 (options stop trading at the close, but Alpaca snapshots keep serving stale quotes after it). Expiry-day (0DTE) trading before the close is allowed.
- **Commission:** the account's flat per-order commission applies to option orders exactly as to stocks.

## Execution: OptionsSimAdapter

New `app/engine/options_sim_adapter.py`, structured like the stock `SimAdapter` (same `process_pending` contract, same NYSE `MarketCalendar` for open/close gating and day-order TIF expiry). **`jobs.run_process_pending` gains `deps.options_execution.process_pending(...)`, None-guarded like `live_execution`** — without this, queued option orders never fill, never cross, and day-order TIF never releases `reserved_cash`.

**owns_order partition (all changed in the same commit):**
- Options sim: `mode == "paper" and is_option_symbol(symbol)`
- Stock sim: `mode == "paper" and not is_crypto_symbol(symbol) and not is_option_symbol(symbol)`
- Crypto sim: unchanged (`mode == "paper" and is_crypto_symbol(symbol)`)

**Fill policy (cross the spread):**
- Market buy → fills at `ask`, immediately when the market is open, else on the first `process_pending` tick after open. Market sell → fills at `bid` under the same timing rules.
- Limit buy → fills at `ask` once `0 < ask ≤ limit`. Limit sell → fills at `bid` once `bid ≥ limit > 0`.
- **The at-fill cash recheck prices notional at the actual fill price** — `ask × qty × 100 + commission` for buys — never at `quote.price` (mid). Checking at mid while debiting at ask would let cash go negative on a widened spread. On shortfall the order is rejected, matching stock behavior.
- **One-sided or empty quotes never fabricate a fill:** a buy with no ask, or a sell with no bid (or bid = 0), stays pending until the side exists. Worthless positions that never see a bid are eventually handled by expiry settlement.
- **Dead contracts never fill:** `process_pending` expires (reason `contract expired`, releasing `reserved_cash`) any order whose parsed expiry is before today's NY date, *before* attempting fills. This makes the adapter self-healing even if the 16:05 expiry job misses a day — otherwise a stale after-hours snapshot could fill an order on an expired contract the next morning.

## Expiry Settlement

New scheduler job `run_option_expiry(deps)` — cron **16:05 America/New_York, weekdays**, deliberately before the 16:10 snapshot job so expiry-day snapshots capture settled cash instead of quoting dead contracts.

For each `mode == "paper"` account, **in this order**:

1. **Release dead orders first:** every still-pending order (day or GTC, buy or sell) on a contract with parsed expiry ≤ today (NY date) is expired with reason `contract expired`, releasing its `reserved_cash`. (Doing this first means a user's open GTC sell can never make the settlement sell fail an availability check.)
2. **Settle positions:** for every position with `qty > 0` and parsed expiry ≤ today: quote the **underlying** stock; intrinsic = `max(0, S − K)` for calls, `max(0, K − S)` for puts.

**Settlement mechanics (does NOT go through `place_order`):** `place_order` would quote the dead contract symbol, apply the expired-contract guard, and consume the idempotency key on rejection — permanently poisoning settlement. Instead, per position, in one transaction:

- Query `Order` by `(account_id, idempotency_key = "settle:{account_id}:{symbol}")`. If a **filled** order exists → already settled, skip (this is the re-run no-op). If an order exists in any other state → log and skip for manual inspection (should be unreachable; nothing else creates `settle:` keys).
- Otherwise construct the `Order` row directly: `side="sell"`, `type="market"`, `tif="day"`, `qty = position.qty`, `status="pending"`, `reserved_cash = 0`, the `settle:` idempotency key — explicitly bypassing `place_order`'s contract quote, expired-contract guard, and available-qty check — then call `engine.apply_fill(session, order, price=intrinsic, commission=Decimal("0"))` and commit.

Realized P&L, cash movement, journal visibility, and win-rate stats all follow from the normal sell-fill path. OTM → fill price $0 and cash moves by exactly $0.

**Self-healing:** the guard is `expiry ≤ today`, not `== today` — if the underlying quote fails, that position is skipped and retried on the next run. Live accounts are excluded (broker settles); replay accounts are excluded (cannot hold options at all).

## API

Two new market endpoints (`app/api/market.py`), following existing error conventions (unknown symbol → 404, `MarketDataError` → upstream-unavailable status):

- `GET /api/market/options/{underlying}/expirations` → `{underlying, expirations: ["YYYY-MM-DD", …]}`. An underlying with no listed contracts returns 404 `"no options listed for symbol"`.
- `GET /api/market/options/{underlying}/chain?expiry=YYYY-MM-DD` → `{underlying, expiry, calls: [ChainRow…], puts: [ChainRow…]}`, each row `{symbol, strike, bid, ask, last, open_interest, iv, delta, gamma, theta, vega}` — decimal strings, `null` where the feed omits a value. Rows sorted by strike ascending. Contracts whose symbols fail `is_option_symbol` (adjusted/non-standard) are filtered out.

Existing surfaces: `GET /api/market/quote/{symbol}` (path param, existing route shape) accepts OCC symbols, routed to the options service; `QuoteOut` gains optional `bid`/`ask` (null for stocks/crypto). `GET /api/market/bars/{occ}` surfaces the options service's `MarketDataError` — never a 500. Order placement/cancel APIs are unchanged — symbol shape does the routing.

## Frontend

- **`lib/options.ts`:** `isOptionSymbol`, `parseOcc`, `formatOptionLabel` (`SPY260821C00625000` → `"SPY 08/21/26 $625 C"`). Strike renders without trailing zeros ($625, $7.50).
- **`lib/types.ts` / `lib/api.ts`:** `OptionChainRow`, `OptionChain`, `Quote` gains `bid`/`ask` nullable; `api.optionExpirations(underlying)`, `api.optionChain(underlying, expiry)` following the existing `request<T>` + `encodeURIComponent` pattern.
- **Options page (`app/options/page.tsx`, "Options" link in `paperLinks`):** underlying input (uppercased, like TradeView) → expiration `<select>` (query `["option-expirations", u]`, staleTime 15 min) → Calls/Puts tabs (client-side; one chain response holds both) → chain table with columns Strike, Bid, Ask, Last, OI, IV, Delta, Theta (query `["option-chain", u, expiry]`, refetchInterval 30 s). Clicking a row selects the contract and mounts `OrderTicket` with the OCC symbol and the row's quote.
- **OrderTicket option mode** (detected via `isOptionSymbol(symbol)`): qty label "Contracts", whole numbers only, header shows `formatOptionLabel` plus the bid/ask line, est. cost = `premium × 100 × contracts` computed as `mulMoney(mulMoney(price, "100"), qty)` (×100 first — exact in BigInt fixed-point). Buy previews at ask (or limit price), sell previews at bid. The existing insufficient-cash check works unchanged against the ×100 cost.
- **PositionsTable:** third group "Options" (checked before the crypto test), rows labeled with `formatOptionLabel`; value/P&L strings arrive multiplier-correct from the backend (valuation multiplies — see Engine).
- **OrdersTable / journal:** "Option" badge (classification order option → crypto → stock); option order rows link to `/options?symbol={underlying}` (encoded) instead of `/trade?symbol={occ}`. The Options page reads the `symbol` query param to preload the underlying.
- Settlement fills appear in the journal as ordinary sells on the contract symbol and count toward closed trades/win rate — a $0 OTM settlement is a loss, an ITM settlement is a win or loss by cost basis, matching real accounting.

## Isolation

| Surface | Fence |
|---|---|
| Live placement | `AlpacaLiveAdapter` rejects option symbols at placement with reason `options not supported on live` (crypto-on-live precedent) |
| Replay session creation | `create_session` raises `ReplayCreationError("options are not supported in replay")` for any `is_option_symbol` in the normalized symbol list, **before `_fetch_history` runs** — yfinance would happily resolve OCC tickers, so natural rejection cannot be relied on; a test pins the 400 |
| Replay placement | Options can never be in a session universe, so the strict `ReplayMarketData` placement guard rejects them as unknown symbols; a test pins this |
| Strategies (orders) | `Context._place` checks `is_option_symbol(symbol)` **before any engine call** and raises `ValueError("strategies cannot trade options")` (caught by the runner's existing per-strategy error handling); no Order row or reservation is ever created |
| Strategies (data) | `ctx.get_quote` on an OCC symbol is permitted (read-only, harmless — pinned by a test); `ctx.get_bars` surfaces the options service's `MarketDataError`; strategies get no chain access |
| Expiry job | Iterates `mode == "paper"` accounts only — never live (broker settles) or replay |
| owns_order partition | Options sim claims paper+option; stock sim excludes options — changed and tested in the same commit |
| Placement routing | All four routing copies (two `AppDeps` methods + two `build_deps` closures) gain the option branch in the same commit; test pins OCC orders reach the options sim |
| Journal | Option trades are paper trades; they appear under the existing Paper filter with an Option badge — no filter changes |
| Live/replay market data | Only the paper routing branch reaches the options service |

## Error Handling

- Provider errors follow the platform contract: contract/underlying not found → `UnknownSymbolError` (definitive, → 404 / rejected order); transport or upstream failures → `MarketDataError` (order rejected with `market data unavailable`, API returns upstream-unavailable status). 422s from options endpoints map to `MarketDataError`, **not** `UnknownSymbolError` (the stock provider's 422 mapping is a known trap and is not reused).
- `get_bars` on option symbols → `MarketDataError`, so the bars endpoint and strategy contexts degrade cleanly.
- Orders on one-sided markets pend rather than fill (see fill policy); the order row's status is visible in Orders as usual.
- Expiry settlement failures skip-and-retry per position; nothing is half-settled because each position settles in its own transaction with an idempotent `settle:` key checked by query, not by insert.

## Testing

**Backend (pytest, MockTransport for HTTP):**
- Provider: chain parsing (quote/greeks/IV extraction, OCC filtering, strike sort, `page_token`/`next_page_token` pagination), expirations derivation **with `expiration_date_lte` always sent**, open-interest merge, error mapping (404 vs 422 vs 5xx), `feed=indicative` always sent, `get_bars` raises `MarketDataError`.
- Assets: `is_option_symbol` accepts valid OCC / rejects crypto pairs, plain tickers, bad dates; `parse_occ` round-trips; `contract_multiplier`.
- Engine: buy reservation ×100 at ask; `apply_fill` cash ×100 both sides; realized P&L ×100; `apply_fill` commission override (settlement at $0 moves cash by exactly $0 on a commission-charging account); fractional contract qty rejected; expired-contract placement rejected (both the `< today` and the `== today after 16:00 NY` branches); commission applied once per order.
- Valuation: `PositionOut.market_value` **and** `unrealized_pnl` ×100 for options; equity/snapshots inherit.
- OptionsSimAdapter: market buy@ask / sell@bid when open; pends when closed then fills after open via `run_process_pending`; limit crossing each side at the touched price; one-sided/zero-bid quotes stay pending; at-fill recheck at the **ask** rejects when mid×100 fits but ask×100 does not (cash unchanged); day-order TIF expiry releases `reserved_cash`; pending order on a contract with expiry < today is expired, never filled, even when a stale quote is present.
- Expiry job: ITM call and put settle at intrinsic (cash, realized P&L, journal fill all correct); OTM settles at $0; position with an open GTC sell on the expired contract → order expired AND position settled in the same run; re-run is a no-op (filled `settle:` order short-circuits); underlying quote failure skips and a later run settles; live and replay accounts untouched.
- Routing/fencing: every (mode, symbol-shape) pair is claimed by exactly one adapter; `POST /accounts/{id}/orders` with an OCC symbol reaches the options sim; live placement of an option rejected; replay session creation with an option symbol → 400 with no network I/O; strategy `_place` raises before any engine call; `GET /api/market/bars/{occ}` returns 4xx/503, never 500.

**Frontend (vitest):**
- `lib/options.ts` parse/format units.
- Options page: expirations load → chain renders both tabs → row click mounts ticket with contract label; `symbol` query param preloads.
- Ticket option mode: "Contracts" label, whole-number enforcement, est-cost ×100 copy, buy-at-ask vs sell-at-bid preview.
- PositionsTable Options group; OrdersTable/journal Option badges; option order links point at `/options?symbol=…`.

## Honest Limitations (documented, not hidden)

- The indicative feed is delayed/derived from OPRA; bid/ask outside market hours may be stale or absent, so mid marks (and therefore equity) can be stale after hours. One-sided contracts mark at last trade.
- The chain shows **no volume column**: neither Alpaca's option snapshots nor its contracts endpoint carries volume on the free tier, and fetching daily bars per chain would add a third upstream call for a cosmetic field. Open interest lags by up to a day (`open_interest_date`).
- Settlement uses the underlying's last trade near 16:05 NY, not official OCC settlement values.
- Early exercise is not modeled: value is captured by selling or by expiry intrinsic settlement (long-only makes assignment irrelevant).
- The after-close placement cutoff uses a fixed 16:00 America/New_York time; on early-close sessions options stop trading earlier and the guard is loose by a few hours (orders placed in that window pend and are swept at 16:05).
- Corporate actions (splits, special dividends) do not adjust held contracts; adjusted/non-standard contracts are filtered out of chains entirely.
- One unquotable option position still skips that account's daily snapshot (existing all-or-nothing snapshot semantics); the expiry-before-snapshot ordering minimizes but does not eliminate this.
- Realized P&L may differ from exact cash movement by up to $0.005 × qty per option round trip: `avg_cost` keeps the platform's per-share 0.0001 quantization, which the ×100 multiplier amplifies to half a cent per contract. Cash itself stays exact.

## Implementation Plan Split

1. **Backend plan** — assets predicates → provider + caches → Quote bid/ask → engine multiplier + guards + `apply_fill` commission param → OptionsSimAdapter + partition + routing (all four copies) + `run_process_pending` wiring → expiry job → API endpoints → fencing (live/replay/strategies) with tests throughout.
2. **Frontend plan** — options lib + types/api → Options page + chain table → ticket option mode → positions/orders/journal surfaces, with vitest coverage.

Each plan lands as its own PR, per the established phase cadence.
