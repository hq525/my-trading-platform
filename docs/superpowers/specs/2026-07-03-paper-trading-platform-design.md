# Paper Trading Platform — Phase 1 Design

**Date:** 2026-07-03
**Status:** Approved by user (brainstorming session)

## Purpose

A personal paper-trading platform for practicing swing trading (holding periods of
days to weeks) of US stocks and ETFs, at near-zero recurring cost. Trades are placed
both manually through a web UI and automatically by Python strategies. The platform
is designed so it can later be extended to live trading and, beyond that, to
investing tools — without rewriting the core.

**Out of scope for Phase 1:** crypto, options, forex/futures, live trading,
backtesting, shorting, margin, fractional shares, multi-user support.

## Requirements

- Single user (the owner). One password protects the internet-facing deployment.
- US stocks and ETFs only; USD only; long-only cash account; whole shares.
- Manual trading via web UI and algorithmic trading via Python strategies, both
  against the same engine.
- Swing-trading cadence: quotes may be minutes old, but staleness must be visible.
- Recurring cost: $0 for data (free tiers only); $0–5/month for hosting
  (local run or one small VPS).
- Money math uses `Decimal` everywhere. Never floats.

## Architecture

Approach chosen: **own trading engine + pluggable execution adapter** (over
"Alpaca paper API as the engine" and "fully self-built including broker
connectivity"). Rationale: keeps account state, journal, and analytics in our own
database; supports any asset class later; going live means implementing one new
adapter rather than rewriting or migrating.

```
Next.js UI ──┐
             ├──> FastAPI core
Strategies ──┘      ├─ Accounts / Orders / Fills / Positions / Journal  (SQLite)
                    ├─ MarketDataService ── AlpacaData (primary)
                    │                     └─ YFinanceData (fallback)
                    ├─ Scheduler (APScheduler): limit checks, day-order expiry,
                    │                           equity snapshots, strategy runs
                    └─ ExecutionAdapter (interface)
                         ├─ SimAdapter          (Phase 1: simulated fills)
                         └─ AlpacaLiveAdapter   (Phase 3, not built now)
```

### Stack

| Concern      | Choice                                   | Why                                                        |
|--------------|------------------------------------------|------------------------------------------------------------|
| Backend      | Python 3.12, FastAPI                     | Trading/data ecosystem; same language as strategies         |
| Frontend     | Next.js + TypeScript                     | User preference; polished UI                                |
| Database     | SQLite via SQLAlchemy                    | Single user; zero setup; backup = copy a file; Postgres path stays open |
| Market data  | Alpaca free data API (primary), yfinance (fallback) | Both $0; Alpaca gives IEX quotes + historical bars; yfinance needs no key |
| Charts       | lightweight-charts (TradingView OSS)     | Free, standard candlestick library                          |
| Scheduling   | APScheduler in the backend process       | No separate worker infrastructure                           |
| Market hours | `exchange_calendars` (XNYS calendar)     | Correct holidays and half-days                              |
| Deployment   | Docker Compose                           | Same file runs locally ($0) or on a ~$5/mo VPS              |

Repo layout: monorepo with `backend/` and `frontend/`.

## Trading Engine (core domain)

### Accounts

- Each account: name, cash balance (USD), starting balance (default $100,000,
  configurable), commission setting (default $0/trade).
- One "manual" account for UI trading. **Each strategy gets its own account** so
  manual and per-strategy performance can be compared on equal footing.

### Orders

- Types: **market** and **limit**. Time-in-force: **day** and **GTC**.
- Long-only, whole shares, cash account.
- Lifecycle: `pending → filled | cancelled | rejected | expired`.
- Validation at placement rejects with a stored human-readable reason:
  unknown symbol, insufficient cash (for buys: qty × price + commission vs. cash,
  where price = the limit price for limit orders or the latest quote for market
  orders; for sells: qty vs. position), non-positive quantity.
- Buying power is reserved when a buy order is accepted and released on
  cancel/expiry/rejection, so overlapping orders cannot overspend cash.

### Fill simulation (SimAdapter)

Deliberately simple — appropriate for swing trading, no order-book realism:

- **Market order, market open:** fills immediately at the latest trade price.
- **Market order, market closed:** queues; fills at the next session's opening price.
  If an overnight gap makes the fill price unaffordable (cost exceeds the order's
  reservation plus free cash), the order is rejected at fill time — cash never
  goes negative.
- **Limit order:** pending; a scheduler job re-checks every ~2 minutes during
  market hours. A buy fills when market price ≤ limit; a sell fills when market
  price ≥ limit. Fill price = the limit price.
- **Day orders** (market-queued or unfilled limit) expire at the 4:00 pm ET close
  of the session in which they are active — an order placed after hours is active
  in the next session and expires at that session's close.
- If no quote is available (both providers down), market orders are **rejected**
  rather than filled at a stale price; pending limit orders wait for the next
  successful check.

### Positions and P&L

- Positions are derived from fills: quantity, average cost (weighted), realized
  P&L on sells, unrealized P&L against the latest quote.
- Every cash/position change traces to a fill record; the engine is the only
  writer. The books must always balance (auditable by construction).
- A scheduler job snapshots each account's total equity (cash + positions marked
  at close prices) at each market close, producing the equity-curve history.

