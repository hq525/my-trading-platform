"use client";

import { useQuery } from "@tanstack/react-query";
import { createContext, useContext } from "react";
import { api } from "@/lib/api";
import type { Account } from "@/lib/types";

const Ctx = createContext<Account | null>(null);

export function LiveGate({ children }: { children: React.ReactNode }) {
  const { data: accounts, isPending } = useQuery({
    queryKey: ["accounts"],
    queryFn: api.accounts,
  });
  if (isPending) return <p className="text-sm text-gray-500">Loading…</p>;
  const live = accounts?.find((a) => a.mode === "live");
  if (!live) {
    return (
      <div className="rounded border border-gray-800 bg-gray-900 p-4 text-sm text-gray-400">
        Live trading not configured — set PT_ALPACA_TRADING_KEY_ID / PT_ALPACA_TRADING_SECRET.
      </div>
    );
  }
  return <Ctx.Provider value={live}>{children}</Ctx.Provider>;
}

export function useLiveAccount(): Account {
  const v = useContext(Ctx);
  if (!v) throw new Error("useLiveAccount must be used inside LiveGate");
  return v;
}
