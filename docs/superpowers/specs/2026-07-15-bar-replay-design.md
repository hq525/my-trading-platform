# Bar Replay — Design

**Date:** 2026-07-15
**Status:** Approved by user (brainstorming session; design hardened by a three-lens
adversarial review before approval)
**Builds on:** Phases 1–3 (stock paper trading, crypto, live trading)

## Purpose

TradingView-style bar replay on top of the paper-trading engine: pick symbols and a
start date, step through historical daily bars under a virtual clock, and trade
against them — manually and with the platform's Python strategies — without ever
seeing the future. A replay session is a deterministic sandbox: same session, same
steps, same fills, offline after creation.

**Chosen approach:** preloaded per-session bars + a dedicated replay execution path
with next-bar fills ("Approach A"), over (B) reusing `SimAdapter` with a virtual
clock (fills at the close you just watched; limit fills miss intrabar touches) and
(C) a fully separate replay engine (duplicates the engine/valuation/UI that Phases
1–3 built fencing mechanisms to share).

## Requirements (user decisions)

- **Daily bars only.** One step = one bar. Minute bars are out of scope.
- **Manual trading AND strategies.** Each session freezes a list of enabled
  strategies at creation; each runs once per step.
- **Accounts mirror the platform's founding design:** each session gets its own
  manual account plus one account per enabled strategy, so manual vs per-strategy
  performance compares on equal footing over identical bars.
- **Multi-symbol universe per session** (stocks and/or crypto). Orders for symbols
  outside the universe are rejected.
- Money/qty math stays `Decimal` (backend) / BigInt fixed-point (frontend). $0
  recurring cost holds — replay uses only the existing free data providers.

## Data Model

New tables (via `create_all`) and one additive column (via `_NEW_COLUMNS`):

- `ReplaySession`: `id`, `name` (default `"{symbols} from {start_date}"`),
  `symbols` (JSON list), `start_date`, `cursor_date` (date of the latest visible
  bar), `end_date` (max bar date captured at creation — exhaustion is
  `cursor_date >= end_date`, stable forever), `strategies` (JSON list of names,
  frozen at creation), `starting_cash`, `created_at`.
- `ReplayBar`: `session_id`, `symbol`, `date`, OHLC (`SqliteDecimal`), `volume`,
  `UniqueConstraint(session_id, symbol, date)`. **Per-session, not shared**:
  providers retro-adjust history (splits/dividends), so shared bars would let a
  later session's preload rewrite a mid-flight session's world — "frozen at
  creation" must be literal. Per-session bars also make exhaustion stable and
  deletion a simple cascade. Storage is trivial at this scale.
- `Account.replay_session_id: int | None` (additive migration). Replay accounts:
  `mode="replay"`, `kind="manual"`, names `replay:{sid}:manual` and
  `replay:{sid}:strategy:{Name}`, cash = session `starting_cash`.
- `EquitySnapshot` is **reused** with virtual dates: each step writes one snapshot
  per session account, valued at that bar's closes, keyed by the virtual date.
  `UniqueConstraint(account_id, date)` is safe: the cursor strictly advances, and
  the real snapshot job skips replay accounts (below).

## Preload (session creation)

The generic `MarketDataService.get_bars` path is unsuitable (Alpaca's window
heuristic returns the *oldest* N bars of a `2×limit`-day window, losing recent
months; the default `limit=200` silently truncates history). Preload therefore
fetches directly, per asset class:

