# Live Trading (Phase 3) — Design

**Date:** 2026-07-05
**Status:** Approved by user (brainstorming session)
**Builds on:** `2026-07-03-paper-trading-platform-design.md` (Phase 1),
`2026-07-04-crypto-phase-2-design.md` (Phase 2)

## Purpose

Phase 3 of the roadmap: live trading through Alpaca's brokerage API, behind the
same `ExecutionAdapter` interface the platform was designed around. The app
gains a **Live** section alongside the existing (now "Paper") experience —
same login, freely navigable between the two — with live orders executed by
Alpaca's real order-matching engine rather than our simulator.

"Live" initially points at Alpaca's **paper-trading endpoint**
(`https://paper-api.alpaca.markets`): real broker mechanics — real order
lifecycle, real fills against real market data, real rejections — but virtual
money. The user has no funded Alpaca brokerage account yet; when they open one,
switching to real money is purely configuration (base URL + keys), zero new
code.

**In scope:** US stocks/ETFs, manual trading via the web UI only, market and
limit orders, day and GTC, whole shares, long-only — the Phase 1 order feature
set, executed by Alpaca.

**Out of scope:** crypto on live accounts (rejected at placement; can be added
later the way Phase 2 added it to paper), strategies trading live accounts
(strategies remain paper-only), options/futures, margin, shorting, fractional
shares, multiple live accounts.

## Requirements

- Both paper and live trading coexist: all existing paper accounts and behavior
  are untouched; live is additive.
- One live account, mirroring one Alpaca trading account.
- **Alpaca is the source of truth for live fills and cash.** Our engine mirrors
  Alpaca's decisions into the same local `Order`/`Fill`/`Position` tables so
  every existing feature (dashboard, valuation, journal, equity snapshots)
  works unchanged for the live account. (Chosen over a full proxy with no local
  ledger, which would duplicate most read paths for no benefit — "Option A".)
- Placing a live order requires an explicit confirmation step in the UI.
- Without trading keys configured, the platform runs exactly as today and the
  Live UI section shows a "not configured" message instead of erroring.
- Money math remains `Decimal` everywhere; frontend qty/money math remains
  exact (BigInt fixed-point).

## Configuration

Three new settings (in `app/config.py` `Settings`, `.env.example` updated):

| Setting | Default | Meaning |
|---|---|---|
| `PT_ALPACA_TRADING_KEY_ID` | `""` | Trading API key ID |
| `PT_ALPACA_TRADING_SECRET` | `""` | Trading API secret |
| `PT_ALPACA_TRADING_BASE` | `https://paper-api.alpaca.markets` | Trading API base URL; change to `https://api.alpaca.markets` to go real-money |

These are separate from the existing market-data keys (`PT_ALPACA_KEY_ID`/
`PT_ALPACA_SECRET`) because trading keys are endpoint-specific — paper-trading
keys only work against the paper endpoint.

**Live trading is enabled iff `PT_ALPACA_TRADING_KEY_ID` is non-empty.** When
disabled: no live account is created, no live adapter is constructed, no sync
job runs, and `GET /api/accounts` simply contains no live account (which is how
the frontend detects "not configured").

## Data Model

Additions only — no changes to existing columns:

- `Account.mode: str = "paper"` — `"paper"` or `"live"`. All existing accounts
  are and remain `"paper"` (SQLite column added with that default).
- `Account.last_synced_at: datetime | None = None` — when the live sync job
  last completed successfully. NULL for paper accounts.
- `Account.sync_detail: str | None = None` — NULL when the last sync found
  local positions consistent with Alpaca's; otherwise a human-readable
  description of the mismatch (surfaced as a UI warning).
- `Order.broker_order_id: str | None = None` — Alpaca's order ID, set when the
  order is accepted by Alpaca. NULL for paper orders.

At startup, when live trading is enabled and no account named `"live"` exists,
one is created: `name="live"`, `kind="manual"`, `mode="live"`, cash `0` — the
first cash sync immediately replaces the placeholder `0` with Alpaca's real
figure (and the startup sequence runs one sync synchronously so the UI never
shows the placeholder).

## AlpacaLiveAdapter

`backend/app/engine/alpaca_live_adapter.py`. Same interface as `SimAdapter`
(`place_order`, `cancel_order`, `process_pending`) so routing code treats them
uniformly. Uses `httpx.Client` with an injectable transport for tests, exactly
like the market-data providers.

