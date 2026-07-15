"use client";

import { OrdersView } from "@/components/OrdersView";
import { useLiveAccount } from "../live-context";

export default function LiveOrdersPage() {
  const live = useLiveAccount();
  return <OrdersView accountId={live.id} />;
}
