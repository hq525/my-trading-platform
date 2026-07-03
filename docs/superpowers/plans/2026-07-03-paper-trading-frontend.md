# Paper Trading Platform — Frontend + Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Next.js web UI (5 pages: Dashboard, Trade, Orders, Journal, Strategies) for the completed FastAPI backend, plus Docker Compose deployment — per the approved spec at `docs/superpowers/specs/2026-07-03-paper-trading-platform-design.md`.

**Architecture:** A client-rendered Next.js App Router app. All data flows through a typed `lib/api.ts` client hitting **relative** `/api/*` URLs; a Next.js rewrite proxies those to the backend (`BACKEND_URL`), making cookie auth same-origin in both dev and Docker — no CORS in play. TanStack Query handles fetching/polling/invalidation. Money values from the API are strings and stay strings; the only exact arithmetic the UI does (order cost preview, P&L deltas) goes through a BigInt-based `lib/money.ts`.

**Tech Stack:** Next.js 15 (App Router) + React 19 + TypeScript strict, Tailwind CSS v4, TanStack Query v5, lightweight-charts v5, Vitest + React Testing Library, Docker Compose.

## Global Constraints

- All commands run from `frontend/` unless stated otherwise. Node ≥ 20, npm (not pnpm/yarn).
- **Money values from the API are strings with trailing zeros stripped** (e.g. `"99000"`, `"100.5"`). NEVER do money arithmetic with `parseFloat`/`Number` — use `lib/money.ts` (BigInt, scale 4). `Number()` conversion is allowed ONLY for chart display coordinates (lightweight-charts requires numbers; display-only).
- **Backend datetimes are naive-UTC ISO strings without "Z"** (e.g. `"2026-07-02T15:00:00"`); snapshot dates are `"YYYY-MM-DD"`. Always parse via `lib/format.ts` helpers, never `new Date(iso)` directly.
- All fetches use relative `/api/*` paths with `credentials: "include"`. Any 401 outside `/login` redirects to `/login`.
- Backend error bodies are FastAPI-shaped: `{"detail": "..."}`.
- Every page/component that uses hooks is a client component (`"use client"`).
- Dark theme (spec): `bg-gray-950` body, data-dense tables, green `#22c55e` gains / red `#ef4444` losses.
- Verify gate per task: `npm test` and `npm run typecheck` green; `npm run build` additionally in Tasks 1 and 11.
- Test output must be pristine. Mock `next/navigation` and `lightweight-charts` in component tests (jsdom has no canvas/navigation).
- TDD every task: failing test → implement → pass → commit. Commit prefixes `feat:`/`fix:`/`test:`/`chore:`.

## Backend API contract (already merged, authoritative)

| Endpoint | Returns |
|---|---|
| POST `/api/login` `{password}` | `{ok:true}` + `pt_session` cookie; 401 wrong password |
| POST `/api/logout` | `{ok:true}` |
| GET `/api/accounts` | `[{id,name,kind,cash,starting_cash}]` |
| GET `/api/accounts/{id}` | `{...account, equity, positions:[{symbol,qty,avg_cost,last_price,market_value,unrealized_pnl,realized_pnl}]}`; 503 data down; 404 missing |
| GET `/api/accounts/{id}/snapshots` | `[{date,equity,cash}]` (date ascending) |
| POST `/api/accounts/{id}/orders` `{symbol,side,order_type,qty,tif,limit_price?,idempotency_key?}` | 201 `Order` (may be `status:"rejected"` with `reject_reason`) |
| GET `/api/accounts/{id}/orders?status=` | `[Order]` newest-first |
| POST `/api/orders/{id}/cancel` | `Order`; 404 missing, 409 not pending |
| PUT `/api/orders/{id}/note` `{text}` | `{ok:true}`; 404 missing order |
| GET `/api/market/quote/{symbol}` | `{symbol,price,as_of}`; 404 unknown, 503 down |
| GET `/api/market/bars/{symbol}?limit=` | `[{timestamp,open,high,low,close,volume}]` (daily) |
| GET `/api/journal?account_id=` | `[{order_id,symbol,side,qty,price,commission,realized_pnl,filled_at,note}]` newest-first |
| GET `/api/journal/stats?account_id=` | `{closed_trades,wins,win_rate,avg_gain,avg_loss}` (win_rate float, rest Money-or-null) |
| GET `/api/strategies` | `[{name,schedule,enabled,account_id}]` |
| POST `/api/strategies/{name}/toggle` | `Strategy` |
| GET `/api/strategies/{name}/runs?limit=` | `[{id,strategy_name,started_at,finished_at,status,detail}]` |

`Order` = `{id,account_id,symbol,side,order_type,tif,qty,limit_price,status,reject_reason,placed_at}`.

## File Structure

```
frontend/
  package.json / tsconfig.json / next.config.ts / postcss.config.mjs
  vitest.config.mts / vitest.setup.ts
  app/
    layout.tsx            dark shell: NavBar + Providers + main
    providers.tsx         QueryClientProvider (+ AccountProvider from Task 5)
    account-context.tsx   selected-account context (Task 5)
    globals.css           Tailwind import
    login/page.tsx        password form (Task 3)
    page.tsx              Dashboard (Task 5)
    trade/page.tsx        Trade (Tasks 6-7)
    orders/page.tsx       Orders (Task 8)
    journal/page.tsx      Journal (Task 9)
    strategies/page.tsx   Strategies (Task 10)
  lib/
    types.ts              API response types (money = string)
    money.ts              BigInt money math + USD formatting
    format.ts             naive-UTC parsing, data-age, datetime display
    api.ts                fetch wrapper + typed endpoints + 401 redirect
  components/
    NavBar.tsx            nav links (T1), logout (T3), AccountSwitcher slot (T5)
    StatCard.tsx          label + big value + tone (T5)
    PositionsTable.tsx    (T5)
    OrdersTable.tsx       shared orders table, optional cancel (T5, reused T8)
    AccountSwitcher.tsx   (T5)
    CandleChart.tsx       lightweight-charts candles+volume (T4)
    EquityCurve.tsx       lightweight-charts area (T4)
    QuoteBadge.tsx        price + data-age + stale/error states (T6)
    OrderTicket.tsx       (T7)
  tests/
    utils.tsx             renderWithClient helper (T3)
    *.test.ts(x)          per-task tests
backend/Dockerfile        (T11)
backend/.dockerignore     (T11)
frontend/Dockerfile       (T11)
frontend/.dockerignore    (T11)
compose.yaml              repo root (T11)
```

---

### Task 1: Next.js scaffold, Tailwind, dark shell, Vitest

**Files:**
- Create: `frontend/package.json`, `frontend/tsconfig.json`, `frontend/next.config.ts`, `frontend/postcss.config.mjs`, `frontend/vitest.config.mts`, `frontend/vitest.setup.ts`, `frontend/app/globals.css`, `frontend/app/layout.tsx`, `frontend/app/providers.tsx`, `frontend/app/page.tsx`, `frontend/components/NavBar.tsx`
- Modify: `.gitignore` (repo root — add `.next/`)
- Test: `frontend/tests/navbar.test.tsx`

**Interfaces:**
- Consumes: the merged backend (running separately on :8000 for manual dev checks only).
- Produces: the app shell every later task mounts pages into; `NavBar` (named export); `Providers` (default export) wrapping a `QueryClient` with `{retry: false, refetchOnWindowFocus: false, staleTime: 10_000}`; the `@/*` path alias; the `npm test` / `npm run typecheck` / `npm run build` gates.

- [ ] **Step 1: Write the config and scaffold files**

`frontend/package.json`:

```json
{
  "name": "paper-trading-frontend",
  "private": true,
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "start": "next start",
    "typecheck": "tsc --noEmit",
    "test": "vitest run"
  },
  "dependencies": {
    "@tanstack/react-query": "^5.60.0",
    "lightweight-charts": "^5.0.0",
    "next": "^15.3.0",
    "react": "^19.0.0",
    "react-dom": "^19.0.0"
  },
  "devDependencies": {
    "@tailwindcss/postcss": "^4.0.0",
    "@testing-library/jest-dom": "^6.6.0",
    "@testing-library/react": "^16.1.0",
    "@testing-library/user-event": "^14.5.0",
    "@types/node": "^22.0.0",
    "@types/react": "^19.0.0",
    "@types/react-dom": "^19.0.0",
    "@vitejs/plugin-react": "^4.3.0",
    "jsdom": "^26.0.0",
    "postcss": "^8.4.0",
    "tailwindcss": "^4.0.0",
    "typescript": "^5.6.0",
    "vitest": "^3.0.0"
  }
}
```

`frontend/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["dom", "dom.iterable", "esnext"],
    "allowJs": false,
    "skipLibCheck": true,
    "strict": true,
    "noEmit": true,
    "esModuleInterop": true,
    "module": "esnext",
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "jsx": "preserve",
    "incremental": true,
    "plugins": [{ "name": "next" }],
    "paths": { "@/*": ["./*"] },
    "types": ["vitest/globals", "@testing-library/jest-dom"]
  },
  "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx", ".next/types/**/*.ts"],
  "exclude": ["node_modules"]
}
```

`frontend/next.config.ts`:

```ts
import type { NextConfig } from "next";

const backendUrl = process.env.BACKEND_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  output: "standalone",
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backendUrl}/api/:path*` }];
  },
};

export default nextConfig;
```

`frontend/postcss.config.mjs`:

```js
export default { plugins: { "@tailwindcss/postcss": {} } };
```

`frontend/vitest.config.mts`:

```ts
import react from "@vitejs/plugin-react";
import path from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    globals: true,
    include: ["tests/**/*.test.{ts,tsx}"],
  },
  resolve: { alias: { "@": path.resolve(__dirname, ".") } },
});
```

`frontend/vitest.setup.ts`:

```ts
import "@testing-library/jest-dom/vitest";
```

`frontend/app/globals.css`:

```css
@import "tailwindcss";
```

Append to the repo-root `.gitignore`:

```
.next/
```

- [ ] **Step 2: Install dependencies**

Run: `cd frontend && npm install`
Expected: resolves and installs without error; `package-lock.json` created (commit it).

- [ ] **Step 3: Write the failing test**

`frontend/tests/navbar.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";

vi.mock("next/navigation", () => ({ usePathname: () => "/" }));

import { NavBar } from "@/components/NavBar";

