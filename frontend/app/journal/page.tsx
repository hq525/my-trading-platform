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