### place_order

1. Call `engine.place_order(...)` first — the same local validation and cash
   reservation as paper (unknown symbol, insufficient cash vs. the synced live
   cash balance, whole-share/positive qty, idempotency key). If the engine
   rejects, stop; nothing is sent to Alpaca. The books stay balanced locally by
   construction.
2. Submit to Alpaca: `POST /v2/orders` with
   `{symbol, qty, side, type ("market"|"limit"), time_in_force ("day"|"gtc"),
   limit_price?, client_order_id: str(order.id)}`. `client_order_id` makes a
   retried submission idempotent on Alpaca's side — a duplicate submit returns
   the existing broker order rather than creating a second one.
3. On HTTP 200/201: store `broker_order_id` from the response; the local order
   stays `pending` until the poll mirrors a terminal state.
4. On Alpaca rejection (4xx) or network failure: `engine.reject_order` with the
   reason from Alpaca's error body (or `"broker unreachable: <error>"`),
   releasing the reservation. Consistent with the existing "reject rather than
   guess" policy — an order is never left in doubt.

### process_pending (mirroring)

Runs inside the existing 2-minute scheduler job. For each local `pending`
order belonging to a live-mode account:

- If it has no `broker_order_id` it was rejected at submit time and cannot be
  pending — skipped defensively.
- `GET /v2/orders/{broker_order_id}` and map Alpaca's `status`:
  - `filled` → `engine.apply_fill(session, order, price)` where `price` is
    Alpaca's `filled_avg_price` — the real execution price, for market and
    limit orders alike (unlike the simulator, which assumes the limit price).
  - `canceled` → `engine.cancel_order` path result: local status `cancelled`,
    reservation released.
  - `expired` → `engine.expire_order`.
  - `rejected` → `engine.reject_order` with Alpaca's reason.
  - Any non-terminal state (`new`, `accepted`, `partially_filled`, …) → leave
    pending; check again next cycle. A partial fill is mirrored only when it
    completes (fills become `filled` with the final average price) — matching
    the local single-fill-per-order model.
- Network/API failure while polling → that order simply waits for the next
  cycle (same policy as paper limit checks).

No local expiry logic for live orders: Alpaca expires its own day orders and
the poll mirrors it.

### cancel_order

`DELETE /v2/orders/{broker_order_id}`. On 204/404/422 alike, the local order is
**not** finalized immediately — the next poll mirrors Alpaca's final state,
because a cancel can race a fill and Alpaca's answer wins. The API response to
the user therefore returns the order still `pending` with the cancel request
sent; the UI already re-fetches orders, so the state resolves visibly within a
poll cycle. If the DELETE itself fails with a network error, surface the error
to the caller (the user can retry).

### Cash & position sync

`sync_account(session)` on the adapter, run by a scheduler job every 10 minutes
(and once at startup):

- `GET /v2/account` → overwrite the live account's `cash` with Alpaca's `cash`
  figure. External effects (fees, corporate actions, out-of-band trades) can
  therefore never cause silent drift; local cash is a cache of Alpaca's truth
  that self-corrects within minutes.
- `GET /v2/positions` → compare against local positions (symbol and qty). On
  mismatch, set `sync_detail` to a description (e.g.
  `"AAPL: local 10, alpaca 12"`); on match, clear it to NULL. Detect and
  surface — never auto-heal positions, since fabricating local fills would
  corrupt the fill-traceable ledger.