it("renders all five nav links", () => {
  render(<NavBar />);
  for (const label of ["Dashboard", "Trade", "Orders", "Journal", "Strategies"]) {
    expect(screen.getByRole("link", { name: label })).toBeInTheDocument();
  }
});
```

- [ ] **Step 4: Run test to verify it fails**

Run: `npm test`
Expected: FAIL — cannot resolve `@/components/NavBar`.

- [ ] **Step 5: Implement the shell**

`frontend/components/NavBar.tsx`:

```tsx
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const links = [
  { href: "/", label: "Dashboard" },
  { href: "/trade", label: "Trade" },
  { href: "/orders", label: "Orders" },
  { href: "/journal", label: "Journal" },
  { href: "/strategies", label: "Strategies" },
];

export function NavBar() {
  const pathname = usePathname();
  return (
    <header className="border-b border-gray-800 bg-gray-900">
      <nav className="mx-auto flex max-w-7xl items-center gap-1 px-4 py-2">
        <span className="mr-4 font-semibold text-gray-100">Paper Trading</span>
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
      </nav>
    </header>
  );
}
```

`frontend/app/providers.tsx`:

```tsx
"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

export default function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { retry: false, refetchOnWindowFocus: false, staleTime: 10_000 },
        },
      }),
  );
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
```

`frontend/app/layout.tsx`:

```tsx
import "./globals.css";
import { NavBar } from "@/components/NavBar";
import Providers from "./providers";

export const metadata = { title: "Paper Trading" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-gray-950 text-gray-200 antialiased">
        <Providers>
          <NavBar />
          <main className="mx-auto max-w-7xl p-4">{children}</main>
        </Providers>
      </body>
    </html>
  );
}
```

`frontend/app/page.tsx` (placeholder, replaced in Task 5):

```tsx
export default function DashboardPage() {
  return <h1 className="text-lg font-semibold">Dashboard</h1>;
}
```

- [ ] **Step 6: Run tests and build to verify**

Run: `npm test` — Expected: `1 passed`.
Run: `npm run build` — Expected: build succeeds (this also generates `next-env.d.ts`; commit it).
Run: `npm run typecheck` — Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: Next.js scaffold with dark shell, Tailwind, and Vitest"
```

### Task 2: lib — types, exact money math, date/format helpers

**Files:**
- Create: `frontend/lib/types.ts`, `frontend/lib/money.ts`, `frontend/lib/format.ts`
- Test: `frontend/tests/money.test.ts`, `frontend/tests/format.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces (every later task uses these exact names):
  - `types.ts`: `Account`, `AccountDetail`, `PositionValue`, `Snapshot`, `Order`, `PlaceOrderBody`, `Quote`, `Bar`, `Trade`, `Stats`, `Strategy`, `StrategyRun` — all money fields typed `string`.
  - `money.ts`: `moneyToBig(s: string): bigint` (scale 4), `bigToMoney(v: bigint): string` (trailing zeros stripped), `mulMoney(price: string, qty: number): string`, `addMoney(a: string, b: string): string`, `subMoney(a: string, b: string): string`, `gtMoney(a: string, b: string): boolean`, `isNeg(s: string): boolean`, `formatUsd(s: string, dp?: number): string` (default 2dp, truncates, thousands separators).
  - `format.ts`: `utcDate(iso: string): Date`, `dataAge(asOfIso: string, now?: Date): string` ("42s ago"/"3m ago"/"2h ago"), `formatDateTime(iso: string): string`.

- [ ] **Step 1: Write the failing tests**

`frontend/tests/money.test.ts`:

```ts
import {
  addMoney, bigToMoney, formatUsd, gtMoney, isNeg, moneyToBig, mulMoney, subMoney,
} from "@/lib/money";

it("round-trips API money strings", () => {
  expect(bigToMoney(moneyToBig("99000"))).toBe("99000");
  expect(bigToMoney(moneyToBig("100.5"))).toBe("100.5");
  expect(bigToMoney(moneyToBig("105.0000"))).toBe("105");
  expect(bigToMoney(moneyToBig("-0.25"))).toBe("-0.25");
});

it("rejects invalid money strings", () => {
  expect(() => moneyToBig("abc")).toThrow();
  expect(() => moneyToBig("1e5")).toThrow();
  expect(() => moneyToBig("")).toThrow();
});

it("multiplies price by integer qty exactly", () => {
  expect(mulMoney("100", 10)).toBe("1000");
  expect(mulMoney("123.45", 3)).toBe("370.35");
  expect(mulMoney("0.1", 3)).toBe("0.3"); // no float 0.30000000000000004
});

it("adds and subtracts exactly", () => {
  expect(addMoney("99000", "1100")).toBe("100100");
  expect(subMoney("100100", "100000")).toBe("100");
  expect(subMoney("100", "100.5")).toBe("-0.5");
});

it("compares", () => {
  expect(gtMoney("1000.0001", "1000")).toBe(true);
  expect(gtMoney("1000", "1000")).toBe(false);
  expect(isNeg("-3")).toBe(true);
  expect(isNeg("3")).toBe(false);
});

it("formats USD with grouping and fixed decimals (truncating)", () => {
  expect(formatUsd("99000")).toBe("$99,000.00");
  expect(formatUsd("-1234.5")).toBe("-$1,234.50");
  expect(formatUsd("100.0499")).toBe("$100.04"); // truncates, never rounds
  expect(formatUsd("105", 4)).toBe("$105.0000");
  expect(formatUsd("0")).toBe("$0.00");
});
```

`frontend/tests/format.test.ts`:

```ts
import { dataAge, formatDateTime, utcDate } from "@/lib/format";

it("parses naive-UTC ISO strings as UTC", () => {
  expect(utcDate("2026-07-02T15:00:00").toISOString()).toBe("2026-07-02T15:00:00.000Z");
  expect(utcDate("2026-07-02T15:00:00Z").toISOString()).toBe("2026-07-02T15:00:00.000Z");
});

it("reports data age in humane units", () => {
  const now = new Date("2026-07-02T15:01:00Z");
  expect(dataAge("2026-07-02T15:00:42", now)).toBe("18s ago");
  expect(dataAge("2026-07-02T14:55:00", now)).toBe("6m ago");
  expect(dataAge("2026-07-02T12:00:00", now)).toBe("3h ago");
  expect(dataAge("2026-07-02T15:02:00", now)).toBe("0s ago"); // clock skew clamps to 0
});

it("formats datetimes without crashing", () => {
  expect(formatDateTime("2026-07-02T15:00:00")).toBeTruthy();
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm test`
Expected: FAIL — cannot resolve `@/lib/money` / `@/lib/format`.

- [ ] **Step 3: Implement**

`frontend/lib/types.ts`:

```ts
export interface Account {
  id: number;
  name: string;
  kind: "manual" | "strategy";
  cash: string;
  starting_cash: string;
}

export interface PositionValue {
  symbol: string;
  qty: number;
  avg_cost: string;
  last_price: string;
  market_value: string;
  unrealized_pnl: string;
  realized_pnl: string;
}

export interface AccountDetail extends Account {
  equity: string;
  positions: PositionValue[];
}

export interface Snapshot {
  date: string; // YYYY-MM-DD
  equity: string;
  cash: string;
}

export interface Order {
  id: number;
  account_id: number;
  symbol: string;
  side: "buy" | "sell";
  order_type: "market" | "limit";
  tif: "day" | "gtc";
  qty: number;
  limit_price: string | null;
  status: "pending" | "filled" | "cancelled" | "rejected" | "expired";
  reject_reason: string | null;
  placed_at: string;
}

export interface PlaceOrderBody {
  symbol: string;
  side: "buy" | "sell";
  order_type: "market" | "limit";
  qty: number;
  tif: "day" | "gtc";
  limit_price?: string;
  idempotency_key?: string;
}

export interface Quote {
  symbol: string;
  price: string;
  as_of: string;
}

export interface Bar {
  timestamp: string;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: number;
}

export interface Trade {
  order_id: number;
  symbol: string;
  side: "buy" | "sell";
  qty: number;
  price: string;
  commission: string;
  realized_pnl: string | null;
  filled_at: string;
  note: string | null;
}

export interface Stats {
  closed_trades: number;
  wins: number;
  win_rate: number | null;
  avg_gain: string | null;
  avg_loss: string | null;
}

export interface Strategy {
  name: string;
  schedule: string;
  enabled: boolean;
  account_id: number;
}

export interface StrategyRun {
  id: number;
  strategy_name: string;
  started_at: string;
  finished_at: string | null;
  status: "ok" | "error";
  detail: string;
}
```

`frontend/lib/money.ts`:

```ts
// Exact money math on the API's decimal strings. BigInt at scale 4 —
// matches the backend's 4dp quantization. Never floats.

const SCALE = 4;
const FACTOR = 10n ** BigInt(SCALE);

export function moneyToBig(s: string): bigint {
  const m = /^(-?)(\d+)(?:\.(\d+))?$/.exec(s.trim());
  if (!m) throw new Error(`invalid money value: ${JSON.stringify(s)}`);
  const [, sign, whole, frac = ""] = m;
  const digits = whole + frac.padEnd(SCALE, "0").slice(0, SCALE);
  const value = BigInt(digits);
  return sign === "-" ? -value : value;
}

export function bigToMoney(v: bigint): string {
  const neg = v < 0n;
  const abs = neg ? -v : v;
  const whole = abs / FACTOR;
  const frac = (abs % FACTOR).toString().padStart(SCALE, "0").replace(/0+$/, "");
  return `${neg ? "-" : ""}${whole}${frac ? "." + frac : ""}`;
}

export function mulMoney(price: string, qty: number): string {
  if (!Number.isInteger(qty)) throw new Error(`qty must be an integer: ${qty}`);
  return bigToMoney(moneyToBig(price) * BigInt(qty));
}

export function addMoney(a: string, b: string): string {
  return bigToMoney(moneyToBig(a) + moneyToBig(b));
}

export function subMoney(a: string, b: string): string {
  return bigToMoney(moneyToBig(a) - moneyToBig(b));
}

export function gtMoney(a: string, b: string): boolean {
  return moneyToBig(a) > moneyToBig(b);
}

export function isNeg(s: string): boolean {
  return s.trim().startsWith("-");
}

export function formatUsd(s: string, dp = 2): string {
  const neg = isNeg(s);
  const [whole, frac = ""] = (neg ? s.trim().slice(1) : s.trim()).split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const fracOut = dp > 0 ? "." + frac.padEnd(dp, "0").slice(0, dp) : "";
  return `${neg ? "-" : ""}$${grouped}${fracOut}`;
}
```

`frontend/lib/format.ts`:

```ts
// Backend datetimes are naive UTC without a "Z" suffix.

export function utcDate(iso: string): Date {
  return new Date(iso.endsWith("Z") ? iso : iso + "Z");
}

export function dataAge(asOfIso: string, now: Date = new Date()): string {
  const secs = Math.max(0, Math.floor((now.getTime() - utcDate(asOfIso).getTime()) / 1000));
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  return `${Math.floor(secs / 3600)}h ago`;
}

export function formatDateTime(iso: string): string {
  return utcDate(iso).toLocaleString();
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm test`
Expected: all pass (navbar + 9 new).

- [ ] **Step 5: Commit**

```bash
git add lib tests
git commit -m "feat: API types, exact BigInt money math, naive-UTC date helpers"
```

---

### Task 3: API client, login page, 401 redirect

**Files:**
- Create: `frontend/lib/api.ts`, `frontend/app/login/page.tsx`, `frontend/tests/utils.tsx`
- Modify: `frontend/components/NavBar.tsx` (add logout button)
- Test: `frontend/tests/api.test.ts`, `frontend/tests/login.test.tsx`

**Interfaces:**
- Consumes: `types.ts` (Task 2).
- Produces:
  - `ApiError` class with `status: number` and `message`.
  - `api` object with methods (exact names later tasks call): `login(password)`, `logout()`, `accounts()`, `accountDetail(id)`, `snapshots(id)`, `orders(accountId, status?)`, `placeOrder(accountId, body: PlaceOrderBody)`, `cancelOrder(orderId)`, `saveNote(orderId, text)`, `quote(symbol)`, `bars(symbol, limit?)`, `journal(accountId)`, `stats(accountId)`, `strategies()`, `toggleStrategy(name)`, `runs(name, limit?)`.
  - 401 on any request outside `/login` → browser redirected to `/login`.
  - `tests/utils.tsx`: `renderWithClient(ui: React.ReactElement)` — wraps in a fresh `QueryClientProvider` (retry off).

- [ ] **Step 1: Write the failing tests**

`frontend/tests/utils.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";

export function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}
```

`frontend/tests/api.test.ts`:

```ts
import { api, ApiError } from "@/lib/api";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => vi.unstubAllGlobals());

it("returns parsed JSON on success", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => jsonResponse([{ id: 1 }])));
  await expect(api.accounts()).resolves.toEqual([{ id: 1 }]);
});

