"use client";

import { ApiError } from "@/lib/api";
import { dataAge, utcDate } from "@/lib/format";
import { formatUsd } from "@/lib/money";
import type { Quote } from "@/lib/types";

export function QuoteBadge({
  quote,
  error,
  now = new Date(),
}: {
  quote?: Quote;
  error?: unknown;
  now?: Date;
}) {
  if (error instanceof ApiError) {
    return (
      <span className="text-sm text-red-400">
        {error.status === 404 ? "Unknown symbol" : "Market data unavailable"}
      </span>
    );
  }
  if (!quote) return <span className="text-sm text-gray-500">—</span>;

  const ageSecs = Math.floor((now.getTime() - utcDate(quote.as_of).getTime()) / 1000);
  const stale = ageSecs > 120;
  return (
    <span className="flex items-baseline gap-2">
      <span className="text-2xl font-semibold tabular-nums text-gray-100">
        {formatUsd(quote.price)}
      </span>
      <span className={`text-xs ${stale ? "text-amber-400" : "text-gray-500"}`}>
        {dataAge(quote.as_of, now)}
        {stale && " · stale"}
      </span>
    </span>
  );
}
