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

  // The ticket tracks the LIVE chain row for the selected contract, so a 30s
  // chain refetch updates the ticket's bid/ask along with the table. Falls
  // back to the click-time snapshot if the contract vanishes from the chain.
  const chainRows = chain.data ? [...chain.data.calls, ...chain.data.puts] : [];
  const liveSelected = selected
    ? (chainRows.find((r) => r.symbol === selected.symbol) ?? selected)
    : null;

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
          {liveSelected ? (
            <OrderTicket
              symbol={liveSelected.symbol}
              quotePrice={liveSelected.ask ?? liveSelected.last ?? undefined}
              bid={liveSelected.bid ?? undefined}
              ask={liveSelected.ask ?? undefined}
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