it("throws ApiError with FastAPI detail on failure", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ detail: "no such account" }, 404)));
  const err = await api.accountDetail(99).catch((e) => e);
  expect(err).toBeInstanceOf(ApiError);
  expect(err.status).toBe(404);
  expect(err.message).toBe("no such account");
});

it("redirects to /login on 401 outside the login page", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ detail: "not authenticated" }, 401)));
  const fake = { pathname: "/", href: "" };
  Object.defineProperty(window, "location", { value: fake, writable: true });
  await api.accounts().catch(() => {});
  expect(fake.href).toBe("/login");
});

it("does not redirect on 401 from the login page itself", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ detail: "wrong password" }, 401)));
  const fake = { pathname: "/login", href: "" };
  Object.defineProperty(window, "location", { value: fake, writable: true });
  await api.login("bad").catch(() => {});
  expect(fake.href).toBe("");
});

it("POSTs JSON bodies with content-type", async () => {
  const fetchMock = vi.fn(async () => jsonResponse({ ok: true }));
  vi.stubGlobal("fetch", fetchMock);
  await api.login("pw");
  const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(url).toBe("/api/login");
  expect(init.method).toBe("POST");
  expect(init.body).toBe(JSON.stringify({ password: "pw" }));
  expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
});
```

`frontend/tests/login.test.tsx`:

```tsx
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithClient } from "./utils";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { ...actual.api, login: vi.fn() } };
});

import { api, ApiError } from "@/lib/api";
import LoginPage from "@/app/login/page";

it("logs in and navigates to the dashboard", async () => {
  vi.mocked(api.login).mockResolvedValue({ ok: true });
  renderWithClient(<LoginPage />);
  await userEvent.type(screen.getByLabelText(/password/i), "pw");
  await userEvent.click(screen.getByRole("button", { name: /log in/i }));
  await waitFor(() => expect(push).toHaveBeenCalledWith("/"));
  expect(api.login).toHaveBeenCalledWith("pw");
});

it("shows the error on a wrong password", async () => {
  vi.mocked(api.login).mockRejectedValue(new ApiError(401, "wrong password"));
  renderWithClient(<LoginPage />);
  await userEvent.type(screen.getByLabelText(/password/i), "nope");
  await userEvent.click(screen.getByRole("button", { name: /log in/i }));
  expect(await screen.findByText("wrong password")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm test`
Expected: FAIL — cannot resolve `@/lib/api` / `@/app/login/page`.

- [ ] **Step 3: Implement**

`frontend/lib/api.ts`:

```ts
import type {
  Account, AccountDetail, Bar, Order, PlaceOrderBody, Quote, Snapshot,
  Stats, Strategy, StrategyRun, Trade,
} from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    credentials: "include",
    ...init,
    headers: {
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });
  if (
    res.status === 401 &&
    typeof window !== "undefined" &&
    !window.location.pathname.startsWith("/login")
  ) {
    window.location.href = "/login";
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      // non-JSON error body: keep statusText
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

const post = (body: unknown): RequestInit => ({
  method: "POST",
  body: JSON.stringify(body),
});

export const api = {
  login: (password: string) => request<{ ok: boolean }>("/api/login", post({ password })),
  logout: () => request<{ ok: boolean }>("/api/logout", { method: "POST" }),
  accounts: () => request<Account[]>("/api/accounts"),
  accountDetail: (id: number) => request<AccountDetail>(`/api/accounts/${id}`),
  snapshots: (id: number) => request<Snapshot[]>(`/api/accounts/${id}/snapshots`),
  orders: (accountId: number, status?: string) =>
    request<Order[]>(`/api/accounts/${accountId}/orders${status ? `?status=${status}` : ""}`),
  placeOrder: (accountId: number, body: PlaceOrderBody) =>
    request<Order>(`/api/accounts/${accountId}/orders`, post(body)),
  cancelOrder: (orderId: number) =>
    request<Order>(`/api/orders/${orderId}/cancel`, { method: "POST" }),
  saveNote: (orderId: number, text: string) =>
    request<{ ok: boolean }>(`/api/orders/${orderId}/note`, {
      method: "PUT",
      body: JSON.stringify({ text }),
    }),
  quote: (symbol: string) =>
    request<Quote>(`/api/market/quote/${encodeURIComponent(symbol)}`),
  bars: (symbol: string, limit = 200) =>
    request<Bar[]>(`/api/market/bars/${encodeURIComponent(symbol)}?limit=${limit}`),
  journal: (accountId: number) => request<Trade[]>(`/api/journal?account_id=${accountId}`),
  stats: (accountId: number) => request<Stats>(`/api/journal/stats?account_id=${accountId}`),
  strategies: () => request<Strategy[]>("/api/strategies"),
  toggleStrategy: (name: string) =>
    request<Strategy>(`/api/strategies/${encodeURIComponent(name)}/toggle`, { method: "POST" }),
  runs: (name: string, limit = 20) =>
    request<StrategyRun[]>(`/api/strategies/${encodeURIComponent(name)}/runs?limit=${limit}`),
};
```

`frontend/app/login/page.tsx`:

```tsx
"use client";

import { useMutation } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, ApiError } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [password, setPassword] = useState("");
  const login = useMutation({
    mutationFn: (pw: string) => api.login(pw),
    onSuccess: () => router.push("/"),
  });

  return (
    <div className="mx-auto mt-24 max-w-sm rounded-lg border border-gray-800 bg-gray-900 p-6">
      <h1 className="mb-4 text-lg font-semibold text-gray-100">Log in</h1>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          login.mutate(password);
        }}
        className="space-y-3"
      >
        <label className="block text-sm text-gray-400" htmlFor="password">
          Password
        </label>
        <input
          id="password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="w-full rounded border border-gray-700 bg-gray-950 px-3 py-2 text-gray-100 outline-none focus:border-gray-500"
          autoFocus
        />
        {login.error && (
          <p className="text-sm text-red-400">
            {login.error instanceof ApiError ? login.error.message : "Login failed"}
          </p>
        )}
        <button
          type="submit"
          disabled={login.isPending || password.length === 0}
          className="w-full rounded bg-emerald-700 px-3 py-2 font-medium text-white hover:bg-emerald-600 disabled:opacity-50"
        >
          Log in
        </button>
      </form>
    </div>
  );
}
```

Modify `frontend/components/NavBar.tsx` — add a logout button as the last child of the `<nav>` (after the links `.map`), and add the import:

```tsx
import { api } from "@/lib/api";
```

```tsx
        <button
          onClick={() => {
            void api.logout().then(() => {
              window.location.href = "/login";
            });
          }}
          className="ml-auto rounded px-3 py-1.5 text-sm text-gray-400 hover:text-gray-200"
        >
          Log out
        </button>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm test` — Expected: all pass.
Run: `npm run typecheck` — Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add lib app components tests
git commit -m "feat: typed API client with 401 redirect, login page, logout"
```

---

### Task 4: Chart components (CandleChart, EquityCurve)

**Files:**
- Create: `frontend/components/CandleChart.tsx`, `frontend/components/EquityCurve.tsx`
- Test: `frontend/tests/charts.test.tsx`

**Interfaces:**
- Consumes: `Bar`, `Snapshot` types (Task 2).
- Produces: `CandleChart({ bars: Bar[] })` — candles + volume histogram; `EquityCurve({ snapshots: Snapshot[], height?: number })` — area series of equity over time. Both dark-themed, auto-sizing, cleaned up on unmount. (lightweight-charts requires numeric coordinates: `Number()` conversion here is the sanctioned display-only exception to the no-float rule.)

- [ ] **Step 1: Write the failing tests**

`frontend/tests/charts.test.tsx` (jsdom has no canvas, so the library is mocked; the tests verify OUR mapping logic — string→number conversion and date handling):