### Trade journal

Free-text notes attachable to any order/trade: why entered, what was learned.

### ExecutionAdapter interface

`place_order`, `cancel_order`, and fill notifications back into the engine.
`SimAdapter` is the only Phase 1 implementation. `AlpacaLiveAdapter` (Phase 3)
implements the same interface; engine, UI, strategies, and journal do not change.

## Market Data

- Internal interface: `get_quote(symbol)`, `get_bars(symbol, timeframe, range)`.
- Providers: **AlpacaData** (free API key, no brokerage account; IEX real-time-ish
  quotes, historical daily/minute bars) with automatic fallback to **YFinanceData**
  (no key; works out of the box before any signup).
- Quotes cached ~30 seconds to respect free-tier rate limits.
- Every quote carries its timestamp; the UI displays data age.

## Strategy Runner

A strategy is a Python class in `backend/strategies/`:

```python
class MyStrategy(Strategy):
    schedule = "daily_after_close"   # or a cron expression

    def run(self, ctx):
        bars = ctx.get_bars("SPY", "1D", 200)
        if some_signal(bars):
            ctx.buy("SPY", qty=10)   # market or limit
```

- `ctx` exposes exactly what the UI user has: quotes, bars, the strategy's own
  account state (positions, cash, orders), and order placement/cancellation.
  No direct DB access — keeps strategies portable to live trading.
- Discovered at startup; enabled/disabled per strategy from the UI.
- Every run is logged: start time, orders placed, errors.
- A strategy exception is contained: logged, surfaced in the UI, never affects
  the scheduler, the platform, or other strategies.
- The interface is designed so a backtest runner could drive the same strategy
  class later (backtesting itself is out of scope).

## Web UI

Five pages, dark theme, data-dense:

1. **Dashboard** — total equity, cash, day/total P&L, equity curve, positions
   table with unrealized P&L, open orders. Account switcher (manual vs. strategy
   accounts).
2. **Trade** — symbol search; candlestick chart with volume (lightweight-charts);
   order ticket (buy/sell, market/limit, quantity, day/GTC) with live cost preview
   vs. available cash; latest quote with data-age indicator.
3. **Orders** — all orders, filterable by status; cancel pending orders; fill details.
4. **Journal** — chronological trade log; attach/edit notes per trade; simple
   stats (win rate, average gain vs. average loss) once history exists.
5. **Strategies** — per strategy: enabled toggle, schedule, its account's equity
   curve, recent run log including errors.

**Auth:** single password → session cookie. No user management.

## Error Handling

- Data provider failure → automatic fallback; both down → quotes shown as
  unavailable, market orders rejected (see fill simulation).
- All scheduler jobs wrapped so one failure never kills the scheduler; failures
  logged and surfaced in the UI.
- Order placement accepts an optional client-supplied idempotency key; a repeated
  key returns the original order instead of creating a duplicate. Rejections
  carry reasons.

## Testing

TDD during implementation. Rigor concentrates in the engine:

- **pytest, deterministic and offline** via a fake market-data provider: order
  lifecycle, fill math, position averaging, realized/unrealized P&L, day-order
  expiry, after-hours queueing, buying-power reservation, insufficient-cash and
  unknown-symbol rejection.
- API endpoint tests via FastAPI's test client.
- Frontend: TypeScript strictness + a smoke test; correctness lives in the backend.

## Roadmap (later phases, not specced here)

2. **Crypto** — new data provider; 24/7 sessions in calendar logic.
3. **Live trading** — `AlpacaLiveAdapter` behind the existing interface.
4. **Options/futures and investing tools** — options/futures need paid data;
   investing tools: dividend tracking, allocation views, watchlist research.

## Costs

| Item                | Cost        |
|---------------------|-------------|
| Alpaca data API     | $0 (free tier) |
| yfinance            | $0          |
| Hosting             | $0 local / ~$5/mo VPS |
| **Total recurring** | **$0–5/month** |
