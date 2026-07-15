# Bar Replay Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Replay section — sessions list/create, and a per-session workbench with chart, step controls, order ticket, per-account tabs, and a manual-vs-strategy equity comparison — over the merged `/api/replay` backend.

**Architecture:** A third nav mode (Paper | Live | Replay) mirroring the Phase-3 pattern; `/replay` lists/creates sessions; `/replay/[id]` is the workbench: session detail gates the page, stepping is a mutation that invalidates every session-scoped query, the chart/quote/ticket are the existing components fed from replay endpoints (bars ≤ cursor, virtual-now staleness), and a `ReplayAccounts` panel reuses `PositionsTable`/`OrdersView`/`EquityCurve` per session account. Spec: `docs/superpowers/specs/2026-07-15-bar-replay-design.md` (Frontend section).

**Tech Stack:** Next.js 15 App Router, TypeScript strict, TanStack Query v5, Tailwind v4, Vitest + Testing Library.

## Global Constraints

- Money/qty cross the API as strings; arithmetic via `lib/money.ts` BigInt helpers (`subMoney`, `isNeg`) — never parseFloat on money.
- Exact copy (verbatim, tested):
  - Ticket note: `Market orders fill at the next bar's open · day = one bar`
  - Exhausted banner: `Session exhausted — no more bars`
  - Not-found state: `Replay session not found.`
  - Nav mode tab labels: `Paper` / `Live` / `Replay`; replay section link label `Sessions`.
- Bars are fetched with `limit=1000` (crypto sessions preload up to 730 bars; the endpoint default of 520 would silently truncate).
- Account identity in the UI comes from the session detail's `role` field (`"manual"` or the strategy name) — never from `Account.kind`.
- `QuoteBadge` gets `now` = the session's virtual now (`cursor_date` at 21:00 UTC) so staleness reads in replay time. (In mixed stock+crypto sessions, a stock's quote after a crypto-only weekend step legitimately reads "1d ago · stale" — its `as_of` is its last bar. That is intended, not a bug.)
- The journal page's all-accounts fan-out excludes `mode === "replay"` accounts; BOTH paper-account filters (`components/AccountSwitcher.tsx` and `app/account-context.tsx`) tighten from `mode !== "live"` to `mode === "paper"` — replay accounts must never appear in the paper switcher nor be adopted from a stale localStorage selection.
- Strategy checkboxes on the create form list names only, all unchecked; the global `enabled` flag is ignored and not displayed.
- Step buttons: `+1`, `+5`, `+20` (steps query param), disabled while stepping or exhausted.
- Every task ends green: `cd frontend && npm test` (65 passing at branch start) and `npm run typecheck`; the final task also runs `npm run build`.

## File Structure

| File | Responsibility |
|---|---|
| `lib/types.ts` | + `"replay"` union members; replay session/step types |
| `lib/api.ts` | + 7 replay endpoints |
| `components/NavBar.tsx` | third mode + switcher hiding |
| `app/journal/page.tsx` | fan-out excludes replay accounts |
| `app/replay/page.tsx` (new) | sessions list + create form + delete |
| `app/replay/[id]/page.tsx` (new) | workbench: gate, header/step controls, chart, quote, ticket |
| `components/ReplayAccounts.tsx` (new) | account tabs (positions/orders/trades) + equity comparison + curve |

---

### Task 1: Types and API client

**Files:**
- Modify: `frontend/lib/types.ts`, `frontend/lib/api.ts`
- Test: `frontend/tests/api.test.ts` (append)