```tsx
import { render } from "@testing-library/react";

const setData = vi.fn();
const chart = {
  addSeries: vi.fn(() => ({ setData })),
  priceScale: vi.fn(() => ({ applyOptions: vi.fn() })),
  timeScale: vi.fn(() => ({ fitContent: vi.fn() })),
  applyOptions: vi.fn(),
  remove: vi.fn(),
};
vi.mock("lightweight-charts", () => ({
  createChart: vi.fn(() => chart),
  CandlestickSeries: "CandlestickSeries",
  HistogramSeries: "HistogramSeries",
  AreaSeries: "AreaSeries",
}));

import { createChart } from "lightweight-charts";
import { CandleChart } from "@/components/CandleChart";
import { EquityCurve } from "@/components/EquityCurve";

beforeEach(() => vi.clearAllMocks());

const bar = {
  timestamp: "2026-06-30T04:00:00",
  open: "100", high: "101.5", low: "99", close: "100.25", volume: 12345,
};

it("maps bars to numeric candle and volume points keyed by date", () => {
  render(<CandleChart bars={[bar]} />);
  expect(createChart).toHaveBeenCalledOnce();
  expect(setData).toHaveBeenCalledTimes(2); // candles + volume
  expect(setData.mock.calls[0][0]).toEqual([
    { time: "2026-06-30", open: 100, high: 101.5, low: 99, close: 100.25 },
  ]);
  expect(setData.mock.calls[1][0]).toEqual([{ time: "2026-06-30", value: 12345 }]);
});

it("maps snapshots to an equity area series", () => {
  render(
    <EquityCurve snapshots={[{ date: "2026-07-02", equity: "100100.5", cash: "99000" }]} />,
  );
  expect(setData).toHaveBeenCalledWith([{ time: "2026-07-02", value: 100100.5 }]);
});

it("removes the chart on unmount", () => {
  const { unmount } = render(<CandleChart bars={[bar]} />);
  unmount();
  expect(chart.remove).toHaveBeenCalled();
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm test`
Expected: FAIL — cannot resolve `@/components/CandleChart`.

- [ ] **Step 3: Implement**

`frontend/components/CandleChart.tsx`:

```tsx
"use client";

import {
  CandlestickSeries, HistogramSeries, createChart,
} from "lightweight-charts";
import { useEffect, useRef } from "react";
import type { Bar } from "@/lib/types";

const DARK = {
  layout: { background: { color: "#030712" }, textColor: "#9ca3af" },
  grid: { vertLines: { color: "#1f2937" }, horzLines: { color: "#1f2937" } },
};

export function CandleChart({ bars }: { bars: Bar[] }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = createChart(ref.current, { autoSize: true, ...DARK });
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: "#22c55e", downColor: "#ef4444",
      wickUpColor: "#22c55e", wickDownColor: "#ef4444",
      borderVisible: false,
    });
    candles.setData(
      bars.map((b) => ({
        time: b.timestamp.slice(0, 10),
        open: Number(b.open), high: Number(b.high),
        low: Number(b.low), close: Number(b.close),
      })),
    );
    const volume = chart.addSeries(HistogramSeries, {
      priceScaleId: "vol",
      color: "#334155",
      priceFormat: { type: "volume" },
    });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });
    volume.setData(bars.map((b) => ({ time: b.timestamp.slice(0, 10), value: b.volume })));
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [bars]);

  return <div ref={ref} className="h-[420px] w-full" />;
}
```

`frontend/components/EquityCurve.tsx`:

```tsx
"use client";

import { AreaSeries, createChart } from "lightweight-charts";
import { useEffect, useRef } from "react";
import type { Snapshot } from "@/lib/types";

export function EquityCurve({
  snapshots,
  height = 220,
}: {
  snapshots: Snapshot[];
  height?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = createChart(ref.current, {
      autoSize: true,
      layout: { background: { color: "#030712" }, textColor: "#9ca3af" },
      grid: { vertLines: { color: "#1f2937" }, horzLines: { color: "#1f2937" } },
    });
    const area = chart.addSeries(AreaSeries, {
      lineColor: "#34d399",
      topColor: "rgba(52, 211, 153, 0.25)",
      bottomColor: "rgba(52, 211, 153, 0.02)",
    });
    area.setData(snapshots.map((s) => ({ time: s.date, value: Number(s.equity) })));
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [snapshots]);

  return <div ref={ref} style={{ height }} className="w-full" />;
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm test` — Expected: all pass.
Run: `npm run typecheck` — Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add components tests
git commit -m "feat: candlestick and equity-curve chart components"
```

### Task 5: Account context, switcher, and Dashboard page

**Files:**
- Create: `frontend/app/account-context.tsx`, `frontend/components/AccountSwitcher.tsx`, `frontend/components/StatCard.tsx`, `frontend/components/PositionsTable.tsx`, `frontend/components/OrdersTable.tsx`
- Modify: `frontend/app/providers.tsx` (wrap with AccountProvider), `frontend/components/NavBar.tsx` (mount AccountSwitcher), `frontend/app/page.tsx` (replace placeholder), `frontend/tests/navbar.test.tsx` (NavBar now needs a QueryClient + AccountProvider)
- Test: `frontend/tests/dashboard.test.tsx`

**Interfaces:**
- Consumes: `api` (Task 3), `EquityCurve` (Task 4), money/format helpers (Task 2).
- Produces:
  - `useAccount(): { accountId: number | null; setAccountId: (id: number) => void }` from `@/app/account-context`; `AccountProvider` picks the first `kind === "manual"` account by default and persists the choice in `localStorage["pt-account"]`.
  - `StatCard({ label, value, tone? })` where `tone` is `"pos" | "neg" | undefined`.
  - `PositionsTable({ positions: PositionValue[] })`.
  - `OrdersTable({ orders: Order[], onCancel?: (id: number) => void, cancellingId?: number | null })` — cancel column rendered only when `onCancel` given (reused by Task 8).
  - Dashboard polls `accountDetail` every 30s; shows equity, cash, total P&L (`equity − starting_cash`), "Since last close" P&L (`equity − last snapshot equity`), equity curve, positions, open orders; a banner when the API returns 503.

- [ ] **Step 1: Write the failing test**

`frontend/tests/dashboard.test.tsx`:

```tsx
import { screen } from "@testing-library/react";
import { renderWithClient } from "./utils";

vi.mock("next/navigation", () => ({ usePathname: () => "/" }));
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
      accounts: vi.fn(),
      accountDetail: vi.fn(),
      snapshots: vi.fn(),
      orders: vi.fn(),
    },
  };
});

import { api } from "@/lib/api";
import { AccountProvider } from "@/app/account-context";
import DashboardPage from "@/app/page";

const manual = { id: 1, name: "manual", kind: "manual" as const, cash: "99000", starting_cash: "100000" };

beforeEach(() => {
  vi.mocked(api.accounts).mockResolvedValue([manual]);
  vi.mocked(api.accountDetail).mockResolvedValue({
    ...manual,
    equity: "100100",
    positions: [{
      symbol: "SPY", qty: 10, avg_cost: "100", last_price: "110",
      market_value: "1100", unrealized_pnl: "100", realized_pnl: "0",
    }],
  });
  vi.mocked(api.snapshots).mockResolvedValue([
    { date: "2026-07-01", equity: "100000", cash: "100000" },
    { date: "2026-07-02", equity: "100050", cash: "99000" },
  ]);
  vi.mocked(api.orders).mockResolvedValue([]);
});

it("shows equity, cash, total and since-close P&L, and positions", async () => {
  renderWithClient(
    <AccountProvider>
      <DashboardPage />
    </AccountProvider>,
  );
  expect(await screen.findByText("$100,100.00")).toBeInTheDocument(); // equity
  expect(screen.getByText("$99,000.00")).toBeInTheDocument(); // cash
  // "+$100.00" appears twice by construction: total P&L stat AND the
  // position's unrealized P&L (equity−starting == unrealized here).
  expect(screen.getAllByText("+$100.00").length).toBeGreaterThanOrEqual(2);
  expect(screen.getByText("+$50.00")).toBeInTheDocument(); // since last close
  expect(screen.getByText("SPY")).toBeInTheDocument();
  expect(screen.getByText("$110.00")).toBeInTheDocument(); // last price
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test`
Expected: FAIL — cannot resolve `@/app/account-context`.

- [ ] **Step 3: Implement**

`frontend/app/account-context.tsx`:

```tsx
"use client";

import { useQuery } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useState } from "react";
import { api } from "@/lib/api";

interface AccountCtx {
  accountId: number | null;
  setAccountId: (id: number) => void;
}

const Ctx = createContext<AccountCtx | null>(null);

export function AccountProvider({ children }: { children: React.ReactNode }) {
  const [accountId, setAccountId] = useState<number | null>(null);
  const { data: accounts } = useQuery({ queryKey: ["accounts"], queryFn: api.accounts });

  useEffect(() => {
    if (accountId === null && accounts?.length) {
      const stored = Number(localStorage.getItem("pt-account") ?? "");
      const fallback = accounts.find((a) => a.kind === "manual") ?? accounts[0];
      setAccountId(accounts.some((a) => a.id === stored) ? stored : fallback.id);
    }
  }, [accounts, accountId]);

  const set = (id: number) => {
    localStorage.setItem("pt-account", String(id));
    setAccountId(id);
  };

  return <Ctx.Provider value={{ accountId, setAccountId: set }}>{children}</Ctx.Provider>;
}

export function useAccount(): AccountCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAccount must be used inside AccountProvider");
  return v;
}
```

Modify `frontend/app/providers.tsx` — wrap children with the provider (inside QueryClientProvider):

```tsx
import { AccountProvider } from "./account-context";
```

```tsx
  return (
    <QueryClientProvider client={client}>
      <AccountProvider>{children}</AccountProvider>
    </QueryClientProvider>
  );
```

`frontend/components/AccountSwitcher.tsx`:

```tsx
"use client";

import { useQuery } from "@tanstack/react-query";
import { useAccount } from "@/app/account-context";
import { api } from "@/lib/api";

