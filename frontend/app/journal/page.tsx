"use client";

import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useAccount } from "@/app/account-context";
import { StatCard } from "@/components/StatCard";
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { formatQty, isCryptoSymbol } from "@/lib/qty";
import { formatOptionLabel, isOptionSymbol } from "@/lib/options";
import { formatUsd, isNeg } from "@/lib/money";

const MODES = ["all", "paper", "live"] as const;
type Mode = (typeof MODES)[number];

export default function JournalPage() {
  const { accountId } = useAccount();
  const qc = useQueryClient();
  const [editing, setEditing] = useState<number | null>(null);
  const [text, setText] = useState("");
  const [mode, setMode] = useState<Mode>("all");

  const accounts = useQuery({ queryKey: ["accounts"], queryFn: api.accounts });
  const journals = useQueries({
    queries: (accounts.data ?? [])
      .filter((a) => a.mode !== "replay")
      .map((a) => ({
        queryKey: ["journal", a.id],
        queryFn: () => api.journal(a.id),
      })),
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
      void qc.invalidateQueries({ queryKey: ["journal"] });
    },
  });

  const loaded =
    accounts.data !== undefined && journals.every((q) => q.data !== undefined);
  const trades = journals
    .flatMap((q) => q.data ?? [])
    .filter((t) => mode === "all" || t.account_mode === mode)
    .sort((a, b) =>
      a.filled_at < b.filled_at ? 1 : a.filled_at > b.filled_at ? -1 : b.order_id - a.order_id,
    );

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

      <div className="space-y-2">
        {trades.map((t) => (
          <div key={`${t.order_id}-${t.filled_at}`}
            className="rounded-lg border border-gray-800 bg-gray-900 p-3">
            <div className="flex flex-wrap items-baseline gap-3 text-sm">
              <span className="text-gray-500">{formatDateTime(t.filled_at)}</span>
              <span className={t.side === "buy" ? "text-emerald-400" : "text-red-400"}>
                {t.side}
              </span>
              <span className="font-medium text-gray-100">
                {formatQty(t.qty)}{" "}
                {isOptionSymbol(t.symbol) ? formatOptionLabel(t.symbol) : t.symbol} @{" "}
                {formatUsd(t.price)}
              </span>
              <span className="rounded bg-gray-800 px-1.5 py-0.5 text-[10px] uppercase text-gray-400">
                {isOptionSymbol(t.symbol)
                  ? "Option"
                  : isCryptoSymbol(t.symbol)
                    ? "Crypto"
                    : "Stock"}
              </span>
              <span
                className={`rounded px-1.5 py-0.5 text-[10px] uppercase ${
                  t.account_mode === "live"
                    ? "bg-amber-900 text-amber-300"
                    : "bg-gray-800 text-gray-400"
                }`}
              >
                {t.account_mode === "live" ? "Live" : "Paper"}
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
        {loaded && trades.length === 0 && (
          <p className="text-sm text-gray-500">No trades yet.</p>
        )}
      </div>
    </div>
  );
}