**Interfaces:**
- Consumes: backend `/api/replay` contract (merged PR #7).
- Produces (all later tasks): `Account.mode`/`Trade.account_mode` unions gain `"replay"`; types `ReplaySession`, `ReplayAccount`, `ReplayCoverage`, `ReplaySessionDetail`, `StepFill`, `StepResult`, `CreateReplaySessionBody`; api methods `createReplaySession(body)`, `replaySessions()`, `replaySession(id)`, `stepReplay(id, steps=1)`, `deleteReplaySession(id)`, `replayBars(id, symbol, limit=1000)`, `replayQuote(id, symbol)`.

- [ ] **Step 1: Write the failing tests**

Append to `frontend/tests/api.test.ts`:

```ts
it("steps a replay session with the steps param", async () => {
  const fetchMock = vi.fn(async () => jsonResponse({
    cursor_date: "2024-06-04", fills: [], expired: [],
    cancelled_at_exhaustion: [], strategy_errors: {}, exhausted: false,
  }));
  vi.stubGlobal("fetch", fetchMock);
  await api.stepReplay(3, 5);
  const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
  expect(url).toBe("/api/replay/sessions/3/step?steps=5");
  expect(init.method).toBe("POST");
});

it("fetches replay bars with limit=1000 by default", async () => {
  const fetchMock = vi.fn(async () => jsonResponse([]));
  vi.stubGlobal("fetch", fetchMock);
  await api.replayBars(3, "BTC-USD");
  const [url] = fetchMock.mock.calls[0] as unknown as [string];
  expect(url).toBe("/api/replay/sessions/3/bars/BTC-USD?limit=1000");
});

it("creates a replay session with a JSON body", async () => {
  const fetchMock = vi.fn(async () => jsonResponse({ id: 1 }));
  vi.stubGlobal("fetch", fetchMock);
  await api.createReplaySession({
    symbols: ["SPY"], start_date: "2024-06-03", strategies: ["SmaCross"],
  });
  const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
  expect(url).toBe("/api/replay/sessions");
  expect(JSON.parse(init.body as string)).toEqual({
    symbols: ["SPY"], start_date: "2024-06-03", strategies: ["SmaCross"],
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run tests/api.test.ts`
Expected: 6 existing pass; 3 new FAIL (`api.stepReplay is not a function`, …).

- [ ] **Step 3: Widen the types**

In `frontend/lib/types.ts`, change the two unions:

```ts
  mode: "paper" | "live" | "replay";
```
(in `Account`) and
```ts
  account_mode: "paper" | "live" | "replay";
```
(in `Trade`), then append at the end of the file:

```ts
export interface ReplaySession {
  id: number;
  name: string;
  symbols: string[];
  start_date: string; // YYYY-MM-DD
  cursor_date: string;
  end_date: string;
  exhausted: boolean;
  created_at: string;
}

export interface ReplayAccount {
  id: number;
  name: string;
  role: string; // "manual" or the strategy name
}

export interface ReplayCoverage {
  symbol: string;
  first_date: string;
  last_date: string;
}

export interface ReplaySessionDetail extends ReplaySession {
  accounts: ReplayAccount[];
  coverage: ReplayCoverage[];
}

export interface StepFill {
  order_id: number;
  symbol: string;
  side: "buy" | "sell";
  qty: string;
  price: string;
}

export interface StepResult {
  cursor_date: string;
  fills: StepFill[];
  expired: number[];
  cancelled_at_exhaustion: number[];
  strategy_errors: Record<string, string>;
  exhausted: boolean;
}

export interface CreateReplaySessionBody {
  symbols: string[];
  start_date: string;
  strategies: string[];
  starting_cash?: string;
  name?: string;
}
```

- [ ] **Step 4: Add the API methods**

In `frontend/lib/api.ts`, extend the type import with `CreateReplaySessionBody, ReplaySession, ReplaySessionDetail, StepResult`, and append inside the `api` object:

```ts
  createReplaySession: (body: CreateReplaySessionBody) =>
    request<ReplaySessionDetail>("/api/replay/sessions", post(body)),
  replaySessions: () => request<ReplaySession[]>("/api/replay/sessions"),
  replaySession: (id: number) =>
    request<ReplaySessionDetail>(`/api/replay/sessions/${id}`),
  stepReplay: (id: number, steps = 1) =>
    request<StepResult>(`/api/replay/sessions/${id}/step?steps=${steps}`, {
      method: "POST",
    }),
  deleteReplaySession: (id: number) =>
    request<{ ok: boolean }>(`/api/replay/sessions/${id}`, { method: "DELETE" }),
  replayBars: (id: number, symbol: string, limit = 1000) =>
    request<Bar[]>(
      `/api/replay/sessions/${id}/bars/${encodeURIComponent(symbol)}?limit=${limit}`),
  replayQuote: (id: number, symbol: string) =>
    request<Quote>(
      `/api/replay/sessions/${id}/quote/${encodeURIComponent(symbol)}`),
```

- [ ] **Step 5: Run tests and typecheck**

Run: `cd frontend && npx vitest run tests/api.test.ts && npm run typecheck`
Expected: 9 passed; typecheck clean (union widening is non-breaking — no fixture builds a replay-mode literal yet).

- [ ] **Step 6: Run the full suite**

Run: `cd frontend && npm test`
Expected: all pass (68).

- [ ] **Step 7: Commit**

```bash
git add frontend/lib/types.ts frontend/lib/api.ts frontend/tests/api.test.ts
git commit -m "feat: replay types and API client"
```

---

### Task 2: NavBar Replay mode and replay fencing

**Files:**
- Modify: `frontend/components/NavBar.tsx`, `frontend/app/journal/page.tsx`, `frontend/components/AccountSwitcher.tsx`, `frontend/app/account-context.tsx`
- Test: `frontend/tests/navbar.test.tsx` (append), `frontend/tests/journal.test.tsx` (append), `frontend/tests/account-switcher.test.tsx` (append)

**Interfaces:**
- Consumes: `Account.mode` union (Task 1).
- Produces: nav mode `Replay` at `/replay` (link label `Sessions`); account switcher hidden in BOTH live and replay modes; journal fan-out skips replay accounts; both paper-account filters tightened to `mode === "paper"` (they currently read `mode !== "live"`, which would let replay accounts into the paper switcher and default selection). Tasks 3–5 build the pages the nav points at.

- [ ] **Step 1: Write the failing tests**

Append to `frontend/tests/navbar.test.tsx`:

```tsx
it("shows the replay section links and hides the switcher there", async () => {
  vi.mocked(api.accounts).mockResolvedValue([manual]);
  pathname = "/replay/3";
  renderNav();
  expect(screen.getByRole("link", { name: "Replay" })).toHaveAttribute("href", "/replay");
  expect(screen.getByRole("link", { name: "Sessions" })).toHaveAttribute("href", "/replay");
  expect(screen.queryByRole("link", { name: "Strategies" })).not.toBeInTheDocument();
  expect(screen.queryByText("LIVE")).not.toBeInTheDocument();
  expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
});
```

Append to `frontend/tests/journal.test.tsx`:

```tsx
it("excludes replay accounts from the journal fan-out", async () => {
  const replayAcct = {
    id: 42, name: "replay:3:manual", kind: "manual" as const,
    mode: "replay" as const, cash: "100000", starting_cash: "100000",
    last_synced_at: null, sync_detail: null,
  };
  vi.mocked(api.accounts).mockResolvedValue([manual, replayAcct]);
  renderWithClient(
    <AccountProvider>
      <JournalPage />
    </AccountProvider>,
  );
  await screen.findByText("took profits into strength");
  expect(vi.mocked(api.journal)).toHaveBeenCalledWith(1);
  expect(vi.mocked(api.journal)).not.toHaveBeenCalledWith(42);
});
```

Append to `frontend/tests/account-switcher.test.tsx` (its `manual`/`live` fixtures, `ShowAccount` helper, and localStorage-clearing `beforeEach` already exist):

```tsx
it("excludes replay accounts from the switcher and stale selections", async () => {
  const replayAcct = {
    id: 40, name: "replay:3:manual", kind: "manual" as const,
    mode: "replay" as const, cash: "100000", starting_cash: "100000",
    last_synced_at: null, sync_detail: null,
  };
  localStorage.setItem("pt-account", "40"); // stale selection of a replay account
  vi.mocked(api.accounts).mockResolvedValue([manual, live, replayAcct]);
  renderWithClient(
    <AccountProvider>
      <AccountSwitcher />
      <ShowAccount />
    </AccountProvider>,
  );
  expect(await screen.findByRole("option", { name: "manual" })).toBeInTheDocument();
  expect(
    screen.queryByRole("option", { name: "replay:3:manual" }),
  ).not.toBeInTheDocument();
  await waitFor(() => expect(screen.getByTestId("selected")).toHaveTextContent("1"));
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run tests/navbar.test.tsx tests/journal.test.tsx tests/account-switcher.test.tsx`
Expected: existing pass; the 3 new FAIL (no Replay link; journal called with 42; replay account listed in the switcher / adopted as selection).

- [ ] **Step 3: Implement NavBar**

In `frontend/components/NavBar.tsx`:

Add after `liveLinks`:

```tsx
const replayLinks = [{ href: "/replay", label: "Sessions" }];
```

In the component body, replace the mode detection and link selection:

```tsx
  const pathname = usePathname();
  const live = pathname === "/live" || pathname.startsWith("/live/");
  const replay = pathname === "/replay" || pathname.startsWith("/replay/");
  const links = live ? liveLinks : replay ? replayLinks : paperLinks;
```

In the mode-tab strip, add a third tab after the `Live` link:

```tsx
          <Link href="/replay" className={modeTab(replay)}>
            Replay
          </Link>
```

and the `Paper` tab's active flag becomes `modeTab(!live && !replay)`.

The switcher line becomes:

```tsx
          {!live && !replay && <AccountSwitcher />}
```

- [ ] **Step 4: Implement the journal exclusion**

In `frontend/app/journal/page.tsx`, the `useQueries` block changes its source list:

```tsx
  const journals = useQueries({
    queries: (accounts.data ?? [])
      .filter((a) => a.mode !== "replay")
      .map((a) => ({
        queryKey: ["journal", a.id],
        queryFn: () => api.journal(a.id),
      })),
  });
```

- [ ] **Step 5: Tighten both paper-account filters**

In `frontend/components/AccountSwitcher.tsx`, the filter line becomes:

```tsx
  const paper = accounts?.filter((a) => a.mode === "paper") ?? [];
```

In `frontend/app/account-context.tsx`, the filter line inside the `useEffect` becomes:

```tsx
      const paper = accounts.filter((a) => a.mode === "paper");
```

- [ ] **Step 6: Run tests, full suite, typecheck**

Run: `cd frontend && npx vitest run tests/navbar.test.tsx tests/journal.test.tsx tests/account-switcher.test.tsx && npm test && npm run typecheck`
Expected: navbar 4 passed, journal 5 passed, account-switcher 3 passed; full suite 71; typecheck clean.

- [ ] **Step 7: Commit**

```bash
git add frontend/components/NavBar.tsx frontend/app/journal/page.tsx frontend/components/AccountSwitcher.tsx frontend/app/account-context.tsx frontend/tests/navbar.test.tsx frontend/tests/journal.test.tsx frontend/tests/account-switcher.test.tsx
git commit -m "feat: Replay nav mode and paper/journal fencing for replay accounts"
```

---

### Task 3: Sessions list and create page

**Files:**
- Create: `frontend/app/replay/page.tsx`
- Test: `frontend/tests/replay-sessions.test.tsx` (new)

**Interfaces:**
- Consumes: `api.replaySessions/createReplaySession/deleteReplaySession/strategies` (Task 1); nav route `/replay` (Task 2).
- Produces: the sessions page linking each session to `/replay/{id}` (Task 4's route).

- [ ] **Step 1: Write the failing tests**

Create `frontend/tests/replay-sessions.test.tsx`:

```tsx
import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithClient } from "./utils";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: {
      ...actual.api,
      replaySessions: vi.fn(), createReplaySession: vi.fn(),
      deleteReplaySession: vi.fn(), strategies: vi.fn(),
    },
  };
});

import ReplaySessionsPage from "@/app/replay/page";
import { api, ApiError } from "@/lib/api";
import type { ReplaySession } from "@/lib/types";

const session: ReplaySession = {
  id: 3, name: "SPY from 2024-06-03", symbols: ["SPY"],
  start_date: "2024-06-03", cursor_date: "2024-06-10", end_date: "2024-06-28",
  exhausted: false, created_at: "2026-07-16T10:00:00",
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.replaySessions).mockResolvedValue([session]);
  vi.mocked(api.strategies).mockResolvedValue([
    { name: "SmaCross", schedule: "daily_after_close", enabled: true, account_id: 2 },
  ]);
});

it("lists sessions with a link to the workbench", async () => {
  renderWithClient(<ReplaySessionsPage />);
  expect(await screen.findByText("SPY from 2024-06-03")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /open/i })).toHaveAttribute("href", "/replay/3");
});

it("creates a session from the form with unchecked strategies by default", async () => {
  vi.mocked(api.createReplaySession).mockResolvedValue({
    ...session, id: 4, accounts: [], coverage: [],
  });
  renderWithClient(<ReplaySessionsPage />);
  await screen.findByText("SPY from 2024-06-03");
  const checkbox = await screen.findByRole("checkbox", { name: "SmaCross" });
  expect(checkbox).not.toBeChecked(); // global enabled flag ignored
  await userEvent.click(checkbox);
  await userEvent.type(screen.getByLabelText(/symbols/i), "SPY, BTC-USD");
  // userEvent.type is unreliable for type="date" inputs in jsdom
  fireEvent.change(screen.getByLabelText(/start date/i),
    { target: { value: "2024-06-03" } });
  await userEvent.click(screen.getByRole("button", { name: /create session/i }));
  await waitFor(() => expect(api.createReplaySession).toHaveBeenCalled());
  const [body] = vi.mocked(api.createReplaySession).mock.calls[0];
  expect(body).toMatchObject({
    symbols: ["SPY", "BTC-USD"], start_date: "2024-06-03",
    strategies: ["SmaCross"], starting_cash: "100000",
  });
});

it("shows the backend's coverage error on creation failure", async () => {
  vi.mocked(api.createReplaySession).mockRejectedValue(
    new ApiError(400, "insufficient coverage at start date: SPY history starts 2024-08-01 (through 2026-07-15)"),
  );
  renderWithClient(<ReplaySessionsPage />);
  await screen.findByText("SPY from 2024-06-03");
  await userEvent.type(screen.getByLabelText(/symbols/i), "SPY");
  fireEvent.change(screen.getByLabelText(/start date/i),
    { target: { value: "2024-06-03" } });
  await userEvent.click(screen.getByRole("button", { name: /create session/i }));
  expect(await screen.findByText(/SPY history starts 2024-08-01/)).toBeInTheDocument();
});

it("deletes a session after inline confirmation", async () => {
  vi.mocked(api.deleteReplaySession).mockResolvedValue({ ok: true });
  renderWithClient(<ReplaySessionsPage />);
  await screen.findByText("SPY from 2024-06-03");
  await userEvent.click(screen.getByRole("button", { name: /^delete$/i }));
  expect(api.deleteReplaySession).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: /confirm delete/i }));
  await waitFor(() => expect(api.deleteReplaySession).toHaveBeenCalledWith(3));
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run tests/replay-sessions.test.tsx`
Expected: FAIL — `Cannot find module '@/app/replay/page'`.

- [ ] **Step 3: Implement the page**

Create `frontend/app/replay/page.tsx`:

```tsx
"use client";

import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, ApiError } from "@/lib/api";

const inputCls =
  "w-full rounded border border-gray-700 bg-gray-950 px-3 py-1.5 text-sm text-gray-100 outline-none focus:border-gray-500";

export default function ReplaySessionsPage() {
  const qc = useQueryClient();
  const sessions = useQuery({ queryKey: ["replay-sessions"], queryFn: api.replaySessions });
  const strategies = useQuery({ queryKey: ["strategies"], queryFn: api.strategies });

  const [name, setName] = useState("");
  const [symbols, setSymbols] = useState("");
  const [startDate, setStartDate] = useState("");
  const [startingCash, setStartingCash] = useState("100000");
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [confirmingDelete, setConfirmingDelete] = useState<number | null>(null);

  const create = useMutation({
    mutationFn: () =>
      api.createReplaySession({
        symbols: symbols.split(",").map((s) => s.trim()).filter(Boolean),
        start_date: startDate,
        strategies: [...checked],
        starting_cash: startingCash,
        ...(name.trim() ? { name: name.trim() } : {}),
      }),
    onSuccess: () => {
      setName("");
      setSymbols("");
      setChecked(new Set());
      void qc.invalidateQueries({ queryKey: ["replay-sessions"] });
      void qc.invalidateQueries({ queryKey: ["accounts"] });
    },
  });

  const remove = useMutation({
    mutationFn: (id: number) => api.deleteReplaySession(id),
    onSuccess: () => {
      setConfirmingDelete(null);
      void qc.invalidateQueries({ queryKey: ["replay-sessions"] });
      void qc.invalidateQueries({ queryKey: ["accounts"] });
    },
  });

  const toggle = (n: string) => {
    const next = new Set(checked);
    if (next.has(n)) next.delete(n);
    else next.add(n);
    setChecked(next);
  };

  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_360px]">
      <section className="space-y-2">
        <h2 className="text-sm font-semibold text-gray-400">Sessions</h2>
        {(sessions.data ?? []).map((s) => (
          <div key={s.id}
            className="flex flex-wrap items-center gap-3 rounded-lg border border-gray-800 bg-gray-900 p-3 text-sm">
            <span className="font-medium text-gray-100">{s.name}</span>
            <span className="text-gray-500">{s.symbols.join(", ")}</span>
            <span className="text-gray-500">
              {s.cursor_date} → {s.end_date}
            </span>
            {s.exhausted && (
              <span className="rounded bg-gray-800 px-1.5 py-0.5 text-[10px] uppercase text-gray-400">
                Exhausted
              </span>
            )}
            <span className="ml-auto flex items-center gap-2">
              <Link href={`/replay/${s.id}`}
                className="rounded border border-gray-700 px-3 py-1 text-gray-300 hover:border-gray-500">
                Open
              </Link>
              {confirmingDelete === s.id ? (
                <button onClick={() => remove.mutate(s.id)}
                  className="rounded bg-red-800 px-3 py-1 text-white hover:bg-red-700">
                  Confirm delete
                </button>
              ) : (
                <button onClick={() => setConfirmingDelete(s.id)}
                  className="rounded px-3 py-1 text-gray-500 hover:text-red-400">
                  Delete
                </button>
              )}
            </span>
          </div>
        ))}
        {sessions.data?.length === 0 && (
          <p className="text-sm text-gray-500">No replay sessions yet.</p>
        )}
      </section>

      <aside className="space-y-3 rounded-lg border border-gray-800 bg-gray-900 p-4">
        <h2 className="text-sm font-semibold text-gray-300">New session</h2>
        <label className="block text-xs text-gray-500" htmlFor="rname">Name (optional)</label>
        <input id="rname" value={name} onChange={(e) => setName(e.target.value)}
          className={inputCls} />
        <label className="block text-xs text-gray-500" htmlFor="rsymbols">
          Symbols (comma-separated)
        </label>
        <input id="rsymbols" value={symbols}
          onChange={(e) => setSymbols(e.target.value)} className={inputCls} />
        <label className="block text-xs text-gray-500" htmlFor="rstart">Start date</label>
        <input id="rstart" type="date" value={startDate}
          onChange={(e) => setStartDate(e.target.value)} className={inputCls} />
        <label className="block text-xs text-gray-500" htmlFor="rcash">Starting cash</label>
        <input id="rcash" inputMode="decimal" value={startingCash}
          onChange={(e) => setStartingCash(e.target.value.replace(/[^0-9.]/g, ""))}
          className={inputCls} />
        {(strategies.data ?? []).length > 0 && (
          <fieldset className="space-y-1">
            <legend className="text-xs text-gray-500">Strategies</legend>
            {(strategies.data ?? []).map((s) => (
              <label key={s.name} className="flex items-center gap-2 text-sm text-gray-300">
                <input type="checkbox" checked={checked.has(s.name)}
                  onChange={() => toggle(s.name)} aria-label={s.name} />
                {s.name}
              </label>
            ))}
          </fieldset>
        )}
        <button
          onClick={() => create.mutate()}
          disabled={create.isPending || !symbols.trim() || !startDate}
          className="w-full rounded bg-emerald-700 px-3 py-2 font-medium text-white hover:bg-emerald-600 disabled:opacity-50"
        >
          {create.isPending ? "Creating…" : "Create session"}
        </button>
        {create.error && (
          <p className="text-sm text-red-400">
            {create.error instanceof ApiError ? create.error.message : "Creation failed"}
          </p>
        )}
      </aside>
    </div>
  );
}
```

- [ ] **Step 4: Run tests, full suite, typecheck**

Run: `cd frontend && npx vitest run tests/replay-sessions.test.tsx && npm test && npm run typecheck`
Expected: 4 passed; full suite 75; typecheck clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/app/replay/page.tsx frontend/tests/replay-sessions.test.tsx
git commit -m "feat: replay sessions list and create form"
```

---

### Task 4: The workbench — gate, stepping, chart, quote, ticket

**Files:**
- Create: `frontend/app/replay/[id]/page.tsx`
- Test: `frontend/tests/replay-workbench.test.tsx` (new)

**Interfaces:**
- Consumes: Task 1 api/types; `CandleChart({ bars })`, `QuoteBadge({ quote, error, now })`, `OrderTicket({ symbol, quotePrice, accountId, live })` (existing).
- Produces: the `/replay/[id]` route. This task's page ends with the chart/ticket grid; Task 5 creates `ReplayAccounts` and appends `<ReplayAccounts accounts={detail.accounts} />` to this page (import and usage both land in Task 5).
- Intentional deviations from the spec's letter (do not "fix"): the gate is inline in the page (404/loading branches) rather than a separate `ReplayGate` component — one consumer, no context needed; and the equity curve (Task 5) shows the ACTIVE tab's account rather than only the manual account — a strict superset, with manual as the default tab.

- [ ] **Step 1: Write the failing tests**

Create `frontend/tests/replay-workbench.test.tsx`:

```tsx
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithClient } from "./utils";

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "3" }),
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
      snapshots: vi.fn(), journal: vi.fn(),
      replaySession: vi.fn(), stepReplay: vi.fn(),
      replayBars: vi.fn(), replayQuote: vi.fn(),
    },
  };
});

