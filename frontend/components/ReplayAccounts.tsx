"use client";

import { useQueries, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { EquityCurve } from "@/components/EquityCurve";
import { OrdersView } from "@/components/OrdersView";
import { PositionsTable } from "@/components/PositionsTable";
import { api } from "@/lib/api";
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
                    !pnl ? "text-gray-500"
                      : isNeg(pnl) ? "text-red-400" : "text-emerald-400"
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
                  <span className="text-gray-500">
                    {/* virtual timestamps are UTC by convention: render the
                        date only, never local time (off-by-one past UTC+3) */}
                    {t.filled_at.slice(0, 10)}{" "}
                  </span>
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
