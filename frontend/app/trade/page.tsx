"use client";

import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { CandleChart } from "@/components/CandleChart";
import { OrderTicket } from "@/components/OrderTicket";
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
        <aside>
          <OrderTicket symbol={symbol} quotePrice={quote.data?.price} />
        </aside>
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