import { AccountProvider } from "@/app/account-context";
import ReplayWorkbenchPage from "@/app/replay/[id]/page";
import { api, ApiError } from "@/lib/api";
import type { ReplaySessionDetail } from "@/lib/types";

const detail: ReplaySessionDetail = {
  id: 3, name: "SPY from 2024-06-03", symbols: ["SPY", "BTC-USD"],
  start_date: "2024-06-03", cursor_date: "2024-06-10", end_date: "2024-06-28",
  exhausted: false, created_at: "2026-07-16T10:00:00",
  accounts: [
    { id: 40, name: "replay:3:manual", role: "manual" },
    { id: 41, name: "replay:3:strategy:SmaCross", role: "SmaCross" },
  ],
  coverage: [
    { symbol: "SPY", first_date: "2024-06-03", last_date: "2024-06-28" },
    { symbol: "BTC-USD", first_date: "2024-06-03", last_date: "2024-06-28" },
  ],
};

const manualDetail = {
  id: 40, name: "replay:3:manual", kind: "manual" as const,
  mode: "replay" as const, cash: "100000", starting_cash: "100000",
  last_synced_at: null, sync_detail: null, equity: "100000", positions: [],
};

beforeEach(() => {
  vi.clearAllMocks();
  // api.accounts is consumed by AccountProvider (OrderTicket's context);
  // it is NOT unused — do not remove.
  vi.mocked(api.accounts).mockResolvedValue([]);
  vi.mocked(api.replaySession).mockResolvedValue(detail);
  vi.mocked(api.replayBars).mockResolvedValue([
    { timestamp: "2024-06-03T00:00:00", open: "100", high: "101",
      low: "99", close: "100", volume: 1000 },
  ]);
  vi.mocked(api.replayQuote).mockResolvedValue({
    symbol: "SPY", price: "100", as_of: "2024-06-10T21:00:00",
  });
  vi.mocked(api.accountDetail).mockResolvedValue(manualDetail);
  vi.mocked(api.orders).mockResolvedValue([]);
  vi.mocked(api.snapshots).mockResolvedValue([]);
  vi.mocked(api.journal).mockResolvedValue([]);
});

