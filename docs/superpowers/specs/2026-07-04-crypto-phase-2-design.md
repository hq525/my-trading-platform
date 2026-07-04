# Paper Trading Platform — Phase 2: Crypto Support Design

**Date:** 2026-07-04
**Status:** Approved by user (brainstorming session)

## Purpose

Extend the Phase 1 paper trading platform (spec:
`docs/superpowers/specs/2026-07-03-paper-trading-platform-design.md`) to support
crypto trading alongside stocks/ETFs, per that spec's roadmap item 2: "new data
provider; 24/7 sessions in calendar logic." Crypto trades 24/7 with no exchange
close and typically in fractional units — both assumptions Phase 1 deliberately
baked in as stock-only (`int` whole shares, a single NYSE calendar).

**Out of scope for Phase 2:** options, forex/futures, live trading (unchanged
from Phase 1's roadmap), and per-asset-class P&L sub-ledgers (see Account Model
below — cash/equity/realized P&L stay fully blended per account).

## Requirements

- An existing account (e.g. the `manual` account) can hold both stock and
  crypto positions simultaneously — no new account-model concept, no
  per-account asset-class restriction.
- Crypto orders support fractional quantities (up to 8 decimal places); stock
  orders keep today's whole-share requirement.
- Crypto quotes/bars come from free, keyless public APIs (Coinbase primary,
  Binance fallback), following the same fallback/cache pattern as Phase 1's
  stock data.
- Crypto markets are always open: market orders fill immediately whenever
  placed, day orders expire at the next UTC midnight (not a session close),
  and there is no after-hours queueing.
- The UI visually separates stock and crypto (grouped positions, tagged
  orders/journal rows) without splitting any underlying data — one cash
  balance, one equity number, one realized P&L per account, exactly as today.

## Architecture

**Two parallel pipelines sharing one engine.** Rather than teaching
`TradingEngine`/`SimAdapter`/`MarketDataService` to branch internally on asset
class, Phase 2 runs a second, independent pipeline for crypto — its own
calendar, market data service, and execution adapter — built from the exact
same classes Phase 1 already has. `TradingEngine`/`SimAdapter` need no
asset-class-aware code themselves; only the calendar and data provider differ
per pipeline.

```
Stock pipeline (existing):  MarketCalendar (XNYS)  + MarketDataService(Alpaca, yfinance)  + TradingEngine + SimAdapter
Crypto pipeline (new):      CryptoCalendar (24/7)   + MarketDataService(Coinbase, Binance) + TradingEngine + SimAdapter
```

**Routing is symbol-based, applied consistently everywhere.** A "-" in the
symbol (e.g. `BTC-USD`) means crypto; no dash means a stock ticker (stock
tickers never contain "-"). This one rule decides, for every operation:

- which calendar governs the order (open/closed, day-order expiry)
- which market-data provider serves the quote/bars
- whether qty must be a whole number (stock) or may be fractional up to 8
  decimal places (crypto)

This applies uniformly to order placement, cancellation, quote/bar lookups,
position valuation, and strategy trading (`ctx.buy("SPY", ...)` and
`ctx.buy("BTC-USD", ...)` in the same strategy run route to the correct
pipeline automatically). There is no account-level or strategy-level
asset-class flag — accounts and strategies are asset-class-agnostic
containers, same as Phase 1.

**Rejected alternatives:**
- *Account-scoped asset class* (an account is either all-stock or all-crypto,
  with an auto-created `manual-crypto` account) — this was the first design
  explored and rejected by the user: they want one account to hold both.
- *Branching inside the shared classes* (`MarketDataService`/calendar dispatch
  internally by symbol/account) — pushes complexity into shared code that
  Phase 1 kept simple; composing two instances of already-simple classes is
  less code overall.
- *Fully duplicated module structure per asset class* (separate strategy
  runner, separate scheduler file) — unwarranted duplication for two asset
  classes that share ~90% of their logic (engine, valuation, journal, API
  schemas all stay unified).

## Data Model

- **Quantities widen from `int` to `Decimal`** everywhere: `Order.qty`,
  `Position.qty`, `Fill.qty` (reusing the existing `SqliteDecimal` column type
  already used for money — no new storage mechanism, no parallel fractional
  path).
- **No new columns on `Account` or `StrategyState`/`Strategy`.** Asset class is
  never stored — it's derived from the symbol string wherever it's needed.
