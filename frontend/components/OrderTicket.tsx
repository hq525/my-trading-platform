"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useAccount } from "@/app/account-context";
import { api, ApiError } from "@/lib/api";
import { formatUsd, gtMoney, mulMoney } from "@/lib/money";
import { isCryptoSymbol, isValidQty } from "@/lib/qty";
import type { Order, PlaceOrderBody } from "@/lib/types";

const radio = (active: boolean) =>
  `flex-1 cursor-pointer rounded border px-3 py-1.5 text-center text-sm ${
    active
      ? "border-gray-500 bg-gray-800 text-white"
      : "border-gray-700 text-gray-400 hover:text-gray-200"
  }`;

export function OrderTicket({
  symbol,
  quotePrice,
  accountId: accountIdProp,
  live = false,
}: {
  symbol: string;
  quotePrice?: string;
  accountId?: number;
  live?: boolean;
}) {
  const ctx = useAccount();
  const accountId = accountIdProp ?? ctx.accountId;
  const qc = useQueryClient();
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [type, setType] = useState<"market" | "limit">("market");
  const [qty, setQty] = useState("1");
  const [tif, setTif] = useState<"day" | "gtc">("day");
  const [limitPrice, setLimitPrice] = useState("");
  const [result, setResult] = useState<Order | null>(null);
  const [confirming, setConfirming] = useState(false);

  const detail = useQuery({
    queryKey: ["account", accountId],
    queryFn: () => api.accountDetail(accountId!),
    enabled: accountId !== null,
  });

  const allowFractional = !live && isCryptoSymbol(symbol);
  const cryptoBlocked = live && isCryptoSymbol(symbol);
  const qtyValid = isValidQty(qty, allowFractional);
  const previewPrice = type === "limit" ? limitPrice : quotePrice;
  let cost: string | null = null;
  try {
    cost = previewPrice && qtyValid ? mulMoney(previewPrice, qty) : null;
  } catch {
    cost = null; // partially-typed limit price, or qty precision exceeded
  }
  const cash = detail.data?.cash;
  const insufficient =
    side === "buy" && cost !== null && cash !== undefined && gtMoney(cost, cash);

  const place = useMutation({
    mutationFn: (body: PlaceOrderBody) => api.placeOrder(accountId!, body),
    onSuccess: (order) => {
      setResult(order);
      void qc.invalidateQueries({ queryKey: ["account", accountId] });
      void qc.invalidateQueries({ queryKey: ["orders", accountId] });
    },
  });

  const canSubmit =
    accountId !== null &&
    qtyValid &&
    !cryptoBlocked &&
    !insufficient &&
    !place.isPending &&
    (type === "market" || (limitPrice.trim().length > 0 && cost !== null));

  const submit = () => {
    setConfirming(false);
    setResult(null);
    place.mutate({
      symbol,
      side,
      order_type: type,
      qty: qty,
      tif,
      ...(type === "limit" ? { limit_price: limitPrice } : {}),
      idempotency_key: crypto.randomUUID(),
    });
  };

  return (
    <div className="space-y-3 rounded-lg border border-gray-800 bg-gray-900 p-4">
      <h2 className="text-sm font-semibold text-gray-300">Order — {symbol}</h2>

      <div className="flex gap-2" role="radiogroup" aria-label="Side">
        {(["buy", "sell"] as const).map((s) => (
          <button key={s} role="radio" aria-checked={side === s}
            className={radio(side === s)} onClick={() => setSide(s)}>
            {s}
          </button>
        ))}
      </div>

      <div className="flex gap-2" role="radiogroup" aria-label="Order type">
        {(["market", "limit"] as const).map((t) => (
          <button key={t} role="radio" aria-checked={type === t}
            className={radio(type === t)} onClick={() => setType(t)}>
            {t}
          </button>
        ))}
      </div>

      <label className="block text-xs text-gray-500" htmlFor="qty">
        Quantity {allowFractional ? "(up to 8 decimal places)" : "(whole shares)"}
      </label>
      <input id="qty" inputMode="decimal" value={qty}
        onChange={(e) => setQty(e.target.value.replace(/[^0-9.]/g, ""))}
        className="w-full rounded border border-gray-700 bg-gray-950 px-3 py-1.5 text-sm text-gray-100 outline-none focus:border-gray-500" />

      {type === "limit" && (
        <>
          <label className="block text-xs text-gray-500" htmlFor="limit">Limit price</label>
          <input id="limit" inputMode="decimal" value={limitPrice}
            onChange={(e) => setLimitPrice(e.target.value.replace(/[^0-9.]/g, ""))}
            className="w-full rounded border border-gray-700 bg-gray-950 px-3 py-1.5 text-sm text-gray-100 outline-none focus:border-gray-500" />
        </>
      )}

      <div className="flex gap-2" role="radiogroup" aria-label="Time in force">
        {(["day", "gtc"] as const).map((t) => (
          <button key={t} role="radio" aria-checked={tif === t}
            className={radio(tif === t)} onClick={() => setTif(t)}>
            {t.toUpperCase()}
          </button>
        ))}
      </div>

      <div className="border-t border-gray-800 pt-2 text-sm">
        <div className="flex justify-between text-gray-400">
          <span>Est. {side === "buy" ? "cost" : "proceeds"}</span>
          <span className="tabular-nums text-gray-100">{cost ? formatUsd(cost) : "—"}</span>
        </div>
        {cash !== undefined && (
          <div className="flex justify-between text-gray-500">
            <span>Cash</span>
            <span className="tabular-nums">{formatUsd(cash)}</span>
          </div>
        )}
        {insufficient && <p className="mt-1 text-xs text-red-400">Insufficient cash</p>}
      </div>

      {live && confirming ? (
        <div className="space-y-2 rounded border border-amber-800 bg-amber-950 p-3">
          <p className="text-sm text-amber-300">
            Place LIVE {side}: {qty} {symbol}, {type}
            {type === "limit" ? ` @ ${limitPrice}` : ""}, {tif.toUpperCase()}
          </p>
          <div className="flex gap-2">
            <button
              onClick={submit}
              className="flex-1 rounded bg-amber-600 px-3 py-2 font-medium text-black hover:bg-amber-500"
            >
              Confirm
            </button>
            <button
              onClick={() => setConfirming(false)}
              className="flex-1 rounded border border-gray-700 px-3 py-2 text-gray-300 hover:border-gray-500"
            >
              Back
            </button>
          </div>
        </div>
      ) : (
        <button
          onClick={() => (live ? setConfirming(true) : submit())}
          disabled={!canSubmit}
          className={`w-full rounded px-3 py-2 font-medium text-white disabled:opacity-50 ${
            side === "buy" ? "bg-emerald-700 hover:bg-emerald-600" : "bg-red-800 hover:bg-red-700"
          }`}
        >
          {place.isPending ? "Placing…" : live ? "Place LIVE order" : "Place order"}
        </button>
      )}
      {cryptoBlocked && (
        <p className="text-xs text-amber-400">Crypto is not supported in live trading</p>
      )}

      {place.error && (
        <p className="text-sm text-red-400">
          {place.error instanceof ApiError ? place.error.message : "Order failed"}
        </p>
      )}
      {result && (
        <p className="text-sm">
          <span
            className={
              result.status === "filled"
                ? "text-emerald-400"
                : result.status === "pending"
                  ? "text-amber-400"
                  : "text-red-400"
            }
          >
            {result.status}
          </span>
          {result.reject_reason && (
            <span className="block text-xs text-gray-400">{result.reject_reason}</span>
          )}
        </p>
      )}
    </div>
  );
}