- **Stocks:** `YFinanceData.get_bars(symbol, "1D", limit=520)` (~2 years of
  trading days; yfinance's own hard cap is 2 years).
- **Crypto:** `BinanceData` with `limit=730`, falling back to `CoinbaseData`
  (~300-day window) on error.
- Any bar dated today is dropped (partial bar).

Validation, all before any DB write: symbols list non-empty; every strategy name
exists in the discovered registry; **every symbol has a bar on or before
`start_date`** (eliminating any "before a symbol's first bar" ambiguity) — else
400 with per-symbol earliest/latest coverage. Then session + accounts + bars are
inserted in **one transaction**: a provider failure cannot orphan accounts or a
session row. `cursor_date` starts at the earliest bar date ≥ `start_date`.
Session data is frozen from this moment; it is never refreshed.

## Virtual Clock

- Virtual now = `cursor_date` 21:00 UTC — a fixed daily convention. Replay never
  consults trading calendars (`MarketCalendar`/`CryptoCalendar` are unused in the
  replay path), so the convention's exact hour has no consumer; it exists so
  `placed_at`/`filled_at`/`as_of` are coherent and journal ordering works (fills
  within one step share the timestamp; existing sorts already fall back to id).
- `TradingEngine` gains `now_fn=utcnow` (constructor param, default preserves all
  existing behavior). It stamps `placed_at` and `filled_at` at exactly its two
  existing explicit call sites; replay engines are constructed with the session's
  virtual clock.
- `ReplayMarketData(session)` implements the provider interface from the session's
  own bars: `get_quote(symbol)` = close of the latest bar ≤ cursor, `as_of` = that
  bar's date 21:00 UTC; symbols outside `session.symbols` raise
  `UnknownSymbolError` (this single check enforces the universe for manual orders
  and strategies alike); a symbol whose coverage has ended (latest bar < cursor
  with no future bars) raises `MarketDataError`, so the engine rejects new orders
  with the standard "market data unavailable" reason. This guard is
  placement-scoped: the replay **valuation** branch (account detail, snapshots)
  reads the latest bar close ≤ cursor directly from the session's bars, so
  positions in coverage-ended symbols stay valued at their last available close
  instead of erroring. `get_bars` returns bars ≤ cursor only — no-lookahead is a
  SQL `WHERE`, not provider discipline.

## Execution & Fill Semantics

One `ReplayExecution` service in `AppDeps` (no config gate — always constructed).
It resolves per call: `account.replay_session_id` → session → per-call
`ReplayMarketData` + `TradingEngine(replay_md, now_fn=virtual clock)`.

- **Placement** (`place_order`): delegates to the bare engine — validation and
  cash reservation at the current bar's close, order stays `pending`. Nothing
  ever fills at placement (the fill-now branch lives only in `SimAdapter`, which
  replay does not use). **Cancel**: `engine.cancel_order`.
- **Fills happen on step, against the NEW bar** (decide on close N, execute on
  bar N+1):
  - Market: fill at the bar's **open**, with the `SimAdapter`-style
    insufficient-cash-at-fill rejection if the open gapped beyond
    reservation + free cash.
  - Limit: **gap-aware** — buy fills at `open` if `open <= limit`, else at
    `limit` if `low <= limit`; sell fills at `open` if `open >= limit`, else at
    `limit` if `high >= limit`. (Fill-always-at-limit would systematically
    punish limit users on every gap-through; the open is known from the bar.)
    Buy-side reservations (`limit × qty`) always cover the equal-or-better fill.
- **Time-in-force, redefined for bars and documented in the UI:**
  - `day` = exactly one bar: expires after the first step in which its symbol
    HAD a bar and it did not fill. In mixed stock+crypto sessions a Friday stock
    order sleeps through weekend crypto steps and lives exactly for Monday's bar.
  - `gtc` persists until filled/cancelled/coverage end.
  - When a symbol's coverage ends mid-session (delisting/provider gap), its
    pending orders auto-expire on the first step past its last bar, and new
    orders are rejected — reserved cash can never be locked forever.
  - When the session reaches `end_date`, all remaining pending orders are
    cancelled (reported in the step response).

## The Step Pipeline

`POST /api/replay/sessions/{id}/step?steps=N` (N = 1–250; a one-year stock replay
should not be 250 clicks). All stepping and deletion for a session runs under a
**per-session in-process lock** (single-process app), so concurrent Step
double-clicks and step-vs-delete races serialize.

Each step, in order:

1. If exhausted: no-op before any write; return `{exhausted: true}`.
2. Advance `cursor_date` to the next date with at least one bar among the
   session's symbols (stock-only sessions skip weekends/holidays naturally).
3. Fill pass over this session's pending orders (scoped via
   `replay_session_id`), with a `session.refresh` of each order immediately
   before filling — the `SimAdapter` guard pattern — so a cancel racing a step
   can never fill a cancelled order.
4. Day-order expiry pass (rules above).
5. Write `EquitySnapshot` rows (virtual date, closes ≤ cursor) for every session
   account, and **commit** — cursor, fills, expiries, and snapshots land
   atomically, before any strategy code runs.