export function AccountSwitcher() {
  const { accountId, setAccountId } = useAccount();
  const { data: accounts } = useQuery({ queryKey: ["accounts"], queryFn: api.accounts });
  if (!accounts?.length || accountId === null) return null;
  return (
    <select
      aria-label="Account"
      value={accountId}
      onChange={(e) => setAccountId(Number(e.target.value))}
      className="rounded border border-gray-700 bg-gray-950 px-2 py-1 text-sm text-gray-200"
    >
      {accounts.map((a) => (
        <option key={a.id} value={a.id}>
          {a.name}
        </option>
      ))}
    </select>
  );
}
```

Modify `frontend/components/NavBar.tsx` — import and mount the switcher immediately before the logout button, and change the logout button's `ml-auto` so the pair sits right-aligned:

```tsx
import { AccountSwitcher } from "@/components/AccountSwitcher";
```

```tsx
        <div className="ml-auto flex items-center gap-2">
          <AccountSwitcher />
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
```

(The `ml-auto` moves from the button to this wrapper div; the button loses it.)

`frontend/components/StatCard.tsx`:

```tsx
export function StatCard({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "pos" | "neg";
}) {
  const color =
    tone === "pos" ? "text-emerald-400" : tone === "neg" ? "text-red-400" : "text-gray-100";
  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900 p-4">
      <div className="text-xs uppercase tracking-wide text-gray-500">{label}</div>
      <div className={`mt-1 text-xl font-semibold tabular-nums ${color}`}>{value}</div>
    </div>
  );
}
```

`frontend/components/PositionsTable.tsx`:

```tsx
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
      <tbody>
        {positions.map((p) => (
          <tr key={p.symbol} className="border-b border-gray-900">
            <td className="py-2 font-medium text-gray-100">{p.symbol}</td>
            <td className="py-2 text-right">{p.qty}</td>
            <td className="py-2 text-right">{formatUsd(p.avg_cost)}</td>
            <td className="py-2 text-right">{formatUsd(p.last_price)}</td>
            <td className="py-2 text-right">{formatUsd(p.market_value)}</td>
            <td className="py-2 text-right"><Pnl value={p.unrealized_pnl} /></td>
            <td className="py-2 text-right"><Pnl value={p.realized_pnl} /></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
```

`frontend/components/OrdersTable.tsx`:

```tsx
import { formatDateTime } from "@/lib/format";
import { formatUsd } from "@/lib/money";
import type { Order } from "@/lib/types";

const statusColor: Record<Order["status"], string> = {
  pending: "text-amber-400",
  filled: "text-emerald-400",
  cancelled: "text-gray-500",
  rejected: "text-red-400",
  expired: "text-gray-500",
};

export function OrdersTable({
  orders,
  onCancel,
  cancellingId,
}: {
  orders: Order[];
  onCancel?: (id: number) => void;
  cancellingId?: number | null;
}) {
  if (orders.length === 0) {
    return <p className="text-sm text-gray-500">No orders.</p>;
  }
  return (
    <table className="w-full text-sm tabular-nums">
      <thead>
        <tr className="border-b border-gray-800 text-left text-xs uppercase text-gray-500">
          <th className="py-2">Placed</th>
          <th className="py-2">Symbol</th>
          <th className="py-2">Side</th>
          <th className="py-2">Type</th>
          <th className="py-2 text-right">Qty</th>
          <th className="py-2 text-right">Limit</th>
          <th className="py-2">TIF</th>
          <th className="py-2">Status</th>
          {onCancel && <th className="py-2" />}
        </tr>
      </thead>
      <tbody>
        {orders.map((o) => (
          <tr key={o.id} className="border-b border-gray-900">
            <td className="py-2 text-gray-400">{formatDateTime(o.placed_at)}</td>
            <td className="py-2 font-medium text-gray-100">{o.symbol}</td>
            <td className={`py-2 ${o.side === "buy" ? "text-emerald-400" : "text-red-400"}`}>
              {o.side}
            </td>
            <td className="py-2">{o.order_type}</td>
            <td className="py-2 text-right">{o.qty}</td>
            <td className="py-2 text-right">
              {o.limit_price ? formatUsd(o.limit_price) : "—"}
            </td>
            <td className="py-2 uppercase">{o.tif}</td>
            <td className={`py-2 ${statusColor[o.status]}`}>
              {o.status}
              {o.reject_reason && (
                <span className="block text-xs text-gray-500">{o.reject_reason}</span>
              )}
            </td>
            {onCancel && (
              <td className="py-2 text-right">
                {o.status === "pending" && (
                  <button
                    onClick={() => onCancel(o.id)}
                    disabled={cancellingId === o.id}
                    className="rounded border border-gray-700 px-2 py-0.5 text-xs text-gray-300 hover:border-gray-500 disabled:opacity-50"
                  >
                    Cancel
                  </button>
                )}
              </td>
            )}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
```

`frontend/app/page.tsx` (replace the placeholder entirely):

```tsx
"use client";

import { useQuery } from "@tanstack/react-query";
import { useAccount } from "@/app/account-context";
import { EquityCurve } from "@/components/EquityCurve";
import { OrdersTable } from "@/components/OrdersTable";
import { PositionsTable } from "@/components/PositionsTable";
import { StatCard } from "@/components/StatCard";
import { api, ApiError } from "@/lib/api";
import { formatUsd, isNeg, subMoney } from "@/lib/money";

function signed(value: string): { text: string; tone: "pos" | "neg" } {
  const neg = isNeg(value);
  return { text: `${neg ? "" : "+"}${formatUsd(value)}`, tone: neg ? "neg" : "pos" };
}

export default function DashboardPage() {
  const { accountId } = useAccount();
  const detail = useQuery({
    queryKey: ["account", accountId],
    queryFn: () => api.accountDetail(accountId!),
    enabled: accountId !== null,
    refetchInterval: 30_000,
  });
  const snapshots = useQuery({
    queryKey: ["snapshots", accountId],
    queryFn: () => api.snapshots(accountId!),
    enabled: accountId !== null,
  });
  const openOrders = useQuery({
    queryKey: ["orders", accountId, "pending"],
    queryFn: () => api.orders(accountId!, "pending"),
    enabled: accountId !== null,
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
  const totalPnl = signed(subMoney(d.equity, d.starting_cash));
  const snaps = snapshots.data ?? [];
  const lastSnap = snaps.length ? snaps[snaps.length - 1] : null;
  const sinceClose = lastSnap ? signed(subMoney(d.equity, lastSnap.equity)) : null;

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatCard label="Equity" value={formatUsd(d.equity)} />
        <StatCard label="Cash" value={formatUsd(d.cash)} />
        <StatCard label="Total P&L" value={totalPnl.text} tone={totalPnl.tone} />
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

Also replace `frontend/tests/navbar.test.tsx` entirely — NavBar now mounts `AccountSwitcher`, which requires a QueryClient and AccountProvider:

```tsx
import { screen } from "@testing-library/react";
import { renderWithClient } from "./utils";

vi.mock("next/navigation", () => ({ usePathname: () => "/" }));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { ...actual.api, accounts: vi.fn(async () => []) } };
});

import { AccountProvider } from "@/app/account-context";
import { NavBar } from "@/components/NavBar";

it("renders all five nav links", () => {
  renderWithClient(
    <AccountProvider>
      <NavBar />
    </AccountProvider>,
  );
  for (const label of ["Dashboard", "Trade", "Orders", "Journal", "Strategies"]) {
    expect(screen.getByRole("link", { name: label })).toBeInTheDocument();
  }
});
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm test` — Expected: all pass.
Run: `npm run typecheck` — Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add app components tests
git commit -m "feat: account context, switcher, and dashboard page"
```

---

### Task 6: Trade page — symbol search, quote badge, chart

**Files:**
- Create: `frontend/components/QuoteBadge.tsx`, `frontend/app/trade/page.tsx`
- Test: `frontend/tests/quote-badge.test.tsx`

**Interfaces:**
- Consumes: `api`, `CandleChart`, `dataAge`, `formatUsd`.
- Produces:
  - `QuoteBadge({ quote, error, now }: { quote?: Quote; error?: unknown; now?: Date })` — shows price + age (`now` injectable for tests, defaults to `new Date()`); amber "stale" styling when age > 120s; "Unknown symbol" on 404; "Market data unavailable" on 503.
  - Trade page: reads initial symbol from `?symbol=` (default `SPY`); symbol form updates state on submit (uppercased); quote polls every 15s; daily candle chart below. The page wraps its content in `<Suspense>` because `useSearchParams` requires it at build time.
  - Task 7 mounts `OrderTicket` into the `{/* ORDER TICKET SLOT */}` marker.

- [ ] **Step 1: Write the failing test**

`frontend/tests/quote-badge.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { ApiError } from "@/lib/api";
import { QuoteBadge } from "@/components/QuoteBadge";

const now = new Date("2026-07-02T15:02:00Z");

it("shows price and age for a fresh quote", () => {
  render(
    <QuoteBadge
      quote={{ symbol: "SPY", price: "512.34", as_of: "2026-07-02T15:01:30" }}
      now={now}
    />,
  );
  expect(screen.getByText("$512.34")).toBeInTheDocument();
  expect(screen.getByText("30s ago")).toBeInTheDocument();
});

it("marks a stale quote", () => {
  render(
    <QuoteBadge
      quote={{ symbol: "SPY", price: "512.34", as_of: "2026-07-02T14:55:00" }}
      now={now}
    />,
  );
  expect(screen.getByText(/stale/i)).toBeInTheDocument();
});

it("reports unknown symbols and outages distinctly", () => {
  const { rerender } = render(<QuoteBadge error={new ApiError(404, "unknown symbol: XXXX")} />);
  expect(screen.getByText(/unknown symbol/i)).toBeInTheDocument();
  rerender(<QuoteBadge error={new ApiError(503, "market data unavailable")} />);
  expect(screen.getByText(/market data unavailable/i)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test`
Expected: FAIL — cannot resolve `@/components/QuoteBadge`.

- [ ] **Step 3: Implement**

`frontend/components/QuoteBadge.tsx`:

```tsx
"use client";

import { ApiError } from "@/lib/api";
import { dataAge, utcDate } from "@/lib/format";
import { formatUsd } from "@/lib/money";
import type { Quote } from "@/lib/types";

export function QuoteBadge({
  quote,
  error,
  now = new Date(),
}: {
  quote?: Quote;
  error?: unknown;
  now?: Date;
}) {
  if (error instanceof ApiError) {
    return (
      <span className="text-sm text-red-400">
        {error.status === 404 ? "Unknown symbol" : "Market data unavailable"}
      </span>
    );
  }
  if (!quote) return <span className="text-sm text-gray-500">—</span>;

  const ageSecs = Math.floor((now.getTime() - utcDate(quote.as_of).getTime()) / 1000);
  const stale = ageSecs > 120;
  return (
    <span className="flex items-baseline gap-2">
      <span className="text-2xl font-semibold tabular-nums text-gray-100">
        {formatUsd(quote.price)}
      </span>
      <span className={`text-xs ${stale ? "text-amber-400" : "text-gray-500"}`}>
        {dataAge(quote.as_of, now)}
        {stale && " · stale"}
      </span>
    </span>
  );
}
```

`frontend/app/trade/page.tsx`:

```tsx
"use client";

import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { CandleChart } from "@/components/CandleChart";
import { QuoteBadge } from "@/components/QuoteBadge";
import { api } from "@/lib/api";

function TradeContent() {
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
        <aside>{/* ORDER TICKET SLOT */}</aside>
      </div>
    </div>
  );
}

export default function TradePage() {
  return (
    <Suspense>
      <TradeContent />
    </Suspense>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm test` — Expected: all pass.
Run: `npm run typecheck` — Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add app components tests
git commit -m "feat: trade page with symbol search, quote badge, and candle chart"
```

---

### Task 7: Order ticket

**Files:**
- Create: `frontend/components/OrderTicket.tsx`
- Modify: `frontend/app/trade/page.tsx` (mount into the ORDER TICKET SLOT)
- Test: `frontend/tests/order-ticket.test.tsx`

**Interfaces:**
- Consumes: `api.placeOrder`, `useAccount`, `api.accountDetail` (for cash), `mulMoney`/`gtMoney`/`formatUsd`, `Quote`.
- Produces: `OrderTicket({ symbol, quotePrice }: { symbol: string; quotePrice?: string })` — side (buy/sell), type (market/limit), qty, TIF, limit price input when limit; live cost preview `qty × price` via `mulMoney`; "insufficient cash" warning + disabled submit when a buy's cost exceeds the account's cash; submits with a fresh `crypto.randomUUID()` idempotency key; shows the resulting order status (including `rejected` + reason) inline; invalidates `["account", ...]` and `["orders", ...]` queries on success.

- [ ] **Step 1: Write the failing tests**

`frontend/tests/order-ticket.test.tsx`:

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

const manual = { id: 1, name: "manual", kind: "manual" as const, cash: "1000", starting_cash: "1000" };

function setup(quotePrice?: string) {
  vi.mocked(api.accounts).mockResolvedValue([manual]);
  vi.mocked(api.accountDetail).mockResolvedValue({ ...manual, equity: "1000", positions: [] });
  return renderWithClient(
    <AccountProvider>
      <OrderTicket symbol="SPY" quotePrice={quotePrice} />
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
    tif: "day", qty: 5, limit_price: null, status: "filled", reject_reason: null,
    placed_at: "2026-07-02T15:00:00",
  });
  setup("100");
  await userEvent.clear(screen.getByLabelText(/quantity/i));
  await userEvent.type(screen.getByLabelText(/quantity/i), "5");
  await userEvent.click(screen.getByRole("button", { name: /place order/i }));
  await waitFor(() => expect(api.placeOrder).toHaveBeenCalled());
  const [accountId, body] = vi.mocked(api.placeOrder).mock.calls[0];
  expect(accountId).toBe(1);
  expect(body).toMatchObject({ symbol: "SPY", side: "buy", order_type: "market", qty: 5, tif: "day" });
  expect(typeof body.idempotency_key).toBe("string");
  expect(body.idempotency_key!.length).toBeGreaterThan(10);
  expect(await screen.findByText(/filled/i)).toBeInTheDocument();
});

it("shows rejection reasons from the backend", async () => {
  vi.mocked(api.placeOrder).mockResolvedValue({
    id: 8, account_id: 1, symbol: "SPY", side: "buy", order_type: "limit",
    tif: "day", qty: 5, limit_price: "90", status: "rejected",
    reject_reason: "market data unavailable", placed_at: "2026-07-02T15:00:00",
  });
  setup("100");
  await userEvent.click(screen.getByRole("radio", { name: /limit/i }));
  await userEvent.type(screen.getByLabelText(/limit price/i), "90");
  await userEvent.clear(screen.getByLabelText(/quantity/i));
  await userEvent.type(screen.getByLabelText(/quantity/i), "5");
  await userEvent.click(screen.getByRole("button", { name: /place order/i }));
  expect(await screen.findByText(/market data unavailable/i)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm test`
Expected: FAIL — cannot resolve `@/components/OrderTicket`.

- [ ] **Step 3: Implement**

`frontend/components/OrderTicket.tsx`:

```tsx
"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useAccount } from "@/app/account-context";
import { api, ApiError } from "@/lib/api";
import { formatUsd, gtMoney, mulMoney } from "@/lib/money";
import type { Order, PlaceOrderBody } from "@/lib/types";

const radio = (active: boolean) =>
  `flex-1 cursor-pointer rounded border px-3 py-1.5 text-center text-sm ${
    active
      ? "border-gray-500 bg-gray-800 text-white"
      : "border-gray-700 text-gray-400 hover:text-gray-200"
  }`;

export function OrderTicket({
  symbol,
  quotePrice,
}: {
  symbol: string;
  quotePrice?: string;
}) {
  const { accountId } = useAccount();
  const qc = useQueryClient();
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [type, setType] = useState<"market" | "limit">("market");
  const [qty, setQty] = useState("1");
  const [tif, setTif] = useState<"day" | "gtc">("day");
  const [limitPrice, setLimitPrice] = useState("");
  const [result, setResult] = useState<Order | null>(null);

  const detail = useQuery({
    queryKey: ["account", accountId],
    queryFn: () => api.accountDetail(accountId!),
    enabled: accountId !== null,
  });

  const qtyNum = /^\d+$/.test(qty) ? parseInt(qty, 10) : 0;
  const previewPrice = type === "limit" ? limitPrice : quotePrice;
  let cost: string | null = null;
  try {
    cost = previewPrice && qtyNum > 0 ? mulMoney(previewPrice, qtyNum) : null;
  } catch {
    cost = null; // partially-typed limit price
  }
  const cash = detail.data?.cash;
  const insufficient =
    side === "buy" && cost !== null && cash !== undefined && gtMoney(cost, cash);

  const place = useMutation({
    mutationFn: (body: PlaceOrderBody) => api.placeOrder(accountId!, body),
    onSuccess: (order) => {
      setResult(order);
      void qc.invalidateQueries({ queryKey: ["account", accountId] });
      void qc.invalidateQueries({ queryKey: ["orders", accountId] });
    },
  });

  const canSubmit =
    accountId !== null &&
    qtyNum > 0 &&
    !insufficient &&
    !place.isPending &&
    (type === "market" || limitPrice.trim().length > 0);

  return (
    <div className="space-y-3 rounded-lg border border-gray-800 bg-gray-900 p-4">
      <h2 className="text-sm font-semibold text-gray-300">Order — {symbol}</h2>

      <div className="flex gap-2" role="radiogroup" aria-label="Side">
        {(["buy", "sell"] as const).map((s) => (
          <button key={s} role="radio" aria-checked={side === s}
            className={radio(side === s)} onClick={() => setSide(s)}>
            {s}
          </button>
        ))}
      </div>

      <div className="flex gap-2" role="radiogroup" aria-label="Order type">
        {(["market", "limit"] as const).map((t) => (
          <button key={t} role="radio" aria-checked={type === t}
            className={radio(type === t)} onClick={() => setType(t)}>
            {t}
          </button>
        ))}
      </div>

      <label className="block text-xs text-gray-500" htmlFor="qty">Quantity</label>
      <input id="qty" inputMode="numeric" value={qty}
        onChange={(e) => setQty(e.target.value.replace(/\D/g, ""))}
        className="w-full rounded border border-gray-700 bg-gray-950 px-3 py-1.5 text-sm text-gray-100 outline-none focus:border-gray-500" />

      {type === "limit" && (
        <>
          <label className="block text-xs text-gray-500" htmlFor="limit">Limit price</label>
          <input id="limit" inputMode="decimal" value={limitPrice}
            onChange={(e) => setLimitPrice(e.target.value.replace(/[^0-9.]/g, ""))}
            className="w-full rounded border border-gray-700 bg-gray-950 px-3 py-1.5 text-sm text-gray-100 outline-none focus:border-gray-500" />
        </>
      )}

      <div className="flex gap-2" role="radiogroup" aria-label="Time in force">
        {(["day", "gtc"] as const).map((t) => (
          <button key={t} role="radio" aria-checked={tif === t}
            className={radio(tif === t)} onClick={() => setTif(t)}>
            {t.toUpperCase()}
          </button>
        ))}
      </div>

      <div className="border-t border-gray-800 pt-2 text-sm">
        <div className="flex justify-between text-gray-400">
          <span>Est. {side === "buy" ? "cost" : "proceeds"}</span>
          <span className="tabular-nums text-gray-100">{cost ? formatUsd(cost) : "—"}</span>
        </div>
        {cash !== undefined && (
          <div className="flex justify-between text-gray-500">
            <span>Cash</span>
            <span className="tabular-nums">{formatUsd(cash)}</span>
          </div>
        )}
        {insufficient && <p className="mt-1 text-xs text-red-400">Insufficient cash</p>}
      </div>

      <button
        onClick={() =>
          place.mutate({
            symbol,
            side,
            order_type: type,
            qty: qtyNum,
            tif,
            ...(type === "limit" ? { limit_price: limitPrice } : {}),
            idempotency_key: crypto.randomUUID(),
          })
        }
        disabled={!canSubmit}
        className={`w-full rounded px-3 py-2 font-medium text-white disabled:opacity-50 ${
          side === "buy" ? "bg-emerald-700 hover:bg-emerald-600" : "bg-red-800 hover:bg-red-700"
        }`}
      >
        {place.isPending ? "Placing…" : "Place order"}
      </button>

      {place.error && (
        <p className="text-sm text-red-400">
          {place.error instanceof ApiError ? place.error.message : "Order failed"}
        </p>
      )}
      {result && (
        <p className="text-sm">
          <span
            className={
              result.status === "filled"
                ? "text-emerald-400"
                : result.status === "pending"
                  ? "text-amber-400"
                  : "text-red-400"
            }
          >
            {result.status}
          </span>
          {result.reject_reason && (
            <span className="block text-xs text-gray-400">{result.reject_reason}</span>
          )}
        </p>
      )}
    </div>
  );
}
```

Modify `frontend/app/trade/page.tsx`: add the import and replace the slot line.

```tsx
import { OrderTicket } from "@/components/OrderTicket";
```

```tsx
        <aside>
          <OrderTicket symbol={symbol} quotePrice={quote.data?.price} />
        </aside>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm test` — Expected: all pass.
Run: `npm run typecheck` — Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add app components tests
git commit -m "feat: order ticket with exact cost preview and idempotent submit"
```

### Task 8: Orders page

**Files:**
- Create: `frontend/app/orders/page.tsx`
- Test: `frontend/tests/orders-page.test.tsx`

**Interfaces:**
- Consumes: `OrdersTable` with `onCancel` (Task 5), `api.orders`/`api.cancelOrder`, `useAccount`.
- Produces: `/orders` — status filter tabs (All, Pending, Filled, Cancelled, Rejected, Expired), the shared table with cancel buttons on pending rows, an error banner when cancel fails (e.g. 409 already-filled), symbols link to `/trade?symbol=X`.

- [ ] **Step 1: Write the failing tests**

`frontend/tests/orders-page.test.tsx`:

```tsx
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithClient } from "./utils";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: { ...actual.api, accounts: vi.fn(), orders: vi.fn(), cancelOrder: vi.fn() },
  };
});

import { AccountProvider } from "@/app/account-context";
import OrdersPage from "@/app/orders/page";
import { api, ApiError } from "@/lib/api";
import type { Order } from "@/lib/types";

const manual = { id: 1, name: "manual", kind: "manual" as const, cash: "1000", starting_cash: "1000" };
const pendingOrder: Order = {
  id: 3, account_id: 1, symbol: "SPY", side: "buy", order_type: "limit", tif: "gtc",
  qty: 10, limit_price: "95", status: "pending", reject_reason: null,
  placed_at: "2026-07-02T15:00:00",
};

function setup(orders: Order[]) {
  vi.mocked(api.accounts).mockResolvedValue([manual]);
  vi.mocked(api.orders).mockResolvedValue(orders);
  return renderWithClient(
    <AccountProvider>
      <OrdersPage />
    </AccountProvider>,
  );
}

it("lists orders and requests the selected status filter", async () => {
  setup([pendingOrder]);
  expect(await screen.findByText("SPY")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /^filled$/i }));
  await waitFor(() => expect(api.orders).toHaveBeenLastCalledWith(1, "filled"));
});

it("cancels a pending order", async () => {
  vi.mocked(api.cancelOrder).mockResolvedValue({ ...pendingOrder, status: "cancelled" });
  setup([pendingOrder]);
  await userEvent.click(await screen.findByRole("button", { name: /cancel/i }));
  await waitFor(() => expect(api.cancelOrder).toHaveBeenCalledWith(3));
});

it("surfaces cancel failures", async () => {
  vi.mocked(api.cancelOrder).mockRejectedValue(
    new ApiError(409, "cannot cancel order in status filled"),
  );
  setup([pendingOrder]);
  await userEvent.click(await screen.findByRole("button", { name: /cancel/i }));
  expect(await screen.findByText(/cannot cancel order/i)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm test`
Expected: FAIL — cannot resolve `@/app/orders/page`.

- [ ] **Step 3: Implement**

`frontend/app/orders/page.tsx`:

```tsx
"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useAccount } from "@/app/account-context";
import { OrdersTable } from "@/components/OrdersTable";
import { api, ApiError } from "@/lib/api";

const FILTERS = ["all", "pending", "filled", "cancelled", "rejected", "expired"] as const;
type Filter = (typeof FILTERS)[number];

export default function OrdersPage() {
  const { accountId } = useAccount();
  const qc = useQueryClient();
  const [filter, setFilter] = useState<Filter>("all");
  const [cancelError, setCancelError] = useState<string | null>(null);

  const orders = useQuery({
    queryKey: ["orders", accountId, filter],
    queryFn: () => api.orders(accountId!, filter === "all" ? undefined : filter),
    enabled: accountId !== null,
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

Also modify `frontend/components/OrdersTable.tsx`: make the symbol cell a link so orders jump to the chart. Replace the symbol `<td>` with:

```tsx
            <td className="py-2 font-medium">
              <a href={`/trade?symbol=${o.symbol}`} className="text-gray-100 hover:underline">
                {o.symbol}
              </a>
            </td>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm test` — Expected: all pass.
Run: `npm run typecheck` — Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add app components tests
git commit -m "feat: orders page with status filters and cancellation"
```

---

### Task 9: Journal page

**Files:**
- Create: `frontend/app/journal/page.tsx`
- Test: `frontend/tests/journal.test.tsx`

**Interfaces:**
- Consumes: `api.journal`, `api.stats`, `api.saveNote`, `useAccount`, `formatUsd`/`formatDateTime`.
- Produces: `/journal` — stats cards (closed trades, win rate as percent, avg gain, avg loss); the trade log newest-first with per-trade realized P&L; a per-row note editor (Edit note → textarea → Save) that PUTs and refreshes the list.

- [ ] **Step 1: Write the failing tests**

`frontend/tests/journal.test.tsx`:

```tsx
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithClient } from "./utils";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: { ...actual.api, accounts: vi.fn(), journal: vi.fn(), stats: vi.fn(), saveNote: vi.fn() },
  };
});

import { AccountProvider } from "@/app/account-context";
import JournalPage from "@/app/journal/page";
import { api } from "@/lib/api";

const manual = { id: 1, name: "manual", kind: "manual" as const, cash: "1000", starting_cash: "1000" };

beforeEach(() => {
  vi.mocked(api.accounts).mockResolvedValue([manual]);
  vi.mocked(api.journal).mockResolvedValue([
    {
      order_id: 5, symbol: "SPY", side: "sell", qty: 5, price: "120",
      commission: "0", realized_pnl: "100", filled_at: "2026-07-02T15:30:00",
      note: "took profits into strength",
    },
    {
      order_id: 4, symbol: "SPY", side: "buy", qty: 10, price: "100",
      commission: "0", realized_pnl: null, filled_at: "2026-07-01T15:30:00",
      note: null,
    },
  ]);
  vi.mocked(api.stats).mockResolvedValue({
    closed_trades: 1, wins: 1, win_rate: 1.0, avg_gain: "100", avg_loss: null,
  });
});

it("shows stats and the trade log with notes", async () => {
  renderWithClient(
    <AccountProvider>
      <JournalPage />
    </AccountProvider>,
  );
  expect(await screen.findByText("100%")).toBeInTheDocument(); // win rate
  expect(screen.getByText("took profits into strength")).toBeInTheDocument();
  expect(screen.getByText("+$100.00")).toBeInTheDocument(); // realized on the sell
});

it("saves an edited note", async () => {
  vi.mocked(api.saveNote).mockResolvedValue({ ok: true });
  renderWithClient(
    <AccountProvider>
      <JournalPage />
    </AccountProvider>,
  );
  const editButtons = await screen.findAllByRole("button", { name: /edit note|add note/i });
  await userEvent.click(editButtons[editButtons.length - 1]); // the buy row (no note yet)
  await userEvent.type(screen.getByRole("textbox"), "breakout entry");
  await userEvent.click(screen.getByRole("button", { name: /save/i }));
  await waitFor(() => expect(api.saveNote).toHaveBeenCalledWith(4, "breakout entry"));
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm test`
Expected: FAIL — cannot resolve `@/app/journal/page`.

- [ ] **Step 3: Implement**

`frontend/app/journal/page.tsx`:

```tsx
"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useAccount } from "@/app/account-context";
import { StatCard } from "@/components/StatCard";
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { formatUsd, isNeg } from "@/lib/money";

export default function JournalPage() {
  const { accountId } = useAccount();
  const qc = useQueryClient();
  const [editing, setEditing] = useState<number | null>(null);
  const [text, setText] = useState("");

  const trades = useQuery({
    queryKey: ["journal", accountId],
    queryFn: () => api.journal(accountId!),
    enabled: accountId !== null,
  });
  const stats = useQuery({
    queryKey: ["stats", accountId],
    queryFn: () => api.stats(accountId!),
    enabled: accountId !== null,
  });

  const save = useMutation({
    mutationFn: ({ id, note }: { id: number; note: string }) => api.saveNote(id, note),
    onSuccess: () => {
      setEditing(null);
      void qc.invalidateQueries({ queryKey: ["journal", accountId] });
    },
  });

  const s = stats.data;

  return (
    <div className="space-y-6">
      {s && (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <StatCard label="Closed trades" value={String(s.closed_trades)} />
          <StatCard
            label="Win rate"
            value={s.win_rate === null ? "—" : `${Math.round(s.win_rate * 100)}%`}
          />
          <StatCard label="Avg gain" value={s.avg_gain ? formatUsd(s.avg_gain) : "—"} tone="pos" />
          <StatCard label="Avg loss" value={s.avg_loss ? formatUsd(s.avg_loss) : "—"} tone="neg" />
        </div>
      )}

      <div className="space-y-2">
        {(trades.data ?? []).map((t) => (
          <div key={`${t.order_id}-${t.filled_at}`}
            className="rounded-lg border border-gray-800 bg-gray-900 p-3">
            <div className="flex flex-wrap items-baseline gap-3 text-sm">
              <span className="text-gray-500">{formatDateTime(t.filled_at)}</span>
              <span className={t.side === "buy" ? "text-emerald-400" : "text-red-400"}>
                {t.side}
              </span>
              <span className="font-medium text-gray-100">
                {t.qty} {t.symbol} @ {formatUsd(t.price)}
              </span>
              {t.realized_pnl !== null && (
                <span className={isNeg(t.realized_pnl) ? "text-red-400" : "text-emerald-400"}>
                  {isNeg(t.realized_pnl) ? "" : "+"}
                  {formatUsd(t.realized_pnl)}
                </span>
              )}
            </div>
            {editing === t.order_id ? (
              <div className="mt-2 space-y-2">
                <textarea
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  rows={3}
                  className="w-full rounded border border-gray-700 bg-gray-950 p-2 text-sm text-gray-100 outline-none focus:border-gray-500"
                />
                <div className="flex gap-2">
                  <button
                    onClick={() => save.mutate({ id: t.order_id, note: text })}
                    disabled={save.isPending}
                    className="rounded bg-emerald-700 px-3 py-1 text-sm text-white hover:bg-emerald-600 disabled:opacity-50"
                  >
                    Save
                  </button>
                  <button
                    onClick={() => setEditing(null)}
                    className="rounded px-3 py-1 text-sm text-gray-400 hover:text-gray-200"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            ) : (
              <div className="mt-1 flex items-baseline gap-3">
                {t.note && <p className="text-sm text-gray-400">{t.note}</p>}
                <button
                  onClick={() => {
                    setEditing(t.order_id);
                    setText(t.note ?? "");
                  }}
                  className="text-xs text-gray-500 hover:text-gray-300"
                >
                  {t.note ? "Edit note" : "Add note"}
                </button>
              </div>
            )}
          </div>
        ))}
        {trades.data?.length === 0 && (
          <p className="text-sm text-gray-500">No trades yet.</p>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm test` — Expected: all pass.
Run: `npm run typecheck` — Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add app tests
git commit -m "feat: journal page with trade log, notes, and stats"
```

---

### Task 10: Strategies page

**Files:**
- Create: `frontend/app/strategies/page.tsx`
- Test: `frontend/tests/strategies.test.tsx`

**Interfaces:**
- Consumes: `api.strategies`/`api.toggleStrategy`/`api.runs`/`api.snapshots`, `EquityCurve` (Task 4), `formatDateTime`.
- Produces: `/strategies` — one card per strategy: name, schedule, enabled toggle (mutation → refetch), its account's equity curve, and the 10 most recent runs (status-colored, error detail shown for failed runs).

- [ ] **Step 1: Write the failing tests**

`frontend/tests/strategies.test.tsx`:

```tsx
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
      strategies: vi.fn(), toggleStrategy: vi.fn(), runs: vi.fn(), snapshots: vi.fn(),
    },
  };
});

import { api } from "@/lib/api";
import StrategiesPage from "@/app/strategies/page";

const sma = { name: "SmaCross", schedule: "daily_after_close", enabled: false, account_id: 2 };

beforeEach(() => {
  vi.mocked(api.strategies).mockResolvedValue([sma]);
  vi.mocked(api.snapshots).mockResolvedValue([]);
  vi.mocked(api.runs).mockResolvedValue([
    {
      id: 1, strategy_name: "SmaCross", started_at: "2026-07-02T20:05:00",
      finished_at: "2026-07-02T20:05:01", status: "error", detail: "RuntimeError: boom",
    },
  ]);
});

it("lists strategies with schedule and run errors", async () => {
  renderWithClient(<StrategiesPage />);
  expect(await screen.findByText("SmaCross")).toBeInTheDocument();
  expect(screen.getByText("daily_after_close")).toBeInTheDocument();
  expect(await screen.findByText(/RuntimeError: boom/)).toBeInTheDocument();
});

it("toggles a strategy", async () => {
  vi.mocked(api.toggleStrategy).mockResolvedValue({ ...sma, enabled: true });
  renderWithClient(<StrategiesPage />);
  await userEvent.click(await screen.findByRole("switch"));
  await waitFor(() => expect(api.toggleStrategy).toHaveBeenCalledWith("SmaCross"));
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm test`
Expected: FAIL — cannot resolve `@/app/strategies/page`.

- [ ] **Step 3: Implement**

`frontend/app/strategies/page.tsx`:

```tsx
"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { EquityCurve } from "@/components/EquityCurve";
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { Strategy } from "@/lib/types";

function StrategyCard({ s }: { s: Strategy }) {
  const qc = useQueryClient();
  const toggle = useMutation({
    mutationFn: () => api.toggleStrategy(s.name),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["strategies"] }),
  });
  const runs = useQuery({
    queryKey: ["runs", s.name],
    queryFn: () => api.runs(s.name, 10),
  });
  const snaps = useQuery({
    queryKey: ["snapshots", s.account_id],
    queryFn: () => api.snapshots(s.account_id),
  });

  return (
    <div className="space-y-3 rounded-lg border border-gray-800 bg-gray-900 p-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="font-semibold text-gray-100">{s.name}</h2>
          <p className="text-xs text-gray-500">{s.schedule}</p>
        </div>
        <button
          role="switch"
          aria-checked={s.enabled}
          aria-label={`Toggle ${s.name}`}
          onClick={() => toggle.mutate()}
          disabled={toggle.isPending}
          className={`h-6 w-11 rounded-full p-0.5 transition-colors ${
            s.enabled ? "bg-emerald-600" : "bg-gray-700"
          }`}
        >
          <span
            className={`block h-5 w-5 rounded-full bg-white transition-transform ${
              s.enabled ? "translate-x-5" : ""
            }`}
          />
        </button>
      </div>

      {(snaps.data?.length ?? 0) > 0 && (
        <EquityCurve snapshots={snaps.data!} height={160} />
      )}

      <div>
        <h3 className="mb-1 text-xs font-semibold uppercase text-gray-500">Recent runs</h3>
        {(runs.data ?? []).length === 0 && (
          <p className="text-sm text-gray-500">No runs yet.</p>
        )}
        <ul className="space-y-1 text-sm">
          {(runs.data ?? []).map((r) => (
            <li key={r.id} className="flex flex-col">
              <span>
                <span className="text-gray-500">{formatDateTime(r.started_at)}</span>{" "}
                <span className={r.status === "ok" ? "text-emerald-400" : "text-red-400"}>
                  {r.status}
                </span>
              </span>
              {r.status === "error" ? (
                <pre className="mt-0.5 max-h-32 overflow-auto rounded bg-gray-950 p-2 text-xs text-red-300">
                  {r.detail}
                </pre>
              ) : (
                <span className="text-xs text-gray-500">{r.detail}</span>
              )}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

export default function StrategiesPage() {
  const strategies = useQuery({ queryKey: ["strategies"], queryFn: api.strategies });

  if (strategies.data?.length === 0) {
    return (
      <p className="text-sm text-gray-500">
        No strategies found. Drop a Python file in backend/strategies/ and restart the backend.
      </p>
    );
  }
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      {(strategies.data ?? []).map((s) => (
        <StrategyCard key={s.name} s={s} />
      ))}
    </div>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm test` — Expected: all pass.
Run: `npm run typecheck` — Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add app tests
git commit -m "feat: strategies page with toggles, equity curves, and run logs"
```

---

### Task 11: Docker Compose deployment + README

**Files:**
- Create: `backend/Dockerfile`, `backend/.dockerignore`, `frontend/Dockerfile`, `frontend/.dockerignore`, `compose.yaml` (repo root)
- Modify: `README.md` (repo root)

**Interfaces:**
- Consumes: everything (both apps must build).
- Produces: `docker compose up` serving the whole platform on port 3000 — the Next server proxies `/api/*` to the backend container (`BACKEND_URL=http://backend:8000`); the backend is NOT exposed on the host; the SQLite DB lives in a named volume (`db-data`, mounted at `/data`). Runs the same on a laptop or a $5/mo VPS.

- [ ] **Step 1: Write the Docker files**

`backend/Dockerfile`:

```dockerfile
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY app ./app
COPY strategies ./strategies
ENV PT_DB_PATH=/data/paper_trading.db
EXPOSE 8000
CMD ["uv", "run", "--no-sync", "uvicorn", "--factory", "app.main:create_app", "--host", "0.0.0.0", "--port", "8000"]
```

`backend/.dockerignore`:

```
.venv
__pycache__
*.db
*.db-wal
*.db-shm
.env
.pytest_cache
```

`frontend/Dockerfile` — **important:** Next.js standalone output serializes `next.config` at BUILD time, so the rewrite destination cannot be changed by a runtime env var. `BACKEND_URL` must be baked in as a build ARG:

```dockerfile
FROM node:22-alpine AS build
WORKDIR /app
ARG BACKEND_URL=http://localhost:8000
ENV BACKEND_URL=$BACKEND_URL
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
EXPOSE 3000
CMD ["node", "server.js"]
```

`frontend/.dockerignore`:

```
node_modules
.next
```

`compose.yaml` (repo root):

```yaml
services:
  backend:
    build: ./backend
    env_file: backend/.env
    environment:
      PT_DB_PATH: /data/paper_trading.db  # wins over any .env value; keeps the DB on the volume
    volumes:
      - db-data:/data
    restart: unless-stopped

  frontend:
    build:
      context: ./frontend
      args:
        BACKEND_URL: http://backend:8000  # baked at build time (standalone serializes next.config)
    ports:
      - "3000:3000"
    depends_on:
      - backend
    restart: unless-stopped

volumes:
  db-data:
```

- [ ] **Step 2: Update the README**

Replace the "Backend quickstart" heading's sibling structure in `README.md` by appending this section after the existing "Strategies" section:

```markdown
## Run everything with Docker

    cp backend/.env.example backend/.env   # then edit PT_PASSWORD and PT_SECRET_KEY
    docker compose up --build -d

Open http://localhost:3000 and log in with PT_PASSWORD. The backend is not
exposed on the host; the UI proxies /api/* to it inside the compose network.
The SQLite database persists in the db-data volume (back it up with
`docker compose cp backend:/data/paper_trading.db ./backup.db`).

On a VPS: install Docker, clone the repo, same two commands, then put the
box behind your firewall of choice with only port 3000 (or a reverse proxy
with TLS) reachable.

## Dev mode (hot reload)

    cd backend && uv run uvicorn --factory app.main:create_app --port 8000
    cd frontend && npm install && npm run dev   # http://localhost:3000

The Next dev server proxies /api/* to http://localhost:8000 (override with
BACKEND_URL).
```

- [ ] **Step 3: Build and boot the stack**

Run: `docker compose build`
Expected: both images build successfully. (If Docker is not installed on this machine, report DONE_WITH_CONCERNS noting compose could not be exercised, and verify `npm run build` in `frontend/` instead — but attempt Docker first.)

Run: `cp backend/.env.example backend/.env` (if backend/.env absent), then `docker compose up -d`, wait ~5s, then:

```bash
curl -s localhost:3000/api/health
```

Expected: `{"ok":true}` — proving the frontend container proxies to the backend container.

Run: `curl -s -X POST localhost:3000/api/login -H 'Content-Type: application/json' -d '{"password":"pick-a-password"}'`
Expected: `{"ok":true}` (password matches the .env.example default if unedited).

Run: `docker compose down`
Expected: clean shutdown.

- [ ] **Step 4: Run the frontend gates one final time**

Run (from `frontend/`): `npm test && npm run typecheck && npm run build`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add backend/Dockerfile backend/.dockerignore frontend/Dockerfile frontend/.dockerignore compose.yaml README.md
git commit -m "feat: Docker Compose deployment for backend and frontend"
```

---

## Verification sweep (after all tasks)

- `cd frontend && npm test && npm run typecheck && npm run build` — all green.
- Dev-mode end-to-end: backend via `uv run uvicorn --factory app.main:create_app --port 8000`, frontend via `npm run dev`; log in at :3000, place a 1-share SPY order from the Trade page, see it on Dashboard/Orders, add a journal note, toggle SmaCross on the Strategies page.
- `docker compose up --build` end-to-end once.
- Spec coverage check: Dashboard (equity/cash/P&L/curve/positions/open orders/switcher) ✓ Trade (search/chart/ticket/cost preview/quote age) ✓ Orders (filter/cancel; order lifecycle + reject reasons — per-fill prices live on the Journal page, since the backend's Order response carries no fill price) ✓ Journal (log with fill prices/notes/stats) ✓ Strategies (toggle/schedule/curve/run log) ✓ single-password auth ✓ Docker Compose $0-5/mo deployment ✓.



