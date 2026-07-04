# Crypto Support — Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing Next.js frontend crypto-aware — fractional quantities in the order ticket, and visual stock/crypto separation in positions/orders/journal — against the now-merged crypto backend API, per the approved spec at `docs/superpowers/specs/2026-07-04-crypto-phase-2-design.md`.

**Architecture:** The backend's `Order`/`Position`/`Trade.qty` now cross the API as decimal strings (was whole-number JSON numbers), so every consumer's TypeScript types widen from `number` to `string`. Only `OrderTicket` ever does arithmetic with qty (via `mulMoney`); every other component (`PositionsTable`, `OrdersTable`, the journal page) only ever *displays* qty, so they compile and behave identically under the wider type with no logic changes needed beyond one-line test-mock updates. A shared `isCryptoSymbol` helper (mirroring the backend's `is_crypto_symbol`) is the one place the "-" rule is checked, used for the order ticket's fractional-input toggle and every visual grouping/tagging.

**Tech Stack:** Next.js 15 (App Router), TypeScript strict, TanStack Query, Vitest + React Testing Library — all already in place from Phase 1.

## Global Constraints

- All commands run from `frontend/` unless stated otherwise.
- **Money values from the API are strings** — unchanged from Phase 1, still routed through `lib/money.ts`'s BigInt helpers, never `parseFloat`/`Number` outside chart components.
- **Quantities are now also strings** — up to 8 decimal places for crypto (symbol contains "-"), whole numbers only for stock (no dash). `lib/qty.ts` is the single place both the fractional-input rule and the stock/crypto symbol-shape check live; import it everywhere, never reimplement `symbol.includes("-")` inline.
- Test output must be pristine. TDD: failing test → implement → pass → commit. Commit prefixes `feat:`/`fix:`/`test:`/`chore:`.
- Verify gate per task: `npm test` and `npm run typecheck` green; run `npm run build` at the end of the final task.
- The full test suite must stay green after every task.

## Current Frontend State (context, not a constraint)

The frontend currently has 37 passing Vitest tests (from Phase 1 + its final-review fixes). This plan's tasks are additive on top of that baseline.

## File Structure

```
frontend/
  lib/
    qty.ts                NEW — isCryptoSymbol, isValidQty, formatQty
    money.ts               MODIFY — mulMoney(price, qty) takes qty as a string
    types.ts                MODIFY — Order/PositionValue/Trade/PlaceOrderBody.qty: string
  components/
    OrderTicket.tsx          MODIFY — decimal-aware qty input, symbol-based hint
    PositionsTable.tsx      MODIFY — grouped Stocks/Crypto sections
    OrdersTable.tsx          MODIFY — stock/crypto tag per row
  app/
    journal/page.tsx        MODIFY — stock/crypto tag per trade row
  tests/
    qty.test.ts              NEW
    money.test.ts            MODIFY — mulMoney tests use string qty; add fractional cases
    order-ticket.test.tsx    MODIFY — qty literals to strings; fractional-qty test
    dashboard.test.tsx        MODIFY — one qty literal to string (mechanical)
    orders-page.test.tsx     MODIFY — one qty literal to string (mechanical)
    journal.test.tsx          MODIFY — two qty literals to strings (mechanical)
    positions-table.test.tsx NEW — grouping behavior
```

---

### Task 1: Quantity library, money math, types, and the order ticket

This is one atomic task rather than several, because `lib/types.ts`'s qty-field widening and `lib/money.ts`'s `mulMoney` signature change are only meaningful together with their one real consumer (`OrderTicket`) — splitting them would leave the suite red between commits (TypeScript would fail to compile `OrderTicket` against the new types before it's updated). The other three qty-displaying files (`PositionsTable`, `OrdersTable`, the journal page) never do arithmetic with qty — only render it as a JSX child, which compiles identically whether the value is a `number` or a `string` — so they need no logic changes, just one-line mock-literal fixes in their tests to keep `npm run typecheck` green.

**Files:**
- Create: `frontend/lib/qty.ts`, `frontend/tests/qty.test.ts`
- Modify: `frontend/lib/money.ts`, `frontend/lib/types.ts`, `frontend/components/OrderTicket.tsx`, `frontend/tests/money.test.ts`, `frontend/tests/order-ticket.test.tsx`, `frontend/tests/dashboard.test.tsx`, `frontend/tests/orders-page.test.tsx`, `frontend/tests/journal.test.tsx`

**Interfaces:**
- Consumes: nothing new from earlier tasks (this is the first task).
- Produces: `isCryptoSymbol(symbol: string): boolean` (the single "-" check, used by every later task); `isValidQty(s: string, allowFractional: boolean): boolean`; `formatQty(s: string): string`. `mulMoney(price: string, qty: string): string` (signature changed from `qty: number`). `Order.qty`, `PositionValue.qty`, `Trade.qty`, `PlaceOrderBody.qty` all become `string` (were `number`).

- [ ] **Step 1: Write the failing tests**

`frontend/tests/qty.test.ts` (new):

```typescript
import { formatQty, isCryptoSymbol, isValidQty } from "@/lib/qty";

it("dash means crypto", () => {
  expect(isCryptoSymbol("BTC-USD")).toBe(true);
  expect(isCryptoSymbol("AAPL")).toBe(false);
});

it("stock qty must be a whole number", () => {
  expect(isValidQty("10", false)).toBe(true);
  expect(isValidQty("10.5", false)).toBe(false);
  expect(isValidQty("0", false)).toBe(false);
});

it("crypto qty allows up to 8 decimal places", () => {
  expect(isValidQty("0.005", true)).toBe(true);
  expect(isValidQty("0.12345678", true)).toBe(true);
  expect(isValidQty("0.123456789", true)).toBe(false); // 9 places
  expect(isValidQty("10", true)).toBe(true); // whole numbers still fine for crypto
  expect(isValidQty("0", true)).toBe(false);
});

it("rejects garbage input", () => {
  expect(isValidQty("", false)).toBe(false);
  expect(isValidQty("abc", true)).toBe(false);
  expect(isValidQty("1.2.3", true)).toBe(false);
});

it("formatQty trims trailing zeros", () => {
  expect(formatQty("0.010000")).toBe("0.01");
  expect(formatQty("10")).toBe("10");
  expect(formatQty("10.00")).toBe("10");
});
```

Replace the three `mulMoney` assertions in `frontend/tests/money.test.ts` (find the existing `it("...", ...)` block asserting `mulMoney("100", 10)`, `mulMoney("123.45", 3)`, `mulMoney("0.1", 3)`) with:

```typescript
it("mulMoney multiplies exactly, no float drift", () => {
  expect(mulMoney("100", "10")).toBe("1000");
  expect(mulMoney("123.45", "3")).toBe("370.35");
  expect(mulMoney("0.1", "3")).toBe("0.3"); // no float 0.30000000000000004
});

it("mulMoney handles fractional crypto quantities", () => {
  expect(mulMoney("65000", "0.005")).toBe("325");
  expect(mulMoney("65000", "0.01")).toBe("650");
});

it("mulMoney rejects quantities with more than 8 decimal places", () => {
  expect(() => mulMoney("100", "0.123456789")).toThrow();
});
```

(Keep this file's other existing tests — `moneyToBig`, `bigToMoney`, `formatUsd`, etc. — untouched.)

Replace the qty-related assertions in `frontend/tests/order-ticket.test.tsx`. The full new file:

```tsx
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithClient } from "./utils";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: {
      ...actual.api,
      accounts: vi.fn(),
      accountDetail: vi.fn(),
      placeOrder: vi.fn(),
    },
  };
});

import { AccountProvider } from "@/app/account-context";
import { OrderTicket } from "@/components/OrderTicket";
import { api } from "@/lib/api";

beforeEach(() => {
  vi.clearAllMocks();
});

const manual = { id: 1, name: "manual", kind: "manual" as const, cash: "1000", starting_cash: "1000" };

function setup(quotePrice?: string, symbol = "SPY") {
  vi.mocked(api.accounts).mockResolvedValue([manual]);
  vi.mocked(api.accountDetail).mockResolvedValue({ ...manual, equity: "1000", positions: [] });
  return renderWithClient(
    <AccountProvider>
      <OrderTicket symbol={symbol} quotePrice={quotePrice} />
    </AccountProvider>,
  );
}

it("previews cost exactly and blocks unaffordable buys", async () => {
  setup("100");
  await userEvent.clear(screen.getByLabelText(/quantity/i));
  await userEvent.type(screen.getByLabelText(/quantity/i), "9");
  expect(await screen.findByText("$900.00")).toBeInTheDocument();
  await userEvent.clear(screen.getByLabelText(/quantity/i));
  await userEvent.type(screen.getByLabelText(/quantity/i), "11"); // 1100 > 1000 cash
  expect(await screen.findByText(/insufficient cash/i)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /place order/i })).toBeDisabled();
});

it("submits a market order with an idempotency key and shows the result", async () => {
  vi.mocked(api.placeOrder).mockResolvedValue({
    id: 7, account_id: 1, symbol: "SPY", side: "buy", order_type: "market",
    tif: "day", qty: "5", limit_price: null, status: "filled", reject_reason: null,
    placed_at: "2026-07-02T15:00:00",
  });
  setup("100");
  await userEvent.clear(screen.getByLabelText(/quantity/i));
  await userEvent.type(screen.getByLabelText(/quantity/i), "5");
  await userEvent.click(screen.getByRole("button", { name: /place order/i }));
  await waitFor(() => expect(api.placeOrder).toHaveBeenCalled());
  const [accountId, body] = vi.mocked(api.placeOrder).mock.calls[0];
  expect(accountId).toBe(1);
  expect(body).toMatchObject({ symbol: "SPY", side: "buy", order_type: "market", qty: "5", tif: "day" });
  expect(typeof body.idempotency_key).toBe("string");
  expect(body.idempotency_key!.length).toBeGreaterThan(10);
  expect(body).not.toHaveProperty("limit_price");
  expect(await screen.findByText(/filled/i)).toBeInTheDocument();
});

it("shows rejection reasons from the backend", async () => {
  vi.mocked(api.placeOrder).mockResolvedValue({
    id: 8, account_id: 1, symbol: "SPY", side: "buy", order_type: "limit",
    tif: "day", qty: "5", limit_price: "90", status: "rejected",
    reject_reason: "market data unavailable", placed_at: "2026-07-02T15:00:00",
  });
  setup("100");
  await userEvent.click(screen.getByRole("radio", { name: /limit/i }));
  await userEvent.type(screen.getByLabelText(/limit price/i), "90");
  await userEvent.clear(screen.getByLabelText(/quantity/i));
  await userEvent.type(screen.getByLabelText(/quantity/i), "5");
  await userEvent.click(screen.getByRole("button", { name: /place order/i }));
  expect(await screen.findByText(/market data unavailable/i)).toBeInTheDocument();
  const [, body] = vi.mocked(api.placeOrder).mock.calls[0];
  expect(body.order_type).toBe("limit");
  expect(body.limit_price).toBe("90");
});

it("disables submit for an unparsable limit price", async () => {
  setup("100");
  await userEvent.click(screen.getByRole("radio", { name: /limit/i }));
  await userEvent.type(screen.getByLabelText(/limit price/i), "9..5");
  await userEvent.clear(screen.getByLabelText(/quantity/i));
  await userEvent.type(screen.getByLabelText(/quantity/i), "5");
  expect(screen.getByRole("button", { name: /place order/i })).toBeDisabled();
});

it("clears the previous result when a new submit starts", async () => {
  vi.mocked(api.placeOrder).mockResolvedValueOnce({
    id: 9, account_id: 1, symbol: "SPY", side: "buy", order_type: "market",
    tif: "day", qty: "1", limit_price: null, status: "filled", reject_reason: null,
    placed_at: "2026-07-02T15:00:00",
  });
  setup("100");
  await userEvent.click(screen.getByRole("button", { name: /place order/i }));
  expect(await screen.findByText(/filled/i)).toBeInTheDocument();
  vi.mocked(api.placeOrder).mockImplementation(() => new Promise(() => {}));
  await userEvent.click(screen.getByRole("button", { name: /place order/i }));
  expect(screen.queryByText(/filled/i)).not.toBeInTheDocument();
});

it("allows fractional quantity and whole numbers for a crypto symbol", async () => {
  setup("65000", "BTC-USD");
  expect(screen.getByLabelText(/quantity/i)).toHaveAccessibleName(/up to 8 decimal places/i);
  await userEvent.clear(screen.getByLabelText(/quantity/i));
  await userEvent.type(screen.getByLabelText(/quantity/i), "0.005");
  expect(await screen.findByText("$325.00")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /place order/i })).not.toBeDisabled();
});

it("rejects fractional quantity for a stock symbol", async () => {
  setup("100", "SPY");
  expect(screen.getByLabelText(/quantity/i)).toHaveAccessibleName(/whole shares/i);
  await userEvent.clear(screen.getByLabelText(/quantity/i));
  await userEvent.type(screen.getByLabelText(/quantity/i), "1.5");
  expect(screen.getByRole("button", { name: /place order/i })).toBeDisabled();
});
```

In `frontend/tests/dashboard.test.tsx`, change the one position mock's `qty: 10` to `qty: "10"` (same line, just the literal — no other change to that file).

In `frontend/tests/orders-page.test.tsx`, change the one order mock's `qty: 10` to `qty: "10"` (same line, just the literal).

In `frontend/tests/journal.test.tsx`, change both trade mocks' `qty: 5` and `qty: 10` to `qty: "5"` and `qty: "10"` (same lines, just the literals).

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm test`
Expected: FAIL — `qty.ts` module not found; `mulMoney` type errors (string passed where number expected, or vice versa depending on which side changed first); `OrderTicket` type errors from `PlaceOrderBody.qty` mismatch once `types.ts` changes; the four one-line mock fixes won't compile against the still-`number` types yet.

- [ ] **Step 3: Implement `frontend/lib/qty.ts`**

```typescript
// Quantity parsing/formatting and the stock/crypto symbol-shape rule shared
// by the order ticket, positions grouping, and orders/journal tagging.
// Not money — no $ prefix, no fixed 2dp; crypto allows up to 8 decimal
// places, stocks require whole numbers.

export function isCryptoSymbol(symbol: string): boolean {
  return symbol.includes("-");
}

export function isValidQty(s: string, allowFractional: boolean): boolean {
  const trimmed = s.trim();
  const pattern = allowFractional ? /^\d+(\.\d{1,8})?$/ : /^\d+$/;
  return pattern.test(trimmed) && Number(trimmed) > 0;
}

export function formatQty(s: string): string {
  const trimmed = s.trim();
  if (!trimmed.includes(".")) return trimmed;
  return trimmed.replace(/0+$/, "").replace(/\.$/, "");
}
```

- [ ] **Step 4: Update `frontend/lib/money.ts`**

Add a quantity-scale helper and change `mulMoney`'s signature. Replace the existing `mulMoney` function (and add the new helper above it):

```typescript
const QTY_SCALE = 8;
const QTY_FACTOR = 10n ** BigInt(QTY_SCALE);

function qtyToBig(s: string): bigint {
  const m = /^(-?)(\d+)(?:\.(\d+))?$/.exec(s.trim());
  if (!m) throw new Error(`invalid quantity: ${JSON.stringify(s)}`);
  const [, sign, whole, frac = ""] = m;
  if (frac.length > QTY_SCALE) {
    throw new Error(`quantity precision exceeds ${QTY_SCALE} decimal places: ${s}`);
  }
  const digits = whole + frac.padEnd(QTY_SCALE, "0");
  const value = BigInt(digits);
  return sign === "-" ? -value : value;
}

export function mulMoney(price: string, qty: string): string {
  const scaled = moneyToBig(price) * qtyToBig(qty);
  return bigToMoney(scaled / QTY_FACTOR);
}
```

(Delete the old `mulMoney` implementation — the one checking `Number.isInteger(qty)` and doing `moneyToBig(price) * BigInt(qty)` — replacing it entirely with the above. `qtyToBig` is scale-8 to match the 8dp crypto precision; the multiply-then-divide-by-`QTY_FACTOR` rescales the product back down to money's scale-4 representation.)

- [ ] **Step 5: Update `frontend/lib/types.ts`**

Change `PositionValue.qty: number` to `qty: string` (one line).
Change `Order.qty: number` to `qty: string` (one line).
Change `PlaceOrderBody.qty: number` to `qty: string` (one line).
Change `Trade.qty: number` to `qty: string` (one line).

- [ ] **Step 6: Update `frontend/components/OrderTicket.tsx`**

Add the import:
```tsx
import { isCryptoSymbol, isValidQty } from "@/lib/qty";
```

Replace the `qtyNum`/`previewPrice`/`cost`/`insufficient` block:

```tsx
  const allowFractional = isCryptoSymbol(symbol);
  const qtyValid = isValidQty(qty, allowFractional);
  const previewPrice = type === "limit" ? limitPrice : quotePrice;
  let cost: string | null = null;
  try {
    cost = previewPrice && qtyValid ? mulMoney(previewPrice, qty) : null;
  } catch {
    cost = null; // partially-typed limit price, or qty precision exceeded
  }
```

Replace `canSubmit`'s `qtyNum > 0` with `qtyValid`:

```tsx
  const canSubmit =
    accountId !== null &&
    qtyValid &&
    !insufficient &&
    !place.isPending &&
    (type === "market" || (limitPrice.trim().length > 0 && cost !== null));
```

Replace the quantity label and input:

```tsx
      <label className="block text-xs text-gray-500" htmlFor="qty">
        Quantity {allowFractional ? "(up to 8 decimal places)" : "(whole shares)"}
      </label>
      <input id="qty" inputMode="decimal" value={qty}
        onChange={(e) => setQty(e.target.value.replace(/[^0-9.]/g, ""))}
        className="w-full rounded border border-gray-700 bg-gray-950 px-3 py-1.5 text-sm text-gray-100 outline-none focus:border-gray-500" />
```

In the submit handler's `place.mutate({...})` call, change `qty: qtyNum` to `qty: qty` (send the validated raw string).

- [ ] **Step 7: Run tests to verify they pass**

Run: `npm test`
Expected: all tests pass — `qty.test.ts` (5 new), `money.test.ts` (updated + 2 new fractional-qty tests), `order-ticket.test.tsx` (7 tests, 2 new).

- [ ] **Step 8: Run typecheck**

Run: `npm run typecheck`
Expected: no errors (confirms `PositionsTable`/`OrdersTable`/journal page still compile against the wider `string` qty types with zero logic changes, since they only ever render `.qty` as a JSX child).

- [ ] **Step 9: Commit**

```bash
git add lib/qty.ts lib/money.ts lib/types.ts components/OrderTicket.tsx tests/qty.test.ts tests/money.test.ts tests/order-ticket.test.tsx tests/dashboard.test.tsx tests/orders-page.test.tsx tests/journal.test.tsx
git commit -m "feat: fractional crypto quantities in the order ticket"
```

---

### Task 2: Positions grouped into Stocks and Crypto sections

**Files:**
- Modify: `frontend/components/PositionsTable.tsx`
- Test: `frontend/tests/positions-table.test.tsx` (new)

**Interfaces:**
- Consumes: `isCryptoSymbol` (Task 1), `PositionValue` (unchanged shape besides `qty: string` from Task 1).
- Produces: `PositionsTable` renders two grouped `<tbody>` sections — "Stocks" then "Crypto" — each only rendered if it has at least one row. No change to `PositionsTable`'s props or exported name; the Dashboard page's existing usage is unaffected.

- [ ] **Step 1: Write the failing test**

`frontend/tests/positions-table.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { PositionsTable } from "@/components/PositionsTable";
import type { PositionValue } from "@/lib/types";

const stock: PositionValue = {
  symbol: "AAPL", qty: "10", avg_cost: "150", last_price: "160",
  market_value: "1600", unrealized_pnl: "100", realized_pnl: "0",
};
const crypto: PositionValue = {
  symbol: "BTC-USD", qty: "0.05", avg_cost: "60000", last_price: "65000",
  market_value: "3250", unrealized_pnl: "250", realized_pnl: "0",
};

it("groups positions into Stocks and Crypto sections", () => {
  render(<PositionsTable positions={[stock, crypto]} />);
  expect(screen.getByText("Stocks")).toBeInTheDocument();
  expect(screen.getByText("Crypto")).toBeInTheDocument();
  expect(screen.getByText("AAPL")).toBeInTheDocument();
  expect(screen.getByText("BTC-USD")).toBeInTheDocument();
});

it("omits an empty group's header", () => {
  render(<PositionsTable positions={[stock]} />);
  expect(screen.getByText("Stocks")).toBeInTheDocument();
  expect(screen.queryByText("Crypto")).not.toBeInTheDocument();
});

it("shows the empty-state message when there are no positions at all", () => {
  render(<PositionsTable positions={[]} />);
  expect(screen.getByText(/no open positions/i)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- tests/positions-table.test.tsx`
Expected: FAIL — no "Stocks"/"Crypto" group headers exist yet.

- [ ] **Step 3: Implement**

Replace `frontend/components/PositionsTable.tsx` entirely:

```tsx
import { formatQty, isCryptoSymbol } from "@/lib/qty";
import { formatUsd, isNeg } from "@/lib/money";
import type { PositionValue } from "@/lib/types";

function Pnl({ value }: { value: string }) {
  const neg = isNeg(value);
  return (
    <span className={neg ? "text-red-400" : "text-emerald-400"}>
      {neg ? "" : "+"}
      {formatUsd(value)}
    </span>
  );
}

export function PositionsTable({ positions }: { positions: PositionValue[] }) {
  if (positions.length === 0) {
    return <p className="text-sm text-gray-500">No open positions.</p>;
  }
  const stocks = positions.filter((p) => !isCryptoSymbol(p.symbol));
  const crypto = positions.filter((p) => isCryptoSymbol(p.symbol));
  const groups: { label: string; rows: PositionValue[] }[] = [
    ...(stocks.length > 0 ? [{ label: "Stocks", rows: stocks }] : []),
    ...(crypto.length > 0 ? [{ label: "Crypto", rows: crypto }] : []),
  ];
  return (
    <table className="w-full text-sm tabular-nums">
      <thead>
        <tr className="border-b border-gray-800 text-left text-xs uppercase text-gray-500">
          <th className="py-2">Symbol</th>
          <th className="py-2 text-right">Qty</th>
          <th className="py-2 text-right">Avg cost</th>
          <th className="py-2 text-right">Last</th>
          <th className="py-2 text-right">Value</th>
          <th className="py-2 text-right">Unrealized</th>
          <th className="py-2 text-right">Realized</th>
        </tr>
      </thead>
      {groups.map((g) => (
        <tbody key={g.label}>
          <tr>
            <td colSpan={7} className="pt-3 pb-1 text-xs font-semibold uppercase text-gray-500">
              {g.label}
            </td>
          </tr>
          {g.rows.map((p) => (
            <tr key={p.symbol} className="border-b border-gray-900">
              <td className="py-2 font-medium text-gray-100">{p.symbol}</td>
              <td className="py-2 text-right">{formatQty(p.qty)}</td>
              <td className="py-2 text-right">{formatUsd(p.avg_cost)}</td>
              <td className="py-2 text-right">{formatUsd(p.last_price)}</td>
              <td className="py-2 text-right">{formatUsd(p.market_value)}</td>
              <td className="py-2 text-right"><Pnl value={p.unrealized_pnl} /></td>
              <td className="py-2 text-right"><Pnl value={p.realized_pnl} /></td>
            </tr>
          ))}
        </tbody>
      ))}
    </table>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm test -- tests/positions-table.test.tsx`
Expected: `3 passed`

- [ ] **Step 5: Run the full suite**

Run: `npm test`
Expected: all tests pass, including `dashboard.test.tsx` (which renders `PositionsTable` with a single SPY position via the Dashboard page — a single-group render is unaffected by the grouping logic).

- [ ] **Step 6: Commit**

```bash
git add components/PositionsTable.tsx tests/positions-table.test.tsx
git commit -m "feat: group positions into Stocks and Crypto sections"
```

---

### Task 3: Orders table tags each row as Stock or Crypto

**Files:**
- Modify: `frontend/components/OrdersTable.tsx`

**Interfaces:**
- Consumes: `isCryptoSymbol` (Task 1).
- Produces: each order row shows a small "Stock"/"Crypto" tag next to its symbol. Row order is unchanged (still newest-first, as passed in) — no regrouping, since this is a chronological list.

- [ ] **Step 1: Write the failing test**

Append to `frontend/tests/orders-page.test.tsx` (this file already renders `OrdersTable` via the Orders page through its `setup(orders: Order[])` helper and `pendingOrder` mock — `OrdersTable` has no dedicated unit-test file of its own, so add the assertion here rather than creating a new file):

```tsx
it("tags each order with its asset class", async () => {
  setup([pendingOrder]);
  await screen.findByText("SPY");
  expect(screen.getByText("Stock")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- tests/orders-page.test.tsx`
Expected: FAIL — no "Stock" tag text exists yet.

- [ ] **Step 3: Implement**

Add the import to `frontend/components/OrdersTable.tsx`:
```tsx
import { formatQty, isCryptoSymbol } from "@/lib/qty";
```

Replace the symbol cell:

```tsx
            <td className="py-2 font-medium">
              <a href={`/trade?symbol=${o.symbol}`} className="text-gray-100 hover:underline">
                {o.symbol}
              </a>
              <span className="ml-2 rounded bg-gray-800 px-1.5 py-0.5 text-[10px] uppercase text-gray-400">
                {isCryptoSymbol(o.symbol) ? "Crypto" : "Stock"}
              </span>
            </td>
```

Replace the qty cell:

```tsx
            <td className="py-2 text-right">{formatQty(o.qty)}</td>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm test -- tests/orders-page.test.tsx`
Expected: all pass (existing tests plus the new one).

- [ ] **Step 5: Run the full suite**

Run: `npm test`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add components/OrdersTable.tsx tests/orders-page.test.tsx
git commit -m "feat: tag each order row with its asset class"
```

---

### Task 4: Journal tags each trade as Stock or Crypto

**Files:**
- Modify: `frontend/app/journal/page.tsx`

**Interfaces:**
- Consumes: `isCryptoSymbol` (Task 1).
- Produces: each trade-log row shows a small "Stock"/"Crypto" tag next to its symbol/qty line. Row order unchanged (still newest-first).

- [ ] **Step 1: Write the failing test**

Append to `frontend/tests/journal.test.tsx`:

```tsx
it("tags each trade with its asset class", async () => {
  renderWithClient(
    <AccountProvider>
      <JournalPage />
    </AccountProvider>,
  );
  await screen.findByText("took profits into strength");
  const tags = screen.getAllByText("Stock");
  expect(tags).toHaveLength(2); // both mocked trades are SPY
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- tests/journal.test.tsx`
Expected: FAIL — no "Stock" tag text exists yet.

- [ ] **Step 3: Implement**

Add the import to `frontend/app/journal/page.tsx`:
```tsx
import { formatQty, isCryptoSymbol } from "@/lib/qty";
```

Replace the trade row's summary line:

```tsx
            <div className="flex flex-wrap items-baseline gap-3 text-sm">
              <span className="text-gray-500">{formatDateTime(t.filled_at)}</span>
              <span className={t.side === "buy" ? "text-emerald-400" : "text-red-400"}>
                {t.side}
              </span>
              <span className="font-medium text-gray-100">
                {formatQty(t.qty)} {t.symbol} @ {formatUsd(t.price)}
              </span>
              <span className="rounded bg-gray-800 px-1.5 py-0.5 text-[10px] uppercase text-gray-400">
                {isCryptoSymbol(t.symbol) ? "Crypto" : "Stock"}
              </span>
              {t.realized_pnl !== null && (
                <span className={isNeg(t.realized_pnl) ? "text-red-400" : "text-emerald-400"}>
                  {isNeg(t.realized_pnl) ? "" : "+"}
                  {formatUsd(t.realized_pnl)}
                </span>
              )}
            </div>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm test -- tests/journal.test.tsx`
Expected: all pass (existing tests plus the new one).

- [ ] **Step 5: Run the full suite and build**

Run: `npm test`
Expected: all tests pass.

Run: `npm run typecheck`
Expected: no errors.

Run: `npm run build`
Expected: build succeeds.

- [ ] **Step 6: Commit**

```bash
git add app/journal/page.tsx tests/journal.test.tsx
git commit -m "feat: tag each journal trade with its asset class"
```

---

## Verification Sweep (after all tasks)

- `cd frontend && npm test` — full suite green (37 pre-existing + ~13 new = ~50 tests).
- `npm run typecheck` — clean.
- `npm run build` — succeeds.
- Spec coverage check against `docs/superpowers/specs/2026-07-04-crypto-phase-2-design.md`'s Frontend section: qty types widened to string ✓, `lib/qty.ts` with `isValidQty`/`formatQty` ✓, `mulMoney` fractional variant ✓, order ticket decimal-aware with symbol-based hint ✓, positions grouped Stocks/Crypto ✓, orders/journal tagged (not regrouped) ✓, Trade page structurally unchanged ✓.
- Manual smoke test (if a browser is available): start the frontend against a running backend, place a `BTC-USD` market order for `0.005` from the Trade page, confirm the cost preview and fill; check the Dashboard's Positions table shows a "Crypto" section.
