"use client";

import { useQuery } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useState } from "react";
import { api } from "@/lib/api";

interface AccountCtx {
  accountId: number | null;
  setAccountId: (id: number) => void;
}

const Ctx = createContext<AccountCtx | null>(null);

export function AccountProvider({ children }: { children: React.ReactNode }) {
  const [accountId, setAccountId] = useState<number | null>(null);
  const { data: accounts } = useQuery({ queryKey: ["accounts"], queryFn: api.accounts });

  useEffect(() => {
    if (accountId === null && accounts?.length) {
      const paper = accounts.filter((a) => a.mode !== "live");
      if (!paper.length) return;
      const stored = Number(localStorage.getItem("pt-account") ?? "");
      const fallback = paper.find((a) => a.kind === "manual") ?? paper[0];
      setAccountId(paper.some((a) => a.id === stored) ? stored : fallback.id);
    }
  }, [accounts, accountId]);

  const set = (id: number) => {
    localStorage.setItem("pt-account", String(id));
    setAccountId(id);
  };

  return <Ctx.Provider value={{ accountId, setAccountId: set }}>{children}</Ctx.Provider>;
}

export function useAccount(): AccountCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAccount must be used inside AccountProvider");
  return v;
}
