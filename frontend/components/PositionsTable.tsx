import { formatQty, isCryptoSymbol } from "@/lib/qty";
import { formatUsd, isNeg } from "@/lib/money";
import type { PositionValue } from "@/lib/types";

function Pnl({ value }: { value: string }) {
  const neg = isNeg(value);
  return (
    <span className={neg ? "text-red-400" : "text-emerald-400"}>
      {neg ? "" : "+"}
      {formatUsd(value)}
    </span>
  );
}

export function PositionsTable({ positions }: { positions: PositionValue[] }) {
  if (positions.length === 0) {
    return <p className="text-sm text-gray-500">No open positions.</p>;
  }
  const stocks = positions.filter((p) => !isCryptoSymbol(p.symbol));
  const crypto = positions.filter((p) => isCryptoSymbol(p.symbol));
  const groups: { label: string; rows: PositionValue[] }[] = [
    ...(stocks.length > 0 ? [{ label: "Stocks", rows: stocks }] : []),
    ...(crypto.length > 0 ? [{ label: "Crypto", rows: crypto }] : []),
  ];
  return (
    <table className="w-full text-sm tabular-nums">
      <thead>
        <tr className="border-b border-gray-800 text-left text-xs uppercase text-gray-500">
          <th className="py-2">Symbol</th>
          <th className="py-2 text-right">Qty</th>
          <th className="py-2 text-right">Avg cost</th>
          <th className="py-2 text-right">Last</th>
          <th className="py-2 text-right">Value</th>
          <th className="py-2 text-right">Unrealized</th>
          <th className="py-2 text-right">Realized</th>
        </tr>
      </thead>
      {groups.map((g) => (
        <tbody key={g.label}>
          <tr>
            <td colSpan={7} className="pt-3 pb-1 text-xs font-semibold uppercase text-gray-500">
              {g.label}
            </td>
          </tr>
          {g.rows.map((p) => (
            <tr key={p.symbol} className="border-b border-gray-900">
              <td className="py-2 font-medium text-gray-100">{p.symbol}</td>
              <td className="py-2 text-right">{formatQty(p.qty)}</td>
              <td className="py-2 text-right">{formatUsd(p.avg_cost)}</td>
              <td className="py-2 text-right">{formatUsd(p.last_price)}</td>
              <td className="py-2 text-right">{formatUsd(p.market_value)}</td>
              <td className="py-2 text-right"><Pnl value={p.unrealized_pnl} /></td>
              <td className="py-2 text-right"><Pnl value={p.realized_pnl} /></td>
            </tr>
          ))}
        </tbody>
      ))}
    </table>
  );
}
