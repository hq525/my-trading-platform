export interface Account {
  id: number;
  name: string;
  kind: "manual" | "strategy";
  cash: string;
  starting_cash: string;
}

export interface PositionValue {
  symbol: string;
  qty: number;
  avg_cost: string;
  last_price: string;
  market_value: string;
  unrealized_pnl: string;
  realized_pnl: string;
}

export interface AccountDetail extends Account {
  equity: string;
  positions: PositionValue[];
}

export interface Snapshot {
  date: string; // YYYY-MM-DD
  equity: string;
  cash: string;
}

export interface Order {
  id: number;
  account_id: number;
  symbol: string;
  side: "buy" | "sell";
  order_type: "market" | "limit";
  tif: "day" | "gtc";
  qty: number;
  limit_price: string | null;
  status: "pending" | "filled" | "cancelled" | "rejected" | "expired";
  reject_reason: string | null;
  placed_at: string;
}

export interface PlaceOrderBody {
  symbol: string;
  side: "buy" | "sell";
  order_type: "market" | "limit";
  qty: number;
  tif: "day" | "gtc";
  limit_price?: string;
  idempotency_key?: string;
}

export interface Quote {
  symbol: string;
  price: string;
  as_of: string;
}

export interface Bar {
  timestamp: string;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: number;
}

export interface Trade {
  order_id: number;
  symbol: string;
  side: "buy" | "sell";
  qty: number;
  price: string;
  commission: string;
  realized_pnl: string | null;
  filled_at: string;
  note: string | null;
}

export interface Stats {
  closed_trades: number;
  wins: number;
  win_rate: number | null;
  avg_gain: string | null;
  avg_loss: string | null;
}

export interface Strategy {
  name: string;
  schedule: string;
  enabled: boolean;
  account_id: number;
}

export interface StrategyRun {
  id: number;
  strategy_name: string;
  started_at: string;
  finished_at: string | null;
  status: "ok" | "error";
  detail: string;
}