6. Run each session strategy once (schedules like `daily_after_close`/cron are
   ignored — one run per bar), each inside its own try/except. `Context` is
   wired to the session's `ReplayExecution` and `ReplayMarketData` and the
   strategy's own session account. Errors go into the step response's
   `strategy_errors` (no `StrategyRun` rows); a strategy file deleted from disk
   since creation is a per-strategy error, never a 500. The session's frozen
   strategy list is authoritative — the global `StrategyState.enabled` toggle is
   a paper-trading concept and is ignored; `runner.run_strategy` is not reused.
7. Return `{cursor_date, fills, expired, cancelled_at_exhaustion,
   strategy_errors, exhausted}` (aggregated when `steps > 1`).

The ordering in 5→6 is load-bearing: strategy orders are only ever placed after
the cursor whose close they saw is durable, so a crash and re-entered step can
never fill an order against the bar whose close informed it. A crash during 6 may
skip remaining strategies for that bar — visible, honest, and non-corrupting.

## Isolation

Every surface that could touch replay state, and its fence (all verified against
the code during the adversarial review):

| Surface | Fence |
|---|---|
| 2-minute `run_process_pending` (both sim adapters) | `owns_order` predicates change from `mode != "live"` to `mode == "paper"` |
| 16:10 `take_snapshots` job (all accounts, live quotes, real dates) | skip `mode == "replay"` |
| `GET /api/accounts/{id}` valuation (**the worst leak**: it prices positions at today's live quotes, and `OrderTicket`/`PositionsTable`/the comparison table all consume it) | replay branch in `account_detail`: value replay accounts from the session's bars ≤ cursor. Also removes any live-provider dependency (no 503s in replay) |
| `execution_for(account, symbol)` and the cancel route | `mode == "replay"` branch → `ReplayExecution` |
| Paper account switcher / default selection (frontend) | filter tightens to `mode === "paper"` |
| Journal page all-accounts fan-out (frontend) | exclude replay accounts; the session page shows per-account trades via the existing `/api/journal?account_id=` |
| Type unions | `Account.mode` and `Trade.account_mode` gain `"replay"`; main journal MODES filter stays `all/paper/live` |
| `StrategyRunner` | untouched — it queries exact `strategy:{name}` account names; `replay:*` names are invisible |
| Live pipeline | already fenced by construction (`mode == "live"` joins) |

**Session deletion** (`DELETE /api/replay/sessions/{id}`): one transaction, under
the session lock, deleting journal notes (for the session's orders) → fills →
orders → positions → snapshots → accounts → bars → session. Journal notes are in
the cascade because SQLite here neither enforces FKs nor avoids rowid reuse — an
orphaned note would eventually reattach to an unrelated future trade.

## API

New router `backend/app/api/replay.py` (same auth dependency):

- `POST /api/replay/sessions` — create + preload (validation and transaction
  semantics above); response = session detail including per-symbol coverage.
- `GET /api/replay/sessions` — list (id, name, symbols, start/cursor/end dates,
  exhausted).
- `GET /api/replay/sessions/{id}` — detail incl. the session's accounts (manual +
  per-strategy, with ids) and coverage.
- `POST /api/replay/sessions/{id}/step?steps=N` — the pipeline above.
- `DELETE /api/replay/sessions/{id}` — cascade delete.
- `GET /api/replay/sessions/{id}/bars/{symbol}` — bars ≤ cursor (chart data).
- `GET /api/replay/sessions/{id}/quote/{symbol}` — current-bar close + `as_of`.

Reused unchanged: order placement/cancel (`/api/accounts/{id}/orders`,
`/api/orders/{id}/cancel`) via the `execution_for` replay branch; account detail
(replay valuation branch); snapshots (equity curves); journal + notes (account-
scoped). Rejected orders carry stored reasons per platform convention
(out-of-universe → `unknown symbol: X`; past-coverage → `market data
unavailable`).

## Frontend

- **NavBar:** third mode — **Paper | Live | Replay** (`/replay` prefix); account
  switcher hidden in replay mode.
- **`/replay`:** session list (symbols, cursor/end, exhausted badge,
  delete-with-confirm) + create form: name (optional, default provided), symbols,
  start date, starting cash, and strategy checkboxes listing **names only, all
  unchecked** (the global enabled flag is ignored and not shown).
- **`/replay/[id]`** (gated like `LiveGate` — `ReplayGate`/`useReplaySession`):
  - Header: cursor date, coverage end, **Step +1 / +5 / +20**, exhausted banner,
    and the latest step's results (fills, expiries, strategy errors).
  - `CandleChart` over bars ≤ cursor with a symbol switcher. (Full redraw per
    step is acceptable at ≤ ~520 daily bars; incremental `series.update()` is a
    later polish.)
  - `QuoteBadge` with `now` = virtual now (prop already exists), so data-age
    reads in replay time.
  - `OrderTicket` bound to the session's manual account, priced from the replay
    quote endpoint, `live=false`, with added copy: **"Market orders fill at the
    next bar's open · day = one bar"** (the est-cost preview is indicative).
  - **Account tabs** (manual + each strategy): positions/equity via the
    replay-branch account detail, open orders via `OrdersView`, trades via the
    journal endpoint rendered by a session-scoped list (no Paper/Live badge).
  - **Equity comparison table** across all session accounts (equity, P&L vs
    starting cash) plus the manual account's `EquityCurve` from snapshots.

## Error Handling

- Creation: provider down or insufficient coverage → 400 with per-symbol detail;
  zero partial state. Unknown strategy name → 400. Empty symbols → 400.
- Step: exhausted → no-op 200; concurrent step/delete → serialized by the lock;
  per-strategy exceptions contained and reported.
- Placement/cancel: engine-rejection pattern with stored reasons; cancel of a
  filled order → 409 (existing behavior).
- Deleting a session is destructive and confirmed in the UI; bars/accounts/orders
  are unrecoverable (by design — sessions are cheap to recreate).

## Honest Limitations (documented, not hidden)

- Daily bars only; the intrabar path is unknown — OHLC-touch fills are a
  convention, not a simulation of order-book reality.
- History reach ≈ 2 years for stocks (yfinance cap) and Binance-served crypto;
  ~300 days if the Coinbase fallback is used. Start dates beyond coverage fail
  clearly at creation.
- No rewind: restarting means creating a new session (sessions are cheap and
  deterministic).
- No-lookahead is enforced through the provided `Context`/API only. Strategies
  are arbitrary Python and could import their own data sources; replay does not
  claim to sandbox them.
- All fills within one step share one virtual timestamp (ordering falls back to
  id, which existing queries already do).
- Replay's `day` = one bar differs from paper crypto's next-UTC-midnight expiry —
  a strategy tuned in replay will see slightly different day-order behavior in
  paper/live.
- Sessions are frozen at creation and never gain new bars.

## Testing

Backend (pytest, deterministic, offline; provider fetches mocked with
`httpx.MockTransport` / fakes):

- Preload: coverage validation per symbol, partial-bar drop, transaction
  atomicity on provider failure, per-provider fetch paths.
- Fill semantics table: market at open; gap-up insufficient-cash rejection
  (reservation released); buy/sell limit touch; **gap-through fills at the
  better price (open)**; no fill at placement.
- TIF: day = one bar; weekend-skip in mixed sessions; GTC persistence;
  coverage-end auto-expiry + placement rejection; exhaustion cancel-all.
- Step: cursor advance over sparse calendars; atomic commit before strategies
  (a strategy order can never fill against the bar whose close it saw);
  exhausted no-op; `steps=N` aggregation; cancel-vs-step refresh guard;
  concurrent-step serialization.
- Strategies: run once per bar; session list authoritative (global toggle
  ignored); error containment incl. missing/deleted strategy class;
  out-of-universe symbol surfaces as a per-step strategy error.
- Isolation regressions: `run_process_pending` never touches replay orders;
  `take_snapshots` skips replay accounts; `execution_for`/cancel routing;
  replay-branch valuation uses session bars (and never live providers).
- Snapshots: virtual dates, one per account per step, feed the existing
  snapshots endpoint.
- Deletion: full cascade including journal notes; lock interaction.
- Engine: `now_fn` stamps `placed_at`/`filled_at`; default behavior unchanged.

Frontend (Vitest + Testing Library): nav third mode; create/list/delete flows;
session page step flow with results; chart receives bars ≤ cursor; ticket copy
and wiring; account tabs; equity comparison; paper-switcher and journal-fan-out
exclusion of replay accounts; `QuoteBadge` virtual now.

## Implementation Plan Split

1. **Backend** — engine `now_fn`, models + migration, preload, `ReplayMarketData`,
   `ReplayExecution` + step pipeline, isolation fences, valuation branch, API.
2. **Frontend** — nav mode, sessions list/create, session workbench, type unions,
   switcher/journal fencing. Written after the backend PR merges.