function renderPage() {
  return renderWithClient(
    <AccountProvider>
      <ReplayWorkbenchPage />
    </AccountProvider>,
  );
}

it("renders the session header, chart data, and fill-semantics note", async () => {
  renderPage();
  expect(await screen.findByText("SPY from 2024-06-03")).toBeInTheDocument();
  expect(screen.getByText(/2024-06-10/)).toBeInTheDocument();
  expect(
    screen.getByText("Market orders fill at the next bar's open · day = one bar"),
  ).toBeInTheDocument();
  await waitFor(() => expect(api.replayBars).toHaveBeenCalledWith(3, "SPY"));
  await waitFor(() => expect(api.replayQuote).toHaveBeenCalledWith(3, "SPY"));
});

it("virtual-now staleness: the replay quote is not flagged stale", async () => {
  // "0s ago" is unique to QuoteBadge ("$100.00" would also match the
  // ticket's est-cost); zero age proves now = virtual cursor time.
  renderPage();
  await screen.findByText("SPY from 2024-06-03");
  expect(await screen.findByText("0s ago")).toBeInTheDocument();
  expect(screen.queryByText(/stale/)).not.toBeInTheDocument();
});

it("steps the session and shows fills and strategy errors", async () => {
  vi.mocked(api.stepReplay).mockResolvedValue({
    cursor_date: "2024-06-11",
    fills: [{ order_id: 9, symbol: "SPY", side: "buy", qty: "10", price: "104" }],
    expired: [7], cancelled_at_exhaustion: [],
    strategy_errors: { SmaCross: "unknown symbol: TSLA" },
    exhausted: false,
  });
  renderPage();
  await screen.findByText("SPY from 2024-06-03");
  await userEvent.click(screen.getByRole("button", { name: "+1" }));
  await waitFor(() => expect(api.stepReplay).toHaveBeenCalledWith(3, 1));
  expect(await screen.findByText(/buy 10 SPY @ \$104\.00/)).toBeInTheDocument();
  expect(screen.getByText(/1 expired/)).toBeInTheDocument();
  // The combined pattern stays unique once Task 5 adds account tabs that
  // also render the bare strategy name.
  expect(screen.getByText(/SmaCross: unknown symbol: TSLA/)).toBeInTheDocument();
});

