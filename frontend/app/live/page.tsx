"use client";

import { useQuery } from "@tanstack/react-query";
import { EquityCurve } from "@/components/EquityCurve";
import { OrdersTable } from "@/components/OrdersTable";
import { PositionsTable } from "@/components/PositionsTable";
import { StatCard } from "@/components/StatCard";
import { api, ApiError } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { formatUsd, isNeg, subMoney } from "@/lib/money";
import { useLiveAccount } from "./live-context";

function signed(value: string): { text: string; tone: "pos" | "neg" } {
  const neg = isNeg(value);
  return { text: `${neg ? "" : "+"}${formatUsd(value)}`, tone: neg ? "neg" : "pos" };
}

export default function LiveDashboardPage() {
  const live = useLiveAccount();
  const detail = useQuery({
    queryKey: ["account", live.id],
    queryFn: () => api.accountDetail(live.id),
    refetchInterval: 30_000,
  });
  const snapshots = useQuery({
    queryKey: ["snapshots", live.id],
    queryFn: () => api.snapshots(live.id),
  });
  const openOrders = useQuery({
    queryKey: ["orders", live.id, "pending"],
    queryFn: () => api.orders(live.id, "pending"),
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
  const snaps = snapshots.data ?? [];
  const lastSnap = snaps.length ? snaps[snaps.length - 1] : null;
  const sinceClose = lastSnap ? signed(subMoney(d.equity, lastSnap.equity)) : null;

  return (
    <div className="space-y-6">
      <p className="text-xs text-gray-500">
        {d.last_synced_at
          ? `Synced with Alpaca as of ${formatDateTime(d.last_synced_at)}`
          : "Not yet synced with Alpaca"}
      </p>
      {d.sync_detail && (
        <div className="rounded border border-amber-800 bg-amber-950 p-3 text-sm text-amber-300">
          Position mismatch vs Alpaca: {d.sync_detail}
        </div>
      )}
      {/* No Total P&L card: the live account's starting_cash is a meaningless 0. */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatCard label="Equity" value={formatUsd(d.equity)} />
        <StatCard label="Cash" value={formatUsd(d.cash)} />
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