- On success set `last_synced_at`; on failure leave both fields as they were
  (the UI shows staleness via the timestamp's age).

## Routing

The account gains a routing dimension alongside Phase 2's symbol shape:

- `AppDeps.execution_for(account, symbol)`: live-mode account →
  `live_execution`; otherwise the existing symbol-shape routing
  (`crypto_execution` / `execution`).
- Placing a crypto symbol (`is_crypto_symbol`) on the live account is rejected
  at placement with reason `"crypto not supported in live trading yet"`.
- **Ownership** (the Phase 2 `owns_symbol` lesson, one dimension up): all three
  adapters share one `orders` table, so each must only process its own pending
  orders. The ownership predicate becomes order-based (`owns_order(order)`,
  evaluated with the account's mode): the two `SimAdapter`s own
  paper-mode orders partitioned by symbol shape as today; the live adapter
  owns live-mode orders only.
- Market data routing is unchanged: the Live section's quotes/charts use the
  existing `market_data_for_symbol` (display only — Alpaca decides real fills).
- `StrategyRunner` is untouched: strategy accounts are paper-mode, so
  strategies cannot reach the live adapter.

## API

- `POST /api/orders` and `DELETE /api/orders/{id}` route via
  `deps.execution_for(account, symbol)`.
- `TradeOut` (journal trades) gains `account_mode: str` so the frontend can tag
  and filter Paper vs Live (mode is not derivable from the symbol, unlike the
  crypto tag).
- `AccountOut` gains `mode: str`, `last_synced_at: datetime | None`, and
  `sync_detail: str | None`.
- No new endpoints: the live account appears in the existing `GET
  /api/accounts` list and detail routes, and its equity snapshots come from the
  existing snapshot job (which already covers all accounts).

## Frontend

The nav gains a top-level mode switcher: **Paper | Live**.

- **Paper** is today's app unchanged: Dashboard, Trade, Orders, Journal,
  Strategies, account switcher.
- **Live** is a parallel section (`/live`, `/live/trade`, `/live/orders`)
  scoped to the live account, reusing the existing components parameterized by
  account. A persistent amber **LIVE** badge in the header identifies the
  section on every live page.
  - **Live Dashboard:** equity, cash, positions, open orders, equity curve —
    plus "Synced with Alpaca as of \<time\>" and, when `sync_detail` is set, a
    warning banner showing it.
  - **Live Trade:** same chart/quote/ticket layout. The order ticket enforces
    whole shares and shows a **confirmation step**: clicking Buy/Sell reveals
    "Place LIVE buy: 10 AAPL, market, day — Confirm / Back"; only Confirm
    submits. Paper's one-click ticket is untouched.
  - **Live Orders:** the existing orders table against the live account.
- **Journal (shared):** remains one page covering all accounts. Each trade
  gains a **Paper**/**Live** tag (from `account_mode`, same visual pattern as
  Phase 2's Stock/Crypto tags), and a filter — **All | Paper | Live** — in the
  same UI pattern as the Orders page's status filters.
- **Not configured:** when no live account exists in `GET /api/accounts`, the
  Live section renders a single message — "Live trading not configured — set
  PT_ALPACA_TRADING_KEY_ID / PT_ALPACA_TRADING_SECRET" — instead of its pages.

## Error Handling

- Submit failure (Alpaca 4xx or unreachable) → local rejection with stored
  reason, reservation released; `client_order_id` idempotency means a retry
  can never double-submit.
- Poll failure → pending live orders wait for the next cycle.
- Sync failure → last-known cash retained; `last_synced_at` ages visibly
  rather than showing wrong-but-fresh numbers.
- Cancel racing a fill → Alpaca's terminal state wins via the poll.
- Position drift → detected by sync, shown as a warning, never auto-healed.
- All new scheduler work runs inside the existing error-contained job wrappers
  (one failure never kills the scheduler).

## Testing

Same discipline as Phases 1–2: pytest, deterministic and offline.

- **Adapter unit tests** via `httpx.MockTransport` (the Coinbase/Binance test
  pattern): submit success stores `broker_order_id`; submit rejection and
  network failure reject locally and release the reservation; poll mirrors
  `filled` (at `filled_avg_price`), `canceled`, `expired`, `rejected`;
  non-terminal states stay pending; cancel defers to the poll (cancel-vs-fill
  race); cash sync overwrites cash and sets `last_synced_at`; position
  mismatch sets `sync_detail`, match clears it.
- **Routing tests:** live account → live adapter; paper accounts unchanged;
  crypto-on-live rejected with the exact reason string; ownership — SimAdapters
  never process live-mode pending orders and vice versa.
- **Startup tests:** live account created iff trading keys configured;
  idempotent across restarts.
- **API tests** via FastAPI's test client with a mocked transport: place,
  cancel, journal `account_mode`, accounts `mode`/sync fields.
- **Frontend component tests:** confirm step (present on live ticket, absent on
  paper), LIVE badge, journal Paper/Live tags and filter, not-configured state,
  sync warning banner.

## Implementation Plan Split

Two plans, like Phase 2:

1. **Backend** — settings, data-model columns, `AlpacaLiveAdapter`, mirroring,
   sync job, ownership/routing, API fields, startup account creation.
2. **Frontend** — Paper/Live nav split, Live section pages, confirm step,
   journal tags + filter, not-configured and sync-warning states. Written after
   the backend PR merges.
