"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { EquityCurve } from "@/components/EquityCurve";
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { Strategy } from "@/lib/types";

function StrategyCard({ s }: { s: Strategy }) {
  const qc = useQueryClient();
  const toggle = useMutation({
    mutationFn: () => api.toggleStrategy(s.name),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["strategies"] }),
  });
  const runs = useQuery({
    queryKey: ["runs", s.name],
    queryFn: () => api.runs(s.name, 10),
  });
  const snaps = useQuery({
    queryKey: ["snapshots", s.account_id],
    queryFn: () => api.snapshots(s.account_id),
  });

  return (
    <div className="space-y-3 rounded-lg border border-gray-800 bg-gray-900 p-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="font-semibold text-gray-100">{s.name}</h2>
          <p className="text-xs text-gray-500">{s.schedule}</p>
        </div>
        <button
          role="switch"
          aria-checked={s.enabled}
          aria-label={`Toggle ${s.name}`}
          onClick={() => toggle.mutate()}
          disabled={toggle.isPending}
          className={`h-6 w-11 rounded-full p-0.5 transition-colors ${
            s.enabled ? "bg-emerald-600" : "bg-gray-700"
          }`}
        >
          <span
            className={`block h-5 w-5 rounded-full bg-white transition-transform ${
              s.enabled ? "translate-x-5" : ""
            }`}
          />
        </button>
      </div>

      {(snaps.data?.length ?? 0) > 0 && (
        <EquityCurve snapshots={snaps.data!} height={160} />
      )}

      <div>
        <h3 className="mb-1 text-xs font-semibold uppercase text-gray-500">Recent runs</h3>
        {(runs.data ?? []).length === 0 && (
          <p className="text-sm text-gray-500">No runs yet.</p>
        )}
        <ul className="space-y-1 text-sm">
          {(runs.data ?? []).map((r) => (
            <li key={r.id} className="flex flex-col">
              <span>
                <span className="text-gray-500">{formatDateTime(r.started_at)}</span>{" "}
                <span className={r.status === "ok" ? "text-emerald-400" : "text-red-400"}>
                  {r.status}
                </span>
              </span>
              {r.status === "error" ? (
                <pre className="mt-0.5 max-h-32 overflow-auto rounded bg-gray-950 p-2 text-xs text-red-300">
                  {r.detail}
                </pre>
              ) : (
                <span className="text-xs text-gray-500">{r.detail}</span>
              )}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

export default function StrategiesPage() {
  const strategies = useQuery({ queryKey: ["strategies"], queryFn: api.strategies });

  if (strategies.data?.length === 0) {
    return (
      <p className="text-sm text-gray-500">
        No strategies found. Drop a Python file in backend/strategies/ and restart the backend.
      </p>
    );
  }
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      {(strategies.data ?? []).map((s) => (
        <StrategyCard key={s.name} s={s} />
      ))}
    </div>
  );
}
