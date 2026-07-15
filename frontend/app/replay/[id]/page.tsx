"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "next/navigation";
import { useState } from "react";
import { CandleChart } from "@/components/CandleChart";
import { OrderTicket } from "@/components/OrderTicket";
import { QuoteBadge } from "@/components/QuoteBadge";
import { ReplayAccounts } from "@/components/ReplayAccounts";
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

      <ReplayAccounts accounts={detail.accounts} />
    </div>
  );
}
