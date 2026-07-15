"use client";

import { useQuery } from "@tanstack/react-query";
import { useAccount } from "@/app/account-context";
import { api } from "@/lib/api";

export function AccountSwitcher() {
  const { accountId, setAccountId } = useAccount();
  const { data: accounts } = useQuery({ queryKey: ["accounts"], queryFn: api.accounts });
  const paper = accounts?.filter((a) => a.mode === "paper") ?? [];
  if (!paper.length || accountId === null) return null;
  return (
    <select
      aria-label="Account"
      value={accountId}
      onChange={(e) => setAccountId(Number(e.target.value))}
      className="rounded border border-gray-700 bg-gray-950 px-2 py-1 text-sm text-gray-200"
    >
      {paper.map((a) => (
        <option key={a.id} value={a.id}>
          {a.name}
        </option>
      ))}
    </select>
  );
}
