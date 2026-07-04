import { formatDateTime } from "@/lib/format";
import { formatUsd } from "@/lib/money";
import { formatQty, isCryptoSymbol } from "@/lib/qty";
import type { Order } from "@/lib/types";

const statusColor: Record<Order["status"], string> = {
  pending: "text-amber-400",
  filled: "text-emerald-400",
  cancelled: "text-gray-500",
  rejected: "text-red-400",
  expired: "text-gray-500",
};

export function OrdersTable({
  orders,
  onCancel,
  cancellingId,
}: {
  orders: Order[];
  onCancel?: (id: number) => void;
  cancellingId?: number | null;
}) {
  if (orders.length === 0) {
    return <p className="text-sm text-gray-500">No orders.</p>;
  }
  return (
    <table className="w-full text-sm tabular-nums">
      <thead>
        <tr className="border-b border-gray-800 text-left text-xs uppercase text-gray-500">
          <th className="py-2">Placed</th>
          <th className="py-2">Symbol</th>
          <th className="py-2">Side</th>
          <th className="py-2">Type</th>
          <th className="py-2 text-right">Qty</th>
          <th className="py-2 text-right">Limit</th>
          <th className="py-2">TIF</th>
          <th className="py-2">Status</th>
          {onCancel && <th className="py-2" />}
        </tr>
      </thead>
      <tbody>
        {orders.map((o) => (
          <tr key={o.id} className="border-b border-gray-900">
            <td className="py-2 text-gray-400">{formatDateTime(o.placed_at)}</td>
            <td className="py-2 font-medium">
              <a href={`/trade?symbol=${o.symbol}`} className="text-gray-100 hover:underline">
                {o.symbol}
              </a>
              <span className="ml-2 rounded bg-gray-800 px-1.5 py-0.5 text-[10px] uppercase text-gray-400">
                {isCryptoSymbol(o.symbol) ? "Crypto" : "Stock"}
              </span>
            </td>
            <td className={`py-2 ${o.side === "buy" ? "text-emerald-400" : "text-red-400"}`}>
              {o.side}
            </td>
            <td className="py-2">{o.order_type}</td>
            <td className="py-2 text-right">{formatQty(o.qty)}</td>
            <td className="py-2 text-right">
              {o.limit_price ? formatUsd(o.limit_price) : "—"}
            </td>
            <td className="py-2 uppercase">{o.tif}</td>
            <td className={`py-2 ${statusColor[o.status]}`}>
              {o.status}
              {o.reject_reason && (
                <span className="block text-xs text-gray-500">{o.reject_reason}</span>
              )}
            </td>
            {onCancel && (
              <td className="py-2 text-right">
                {o.status === "pending" && (
                  <button
                    onClick={() => onCancel(o.id)}
                    disabled={cancellingId === o.id}
                    className="rounded border border-gray-700 px-2 py-0.5 text-xs text-gray-300 hover:border-gray-500 disabled:opacity-50"
                  >
                    Cancel
                  </button>
                )}
              </td>
            )}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
