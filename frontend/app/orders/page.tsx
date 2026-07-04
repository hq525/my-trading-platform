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
