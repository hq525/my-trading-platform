"use client";

import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, ApiError } from "@/lib/api";

const inputCls =
  "w-full rounded border border-gray-700 bg-gray-950 px-3 py-1.5 text-sm text-gray-100 outline-none focus:border-gray-500";

export default function ReplaySessionsPage() {
  const qc = useQueryClient();
  const sessions = useQuery({ queryKey: ["replay-sessions"], queryFn: api.replaySessions });
  const strategies = useQuery({ queryKey: ["strategies"], queryFn: api.strategies });

  const [name, setName] = useState("");
  const [symbols, setSymbols] = useState("");
  const [startDate, setStartDate] = useState("");
  const [startingCash, setStartingCash] = useState("100000");
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [confirmingDelete, setConfirmingDelete] = useState<number | null>(null);

  const create = useMutation({
    mutationFn: () =>
      api.createReplaySession({
        symbols: symbols.split(",").map((s) => s.trim()).filter(Boolean),
        start_date: startDate,
        strategies: [...checked],
        starting_cash: startingCash,
        ...(name.trim() ? { name: name.trim() } : {}),
      }),
    onSuccess: () => {
      setName("");
      setSymbols("");
      setChecked(new Set());
      void qc.invalidateQueries({ queryKey: ["replay-sessions"] });
      void qc.invalidateQueries({ queryKey: ["accounts"] });
    },
  });

  const remove = useMutation({
    mutationFn: (id: number) => api.deleteReplaySession(id),
    onSuccess: () => {
      setConfirmingDelete(null);
      void qc.invalidateQueries({ queryKey: ["replay-sessions"] });
      void qc.invalidateQueries({ queryKey: ["accounts"] });
    },
  });

  const toggle = (n: string) => {
    const next = new Set(checked);
    if (next.has(n)) next.delete(n);
    else next.add(n);
    setChecked(next);
  };

  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_360px]">
      <section className="space-y-2">
        <h2 className="text-sm font-semibold text-gray-400">Sessions</h2>
        {(sessions.data ?? []).map((s) => (
          <div key={s.id}
            className="flex flex-wrap items-center gap-3 rounded-lg border border-gray-800 bg-gray-900 p-3 text-sm">
            <span className="font-medium text-gray-100">{s.name}</span>
            <span className="text-gray-500">{s.symbols.join(", ")}</span>
            <span className="text-gray-500">
              {s.cursor_date} → {s.end_date}
            </span>
            {s.exhausted && (
              <span className="rounded bg-gray-800 px-1.5 py-0.5 text-[10px] uppercase text-gray-400">
                Exhausted
              </span>
            )}
            <span className="ml-auto flex items-center gap-2">
              <Link href={`/replay/${s.id}`}
                className="rounded border border-gray-700 px-3 py-1 text-gray-300 hover:border-gray-500">
                Open
              </Link>
              {confirmingDelete === s.id ? (
                <button onClick={() => remove.mutate(s.id)}
                  className="rounded bg-red-800 px-3 py-1 text-white hover:bg-red-700">
                  Confirm delete
                </button>
              ) : (
                <button onClick={() => setConfirmingDelete(s.id)}
                  className="rounded px-3 py-1 text-gray-500 hover:text-red-400">
                  Delete
                </button>
              )}
            </span>
          </div>
        ))}
        {sessions.data?.length === 0 && (
          <p className="text-sm text-gray-500">No replay sessions yet.</p>
        )}
      </section>

      <aside className="space-y-3 rounded-lg border border-gray-800 bg-gray-900 p-4">
        <h2 className="text-sm font-semibold text-gray-300">New session</h2>
        <label className="block text-xs text-gray-500" htmlFor="rname">Name (optional)</label>
        <input id="rname" value={name} onChange={(e) => setName(e.target.value)}
          className={inputCls} />
        <label className="block text-xs text-gray-500" htmlFor="rsymbols">
          Symbols (comma-separated)
        </label>
        <input id="rsymbols" value={symbols}
          onChange={(e) => setSymbols(e.target.value)} className={inputCls} />
        <label className="block text-xs text-gray-500" htmlFor="rstart">Start date</label>
        <input id="rstart" type="date" value={startDate}
          onChange={(e) => setStartDate(e.target.value)} className={inputCls} />
        <label className="block text-xs text-gray-500" htmlFor="rcash">Starting cash</label>
        <input id="rcash" inputMode="decimal" value={startingCash}
          onChange={(e) => setStartingCash(e.target.value.replace(/[^0-9.]/g, ""))}
          className={inputCls} />
        {(strategies.data ?? []).length > 0 && (
          <fieldset className="space-y-1">
            <legend className="text-xs text-gray-500">Strategies</legend>
            {(strategies.data ?? []).map((s) => (
              <label key={s.name} className="flex items-center gap-2 text-sm text-gray-300">
                <input type="checkbox" checked={checked.has(s.name)}
                  onChange={() => toggle(s.name)} aria-label={s.name} />
                {s.name}
              </label>
            ))}
          </fieldset>
        )}
        <button
          onClick={() => create.mutate()}
          disabled={create.isPending || !symbols.trim() || !startDate}
          className="w-full rounded bg-emerald-700 px-3 py-2 font-medium text-white hover:bg-emerald-600 disabled:opacity-50"
        >
          {create.isPending ? "Creating…" : "Create session"}
        </button>
        {create.error && (
          <p className="text-sm text-red-400">
            {create.error instanceof ApiError ? create.error.message : "Creation failed"}
          </p>
        )}
      </aside>
    </div>
  );
}
