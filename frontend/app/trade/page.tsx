"use client";

import { Suspense } from "react";
import { TradeView } from "@/components/TradeView";

export default function TradePage() {
  return (
    <Suspense>
      <TradeView />
    </Suspense>
  );
}
