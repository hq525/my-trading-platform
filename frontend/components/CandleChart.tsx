"use client";

import {
  CandlestickSeries, HistogramSeries, createChart,
} from "lightweight-charts";
import { useEffect, useRef } from "react";
import type { Bar } from "@/lib/types";

const DARK = {
  layout: { background: { color: "#030712" }, textColor: "#9ca3af" },
  grid: { vertLines: { color: "#1f2937" }, horzLines: { color: "#1f2937" } },
};

export function CandleChart({ bars }: { bars: Bar[] }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = createChart(ref.current, { autoSize: true, ...DARK });
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: "#22c55e", downColor: "#ef4444",
      wickUpColor: "#22c55e", wickDownColor: "#ef4444",
      borderVisible: false,
    });
    candles.setData(
      bars.map((b) => ({
        time: b.timestamp.slice(0, 10),
        open: Number(b.open), high: Number(b.high),
        low: Number(b.low), close: Number(b.close),
      })),
    );
    const volume = chart.addSeries(HistogramSeries, {
      priceScaleId: "vol",
      color: "#334155",
      priceFormat: { type: "volume" },
    });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });
    volume.setData(bars.map((b) => ({ time: b.timestamp.slice(0, 10), value: b.volume })));
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [bars]);

  return <div ref={ref} className="h-[420px] w-full" />;
}
