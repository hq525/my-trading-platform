"use client";

import { AreaSeries, createChart } from "lightweight-charts";
import { useEffect, useRef } from "react";
import type { Snapshot } from "@/lib/types";

export function EquityCurve({
  snapshots,
  height = 220,
}: {
  snapshots: Snapshot[];
  height?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = createChart(ref.current, {
      autoSize: true,
      layout: { background: { color: "#030712" }, textColor: "#9ca3af" },
      grid: { vertLines: { color: "#1f2937" }, horzLines: { color: "#1f2937" } },
    });
    const area = chart.addSeries(AreaSeries, {
      lineColor: "#34d399",
      topColor: "rgba(52, 211, 153, 0.25)",
      bottomColor: "rgba(52, 211, 153, 0.02)",
    });
    area.setData(snapshots.map((s) => ({ time: s.date, value: Number(s.equity) })));
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [snapshots]);

  return <div ref={ref} style={{ height }} className="w-full" />;
}
