"use client";

import { useAccount } from "@/app/account-context";
import { OrdersView } from "@/components/OrdersView";

export default function OrdersPage() {
  const { accountId } = useAccount();
  if (accountId === null) return null;
  return <OrdersView accountId={accountId} />;
}
