# Live Trading (Phase 3) Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Paper/Live nav split with a `/live` section (dashboard, trade, orders) scoped to the live account, a confirm step on the live order ticket, and journal Paper/Live tags with an All | Paper | Live filter.

**Architecture:** The backend (merged PR #5) exposes `mode`/`last_synced_at`/`sync_detail` on accounts and `account_mode` on trades. The frontend routes on that: the paper section filters the live account out of the switcher; the live section (`/live/*`) is gated by a `LiveGate` that finds the `mode === "live"` account or shows the not-configured message; `OrderTicket` gains `accountId`/`live` props for the confirm-step flow; trade/orders page bodies are extracted into shared views parameterized by account. Spec: `docs/superpowers/specs/2026-07-05-live-trading-phase-3-design.md` (Frontend section).

**Tech Stack:** Next.js 15 App Router, TypeScript strict, TanStack Query v5, Tailwind v4, Vitest + Testing Library.

## Global Constraints

- Money/qty cross the API as strings; all arithmetic goes through `lib/money.ts`/`lib/qty.ts` (BigInt) — never parseFloat on money.
- Exact copy (verbatim, tested):
  - Not-configured message: `Live trading not configured — set PT_ALPACA_TRADING_KEY_ID / PT_ALPACA_TRADING_SECRET.`
  - Badge text: `LIVE` (amber, in the NavBar on every live page).
  - Journal tags: `Paper` / `Live`; filter buttons `all` / `paper` / `live` (lowercase content, capitalize CSS, same pattern as the orders status filters).
  - Live ticket: primary button `Place LIVE order`; confirm summary `Place LIVE {side}: {qty} {symbol}, {type}[ @ {limitPrice}], {TIF}`; buttons `Confirm` / `Back`; crypto hint `Crypto is not supported in live trading`.
  - Sync line: `Synced with Alpaca as of {formatDateTime(last_synced_at)}` or `Not yet synced with Alpaca`.
- The live dashboard shows NO "Total P&L" card — the live account's `starting_cash` is a meaningless `0` (backend ledger note); equity/cash/since-close only.
- Live detection = an account with `mode === "live"` in `GET /api/accounts`. 503s from placement/cancel surface through the existing `ApiError` rendering — no special-casing beyond what exists.
- The paper section (account switcher + default account selection) must exclude the live account.
- The live ticket allows whole shares only and blocks crypto symbols client-side (server rejects too).
- Every task ends green: `cd frontend && npm test` (51 passing at branch start; each task adds more) and `cd frontend && npm run typecheck`.

## File Structure

| File | Responsibility |
|---|---|
| `lib/types.ts` | + `Account.mode/last_synced_at/sync_detail`, `Trade.account_mode` |
| `app/account-context.tsx`, `components/AccountSwitcher.tsx` | paper-only account selection |
| `components/OrderTicket.tsx` | + `accountId`/`live` props, confirm step, crypto block |
| `components/NavBar.tsx` | Paper \| Live switcher, LIVE badge, live links, hide switcher |
| `app/live/live-context.tsx` (new) | `LiveGate` + `useLiveAccount()` |
| `app/live/layout.tsx` (new) | wraps live pages in `LiveGate` |
| `app/live/page.tsx` (new) | live dashboard (sync line, warning banner, no Total P&L) |
| `components/TradeView.tsx` (new) | trade page body, parameterized ticket |
| `components/OrdersView.tsx` (new) | orders page body, parameterized account |
| `app/live/trade/page.tsx`, `app/live/orders/page.tsx` (new) | live trade/orders |
| `app/journal/page.tsx` | cross-account trades, Paper/Live tags, All \| Paper \| Live filter |

---

### Task 1: Types, fixtures, and paper-only account selection

**Files:**
- Modify: `frontend/lib/types.ts`
- Modify: `frontend/app/account-context.tsx`
- Modify: `frontend/components/AccountSwitcher.tsx`
- Modify (fixtures only): `frontend/tests/order-ticket.test.tsx:26`, `frontend/tests/dashboard.test.tsx:33`, `frontend/tests/journal.test.tsx:17,22-33`, `frontend/tests/orders-page.test.tsx:22`
- Test: `frontend/tests/account-switcher.test.tsx` (new)

**Interfaces:**
- Consumes: backend fields already served by the merged API.
- Produces: `Account` gains `mode: "paper" | "live"`, `last_synced_at: string | null`, `sync_detail: string | null`; `Trade` gains `account_mode: "paper" | "live"`. Every later task relies on these. AccountSwitcher/AccountProvider only ever select paper accounts.

**Why atomic:** widening required fields breaks `npm run typecheck` in every test file that builds an `Account`/`Trade` literal — types and fixtures must land together.

- [ ] **Step 1: Widen the types**

In `frontend/lib/types.ts`, `Account` becomes:

```ts
export interface Account {
  id: number;
  name: string;
  kind: "manual" | "strategy";
  mode: "paper" | "live";
  cash: string;
  starting_cash: string;
  last_synced_at: string | null;
  sync_detail: string | null;
}
```

and `Trade` gains one field after `note`:

```ts
  note: string | null;
  account_mode: "paper" | "live";
```

- [ ] **Step 2: Update the four fixture files**

Every `manual = { ... }` account literal gains `mode: "paper" as const, last_synced_at: null, sync_detail: null`. Exact replacements:

`frontend/tests/order-ticket.test.tsx:26`:
```ts
const manual = {
  id: 1, name: "manual", kind: "manual" as const, mode: "paper" as const,
  cash: "1000", starting_cash: "1000", last_synced_at: null, sync_detail: null,
};
```

`frontend/tests/dashboard.test.tsx:33`:
```ts
const manual = {
  id: 1, name: "manual", kind: "manual" as const, mode: "paper" as const,
  cash: "99000", starting_cash: "100000", last_synced_at: null, sync_detail: null,
};
```

`frontend/tests/orders-page.test.tsx:22`:
```ts
const manual = {
  id: 1, name: "manual", kind: "manual" as const, mode: "paper" as const,
  cash: "1000", starting_cash: "1000", last_synced_at: null, sync_detail: null,
};
```

`frontend/tests/journal.test.tsx:17` — same shape as orders-page's `manual` (cash `"1000"`), and both trade literals in the `api.journal` mock gain `account_mode: "paper" as const`:
```ts
  vi.mocked(api.journal).mockResolvedValue([
    {
      order_id: 5, symbol: "SPY", side: "sell", qty: "5", price: "120",
      commission: "0", realized_pnl: "100", filled_at: "2026-07-02T15:30:00",
      note: "took profits into strength", account_mode: "paper" as const,
    },
    {
      order_id: 4, symbol: "SPY", side: "buy", qty: "10", price: "100",
      commission: "0", realized_pnl: null, filled_at: "2026-07-01T15:30:00",
      note: null, account_mode: "paper" as const,
    },
  ]);
```

- [ ] **Step 3: Write the failing tests for paper-only selection**

Create `frontend/tests/account-switcher.test.tsx`:

```tsx
import { screen, waitFor } from "@testing-library/react";
import { renderWithClient } from "./utils";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { ...actual.api, accounts: vi.fn() } };
});

import { AccountProvider, useAccount } from "@/app/account-context";
import { AccountSwitcher } from "@/components/AccountSwitcher";
import { api } from "@/lib/api";

const manual = {
  id: 1, name: "manual", kind: "manual" as const, mode: "paper" as const,
  cash: "1000", starting_cash: "1000", last_synced_at: null, sync_detail: null,
};
const live = {
  id: 9, name: "live", kind: "manual" as const, mode: "live" as const,
  cash: "50000", starting_cash: "0", last_synced_at: null, sync_detail: null,
};

function ShowAccount() {
  const { accountId } = useAccount();
  return <p data-testid="selected">{accountId ?? "none"}</p>;
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
});

it("lists only paper accounts in the switcher", async () => {
  vi.mocked(api.accounts).mockResolvedValue([manual, live]);
  renderWithClient(
    <AccountProvider>
      <AccountSwitcher />
    </AccountProvider>,
  );
  expect(await screen.findByRole("option", { name: "manual" })).toBeInTheDocument();
  expect(screen.queryByRole("option", { name: "live" })).not.toBeInTheDocument();
});

it("never defaults the paper section to the live account", async () => {
  localStorage.setItem("pt-account", "9"); // stale selection of the live account
  vi.mocked(api.accounts).mockResolvedValue([manual, live]);
  renderWithClient(
    <AccountProvider>
      <ShowAccount />
    </AccountProvider>,
  );
  await waitFor(() => expect(screen.getByTestId("selected")).toHaveTextContent("1"));
});
```

- [ ] **Step 4: Run tests to verify the new ones fail**

Run: `cd frontend && npx vitest run tests/account-switcher.test.tsx`
Expected: FAIL — the switcher lists "live" and the stored live id `9` is accepted as the selection.

- [ ] **Step 5: Implement paper-only selection**

In `frontend/app/account-context.tsx`, replace the `useEffect` body:

```tsx
  useEffect(() => {
    if (accountId === null && accounts?.length) {
      const paper = accounts.filter((a) => a.mode !== "live");
      if (!paper.length) return;
      const stored = Number(localStorage.getItem("pt-account") ?? "");
      const fallback = paper.find((a) => a.kind === "manual") ?? paper[0];
      setAccountId(paper.some((a) => a.id === stored) ? stored : fallback.id);
    }
  }, [accounts, accountId]);
```

In `frontend/components/AccountSwitcher.tsx`, filter before rendering:

```tsx
export function AccountSwitcher() {
  const { accountId, setAccountId } = useAccount();
  const { data: accounts } = useQuery({ queryKey: ["accounts"], queryFn: api.accounts });
  const paper = accounts?.filter((a) => a.mode !== "live") ?? [];
  if (!paper.length || accountId === null) return null;
  return (
    <select
      aria-label="Account"
      value={accountId}
      onChange={(e) => setAccountId(Number(e.target.value))}
      className="rounded border border-gray-700 bg-gray-950 px-2 py-1 text-sm text-gray-200"
    >
      {paper.map((a) => (
        <option key={a.id} value={a.id}>
          {a.name}
        </option>
      ))}
    </select>
  );
}
```

- [ ] **Step 6: Run all tests and typecheck**

Run: `cd frontend && npm test && npm run typecheck`
Expected: all pass (53), typecheck clean.

- [ ] **Step 7: Commit**

```bash
git add frontend/lib/types.ts frontend/app/account-context.tsx frontend/components/AccountSwitcher.tsx frontend/tests/account-switcher.test.tsx frontend/tests/order-ticket.test.tsx frontend/tests/dashboard.test.tsx frontend/tests/journal.test.tsx frontend/tests/orders-page.test.tsx
git commit -m "feat: account mode types and paper-only account selection"
```

---

### Task 2: OrderTicket live mode — confirm step, whole shares, crypto block

**Files:**
- Modify: `frontend/components/OrderTicket.tsx`
- Test: `frontend/tests/order-ticket.test.tsx` (append)

**Interfaces:**
- Consumes: `Account.mode` types (Task 1); existing `useAccount`, `isCryptoSymbol`, `isValidQty`.
- Produces: `OrderTicket({ symbol, quotePrice, accountId?, live? })` — `accountId` overrides the paper context's account; `live` turns on the confirm step, forces whole shares, and blocks crypto symbols. Task 5's live trade page passes both.

- [ ] **Step 1: Write the failing tests**

Append to `frontend/tests/order-ticket.test.tsx`:

```tsx
function setupLive(quotePrice?: string, symbol = "SPY") {
  vi.mocked(api.accounts).mockResolvedValue([manual]);
  vi.mocked(api.accountDetail).mockResolvedValue({ ...manual, equity: "1000", positions: [] });
  return renderWithClient(
    <AccountProvider>
      <OrderTicket symbol={symbol} quotePrice={quotePrice} accountId={9} live />
    </AccountProvider>,
  );
}

it("live mode requires an explicit confirmation before submitting", async () => {
  vi.mocked(api.placeOrder).mockResolvedValue({
    id: 11, account_id: 9, symbol: "SPY", side: "buy", order_type: "market",
    tif: "day", qty: "5", limit_price: null, status: "pending", reject_reason: null,
    placed_at: "2026-07-05T15:00:00",
  });
  setupLive("100");
  await userEvent.clear(screen.getByLabelText(/quantity/i));
  await userEvent.type(screen.getByLabelText(/quantity/i), "5");
  await userEvent.click(screen.getByRole("button", { name: /place live order/i }));
  expect(api.placeOrder).not.toHaveBeenCalled();
  expect(screen.getByText(/Place LIVE buy: 5 SPY, market, DAY/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /^confirm$/i }));
  await waitFor(() => expect(api.placeOrder).toHaveBeenCalled());
  const [accountId] = vi.mocked(api.placeOrder).mock.calls[0];
  expect(accountId).toBe(9); // the live account, not the paper context's
  expect(await screen.findByText(/pending/i)).toBeInTheDocument();
});

it("live confirmation can be backed out of", async () => {
  setupLive("100");
  await userEvent.click(screen.getByRole("button", { name: /place live order/i }));
  await userEvent.click(screen.getByRole("button", { name: /^back$/i }));
  expect(screen.queryByText(/Place LIVE buy/)).not.toBeInTheDocument();
  expect(api.placeOrder).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: /place live order/i })).toBeInTheDocument();
});

it("live mode blocks crypto symbols and forces whole shares", async () => {
  setupLive("65000", "BTC-USD");
  expect(screen.getByLabelText(/quantity/i)).toHaveAccessibleName(/whole shares/i);
  expect(screen.getByText(/crypto is not supported in live trading/i)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /place live order/i })).toBeDisabled();
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run tests/order-ticket.test.tsx`
Expected: the 7 existing tests pass; the 3 new ones FAIL (no `accountId`/`live` props, no "Place LIVE order" button).

- [ ] **Step 3: Implement**

In `frontend/components/OrderTicket.tsx`:

Signature and account resolution:

```tsx
export function OrderTicket({
  symbol,
  quotePrice,
  accountId: accountIdProp,
  live = false,
}: {
  symbol: string;
  quotePrice?: string;
  accountId?: number;
  live?: boolean;
}) {
  const ctx = useAccount();
  const accountId = accountIdProp ?? ctx.accountId;
```

(remove the old `const { accountId } = useAccount();` line — everything downstream keeps using `accountId`.)

State and derived flags — add `confirming`, change `allowFractional`, add `cryptoBlocked`:

```tsx
  const [result, setResult] = useState<Order | null>(null);
  const [confirming, setConfirming] = useState(false);
  ...
  const allowFractional = !live && isCryptoSymbol(symbol);
  const cryptoBlocked = live && isCryptoSymbol(symbol);
```

`canSubmit` gains `!cryptoBlocked`:

```tsx
  const canSubmit =
    accountId !== null &&
    qtyValid &&
    !cryptoBlocked &&
    !insufficient &&
    !place.isPending &&
    (type === "market" || (limitPrice.trim().length > 0 && cost !== null));
```

Extract the submit handler (identical body to the old button `onClick`, plus resetting `confirming`):

```tsx
  const submit = () => {
    setConfirming(false);
    setResult(null);
    place.mutate({
      symbol,
      side,
      order_type: type,
      qty: qty,
      tif,
      ...(type === "limit" ? { limit_price: limitPrice } : {}),
      idempotency_key: crypto.randomUUID(),
    });
  };
```

Replace the submit button block (keep the exact existing `className` on the primary button):

```tsx
      {live && confirming ? (
        <div className="space-y-2 rounded border border-amber-800 bg-amber-950 p-3">
          <p className="text-sm text-amber-300">
            Place LIVE {side}: {qty} {symbol}, {type}
            {type === "limit" ? ` @ ${limitPrice}` : ""}, {tif.toUpperCase()}
          </p>
          <div className="flex gap-2">
            <button
              onClick={submit}
              className="flex-1 rounded bg-amber-600 px-3 py-2 font-medium text-black hover:bg-amber-500"
            >
              Confirm
            </button>
            <button
              onClick={() => setConfirming(false)}
              className="flex-1 rounded border border-gray-700 px-3 py-2 text-gray-300 hover:border-gray-500"
            >
              Back
            </button>
          </div>
        </div>
      ) : (
        <button
          onClick={() => (live ? setConfirming(true) : submit())}
          disabled={!canSubmit}
          className={`w-full rounded px-3 py-2 font-medium text-white disabled:opacity-50 ${
            side === "buy" ? "bg-emerald-700 hover:bg-emerald-600" : "bg-red-800 hover:bg-red-700"
          }`}
        >
          {place.isPending ? "Placing…" : live ? "Place LIVE order" : "Place order"}
        </button>
      )}
      {cryptoBlocked && (
        <p className="text-xs text-amber-400">Crypto is not supported in live trading</p>
      )}
```

- [ ] **Step 4: Run tests and typecheck**

Run: `cd frontend && npx vitest run tests/order-ticket.test.tsx && npm run typecheck`
Expected: 10 passed, typecheck clean.

- [ ] **Step 5: Run the full suite**

Run: `cd frontend && npm test`
Expected: all pass (56).

- [ ] **Step 6: Commit**

```bash
git add frontend/components/OrderTicket.tsx frontend/tests/order-ticket.test.tsx
git commit -m "feat: live order ticket with confirm step, whole shares, and crypto block"
```

---

### Task 3: NavBar — Paper | Live switcher, LIVE badge, live links

**Files:**
- Modify: `frontend/components/NavBar.tsx`
- Test: `frontend/tests/navbar.test.tsx` (replace)

**Interfaces:**
- Consumes: `usePathname` (existing), `Account.mode` fixtures (Task 1).
- Produces: the live section is entered at `/live`; live pages get the amber `LIVE` badge and links Dashboard→`/live`, Trade→`/live/trade`, Orders→`/live/orders`; the account switcher is hidden on live pages. Tasks 4–5 create those routes.

- [ ] **Step 1: Replace the test file**

Replace `frontend/tests/navbar.test.tsx` entirely with:

```tsx
import { screen } from "@testing-library/react";
import { renderWithClient } from "./utils";

let pathname = "/";
vi.mock("next/navigation", () => ({ usePathname: () => pathname }));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { ...actual.api, accounts: vi.fn(async () => []) } };
});

import { AccountProvider } from "@/app/account-context";
import { NavBar } from "@/components/NavBar";
import { api } from "@/lib/api";

const manual = {
  id: 1, name: "manual", kind: "manual" as const, mode: "paper" as const,
  cash: "1000", starting_cash: "1000", last_synced_at: null, sync_detail: null,
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.accounts).mockResolvedValue([]);
});

function renderNav() {
  return renderWithClient(
    <AccountProvider>
      <NavBar />
    </AccountProvider>,
  );
}

it("renders the paper links and no LIVE badge on paper pages", () => {
  pathname = "/";
  renderNav();
  for (const label of ["Dashboard", "Trade", "Orders", "Journal", "Strategies"]) {
    expect(screen.getByRole("link", { name: label })).toBeInTheDocument();
  }
  expect(screen.getByRole("link", { name: "Paper" })).toHaveAttribute("href", "/");
  expect(screen.getByRole("link", { name: "Live" })).toHaveAttribute("href", "/live");
  expect(screen.queryByText("LIVE")).not.toBeInTheDocument();
});

it("shows the LIVE badge and live links in the live section", () => {
  pathname = "/live/trade";
  renderNav();
  expect(screen.getByText("LIVE")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Dashboard" })).toHaveAttribute("href", "/live");
  expect(screen.getByRole("link", { name: "Trade" })).toHaveAttribute("href", "/live/trade");
  expect(screen.getByRole("link", { name: "Orders" })).toHaveAttribute("href", "/live/orders");
  expect(screen.queryByRole("link", { name: "Journal" })).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Strategies" })).not.toBeInTheDocument();
});

it("hides the account switcher in the live section only", async () => {
  vi.mocked(api.accounts).mockResolvedValue([manual]);
  pathname = "/";
  const paper = renderNav();
  expect(await screen.findByRole("combobox")).toBeInTheDocument();
  paper.unmount();

  pathname = "/live";
  renderNav();
  expect(screen.getByText("LIVE")).toBeInTheDocument();
  expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run tests/navbar.test.tsx`
Expected: FAIL — no Paper/Live links, no LIVE badge, switcher always rendered.

- [ ] **Step 3: Implement**

Replace `frontend/components/NavBar.tsx` with:

```tsx
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { api } from "@/lib/api";
import { AccountSwitcher } from "@/components/AccountSwitcher";

const paperLinks = [
  { href: "/", label: "Dashboard" },
  { href: "/trade", label: "Trade" },
  { href: "/orders", label: "Orders" },
  { href: "/journal", label: "Journal" },
  { href: "/strategies", label: "Strategies" },
];

const liveLinks = [
  { href: "/live", label: "Dashboard" },
  { href: "/live/trade", label: "Trade" },
  { href: "/live/orders", label: "Orders" },
];

const modeTab = (active: boolean) =>
  `px-3 py-1 ${active ? "bg-gray-800 text-white" : "text-gray-400 hover:text-gray-200"}`;

export function NavBar() {
  const pathname = usePathname();
  const live = pathname === "/live" || pathname.startsWith("/live/");
  const links = live ? liveLinks : paperLinks;
  return (
    <header className="border-b border-gray-800 bg-gray-900">
      <nav className="mx-auto flex max-w-7xl items-center gap-1 px-4 py-2">
        <span className="mr-2 font-semibold text-gray-100">Trading</span>
        {live && (
          <span className="mr-2 rounded bg-amber-600 px-1.5 py-0.5 text-[10px] font-bold text-black">
            LIVE
          </span>
        )}
        <div className="mr-4 flex overflow-hidden rounded border border-gray-700 text-sm">
          <Link href="/" className={modeTab(!live)}>
            Paper
          </Link>
          <Link href="/live" className={modeTab(live)}>
            Live
          </Link>
        </div>
        {links.map((l) => (
          <Link
            key={l.href}
            href={l.href}
            className={`rounded px-3 py-1.5 text-sm ${
              pathname === l.href
                ? "bg-gray-800 text-white"
                : "text-gray-400 hover:text-gray-200"
            }`}
          >
            {l.label}
          </Link>
        ))}
        <div className="ml-auto flex items-center gap-2">
          {!live && <AccountSwitcher />}
          <button
            onClick={() => {
              void api.logout().then(() => {
                window.location.href = "/login";
              });
            }}
            className="rounded px-3 py-1.5 text-sm text-gray-400 hover:text-gray-200"
          >
            Log out
          </button>
        </div>
      </nav>
    </header>
  );
}
```

(The brand changes from "Paper Trading" to "Trading" — it now heads both sections.)

- [ ] **Step 4: Run tests and typecheck**

Run: `cd frontend && npx vitest run tests/navbar.test.tsx && npm run typecheck`
Expected: 3 passed, typecheck clean.

- [ ] **Step 5: Run the full suite**

Run: `cd frontend && npm test`
Expected: all pass (58 — this file went from 1 test to 3).

- [ ] **Step 6: Commit**

```bash
git add frontend/components/NavBar.tsx frontend/tests/navbar.test.tsx
git commit -m "feat: Paper/Live nav switcher with LIVE badge and live section links"
```

---

### Task 4: LiveGate and the live dashboard

**Files:**
- Create: `frontend/app/live/live-context.tsx`
- Create: `frontend/app/live/layout.tsx`
- Create: `frontend/app/live/page.tsx`
- Test: `frontend/tests/live-dashboard.test.tsx` (new)

**Interfaces:**
- Consumes: `Account.mode/last_synced_at/sync_detail` (Task 1), existing `EquityCurve`/`PositionsTable`/`OrdersTable`/`StatCard`, `formatDateTime` from `lib/format.ts`.
- Produces: `LiveGate` (renders children only when a live account exists; otherwise the not-configured message) and `useLiveAccount(): Account`, both from `app/live/live-context.tsx`. Task 5's pages call `useLiveAccount()` inside the same gate (applied by `app/live/layout.tsx`).

- [ ] **Step 1: Write the failing tests**

Create `frontend/tests/live-dashboard.test.tsx`:

```tsx
import { screen } from "@testing-library/react";
import { renderWithClient } from "./utils";

vi.mock("lightweight-charts", () => ({
  createChart: vi.fn(() => ({
    addSeries: vi.fn(() => ({ setData: vi.fn() })),
    priceScale: vi.fn(() => ({ applyOptions: vi.fn() })),
    timeScale: vi.fn(() => ({ fitContent: vi.fn() })),
    applyOptions: vi.fn(),
    remove: vi.fn(),
  })),
  CandlestickSeries: "C", HistogramSeries: "H", AreaSeries: "A",
}));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: {
      ...actual.api,
      accounts: vi.fn(), accountDetail: vi.fn(), snapshots: vi.fn(), orders: vi.fn(),
    },
  };
});

import { LiveGate } from "@/app/live/live-context";
import LiveDashboardPage from "@/app/live/page";
import { api } from "@/lib/api";

const manual = {
  id: 1, name: "manual", kind: "manual" as const, mode: "paper" as const,
  cash: "1000", starting_cash: "1000", last_synced_at: null, sync_detail: null,
};
const liveAcct = {
  id: 9, name: "live", kind: "manual" as const, mode: "live" as const,
  cash: "50000", starting_cash: "0",
  last_synced_at: "2026-07-05T12:00:00", sync_detail: null,
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.snapshots).mockResolvedValue([
    { date: "2026-07-04", equity: "50500", cash: "50000" },
  ]);
  vi.mocked(api.orders).mockResolvedValue([]);
});

function renderLive() {
  return renderWithClient(
    <LiveGate>
      <LiveDashboardPage />
    </LiveGate>,
  );
}

it("shows the not-configured message when no live account exists", async () => {
  vi.mocked(api.accounts).mockResolvedValue([manual]);
  renderLive();
  expect(
    await screen.findByText(
      "Live trading not configured — set PT_ALPACA_TRADING_KEY_ID / PT_ALPACA_TRADING_SECRET.",
    ),
  ).toBeInTheDocument();
  expect(api.accountDetail).not.toHaveBeenCalled();
});

it("shows equity, cash, since-close, and the sync line — but no Total P&L", async () => {
  vi.mocked(api.accounts).mockResolvedValue([manual, liveAcct]);
  vi.mocked(api.accountDetail).mockResolvedValue({
    ...liveAcct, equity: "51000", positions: [],
  });
  renderLive();
  expect(await screen.findByText("$51,000.00")).toBeInTheDocument(); // equity
  expect(screen.getByText("$50,000.00")).toBeInTheDocument(); // cash
  expect(screen.getByText("+$500.00")).toBeInTheDocument(); // since last close
  expect(screen.getByText(/Synced with Alpaca as of/)).toBeInTheDocument();
  expect(screen.queryByText("Total P&L")).not.toBeInTheDocument();
  expect(api.accountDetail).toHaveBeenCalledWith(9);
});

it("shows a warning banner when sync_detail reports position drift", async () => {
  vi.mocked(api.accounts).mockResolvedValue([manual, liveAcct]);
  vi.mocked(api.accountDetail).mockResolvedValue({
    ...liveAcct, sync_detail: "AAPL: local 10, alpaca 12", equity: "51000", positions: [],
  });
  renderLive();
  expect(await screen.findByText(/AAPL: local 10, alpaca 12/)).toBeInTheDocument();
});

it("shows the not-yet-synced note when last_synced_at is null", async () => {
  vi.mocked(api.accounts).mockResolvedValue([manual, { ...liveAcct, last_synced_at: null }]);
  vi.mocked(api.accountDetail).mockResolvedValue({
    ...liveAcct, last_synced_at: null, equity: "51000", positions: [],
  });
  renderLive();
  expect(await screen.findByText("Not yet synced with Alpaca")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run tests/live-dashboard.test.tsx`
Expected: FAIL — `Cannot find module '@/app/live/live-context'`.

- [ ] **Step 3: Implement the gate**

Create `frontend/app/live/live-context.tsx`:

```tsx
"use client";

import { useQuery } from "@tanstack/react-query";
import { createContext, useContext } from "react";
import { api } from "@/lib/api";
import type { Account } from "@/lib/types";

const Ctx = createContext<Account | null>(null);

export function LiveGate({ children }: { children: React.ReactNode }) {
  const { data: accounts, isPending } = useQuery({
    queryKey: ["accounts"],
    queryFn: api.accounts,
  });
  if (isPending) return <p className="text-sm text-gray-500">Loading…</p>;
  const live = accounts?.find((a) => a.mode === "live");
  if (!live) {
    return (
      <div className="rounded border border-gray-800 bg-gray-900 p-4 text-sm text-gray-400">
        Live trading not configured — set PT_ALPACA_TRADING_KEY_ID / PT_ALPACA_TRADING_SECRET.
      </div>
    );
  }
  return <Ctx.Provider value={live}>{children}</Ctx.Provider>;
}

export function useLiveAccount(): Account {
  const v = useContext(Ctx);
  if (!v) throw new Error("useLiveAccount must be used inside LiveGate");
  return v;
}
```

Create `frontend/app/live/layout.tsx`:

```tsx
import { LiveGate } from "./live-context";

export default function LiveLayout({ children }: { children: React.ReactNode }) {
  return <LiveGate>{children}</LiveGate>;
}
```

- [ ] **Step 4: Implement the live dashboard**

Create `frontend/app/live/page.tsx`:

```tsx
"use client";

import { useQuery } from "@tanstack/react-query";
import { EquityCurve } from "@/components/EquityCurve";
import { OrdersTable } from "@/components/OrdersTable";
import { PositionsTable } from "@/components/PositionsTable";
import { StatCard } from "@/components/StatCard";
import { api, ApiError } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { formatUsd, isNeg, subMoney } from "@/lib/money";
import { useLiveAccount } from "./live-context";

function signed(value: string): { text: string; tone: "pos" | "neg" } {
  const neg = isNeg(value);
  return { text: `${neg ? "" : "+"}${formatUsd(value)}`, tone: neg ? "neg" : "pos" };
}

export default function LiveDashboardPage() {
  const live = useLiveAccount();
  const detail = useQuery({
    queryKey: ["account", live.id],
    queryFn: () => api.accountDetail(live.id),
    refetchInterval: 30_000,
  });
  const snapshots = useQuery({
    queryKey: ["snapshots", live.id],
    queryFn: () => api.snapshots(live.id),
  });
  const openOrders = useQuery({
    queryKey: ["orders", live.id, "pending"],
    queryFn: () => api.orders(live.id, "pending"),
    refetchInterval: 30_000,
  });

  if (detail.error instanceof ApiError && detail.error.status === 503) {
    return (
      <div className="rounded border border-amber-800 bg-amber-950 p-4 text-amber-300">
        Market data unavailable — account values cannot be computed right now.
      </div>
    );
  }
  if (!detail.data) return <p className="text-sm text-gray-500">Loading…</p>;

  const d = detail.data;
  const snaps = snapshots.data ?? [];
  const lastSnap = snaps.length ? snaps[snaps.length - 1] : null;
  const sinceClose = lastSnap ? signed(subMoney(d.equity, lastSnap.equity)) : null;

  return (
    <div className="space-y-6">
      <p className="text-xs text-gray-500">
        {d.last_synced_at
          ? `Synced with Alpaca as of ${formatDateTime(d.last_synced_at)}`
          : "Not yet synced with Alpaca"}
      </p>
      {d.sync_detail && (
        <div className="rounded border border-amber-800 bg-amber-950 p-3 text-sm text-amber-300">
          Position mismatch vs Alpaca: {d.sync_detail}
        </div>
      )}
      {/* No Total P&L card: the live account's starting_cash is a meaningless 0. */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatCard label="Equity" value={formatUsd(d.equity)} />
        <StatCard label="Cash" value={formatUsd(d.cash)} />
        {sinceClose && (
          <StatCard label="Since last close" value={sinceClose.text} tone={sinceClose.tone} />
        )}
      </div>
      {snaps.length > 0 && (
        <section>
          <h2 className="mb-2 text-sm font-semibold text-gray-400">Equity curve</h2>
          <EquityCurve snapshots={snaps} />
        </section>
      )}
      <section>
        <h2 className="mb-2 text-sm font-semibold text-gray-400">Positions</h2>
        <PositionsTable positions={d.positions} />
      </section>
      <section>
        <h2 className="mb-2 text-sm font-semibold text-gray-400">Open orders</h2>
        <OrdersTable orders={openOrders.data ?? []} />
      </section>
    </div>
  );
}
```

- [ ] **Step 5: Run tests and typecheck**

Run: `cd frontend && npx vitest run tests/live-dashboard.test.tsx && npm run typecheck`
Expected: 4 passed, typecheck clean.

- [ ] **Step 6: Run the full suite**

Run: `cd frontend && npm test`
Expected: all pass (62).

- [ ] **Step 7: Commit**

```bash
git add frontend/app/live/live-context.tsx frontend/app/live/layout.tsx frontend/app/live/page.tsx frontend/tests/live-dashboard.test.tsx
git commit -m "feat: live section gate and live dashboard with sync status"
```

---

### Task 5: Shared Trade/Orders views and the live trade/orders pages

**Files:**
- Create: `frontend/components/TradeView.tsx` (body moved from `app/trade/page.tsx`)
- Create: `frontend/components/OrdersView.tsx` (body moved from `app/orders/page.tsx`)
- Modify: `frontend/app/trade/page.tsx`, `frontend/app/orders/page.tsx` (become thin wrappers)
- Create: `frontend/app/live/trade/page.tsx`, `frontend/app/live/orders/page.tsx`
- Test: `frontend/tests/live-pages.test.tsx` (new)

**Interfaces:**
- Consumes: `OrderTicket`'s `accountId`/`live` props (Task 2), `useLiveAccount`/`LiveGate` (Task 4).
- Produces: `TradeView({ ticketAccountId?, liveTicket? })` and `OrdersView({ accountId })` in `components/`. The existing paper pages keep identical DOM (their current tests must pass unchanged — that is the regression gate for the extraction).

- [ ] **Step 1: Write the failing tests**

Create `frontend/tests/live-pages.test.tsx`:

```tsx
import { screen, waitFor } from "@testing-library/react";
import { renderWithClient } from "./utils";

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("lightweight-charts", () => ({
  createChart: vi.fn(() => ({
    addSeries: vi.fn(() => ({ setData: vi.fn() })),
    priceScale: vi.fn(() => ({ applyOptions: vi.fn() })),
    timeScale: vi.fn(() => ({ fitContent: vi.fn() })),
    applyOptions: vi.fn(),
    remove: vi.fn(),
  })),
  CandlestickSeries: "C", HistogramSeries: "H", AreaSeries: "A",
}));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: {
      ...actual.api,
      accounts: vi.fn(), accountDetail: vi.fn(), orders: vi.fn(),
      quote: vi.fn(), bars: vi.fn(),
    },
  };
});

import { AccountProvider } from "@/app/account-context";
import { LiveGate } from "@/app/live/live-context";
import LiveOrdersPage from "@/app/live/orders/page";
import LiveTradePage from "@/app/live/trade/page";
import { api } from "@/lib/api";
import type { Order } from "@/lib/types";

const manual = {
  id: 1, name: "manual", kind: "manual" as const, mode: "paper" as const,
  cash: "1000", starting_cash: "1000", last_synced_at: null, sync_detail: null,
};
const liveAcct = {
  id: 9, name: "live", kind: "manual" as const, mode: "live" as const,
  cash: "50000", starting_cash: "0", last_synced_at: null, sync_detail: null,
};
const liveOrder: Order = {
  id: 21, account_id: 9, symbol: "AAPL", side: "buy", order_type: "limit", tif: "gtc",
  qty: "5", limit_price: "150", status: "pending", reject_reason: null,
  placed_at: "2026-07-05T15:00:00",
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.accounts).mockResolvedValue([manual, liveAcct]);
});

it("live orders page lists the live account's orders", async () => {
  vi.mocked(api.orders).mockResolvedValue([liveOrder]);
  renderWithClient(
    <LiveGate>
      <LiveOrdersPage />
    </LiveGate>,
  );
  expect(await screen.findByText("AAPL")).toBeInTheDocument();
  await waitFor(() => expect(api.orders).toHaveBeenCalledWith(9, undefined));
});

it("live trade page renders the ticket in live mode against the live account", async () => {
  vi.mocked(api.quote).mockResolvedValue({
    symbol: "SPY", price: "100", as_of: "2026-07-05T15:00:00",
  });
  vi.mocked(api.bars).mockResolvedValue([]);
  vi.mocked(api.accountDetail).mockResolvedValue({
    ...liveAcct, equity: "50000", positions: [],
  });
  renderWithClient(
    <AccountProvider>
      <LiveGate>
        <LiveTradePage />
      </LiveGate>
    </AccountProvider>,
  );
  expect(
    await screen.findByRole("button", { name: /place live order/i }),
  ).toBeInTheDocument();
  await waitFor(() => expect(api.accountDetail).toHaveBeenCalledWith(9));
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run tests/live-pages.test.tsx`
Expected: FAIL — `Cannot find module '@/app/live/orders/page'` (and `.../trade/page`).

- [ ] **Step 3: Extract OrdersView**

Create `frontend/components/OrdersView.tsx` — the entire body of today's `app/orders/page.tsx` with `accountId` as a required prop (no `useAccount`, no `enabled` guards):

```tsx
"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { OrdersTable } from "@/components/OrdersTable";
import { api, ApiError } from "@/lib/api";

const FILTERS = ["all", "pending", "filled", "cancelled", "rejected", "expired"] as const;
type Filter = (typeof FILTERS)[number];

export function OrdersView({ accountId }: { accountId: number }) {
  const qc = useQueryClient();
  const [filter, setFilter] = useState<Filter>("all");
  const [cancelError, setCancelError] = useState<string | null>(null);

  const orders = useQuery({
    queryKey: ["orders", accountId, filter],
    queryFn: () => api.orders(accountId, filter === "all" ? undefined : filter),
    refetchInterval: 30_000,
  });

  const cancel = useMutation({
    mutationFn: (id: number) => api.cancelOrder(id),
    onSuccess: () => {
      setCancelError(null);
      void qc.invalidateQueries({ queryKey: ["orders", accountId] });
      void qc.invalidateQueries({ queryKey: ["account", accountId] });
    },
    onError: (e) =>
      setCancelError(e instanceof ApiError ? e.message : "Cancel failed"),
  });

  return (
    <div className="space-y-4">
      <div className="flex gap-1">
        {FILTERS.map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`rounded px-3 py-1.5 text-sm capitalize ${
              filter === f ? "bg-gray-800 text-white" : "text-gray-400 hover:text-gray-200"
            }`}
          >
            {f}
          </button>
        ))}
      </div>
      {cancelError && (
        <p className="rounded border border-red-900 bg-red-950 p-2 text-sm text-red-300">
          {cancelError}
        </p>
      )}
      <OrdersTable
        orders={orders.data ?? []}
        onCancel={(id) => cancel.mutate(id)}
        cancellingId={cancel.isPending ? (cancel.variables ?? null) : null}
      />
    </div>
  );
}
```

Replace `frontend/app/orders/page.tsx` with:

```tsx
"use client";

import { useAccount } from "@/app/account-context";
import { OrdersView } from "@/components/OrdersView";

export default function OrdersPage() {
  const { accountId } = useAccount();
  if (accountId === null) return null;
  return <OrdersView accountId={accountId} />;
}
```

- [ ] **Step 4: Extract TradeView**

Create `frontend/components/TradeView.tsx` — the body of today's `TradeContent` in `app/trade/page.tsx`, with the two new props threaded into `OrderTicket`:

```tsx
"use client";

import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { useState } from "react";
import { CandleChart } from "@/components/CandleChart";
import { OrderTicket } from "@/components/OrderTicket";
import { QuoteBadge } from "@/components/QuoteBadge";
import { api } from "@/lib/api";

export function TradeView({
  ticketAccountId,
  liveTicket = false,
}: {
  ticketAccountId?: number;
  liveTicket?: boolean;
}) {
  const params = useSearchParams();
  const [symbol, setSymbol] = useState(
    (params.get("symbol") ?? "SPY").toUpperCase(),
  );
  const [input, setInput] = useState(symbol);

  const quote = useQuery({
    queryKey: ["quote", symbol],
    queryFn: () => api.quote(symbol),
    refetchInterval: 15_000,
  });
  const bars = useQuery({
    queryKey: ["bars", symbol],
    queryFn: () => api.bars(symbol),
  });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-4">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            const s = input.trim().toUpperCase();
            if (s) {
              setSymbol(s);
              setInput(s);
            }
          }}
          className="flex items-center gap-2"
        >
          <input
            aria-label="Symbol"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            className="w-28 rounded border border-gray-700 bg-gray-950 px-3 py-1.5 text-sm uppercase text-gray-100 outline-none focus:border-gray-500"
          />
          <button
            type="submit"
            className="rounded border border-gray-700 px-3 py-1.5 text-sm text-gray-300 hover:border-gray-500"
          >
            Load
          </button>
        </form>
        <QuoteBadge quote={quote.data} error={quote.error ?? undefined} />
      </div>
      <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
        <section className="rounded-lg border border-gray-800 bg-gray-900 p-2">
          {bars.data ? (
            <CandleChart bars={bars.data} />
          ) : (
            <div className="flex h-[420px] items-center justify-center text-sm text-gray-500">
              {bars.isError ? "No chart data" : "Loading chart…"}
            </div>
          )}
        </section>
        <aside>
          <OrderTicket
            symbol={symbol}
            quotePrice={quote.data?.price}
            accountId={ticketAccountId}
            live={liveTicket}
          />
        </aside>
      </div>
    </div>
  );
}
```

Replace `frontend/app/trade/page.tsx` with:

```tsx
"use client";

import { Suspense } from "react";
import { TradeView } from "@/components/TradeView";

export default function TradePage() {
  return (
    <Suspense>
      <TradeView />
    </Suspense>
  );
}
```

- [ ] **Step 5: Create the live pages**

Create `frontend/app/live/orders/page.tsx`:

```tsx
"use client";

import { OrdersView } from "@/components/OrdersView";
import { useLiveAccount } from "../live-context";

export default function LiveOrdersPage() {
  const live = useLiveAccount();
  return <OrdersView accountId={live.id} />;
}
```

Create `frontend/app/live/trade/page.tsx`:

```tsx
"use client";

import { Suspense } from "react";
import { TradeView } from "@/components/TradeView";
import { useLiveAccount } from "../live-context";

export default function LiveTradePage() {
  const live = useLiveAccount();
  return (
    <Suspense>
      <TradeView ticketAccountId={live.id} liveTicket />
    </Suspense>
  );
}
```

- [ ] **Step 6: Run tests and typecheck**

Run: `cd frontend && npx vitest run tests/live-pages.test.tsx tests/orders-page.test.tsx && npm run typecheck`
Expected: live-pages 2 passed; orders-page 4 passed UNCHANGED (the extraction preserved the paper page's DOM); typecheck clean.

- [ ] **Step 7: Run the full suite**

Run: `cd frontend && npm test`
Expected: all pass (64).

- [ ] **Step 8: Commit**

```bash
git add frontend/components/TradeView.tsx frontend/components/OrdersView.tsx frontend/app/trade/page.tsx frontend/app/orders/page.tsx frontend/app/live/trade/page.tsx frontend/app/live/orders/page.tsx frontend/tests/live-pages.test.tsx
git commit -m "feat: live trade and orders pages via shared account-parameterized views"
```

---

### Task 6: Journal — cross-account trades, Paper/Live tags, All | Paper | Live filter

**Files:**
- Modify: `frontend/app/journal/page.tsx`
- Test: `frontend/tests/journal.test.tsx` (append + one fixture-driven behavior note)

**Interfaces:**
- Consumes: `Trade.account_mode` (Task 1), `useQueries` from `@tanstack/react-query`.
- Produces: the journal's trades list covers ALL accounts (merged, newest first) with a Paper/Live tag per trade and an `all | paper | live` filter. The stats cards intentionally stay bound to the selected paper account (the stats endpoint is per-account; this preserves existing behavior).

- [ ] **Step 1: Write the failing tests**

Append to `frontend/tests/journal.test.tsx`:

```tsx
const liveAcct = {
  id: 9, name: "live", kind: "manual" as const, mode: "live" as const,
  cash: "50000", starting_cash: "0", last_synced_at: null, sync_detail: null,
};
const liveTrade = {
  order_id: 21, symbol: "AAPL", side: "buy" as const, qty: "2", price: "150",
  commission: "0", realized_pnl: null, filled_at: "2026-07-03T15:30:00",
  note: null, account_mode: "live" as const,
};

it("merges live trades into the list and filters by mode", async () => {
  const paperTrade = {
    order_id: 4, symbol: "SPY", side: "buy" as const, qty: "10", price: "100",
    commission: "0", realized_pnl: null, filled_at: "2026-07-01T15:30:00",
    note: null, account_mode: "paper" as const,
  };
  vi.mocked(api.accounts).mockResolvedValue([manual, liveAcct]);
  vi.mocked(api.journal).mockImplementation(async (id: number) =>
    id === 9 ? [liveTrade] : [paperTrade],
  );
  renderWithClient(
    <AccountProvider>
      <JournalPage />
    </AccountProvider>,
  );
  expect(await screen.findByText(/AAPL/)).toBeInTheDocument();
  expect(screen.getByText(/SPY/)).toBeInTheDocument();
  expect(screen.getByText("Live")).toBeInTheDocument();
  expect(screen.getByText("Paper")).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: /^live$/i }));
  expect(screen.getByText(/AAPL/)).toBeInTheDocument();
  expect(screen.queryByText(/SPY/)).not.toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: /^paper$/i }));
  expect(screen.getByText(/SPY/)).toBeInTheDocument();
  expect(screen.queryByText(/AAPL/)).not.toBeInTheDocument();
});
```

Note for the implementer: the three existing tests in this file keep passing because `api.accounts` returns `[manual]` there — the merged query then covers exactly the one account, same data as before.

- [ ] **Step 2: Run tests to verify the new one fails**

Run: `cd frontend && npx vitest run tests/journal.test.tsx`
Expected: 3 existing pass; the new one FAILS (no filter buttons, live account's journal never fetched).

- [ ] **Step 3: Implement**

In `frontend/app/journal/page.tsx`:

Imports — add `useQueries`:

```tsx
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
```

Add above the component:

```tsx
const MODES = ["all", "paper", "live"] as const;
type Mode = (typeof MODES)[number];
```

Inside the component, replace the single `trades` query with the cross-account merge, and add the `mode` state:

```tsx
  const [mode, setMode] = useState<Mode>("all");

  const accounts = useQuery({ queryKey: ["accounts"], queryFn: api.accounts });
  const journals = useQueries({
    queries: (accounts.data ?? []).map((a) => ({
      queryKey: ["journal", a.id],
      queryFn: () => api.journal(a.id),
    })),
  });
  const loaded =
    accounts.data !== undefined && journals.every((q) => q.data !== undefined);
  const trades = journals
    .flatMap((q) => q.data ?? [])
    .filter((t) => mode === "all" || t.account_mode === mode)
    .sort((a, b) =>
      a.filled_at < b.filled_at ? 1 : a.filled_at > b.filled_at ? -1 : b.order_id - a.order_id,
    );
```

(The `stats` query and its cards stay exactly as they are — per selected account.)

The `save` mutation's `onSuccess` invalidation becomes prefix-wide so every account's journal refreshes:

```tsx
    onSuccess: () => {
      setEditing(null);
      void qc.invalidateQueries({ queryKey: ["journal"] });
    },
```

Add the filter buttons directly above the trades list `<div className="space-y-2">`:

```tsx
      <div className="flex gap-1">
        {MODES.map((m) => (
          <button
            key={m}
            onClick={() => setMode(m)}
            className={`rounded px-3 py-1.5 text-sm capitalize ${
              mode === m ? "bg-gray-800 text-white" : "text-gray-400 hover:text-gray-200"
            }`}
          >
            {m}
          </button>
        ))}
      </div>
```

In the trades list, iterate `trades` (not `trades.data`), and add the mode tag right after the existing Stock/Crypto tag span:

```tsx
              <span
                className={`rounded px-1.5 py-0.5 text-[10px] uppercase ${
                  t.account_mode === "live"
                    ? "bg-amber-900 text-amber-300"
                    : "bg-gray-800 text-gray-400"
                }`}
              >
                {t.account_mode === "live" ? "Live" : "Paper"}
              </span>
```

The empty state becomes:

```tsx
        {loaded && trades.length === 0 && (
          <p className="text-sm text-gray-500">No trades yet.</p>
        )}
```

- [ ] **Step 4: Run tests and typecheck**

Run: `cd frontend && npx vitest run tests/journal.test.tsx && npm run typecheck`
Expected: 4 passed, typecheck clean.

- [ ] **Step 5: Run the full suite and the production build**

Run: `cd frontend && npm test && npm run build`
Expected: all pass (65); build succeeds (catches any App Router issue with the new `/live` routes).

- [ ] **Step 6: Commit**

```bash
git add frontend/app/journal/page.tsx frontend/tests/journal.test.tsx
git commit -m "feat: journal covers all accounts with Paper/Live tags and mode filter"
```
