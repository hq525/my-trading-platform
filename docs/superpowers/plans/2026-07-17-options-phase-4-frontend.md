# Options Paper Trading (Phase 4) — Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Options UI for the Paper section — an Options page (underlying → expirations → greeks chain → click-to-ticket), an option mode for the shared OrderTicket (contracts, ×100 premium math, ask/bid previews), and Option-aware positions/orders/journal surfaces.

**Architecture:** A new `lib/options.ts` mirrors the backend's OCC symbology (classification order option → crypto → stock at every call site). The chain page follows TradeView's layout patterns and TanStack Query conventions (expirations 15-min stale, chain 30-s refetch). The ticket stays one shared component: option mode is derived from the symbol shape plus optional bid/ask props, with premium = `mulMoney(mulMoney(price, "100"), qty)` (×100 first — exact in BigInt fixed-point).

**Tech Stack:** Next.js 15 app router, TanStack Query v5, Tailwind v4, Vitest + Testing Library.

**Spec:** `docs/superpowers/specs/2026-07-17-options-phase-4-design.md` (Frontend section). Backend API merged in PR #9.

## Global Constraints

- All money travels as decimal strings; math via `lib/money.ts` BigInt helpers — never floats. Display-only rounding of IV/greeks (`Number(v)` for percent/2dp formatting) is permitted ONLY in chain-table cell formatters, never in anything that feeds an order or a money value.
- Premium math: `mulMoney(mulMoney(price, "100"), qty)` — multiply by "100" FIRST (exact; reversing the order can truncate).
- Classification order at every call site: **option → crypto → stock** (`isOptionSymbol` checked before `isCryptoSymbol`).
- OCC regex mirrors backend: `^([A-Z]{1,6})(\d{2})(\d{2})(\d{2})([CP])(\d{8})$` plus a real-date round-trip check; strike = last 8 digits / 1000, kept as a string (no floats).
- Exact copy/format strings: contract label `SPY 08/21/26 $625 C` (fractional strikes pad to two decimals: `$7.50`); qty label `Contracts (whole numbers)` — a deliberate extension of the spec's bare "Contracts" to match the existing `Quantity (whole shares)` hint pattern, do not "correct" it back; badge `Option`; nav link `Options`; bid/ask line `Bid $4.90 · Ask $5.10` with `—` for a missing side.
- Buy previews price at the **ask** (or limit price), sell previews at the **bid**.
- Option order rows link to `/options?symbol={underlying}` (encoded), never `/trade?symbol={occ}`; the Options page reads the `symbol` query param to preload.
- Chain API shapes (backend PR #9): `GET /api/market/options/{u}/expirations` → `{underlying, expirations: ["YYYY-MM-DD",…]}`; `GET /api/market/options/{u}/chain?expiry=` → `{underlying, expiry, calls: Row[], puts: Row[]}` with Row = `{symbol, strike, right: "call"|"put", bid, ask, last, open_interest, iv, delta, gamma, theta, vega}` (decimal strings or null; **`right` is present** — include it in the type). `Quote` now always carries `bid`/`ask` (null for stocks/crypto).
- All commands run from `frontend/`: `cd frontend && npx vitest run <file>` / `npm run typecheck` / `npm run build` (cwd drift between repo root and frontend has burned prior sessions). Baseline: 85 tests passing.

---

### Task 1: `lib/options.ts`, types, api methods

**Files:**
- Create: `frontend/lib/options.ts`
- Modify: `frontend/lib/types.ts` (Quote + new interfaces)
- Modify: `frontend/lib/api.ts` (two methods)
- Test: `frontend/tests/options.test.ts` (new), `frontend/tests/api.test.ts` (append)

**Interfaces:**
- Consumes: existing `request<T>` pattern in `lib/api.ts`.
- Produces: `isOptionSymbol(symbol: string): boolean`; `parseOcc(symbol: string): OccContract` where `OccContract = {underlying: string, expiry: string /* YYYY-MM-DD */, right: "call"|"put", strike: string /* trailing zeros stripped, e.g. "625", "7.5" */}` (throws `Error` on non-option symbols); `formatStrike(strike: string): string` (`"625"` stays, `"7.5"` → `"7.50"` — fractional strikes pad to ≥2 decimals); `formatOptionLabel(symbol: string): string` → `"SPY 08/21/26 $625 C"`. Types `OptionChainRow`, `OptionChain`, `OptionExpirations`; `Quote` gains `bid: string | null; ask: string | null`. API: `api.optionExpirations(underlying)`, `api.optionChain(underlying, expiry)`.

- [ ] **Step 1: Write the failing tests** — create `frontend/tests/options.test.ts`:

```ts
import { formatOptionLabel, formatStrike, isOptionSymbol, parseOcc } from "@/lib/options";

it("classifies OCC symbols as options", () => {
  expect(isOptionSymbol("SPY260821C00625000")).toBe(true);
  expect(isOptionSymbol("F260918P00007500")).toBe(true);
});

it("rejects non-option symbols", () => {
  expect(isOptionSymbol("SPY")).toBe(false);
  expect(isOptionSymbol("BTC-USD")).toBe(false);
  expect(isOptionSymbol("spy260821c00625000")).toBe(false); // lowercase
  expect(isOptionSymbol("SPY260821X00625000")).toBe(false); // bad right
  expect(isOptionSymbol("SPY261341C00625000")).toBe(false); // month 13
  expect(isOptionSymbol("SPY260230C00625000")).toBe(false); // Feb 30
});

it("parses an OCC call", () => {
  expect(parseOcc("SPY260821C00625000")).toEqual({
    underlying: "SPY", expiry: "2026-08-21", right: "call", strike: "625",
  });
});

it("parses a fractional-strike put", () => {
  expect(parseOcc("F260918P00007500")).toEqual({
    underlying: "F", expiry: "2026-09-18", right: "put", strike: "7.5",
  });
});

it("throws on non-option symbols", () => {
  expect(() => parseOcc("SPY")).toThrow(/not an OCC option symbol/);
});

it("formats strikes with at least two decimals when fractional", () => {
  expect(formatStrike("625")).toBe("625");
  expect(formatStrike("7.5")).toBe("7.50");
  expect(formatStrike("7.125")).toBe("7.125");
});

it("formats human contract labels", () => {
  expect(formatOptionLabel("SPY260821C00625000")).toBe("SPY 08/21/26 $625 C");
  expect(formatOptionLabel("F260918P00007500")).toBe("F 09/18/26 $7.50 P");
});
```

Append to `frontend/tests/api.test.ts`:

```ts
it("builds option endpoint URLs", async () => {
  const fetchMock = vi.fn(async () =>
    jsonResponse({ underlying: "SPY", expirations: [] }));
  vi.stubGlobal("fetch", fetchMock);
  await api.optionExpirations("SPY");
  // Cast per this file's convention: a zero-arg vi.fn types mock.calls as [].
  const [expUrl] = fetchMock.mock.calls[0] as unknown as [string];
  expect(expUrl).toBe("/api/market/options/SPY/expirations");
  await api.optionChain("SPY", "2026-08-21");
  const [chainUrl] = fetchMock.mock.calls[1] as unknown as [string];
  expect(chainUrl).toBe("/api/market/options/SPY/chain?expiry=2026-08-21");
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npx vitest run tests/options.test.ts tests/api.test.ts`
Expected: FAIL — cannot resolve `@/lib/options`; `api.optionExpirations` is not a function.

- [ ] **Step 3: Implement.** Create `frontend/lib/options.ts`:

```ts
// OCC option-contract symbols: classification, parsing, display labels.
// Mirrors backend app/assets.py — compact OCC (ROOT + YYMMDD + C/P +
// strike*1000 zero-padded to 8 digits). Classification order everywhere:
// option -> crypto -> stock. Strike stays a string — no float math.

const OCC_RE = /^([A-Z]{1,6})(\d{2})(\d{2})(\d{2})([CP])(\d{8})$/;

export interface OccContract {
  underlying: string;
  expiry: string; // YYYY-MM-DD
  right: "call" | "put";
  strike: string; // trailing zeros stripped: "625", "7.5"
}

export function isOptionSymbol(symbol: string): boolean {
  const m = OCC_RE.exec(symbol);
  if (!m) return false;
  const [, , yy, mm, dd] = m;
  const month = Number(mm);
  const day = Number(dd);
  // Round-trip through Date.UTC: invalid dates (month 13, Feb 30) roll over
  // and fail the equality check, mirroring the backend's strptime guard.
  const d = new Date(Date.UTC(2000 + Number(yy), month - 1, day));
  return d.getUTCMonth() === month - 1 && d.getUTCDate() === day;
}

export function parseOcc(symbol: string): OccContract {
  const m = OCC_RE.exec(symbol);
  if (!m || !isOptionSymbol(symbol)) {
    throw new Error(`not an OCC option symbol: ${symbol}`);
  }
  const [, underlying, yy, mm, dd, right, strikeRaw] = m;
  // 8 digits = strike * 1000: first 5 are dollars, last 3 thousandths.
  const whole = strikeRaw.slice(0, 5).replace(/^0+(?=\d)/, "");
  const frac = strikeRaw.slice(5).replace(/0+$/, "");
  return {
    underlying,
    expiry: `20${yy}-${mm}-${dd}`,
    right: right === "C" ? "call" : "put",
    strike: frac ? `${whole}.${frac}` : whole,
  };
}

export function formatStrike(strike: string): string {
  if (!strike.includes(".")) return strike;
  const [whole, frac] = strike.split(".");
  return `${whole}.${frac.padEnd(2, "0")}`;
}

export function formatOptionLabel(symbol: string): string {
  const c = parseOcc(symbol);
  const yy = c.expiry.slice(2, 4);
  const month = c.expiry.slice(5, 7);
  const day = c.expiry.slice(8, 10);
  return `${c.underlying} ${month}/${day}/${yy} $${formatStrike(c.strike)} ${
    c.right === "call" ? "C" : "P"}`;
}
```

In `frontend/lib/types.ts`, replace the `Quote` interface and add the option types after `Bar`:

```ts
export interface Quote {
  symbol: string;
  price: string;
  as_of: string;
  bid: string | null;
  ask: string | null;
}

export interface OptionChainRow {
  symbol: string;
  strike: string;
  right: "call" | "put";
  bid: string | null;
  ask: string | null;
  last: string | null;
  open_interest: string | null;
  iv: string | null;
  delta: string | null;
  gamma: string | null;
  theta: string | null;
  vega: string | null;
}

export interface OptionChain {
  underlying: string;
  expiry: string; // YYYY-MM-DD
  calls: OptionChainRow[];
  puts: OptionChainRow[];
}

export interface OptionExpirations {
  underlying: string;
  expirations: string[]; // YYYY-MM-DD, ascending
}
```

In `frontend/lib/api.ts`: add `OptionChain, OptionExpirations` to the type import list, and add after the `bars` method:

```ts
  optionExpirations: (underlying: string) =>
    request<OptionExpirations>(
      `/api/market/options/${encodeURIComponent(underlying)}/expirations`),
  optionChain: (underlying: string, expiry: string) =>
    request<OptionChain>(
      `/api/market/options/${encodeURIComponent(underlying)}/chain?expiry=${
        encodeURIComponent(expiry)}`),
```

**Quote's new fields are REQUIRED, so three existing test fixtures must be updated in this same task** (tsconfig typechecks tests; without this, `npm run typecheck` fails with TS2345/TS2739):

- `frontend/tests/live-pages.test.tsx` (~line 67): the object passed to `vi.mocked(api.quote).mockResolvedValue({...})` gains `bid: null, ask: null`.
- `frontend/tests/replay-workbench.test.tsx` (~line 67): the object passed to `vi.mocked(api.replayQuote).mockResolvedValue({...})` gains `bid: null, ask: null`.
- `frontend/tests/quote-badge.test.tsx` (~lines 10 and 21): BOTH inline `quote={{...}}` prop literals gain `bid: null, ask: null`.

- [ ] **Step 4: Run to verify pass, plus type regressions**

Run: `cd frontend && npx vitest run tests/options.test.ts tests/api.test.ts tests/live-pages.test.tsx tests/replay-workbench.test.tsx tests/quote-badge.test.tsx && npm run typecheck`
Expected: all PASS; typecheck clean.

- [ ] **Step 5: Commit**

```bash
cd frontend && git add lib/options.ts lib/types.ts lib/api.ts tests/options.test.ts tests/api.test.ts tests/live-pages.test.tsx tests/replay-workbench.test.tsx tests/quote-badge.test.tsx && git commit -m "feat: OCC option lib, chain types, and option API methods"
```

---

### Task 2: OrderTicket option mode

**Files:**
- Modify: `frontend/components/OrderTicket.tsx`
- Test: `frontend/tests/order-ticket.test.tsx` (append)

**Interfaces:**
- Consumes: `isOptionSymbol`, `formatOptionLabel` (Task 1); existing `mulMoney`/`gtMoney`/`formatUsd`.
- Produces: `OrderTicket` gains optional props `bid?: string; ask?: string`. In option mode (derived from `isOptionSymbol(symbol)`): header shows `Order — {formatOptionLabel(symbol)}` plus a `Bid … · Ask …` line; qty label is `Contracts (whole numbers)`; market-order preview prices at ask (buy) / bid (sell); est. cost/proceeds = premium × 100 × contracts. Task 3 mounts the ticket with these props from chain rows.

- [ ] **Step 1: Write the failing tests** — append to `frontend/tests/order-ticket.test.tsx`:

```tsx
const OCC = "SPY260821C00625000";

function setupOption() {
  vi.mocked(api.accounts).mockResolvedValue([manual]);
  vi.mocked(api.accountDetail).mockResolvedValue({ ...manual, equity: "1000", positions: [] });
  return renderWithClient(
    <AccountProvider>
      <OrderTicket symbol={OCC} quotePrice="5.00" bid="4.90" ask="5.10" />
    </AccountProvider>,
  );
}

it("option mode: human label, contracts qty, x100 cost at the ask", async () => {
  setupOption();
  expect(screen.getByText(/Order — SPY 08\/21\/26 \$625 C/)).toBeInTheDocument();
  expect(screen.getByText("Bid $4.90 · Ask $5.10")).toBeInTheDocument();
  expect(screen.getByLabelText(/contracts \(whole numbers\)/i)).toBeInTheDocument();
  // qty defaults to 1: est cost = 5.10 x 100 x 1
  expect(await screen.findByText("$510.00")).toBeInTheDocument();
});

it("option mode: sell previews proceeds at the bid", async () => {
  setupOption();
  await userEvent.click(screen.getByRole("radio", { name: /sell/i }));
  await userEvent.clear(screen.getByLabelText(/contracts/i));
  await userEvent.type(screen.getByLabelText(/contracts/i), "2");
  // 4.90 x 100 x 2
  expect(await screen.findByText("$980.00")).toBeInTheDocument();
});

it("option mode: fractional contracts are invalid", async () => {
  setupOption();
  await userEvent.clear(screen.getByLabelText(/contracts/i));
  await userEvent.type(screen.getByLabelText(/contracts/i), "1.5");
  expect(screen.getByRole("button", { name: /place order/i })).toBeDisabled();
});

it("option mode: unaffordable premium blocks the buy", async () => {
  setupOption(); // cash 1000; 2 contracts at ask = 1020
  await userEvent.clear(screen.getByLabelText(/contracts/i));
  await userEvent.type(screen.getByLabelText(/contracts/i), "2");
  expect(await screen.findByText(/insufficient cash/i)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /place order/i })).toBeDisabled();
});

it("option mode: submits the raw OCC symbol", async () => {
  vi.mocked(api.placeOrder).mockResolvedValue({
    id: 11, account_id: 1, symbol: OCC, side: "buy", order_type: "market",
    tif: "day", qty: "1", limit_price: null, status: "filled", reject_reason: null,
    placed_at: "2026-07-17T15:00:00",
  });
  setupOption();
  await userEvent.click(screen.getByRole("button", { name: /place order/i }));
  await waitFor(() => expect(api.placeOrder).toHaveBeenCalled());
  const [, body] = vi.mocked(api.placeOrder).mock.calls[0];
  expect(body.symbol).toBe(OCC);
  expect(body.qty).toBe("1");
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npx vitest run tests/order-ticket.test.tsx`
Expected: new tests FAIL (no option label/props, cost shows $5.10 not $510.00); existing tests pass.

- [ ] **Step 3: Implement.** In `frontend/components/OrderTicket.tsx`:

Update imports:

```tsx
import { formatUsd, gtMoney, mulMoney } from "@/lib/money";
import { formatOptionLabel, isOptionSymbol } from "@/lib/options";
import { isCryptoSymbol, isValidQty } from "@/lib/qty";
```

Extend the props:

```tsx
export function OrderTicket({
  symbol,
  quotePrice,
  bid,
  ask,
  accountId: accountIdProp,
  live = false,
}: {
  symbol: string;
  quotePrice?: string;
  bid?: string;
  ask?: string;
  accountId?: number;
  live?: boolean;
}) {
```

After the state declarations, replace the derivation block (the lines from `const allowFractional` through the `insufficient` computation) with:

```tsx
  const option = isOptionSymbol(symbol);
  const allowFractional = !live && !option && isCryptoSymbol(symbol);
  const cryptoBlocked = live && isCryptoSymbol(symbol);
  const qtyValid = isValidQty(qty, allowFractional);
  const marketPrice = option ? (side === "buy" ? (ask ?? quotePrice) : (bid ?? quotePrice))
                             : quotePrice;
  const previewPrice = type === "limit" ? limitPrice : marketPrice;
  let cost: string | null = null;
  try {
    cost = previewPrice && qtyValid
      ? (option ? mulMoney(mulMoney(previewPrice, "100"), qty)
                : mulMoney(previewPrice, qty))
      : null;
  } catch {
    cost = null; // partially-typed limit price, or qty precision exceeded
  }
  const cash = detail.data?.cash;
  const insufficient =
    side === "buy" && cost !== null && cash !== undefined && gtMoney(cost, cash);
```

Replace the header line:

```tsx
      <h2 className="text-sm font-semibold text-gray-300">
        Order — {option ? formatOptionLabel(symbol) : symbol}
      </h2>
      {option && (
        <p className="text-xs text-gray-500">
          Bid {bid ? formatUsd(bid) : "—"} · Ask {ask ? formatUsd(ask) : "—"}
        </p>
      )}
```

Replace the qty label content:

```tsx
      <label className="block text-xs text-gray-500" htmlFor="qty">
        {option
          ? "Contracts (whole numbers)"
          : `Quantity ${allowFractional ? "(up to 8 decimal places)" : "(whole shares)"}`}
      </label>
```

(Everything else — submit body, confirm flow, result rendering — is unchanged; `isCryptoSymbol(OCC)` is false so option symbols were already whole-number, but the explicit `!option` in `allowFractional` pins intent.)

- [ ] **Step 4: Run to verify pass**

Run: `cd frontend && npx vitest run tests/order-ticket.test.tsx`
Expected: all PASS (old and new).

- [ ] **Step 5: Commit**

```bash
cd frontend && git add components/OrderTicket.tsx tests/order-ticket.test.tsx && git commit -m "feat: OrderTicket option mode with x100 premium math"
```

---

### Task 3: Options page + NavBar link

**Files:**
- Create: `frontend/app/options/page.tsx`
- Modify: `frontend/components/NavBar.tsx` (paperLinks)
- Test: `frontend/tests/options-page.test.tsx` (new), `frontend/tests/navbar.test.tsx` (append)

**Interfaces:**
- Consumes: `api.optionExpirations`/`api.optionChain` (Task 1), `OptionChainRow` type (Task 1), `formatStrike` (Task 1), `OrderTicket` with `bid`/`ask` props (Task 2).
- Produces: `/options` page in the Paper nav; reads `?symbol=` to preload the underlying. Task 4's order rows link here.

- [ ] **Step 1: Write the failing tests** — create `frontend/tests/options-page.test.tsx`:

```tsx
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithClient } from "./utils";

let search = new URLSearchParams();
vi.mock("next/navigation", () => ({ useSearchParams: () => search }));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: {
      ...actual.api,
      accounts: vi.fn(),
      accountDetail: vi.fn(),
      optionExpirations: vi.fn(),
      optionChain: vi.fn(),
    },
  };
});

import { AccountProvider } from "@/app/account-context";
import OptionsPage from "@/app/options/page";
import { api } from "@/lib/api";
import type { OptionChainRow } from "@/lib/types";

const manual = {
  id: 1, name: "manual", kind: "manual" as const, mode: "paper" as const,
  cash: "100000", starting_cash: "100000", last_synced_at: null, sync_detail: null,
};

const call: OptionChainRow = {
  symbol: "SPY260821C00625000", strike: "625", right: "call",
  bid: "4.90", ask: "5.10", last: "5.05", open_interest: "120",
  iv: "0.172", delta: "0.55", gamma: "0.01", theta: "-0.12", vega: "0.35",
};
const put: OptionChainRow = {
  symbol: "SPY260821P00600000", strike: "600", right: "put",
  bid: "1.00", ask: "1.20", last: null, open_interest: null,
  iv: null, delta: "-0.40", gamma: null, theta: null, vega: null,
};

beforeEach(() => {
  vi.clearAllMocks();
  search = new URLSearchParams();
  vi.mocked(api.accounts).mockResolvedValue([manual]);
  vi.mocked(api.accountDetail).mockResolvedValue({
    ...manual, equity: "100000", positions: [],
  });
  vi.mocked(api.optionExpirations).mockResolvedValue({
    underlying: "SPY", expirations: ["2026-08-21", "2026-09-18"],
  });
  vi.mocked(api.optionChain).mockResolvedValue({
    underlying: "SPY", expiry: "2026-08-21", calls: [call], puts: [put],
  });
});

function renderPage() {
  return renderWithClient(
    <AccountProvider>
      <OptionsPage />
    </AccountProvider>,
  );
}

async function loadSpy() {
  await userEvent.type(screen.getByLabelText(/underlying/i), "spy");
  await userEvent.click(screen.getByRole("button", { name: /load/i }));
}

it("loads expirations and renders the calls chain", async () => {
  renderPage();
  await loadSpy();
  expect(await screen.findByLabelText(/expiration/i)).toBeInTheDocument();
  expect(api.optionExpirations).toHaveBeenCalledWith("SPY");
  await waitFor(() =>
    expect(api.optionChain).toHaveBeenCalledWith("SPY", "2026-08-21"));
  const row = (await screen.findByText("625")).closest("tr")!;
  expect(within(row).getByText("$4.90")).toBeInTheDocument(); // bid
  expect(within(row).getByText("$5.10")).toBeInTheDocument(); // ask
  expect(within(row).getByText("17.2%")).toBeInTheDocument(); // iv
  expect(within(row).getByText("120")).toBeInTheDocument(); // OI
});

it("switches to the puts tab and renders null fields as em dashes", async () => {
  renderPage();
  await loadSpy();
  await screen.findByText("625");
  await userEvent.click(screen.getByRole("tab", { name: /puts/i }));
  const row = (await screen.findByText("600")).closest("tr")!;
  expect(within(row).getByText("$1.00")).toBeInTheDocument();
  expect(within(row).getAllByText("—").length).toBeGreaterThan(0);
  expect(screen.queryByText("625")).not.toBeInTheDocument();
});

it("clicking a row mounts the order ticket for that contract", async () => {
  renderPage();
  await loadSpy();
  await userEvent.click(await screen.findByText("625"));
  expect(
    await screen.findByText(/Order — SPY 08\/21\/26 \$625 C/),
  ).toBeInTheDocument();
  expect(screen.getByText("Bid $4.90 · Ask $5.10")).toBeInTheDocument();
});

it("preloads the underlying from the symbol query param", async () => {
  search = new URLSearchParams("symbol=SPY");
  renderPage();
  await waitFor(() => expect(api.optionExpirations).toHaveBeenCalledWith("SPY"));
  expect(await screen.findByText("625")).toBeInTheDocument();
});

it("shows the backend message when an underlying has no options", async () => {
  const { ApiError } = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  vi.mocked(api.optionExpirations).mockRejectedValue(
    new ApiError(404, "no options listed for symbol"));
  renderPage();
  await userEvent.type(screen.getByLabelText(/underlying/i), "ZZZZ");
  await userEvent.click(screen.getByRole("button", { name: /load/i }));
  expect(
    await screen.findByText(/no options listed for symbol/i),
  ).toBeInTheDocument();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npx vitest run tests/options-page.test.tsx`
Expected: FAIL — cannot resolve `@/app/options/page`.

- [ ] **Step 3: Implement.** Create `frontend/app/options/page.tsx`:

```tsx
"use client";

import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { OrderTicket } from "@/components/OrderTicket";
import { api, ApiError } from "@/lib/api";
import { formatUsd } from "@/lib/money";
import { formatStrike } from "@/lib/options";
import type { OptionChainRow } from "@/lib/types";

// Display-only formatters for chain cells (never used for order math).
const money = (v: string | null) => (v === null ? "—" : formatUsd(v));
const pct = (v: string | null) =>
  v === null ? "—" : `${(Number(v) * 100).toFixed(1)}%`;
const num2 = (v: string | null) => (v === null ? "—" : Number(v).toFixed(2));

const tabClass = (active: boolean) =>
  `rounded px-3 py-1.5 text-sm ${
    active ? "bg-gray-800 text-white" : "text-gray-400 hover:text-gray-200"
  }`;

function OptionsView() {
  const params = useSearchParams();
  const [input, setInput] = useState((params.get("symbol") ?? "").toUpperCase());
  const [underlying, setUnderlying] = useState(input);
  const [expiry, setExpiry] = useState("");
  const [tab, setTab] = useState<"calls" | "puts">("calls");
  const [selected, setSelected] = useState<OptionChainRow | null>(null);

  const expirations = useQuery({
    queryKey: ["option-expirations", underlying],
    queryFn: () => api.optionExpirations(underlying),
    enabled: underlying.length > 0,
    staleTime: 15 * 60_000,
    retry: false,
  });
  const available = expirations.data?.expirations ?? [];
  const activeExpiry =
    expiry && available.includes(expiry) ? expiry : (available[0] ?? "");

  const chain = useQuery({
    queryKey: ["option-chain", underlying, activeExpiry],
    queryFn: () => api.optionChain(underlying, activeExpiry),
    enabled: underlying.length > 0 && activeExpiry.length > 0,
    refetchInterval: 30_000,
  });
  const rows = (tab === "calls" ? chain.data?.calls : chain.data?.puts) ?? [];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-4">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            const s = input.trim().toUpperCase();
            if (s) {
              setInput(s);
              setUnderlying(s);
              setExpiry("");
              setSelected(null);
            }
          }}
          className="flex items-center gap-2"
        >
          <input
            aria-label="Underlying"
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
        {available.length > 0 && (
          <label className="flex items-center gap-2 text-sm text-gray-400">
            Expiration
            <select
              aria-label="Expiration"
              value={activeExpiry}
              onChange={(e) => {
                setExpiry(e.target.value);
                setSelected(null);
              }}
              className="rounded border border-gray-700 bg-gray-950 px-2 py-1 text-sm text-gray-200"
            >
              {available.map((d) => (
                <option key={d} value={d}>{d}</option>
              ))}
            </select>
          </label>
        )}
        <div role="tablist" className="flex gap-1">
          {(["calls", "puts"] as const).map((t) => (
            <button key={t} role="tab" aria-selected={tab === t}
              onClick={() => setTab(t)} className={tabClass(tab === t)}>
              {t === "calls" ? "Calls" : "Puts"}
            </button>
          ))}
        </div>
      </div>

      {expirations.error && (
        <p className="text-sm text-red-400">
          {expirations.error instanceof ApiError
            ? expirations.error.message
            : "Could not load expirations"}
        </p>
      )}

      <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
        <section className="overflow-x-auto rounded-lg border border-gray-800 bg-gray-900 p-2">
          {rows.length > 0 ? (
            <table className="w-full text-sm tabular-nums">
              <thead>
                <tr className="border-b border-gray-800 text-left text-xs uppercase text-gray-500">
                  <th className="py-2">Strike</th>
                  <th className="py-2 text-right">Bid</th>
                  <th className="py-2 text-right">Ask</th>
                  <th className="py-2 text-right">Last</th>
                  <th className="py-2 text-right">OI</th>
                  <th className="py-2 text-right">IV</th>
                  <th className="py-2 text-right">Delta</th>
                  <th className="py-2 text-right">Theta</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr
                    key={r.symbol}
                    onClick={() => setSelected(r)}
                    className={`cursor-pointer border-b border-gray-900 hover:bg-gray-800 ${
                      selected?.symbol === r.symbol ? "bg-gray-800" : ""
                    }`}
                  >
                    <td className="py-2 font-medium text-gray-100">
                      {formatStrike(r.strike)}
                    </td>
                    <td className="py-2 text-right">{money(r.bid)}</td>
                    <td className="py-2 text-right">{money(r.ask)}</td>
                    <td className="py-2 text-right">{money(r.last)}</td>
                    <td className="py-2 text-right">{r.open_interest ?? "—"}</td>
                    <td className="py-2 text-right">{pct(r.iv)}</td>
                    <td className="py-2 text-right">{num2(r.delta)}</td>
                    <td className="py-2 text-right">{num2(r.theta)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="flex h-40 items-center justify-center text-sm text-gray-500">
              {underlying
                ? chain.isFetching || expirations.isFetching
                  ? "Loading chain…"
                  : "No contracts"
                : "Enter an underlying to load its option chain"}
            </div>
          )}
        </section>
        <aside>
          {selected ? (
            <OrderTicket
              symbol={selected.symbol}
              quotePrice={selected.ask ?? selected.last ?? undefined}
              bid={selected.bid ?? undefined}
              ask={selected.ask ?? undefined}
            />
          ) : (
            <p className="text-sm text-gray-500">
              Click a contract to open the order ticket.
            </p>
          )}
        </aside>
      </div>
    </div>
  );
}

// useSearchParams requires a Suspense boundary for `next build` prerendering —
// same wrapper pattern as app/trade/page.tsx and app/live/trade/page.tsx.
export default function OptionsPage() {
  return (
    <Suspense>
      <OptionsView />
    </Suspense>
  );
}
```

In `frontend/components/NavBar.tsx`, add the Options link to `paperLinks` (after Trade):

```tsx
const paperLinks = [
  { href: "/", label: "Dashboard" },
  { href: "/trade", label: "Trade" },
  { href: "/options", label: "Options" },
  { href: "/orders", label: "Orders" },
  { href: "/journal", label: "Journal" },
  { href: "/strategies", label: "Strategies" },
];
```

Append to `frontend/tests/navbar.test.tsx`:

```tsx
it("shows the Options link in the paper section only", () => {
  pathname = "/options";
  renderNav();
  expect(screen.getByRole("link", { name: "Options" })).toHaveAttribute(
    "href", "/options");
  const paper = screen.getByRole("link", { name: "Paper" });
  expect(paper).toBeInTheDocument();
});
```

- [ ] **Step 4: Run to verify pass**

Run: `cd frontend && npx vitest run tests/options-page.test.tsx tests/navbar.test.tsx`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
cd frontend && git add app/options/page.tsx components/NavBar.tsx tests/options-page.test.tsx tests/navbar.test.tsx && git commit -m "feat: options chain page with click-to-ticket and nav link"
```

---

### Task 4: Positions/Orders/Journal option surfaces + full gate

**Files:**
- Modify: `frontend/components/PositionsTable.tsx`
- Modify: `frontend/components/OrdersTable.tsx`
- Modify: `frontend/app/journal/page.tsx` (asset badge)
- Test: `frontend/tests/positions-table.test.tsx` (append), `frontend/tests/orders-table.test.tsx` (new), `frontend/tests/journal.test.tsx` (append)

**Interfaces:**
- Consumes: `isOptionSymbol`, `parseOcc`, `formatOptionLabel` (Task 1); `/options?symbol=` preload (Task 3).
- Produces: third "Options" positions group with human labels; "Option" badge in orders and journal (classification order option → crypto → stock); option order rows link to `/options?symbol={underlying}`.

- [ ] **Step 1: Write the failing tests.**

Append to `frontend/tests/positions-table.test.tsx`:

```tsx
const optionPos: PositionValue = {
  symbol: "SPY260821C00625000", qty: "2", avg_cost: "5.1", last_price: "6",
  market_value: "1200", unrealized_pnl: "180", realized_pnl: "0",
};

it("groups option positions separately with human labels", () => {
  render(<PositionsTable positions={[stock, crypto, optionPos]} />);
  expect(screen.getByText("Options")).toBeInTheDocument();
  expect(screen.getByText("SPY 08/21/26 $625 C")).toBeInTheDocument();
  expect(screen.queryByText("SPY260821C00625000")).not.toBeInTheDocument();
  // option rows never leak into the Stocks group
  expect(screen.getByText("Stocks")).toBeInTheDocument();
  expect(screen.getByText("AAPL")).toBeInTheDocument();
});
```

Create `frontend/tests/orders-table.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { OrdersTable } from "@/components/OrdersTable";
import type { Order } from "@/lib/types";

const occOrder: Order = {
  id: 31, account_id: 1, symbol: "SPY260821C00625000", side: "buy",
  order_type: "market", tif: "day", qty: "2", limit_price: null,
  status: "filled", reject_reason: null, placed_at: "2026-07-17T15:00:00",
};
const stockOrder: Order = {
  id: 32, account_id: 1, symbol: "AAPL", side: "buy", order_type: "market",
  tif: "day", qty: "5", limit_price: null, status: "filled",
  reject_reason: null, placed_at: "2026-07-17T15:00:00",
};

it("badges option orders and links them to the chain page", () => {
  render(<OrdersTable orders={[occOrder, stockOrder]} />);
  expect(screen.getByText("Option")).toBeInTheDocument();
  const link = screen.getByRole("link", { name: /SPY 08\/21\/26 \$625 C/ });
  expect(link).toHaveAttribute("href", "/options?symbol=SPY");
  expect(screen.getByText("Stock")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "AAPL" })).toHaveAttribute(
    "href", "/trade?symbol=AAPL");
});
```

Append to `frontend/tests/journal.test.tsx`:

```tsx
it("tags option trades with an Option badge", async () => {
  vi.mocked(api.journal).mockResolvedValue([
    {
      order_id: 30, symbol: "SPY260821C00625000", side: "sell", qty: "2",
      price: "25", commission: "0", realized_pnl: "4000",
      filled_at: "2026-07-17T20:05:00", note: null, account_mode: "paper" as const,
    },
  ]);
  renderWithClient(
    <AccountProvider>
      <JournalPage />
    </AccountProvider>,
  );
  expect(await screen.findByText("Option")).toBeInTheDocument();
  expect(screen.queryByText("Stock")).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npx vitest run tests/positions-table.test.tsx tests/orders-table.test.tsx tests/journal.test.tsx`
Expected: new tests FAIL (no Options group/badge, raw OCC rendered, trade link); existing pass.

- [ ] **Step 3: Implement.**

In `frontend/components/PositionsTable.tsx`, update imports and grouping:

```tsx
import { formatQty, isCryptoSymbol } from "@/lib/qty";
import { formatOptionLabel, isOptionSymbol } from "@/lib/options";
```

```tsx
  const options = positions.filter((p) => isOptionSymbol(p.symbol));
  const stocks = positions.filter(
    (p) => !isOptionSymbol(p.symbol) && !isCryptoSymbol(p.symbol));
  const crypto = positions.filter(
    (p) => !isOptionSymbol(p.symbol) && isCryptoSymbol(p.symbol));
  const groups: { label: string; rows: PositionValue[] }[] = [
    ...(stocks.length > 0 ? [{ label: "Stocks", rows: stocks }] : []),
    ...(crypto.length > 0 ? [{ label: "Crypto", rows: crypto }] : []),
    ...(options.length > 0 ? [{ label: "Options", rows: options }] : []),
  ];
```

and the symbol cell:

```tsx
              <td className="py-2 font-medium text-gray-100">
                {isOptionSymbol(p.symbol) ? formatOptionLabel(p.symbol) : p.symbol}
              </td>
```

In `frontend/components/OrdersTable.tsx`, update imports:

```tsx
import { formatQty, isCryptoSymbol } from "@/lib/qty";
import { formatOptionLabel, isOptionSymbol, parseOcc } from "@/lib/options";
```

and replace the symbol cell:

```tsx
            <td className="py-2 font-medium">
              <a
                href={
                  isOptionSymbol(o.symbol)
                    ? `/options?symbol=${encodeURIComponent(parseOcc(o.symbol).underlying)}`
                    : `/trade?symbol=${o.symbol}`
                }
                className="text-gray-100 hover:underline"
              >
                {isOptionSymbol(o.symbol) ? formatOptionLabel(o.symbol) : o.symbol}
              </a>
              <span className="ml-2 rounded bg-gray-800 px-1.5 py-0.5 text-[10px] uppercase text-gray-400">
                {isOptionSymbol(o.symbol)
                  ? "Option"
                  : isCryptoSymbol(o.symbol)
                    ? "Crypto"
                    : "Stock"}
              </span>
            </td>
```

In `frontend/app/journal/page.tsx`, add the import and update the badge:

```tsx
import { formatQty, isCryptoSymbol } from "@/lib/qty";
import { isOptionSymbol } from "@/lib/options";
```

```tsx
              <span className="rounded bg-gray-800 px-1.5 py-0.5 text-[10px] uppercase text-gray-400">
                {isOptionSymbol(t.symbol)
                  ? "Option"
                  : isCryptoSymbol(t.symbol)
                    ? "Crypto"
                    : "Stock"}
              </span>
```

- [ ] **Step 4: Run to verify pass, then the full gate**

Run: `cd frontend && npx vitest run tests/positions-table.test.tsx tests/orders-table.test.tsx tests/journal.test.tsx`
Expected: all PASS.

Run: `cd frontend && npx vitest run && npm run typecheck && npm run build`
Expected: full suite green (85 baseline + all new), typecheck clean, build succeeds.

- [ ] **Step 5: Commit**

```bash
cd frontend && git add components/PositionsTable.tsx components/OrdersTable.tsx app/journal/page.tsx tests/positions-table.test.tsx tests/orders-table.test.tsx tests/journal.test.tsx && git commit -m "feat: option badges, labels, and grouping across positions, orders, journal"
```