- **Qty validation in `TradingEngine.place_order`**, decided by the order's own
  symbol:
  - No "-" in symbol (stock) → qty must equal a whole number, else reject
    `"quantity must be a whole share count"` — preserves Phase 1 behavior
    exactly.
  - "-" in symbol (crypto) → qty must be positive with at most 8 decimal
    places, else reject `"quantity precision exceeds 8 decimal places"`.
- All existing engine math (weighted average cost, realized/unrealized P&L,
  cash/share reservations) already operates on `Decimal` for price — qty
  simply joins it in the same formulas; no formula changes, just a wider
  column type.

## Calendar

**`CryptoCalendar`** implements the same 4-method interface as
`MarketCalendar` (`is_open`, `is_trading_day`, `next_open`, `expiry_time`):

- `is_open(at)` → always `True`.
- `is_trading_day(d)` → always `True`.
- `next_open(after)` → returns `after` unchanged (the market's always open).
- `expiry_time(placed_at)` → the next UTC midnight *strictly after*
  `placed_at` (so an order placed exactly at midnight still gets a full ~24h,
  not zero duration).

Because `is_open` is always `True`, crypto market orders always fill
immediately when placed and the after-hours-queueing code path in
`SimAdapter` is simply never exercised for crypto — no new logic needed there.

## Market Data

- **`CoinbaseData`** (primary) — Coinbase's public Exchange API, no API key
  required: `GET /products/{symbol}/ticker` for quotes, `GET
  /products/{symbol}/candles?granularity=86400` for daily bars. Maps 404 →
  `UnknownSymbolError`; other failures → `MarketDataError`. Same
  `httpx.Client` + offline `httpx.MockTransport`-tested pattern as
  `AlpacaData` — no live network calls in tests.
- **`BinanceData`** (fallback) — Binance's public API, no key required.
  Translates the canonical `BTC-USD` symbol to Binance's `BTCUSDT` format
  internally (strip the dash, swap the trailing `USD` for the stablecoin
  `USDT` — close enough for paper-trading fallback pricing). `GET
  /api/v3/ticker/price` for quotes (stamped with local `utcnow()`, since this
  endpoint has no timestamp field), `GET /api/v3/klines` for daily bars.
  Binance signals an unknown symbol via HTTP 400 + `code: -1121` (not 404) —
  mapped specifically to `UnknownSymbolError`; other errors → `MarketDataError`.
- Both plug into the *existing* `MarketDataService` class unchanged — the
  crypto stack is just `MarketDataService([CoinbaseData(), BinanceData()])`,
  reusing the same fallback-on-`MarketDataError` and 30-second quote-cache
  logic already built in Phase 1.
- Exact Coinbase/Binance response field names are verified against their
  current public docs at implementation time, with offline `MockTransport`
  fixtures (not live-call assumptions baked into tests) — same rigor as the
  Phase 1 `AlpacaData` provider.

## Execution & API Routing

- **`AppDeps`** gains `crypto_market_data`, `crypto_calendar`, `crypto_engine`,
  `crypto_execution` fields (built the same way as the stock versions in
  `build_deps`), plus symbol-keyed lookup helpers: `execution_for_symbol(symbol)`
  and `market_data_for_symbol(symbol)`.
- **`orders.py`**: `place_order` routes via `execution_for_symbol(body.symbol)`.
  `cancel_order` looks up the order (which already stores its own `symbol`)
  and routes via that — no account lookup needed for routing at all.
- **`accounts.py` / `valuation.py`**: since one account can hold mixed
  positions, `position_values`/`account_equity` can no longer take a single
  `market_data` instance. They change to accept a `market_data_for_symbol`
  lookup and call it per-position internally, fetching each position's quote
  from whichever provider matches *that position's* symbol.
- **`valuation.py` snapshots**: the per-account trading-day skip-gate is
  **removed**. Rather than deciding "whose calendar governs a mixed account,"
  `take_snapshots` simply takes a snapshot every day the scheduled job runs,
  for every account, always — simpler, and correct once any account can hold
  crypto that moves on weekends/holidays. (Stock-only accounts will now also
  get weekend/holiday snapshots showing unchanged equity — harmless, and
  arguably more informative than a gap.)
- **`market.py`**: `/market/quote/{symbol}` and `/market/bars/{symbol}` route
  by symbol shape directly — a "-" in the symbol skips the stock providers
  entirely and goes straight to the crypto stack (cheap, unambiguous, no
  wasted round-trip).
- **`jobs.py`**: `run_process_pending` runs both `deps.execution.process_pending`
  and `deps.crypto_execution.process_pending` in the same 2-minute job (one
  job, not two). `run_snapshots` passes `market_data_for_symbol` through to
  `take_snapshots`.
- **`strategy/runner.py`**: `Context` (what a strategy script calls
  `ctx.buy(...)`/`ctx.sell(...)`/`ctx.get_quote(...)` on) also routes
  per-call by the symbol argument, so a single strategy could trade both a
  stock and a crypto pair in the same run, exactly like a human using the
  Trade page. No `Strategy.asset_class` attribute — the runner itself needs
  no per-strategy asset-class knowledge.

## Frontend

- **Types**: `Order.qty`, `PositionValue.qty`, `Trade.qty` become `string`
  (was `number`) — matching the backend's Decimal-as-string convention already
  used for money. No `Account.asset_class` field.
- **New `lib/qty.ts`**: a small library separate from `lib/money.ts`
  (quantities aren't USD amounts — no `$` prefix, no fixed 2dp, and crypto
  needs up to 8dp vs. money's 4dp). Exports `isValidQty(s, allowFractional)`
  and `formatQty(s)` (trims trailing zeros, no currency formatting).
  `mulMoney` (price × qty, for the order-ticket cost preview) gains a variant
  that correctly rescales when multiplying a 4dp money string by an
  up-to-8dp qty string.
- **`OrderTicket`**: qty input becomes a plain decimal-aware text field;
  whether it accepts fractions is derived from the symbol itself — a "-" in
  the symbol allows fractional input (hint: "up to 8 decimal places"),
  otherwise whole numbers only (hint: "whole shares").
- **Positions grouping**: `PositionsTable` renders two grouped sections —
  **Stocks** then **Crypto** — split client-side by symbol shape. No
  subtotals (P&L stays blended per-account); just a grouped, labeled list.
- **Orders/Journal tagging**: these are chronological, time-ordered lists
  (newest-first) — regrouping them by asset class would break the "what
  happened recently" reading order. Instead each row gets a small stock/crypto
  tag next to its symbol; order is preserved.
- **Trade page**: structurally unchanged; only the qty input's
  behavior/hint responds to the symbol as above.

## Error Handling

- `CoinbaseData`/`BinanceData` follow the exact same `UnknownSymbolError`/
  `MarketDataError` contract as the Phase 1 providers, so `MarketDataService`'s
  existing fallback logic works for the crypto stack completely unchanged.
- New stored-rejection reasons in `TradingEngine.place_order`:
  `"quantity must be a whole share count"` and
  `"quantity precision exceeds 8 decimal places"` — rejections stored on the
  order with a reason, not exceptions, consistent with Phase 1's pattern.
- Crypto market orders always fill immediately when a quote is available (the
  calendar is always "open"); on a market-data outage they reject with
  `"market data unavailable"`, same as Phase 1's stock behavior.

## Testing

- Same approach as Phase 1 throughout: offline fakes for engine/valuation
  tests (the existing `FakeMarketData`/`FakeCalendar` pattern works unchanged
  for constructing a second engine/adapter with different fake instances),
  `httpx.MockTransport` for the two new providers (no live network calls).
- Existing Phase 1 tests for `position_values`/`account_equity`/
  `take_snapshots` get mechanically updated for the new lookup-function
  signatures (pass a lambda returning the fake instead of the fake directly).
- New tests: order routing by symbol shape (stock vs. crypto pipeline
  selection for placement/cancellation/quotes), fractional qty validation and
  rejection, mixed-account valuation (one account, both position types,
  correct blended equity), `CryptoCalendar`'s always-open/UTC-midnight-expiry
  semantics, `CoinbaseData`/`BinanceData` response parsing and error mapping.
- Frontend: new tests for `lib/qty.ts`, `OrderTicket`'s fractional-qty path
  for a crypto symbol, `PositionsTable`'s grouping logic.

## Implementation Plan Split

Following Phase 1's precedent, Phase 2 is implemented as two plans, executed
and reviewed the same way (subagent-driven, task-by-task, task + final
whole-branch review):

1. **Backend crypto plan** — data model, calendar, providers, routing,
   engine/valuation/scheduler/strategy-runner changes.
2. **Frontend crypto plan** — types, qty lib, table grouping/tagging, order
   ticket changes.
