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
