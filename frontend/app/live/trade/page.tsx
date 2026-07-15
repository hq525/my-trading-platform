"use client";

import { Suspense } from "react";
import { TradeView } from "@/components/TradeView";
import { useLiveAccount } from "../live-context";

export default function LiveTradePage() {
  const live = useLiveAccount();
  return (
    <Suspense>
      <TradeView ticketAccountId={live.id} liveTicket />
    </Suspense>
  );
}