it("multi-step buttons pass the steps param and disable when exhausted", async () => {
  vi.mocked(api.replaySession).mockResolvedValue({ ...detail, exhausted: true });
  renderPage();
  await screen.findByText("SPY from 2024-06-03");
  expect(screen.getByText("Session exhausted — no more bars")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "+5" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "+20" })).toBeDisabled();
});

it("switches the chart symbol", async () => {
  renderPage();
  await screen.findByText("SPY from 2024-06-03");
  await userEvent.click(screen.getByRole("button", { name: "BTC-USD" }));
  await waitFor(() => expect(api.replayBars).toHaveBeenCalledWith(3, "BTC-USD"));
});

it("shows the not-found state for a missing session", async () => {
  vi.mocked(api.replaySession).mockRejectedValue(
    new ApiError(404, "no such replay session"),
  );
  renderPage();
  expect(await screen.findByText("Replay session not found.")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run tests/replay-workbench.test.tsx`
Expected: FAIL — `Cannot find module '@/app/replay/[id]/page'`.

- [ ] **Step 3: Implement the workbench page**

Create `frontend/app/replay/[id]/page.tsx`:

```tsx
"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "next/navigation";
import { useState } from "react";
import { CandleChart } from "@/components/CandleChart";
import { OrderTicket } from "@/components/OrderTicket";
import { QuoteBadge } from "@/components/QuoteBadge";
import { api, ApiError } from "@/lib/api";
import { formatUsd } from "@/lib/money";
import { formatQty } from "@/lib/qty";
import type { StepResult } from "@/lib/types";

const stepBtn =
  "rounded border border-gray-700 px-3 py-1.5 text-sm text-gray-300 hover:border-gray-500 disabled:opacity-50";

export default function ReplayWorkbenchPage() {
  const params = useParams<{ id: string }>();
  const sessionId = Number(params.id);
  const qc = useQueryClient();
  const [symbol, setSymbol] = useState<string | null>(null);
  const [lastStep, setLastStep] = useState<StepResult | null>(null);

  const session = useQuery({
    queryKey: ["replay-session", sessionId],
    queryFn: () => api.replaySession(sessionId),
  });

  const detail = session.data;
  const activeSymbol = symbol ?? detail?.symbols[0];

  const bars = useQuery({
    queryKey: ["replay-bars", sessionId, activeSymbol],
    queryFn: () => api.replayBars(sessionId, activeSymbol!),
    enabled: activeSymbol !== undefined,
  });
  const quote = useQuery({
    queryKey: ["replay-quote", sessionId, activeSymbol],
    queryFn: () => api.replayQuote(sessionId, activeSymbol!),
    enabled: activeSymbol !== undefined,
  });

  const step = useMutation({
    mutationFn: (steps: number) => api.stepReplay(sessionId, steps),
    onSuccess: (result) => {
      setLastStep(result);
      void qc.invalidateQueries({ queryKey: ["replay-session", sessionId] });
      void qc.invalidateQueries({ queryKey: ["replay-bars", sessionId] });
      void qc.invalidateQueries({ queryKey: ["replay-quote", sessionId] });
      void qc.invalidateQueries({ queryKey: ["account"] });
      void qc.invalidateQueries({ queryKey: ["orders"] });
      void qc.invalidateQueries({ queryKey: ["journal"] });
      void qc.invalidateQueries({ queryKey: ["snapshots"] });
    },
  });

  if (session.error instanceof ApiError && session.error.status === 404) {
    return <p className="text-sm text-gray-500">Replay session not found.</p>;
  }
  if (!detail) return <p className="text-sm text-gray-500">Loading…</p>;

  const manual = detail.accounts.find((a) => a.role === "manual");
  const virtualNow = new Date(`${detail.cursor_date}T21:00:00Z`);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-4">
        <h1 className="font-semibold text-gray-100">{detail.name}</h1>
        <span className="text-sm text-gray-500">
          {detail.cursor_date} of {detail.end_date}
        </span>
        <span className="flex gap-2">
          {[1, 5, 20].map((n) => (
            <button key={n} onClick={() => step.mutate(n)}
              disabled={step.isPending || detail.exhausted} className={stepBtn}>
              +{n}
            </button>
          ))}
        </span>
        {detail.exhausted && (
          <span className="rounded border border-amber-800 bg-amber-950 px-2 py-1 text-sm text-amber-300">
            Session exhausted — no more bars
          </span>
        )}
      </div>

      {lastStep && (
        <div className="space-y-1 rounded-lg border border-gray-800 bg-gray-900 p-3 text-sm">
          {lastStep.fills.map((f) => (
            <p key={f.order_id} className="text-gray-300">
              {f.side} {formatQty(f.qty)} {f.symbol} @ {formatUsd(f.price)}
            </p>
          ))}
          {lastStep.fills.length === 0 && (
            <p className="text-gray-500">No fills this step.</p>
          )}
          {lastStep.expired.length > 0 && (
            <p className="text-gray-500">{lastStep.expired.length} expired</p>
          )}
          {lastStep.cancelled_at_exhaustion.length > 0 && (
            <p className="text-gray-500">
              {lastStep.cancelled_at_exhaustion.length} cancelled at exhaustion
            </p>
          )}
          {Object.entries(lastStep.strategy_errors).map(([name, err]) => (
            <p key={name} className="text-red-400">
              {/* tail, not head: the end of a traceback carries the message */}
              {name}: {err.slice(-200)}
            </p>
          ))}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-4">
        <span className="flex gap-1">
          {detail.symbols.map((s) => (
            <button key={s} onClick={() => setSymbol(s)}
              className={`rounded px-3 py-1.5 text-sm ${
                s === activeSymbol
                  ? "bg-gray-800 text-white"
                  : "text-gray-400 hover:text-gray-200"
              }`}>
              {s}
            </button>
          ))}
        </span>
        <QuoteBadge quote={quote.data} error={quote.error ?? undefined}
          now={virtualNow} />
      </div>

      <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
        <section className="rounded-lg border border-gray-800 bg-gray-900 p-2">
          {bars.data ? (
            <CandleChart bars={bars.data} />
          ) : (
            <div className="flex h-[420px] items-center justify-center text-sm text-gray-500">
              Loading chart…
            </div>
          )}
        </section>
        <aside className="space-y-2">
          {manual && (
            <OrderTicket symbol={activeSymbol!} quotePrice={quote.data?.price}
              accountId={manual.id} live={false} />
          )}
          <p className="text-xs text-gray-500">
            Market orders fill at the next bar's open · day = one bar
          </p>
          {detail.exhausted && (
            <p className="text-xs text-amber-400">
              Orders placed now can never fill.
            </p>
          )}
        </aside>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run tests, full suite, typecheck**

Run: `cd frontend && npx vitest run tests/replay-workbench.test.tsx && npm test && npm run typecheck`
Expected: 6 passed; full suite 81; typecheck clean.

- [ ] **Step 5: Commit**

```bash
git add "frontend/app/replay/[id]/page.tsx" frontend/tests/replay-workbench.test.tsx
git commit -m "feat: replay workbench with stepping, chart, and virtual-time ticket"
```

---

### Task 5: Account tabs and the equity comparison

**Files:**
- Create: `frontend/components/ReplayAccounts.tsx`
- Modify: `frontend/app/replay/[id]/page.tsx` (append the panel)
- Test: `frontend/tests/replay-accounts.test.tsx` (new)

**Interfaces:**
- Consumes: `ReplayAccount` (Task 1), `api.accountDetail/orders/journal/snapshots`, `PositionsTable({ positions })`, `OrdersView({ accountId })`, `EquityCurve({ snapshots })`, `formatUsd/subMoney/isNeg` from `lib/money`, `formatQty` from `lib/qty`, `formatDateTime` from `lib/format`.
- Produces: `ReplayAccounts({ accounts }: { accounts: ReplayAccount[] })` — the manual-vs-strategy comparison the feature exists for.

- [ ] **Step 1: Write the failing tests**

Create `frontend/tests/replay-accounts.test.tsx`:

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
      accountDetail: vi.fn(), orders: vi.fn(), journal: vi.fn(), snapshots: vi.fn(),
    },
  };
});

import { ReplayAccounts } from "@/components/ReplayAccounts";
import { api } from "@/lib/api";
import type { ReplayAccount } from "@/lib/types";

const accounts: ReplayAccount[] = [
  { id: 40, name: "replay:3:manual", role: "manual" },
  { id: 41, name: "replay:3:strategy:SmaCross", role: "SmaCross" },
];

function detailFor(id: number, equity: string) {
  return {
    id, name: `acct-${id}`, kind: "manual" as const, mode: "replay" as const,
    cash: equity, starting_cash: "100000", last_synced_at: null,
    sync_detail: null, equity, positions: [],
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.accountDetail).mockImplementation(async (id: number) =>
    detailFor(id, id === 40 ? "105000" : "98000"));
  vi.mocked(api.orders).mockResolvedValue([]);
  vi.mocked(api.journal).mockResolvedValue([]);
  vi.mocked(api.snapshots).mockResolvedValue([
    { date: "2024-06-04", equity: "100000", cash: "100000" },
  ]);
});

it("shows the equity comparison with signed P&L per account", async () => {
  // Role labels render twice (comparison table + tab strip): use roles for
  // presence, unique money strings for values.
  renderWithClient(<ReplayAccounts accounts={accounts} />);
  expect(await screen.findByRole("tab", { name: "Manual" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "SmaCross" })).toBeInTheDocument();
  expect(await screen.findByText("$105,000.00")).toBeInTheDocument();
  expect(screen.getByText("+$5,000.00")).toBeInTheDocument();
  expect(screen.getByText("$98,000.00")).toBeInTheDocument();
  expect(screen.getByText("-$2,000.00")).toBeInTheDocument();
});

it("switches account tabs and scopes orders/journal to the active account", async () => {
  renderWithClient(<ReplayAccounts accounts={accounts} />);
  await screen.findByRole("tab", { name: "Manual" });
  await waitFor(() => expect(api.orders).toHaveBeenCalledWith(40, undefined));
  await userEvent.click(screen.getByRole("tab", { name: "SmaCross" }));
  await waitFor(() => expect(api.orders).toHaveBeenCalledWith(41, undefined));
  await waitFor(() => expect(api.journal).toHaveBeenCalledWith(41));
});

it("renders the active account's trades", async () => {
  vi.mocked(api.journal).mockResolvedValue([
    { order_id: 9, symbol: "SPY", side: "buy", qty: "10", price: "104",
      commission: "0", realized_pnl: null, filled_at: "2024-06-11T21:00:00",
      note: null, account_mode: "replay" },
  ]);
  renderWithClient(<ReplayAccounts accounts={accounts} />);
  await screen.findByRole("tab", { name: "Manual" });
  expect(await screen.findByText(/10 SPY @ \$104\.00/)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run tests/replay-accounts.test.tsx`
Expected: FAIL — `Cannot find module '@/components/ReplayAccounts'`.

- [ ] **Step 3: Implement the component**

Create `frontend/components/ReplayAccounts.tsx`:

```tsx
"use client";

import { useQueries, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { EquityCurve } from "@/components/EquityCurve";
import { OrdersView } from "@/components/OrdersView";
import { PositionsTable } from "@/components/PositionsTable";
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { formatUsd, isNeg, subMoney } from "@/lib/money";
import { formatQty } from "@/lib/qty";
import type { ReplayAccount } from "@/lib/types";

function label(role: string): string {
  return role === "manual" ? "Manual" : role;
}

export function ReplayAccounts({ accounts }: { accounts: ReplayAccount[] }) {
  const [activeId, setActiveId] = useState(accounts[0]?.id ?? null);

  const details = useQueries({
    queries: accounts.map((a) => ({
      queryKey: ["account", a.id],
      queryFn: () => api.accountDetail(a.id),
    })),
  });
  const active = accounts.find((a) => a.id === activeId);
  const activeDetail = details.find((d) => d.data?.id === activeId)?.data;

  const trades = useQuery({
    queryKey: ["journal", activeId],
    queryFn: () => api.journal(activeId!),
    enabled: activeId !== null,
  });
  const snapshots = useQuery({
    queryKey: ["snapshots", activeId],
    queryFn: () => api.snapshots(activeId!),
    enabled: activeId !== null,
  });

  return (
    <div className="space-y-4">
      <section>
        <h2 className="mb-2 text-sm font-semibold text-gray-400">Performance</h2>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase text-gray-500">
              <th className="py-1">Account</th>
              <th className="py-1">Equity</th>
              <th className="py-1">P&L</th>
            </tr>
          </thead>
          <tbody>
            {accounts.map((a, i) => {
              const d = details[i]?.data;
              const pnl = d ? subMoney(d.equity, d.starting_cash) : null;
              return (
                <tr key={a.id} className="border-t border-gray-800">
                  <td className="py-1.5 text-gray-100">{label(a.role)}</td>
                  <td className="py-1.5 tabular-nums text-gray-100">
                    {d ? formatUsd(d.equity) : "—"}
                  </td>
                  <td className={`py-1.5 tabular-nums ${
                    pnl && isNeg(pnl) ? "text-red-400" : "text-emerald-400"
                  }`}>
                    {pnl ? `${isNeg(pnl) ? "" : "+"}${formatUsd(pnl)}` : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>

      <div className="flex gap-1" role="tablist" aria-label="Session accounts">
        {accounts.map((a) => (
          <button key={a.id} role="tab" aria-selected={a.id === activeId}
            onClick={() => setActiveId(a.id)}
            className={`rounded px-3 py-1.5 text-sm ${
              a.id === activeId
                ? "bg-gray-800 text-white"
                : "text-gray-400 hover:text-gray-200"
            }`}>
            {label(a.role)}
          </button>
        ))}
      </div>

      {active && activeDetail && (
        <div className="space-y-4">
          {(snapshots.data ?? []).length > 0 && (
            <section>
              <h2 className="mb-2 text-sm font-semibold text-gray-400">Equity curve</h2>
              <EquityCurve snapshots={snapshots.data!} />
            </section>
          )}
          <section>
            <h2 className="mb-2 text-sm font-semibold text-gray-400">Positions</h2>
            <PositionsTable positions={activeDetail.positions} />
          </section>
          <section>
            <h2 className="mb-2 text-sm font-semibold text-gray-400">Orders</h2>
            <OrdersView accountId={active.id} />
          </section>
          <section>
            <h2 className="mb-2 text-sm font-semibold text-gray-400">Trades</h2>
            <div className="space-y-1">
              {(trades.data ?? []).map((t) => (
                <p key={`${t.order_id}-${t.filled_at}`} className="text-sm">
                  <span className="text-gray-500">{formatDateTime(t.filled_at)} </span>
                  <span className={t.side === "buy" ? "text-emerald-400" : "text-red-400"}>
                    {t.side}
                  </span>
                  <span className="text-gray-100">
                    {" "}{formatQty(t.qty)} {t.symbol} @ {formatUsd(t.price)}
                  </span>
                  {t.realized_pnl !== null && (
                    <span className={isNeg(t.realized_pnl) ? "text-red-400" : "text-emerald-400"}>
                      {" "}{isNeg(t.realized_pnl) ? "" : "+"}{formatUsd(t.realized_pnl)}
                    </span>
                  )}
                </p>
              ))}
              {trades.data?.length === 0 && (
                <p className="text-sm text-gray-500">No trades yet.</p>
              )}
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Wire it into the workbench**

In `frontend/app/replay/[id]/page.tsx`: add `import { ReplayAccounts } from "@/components/ReplayAccounts";` and append inside the outermost `<div className="space-y-4">`, after the chart/ticket grid:

```tsx
      <ReplayAccounts accounts={detail.accounts} />
```

- [ ] **Step 5: Run tests, full suite, typecheck, build**

Run: `cd frontend && npx vitest run tests/replay-accounts.test.tsx tests/replay-workbench.test.tsx && npm test && npm run typecheck && npm run build`
Expected: 3 + 6 passed; full suite 84; typecheck clean; build succeeds with `/replay` and `/replay/[id]` routes listed.

- [ ] **Step 6: Commit**

```bash
git add frontend/components/ReplayAccounts.tsx "frontend/app/replay/[id]/page.tsx" frontend/tests/replay-accounts.test.tsx
git commit -m "feat: replay account tabs with manual-vs-strategy equity comparison"
```
