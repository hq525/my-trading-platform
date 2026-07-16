export interface Account {
  id: number;
  name: string;
  kind: "manual" | "strategy";
  mode: "paper" | "live" | "replay";
  cash: string;
  starting_cash: string;
  last_synced_at: string | null;
  sync_detail: string | null;
}

export interface PositionValue {
  symbol: string;
  qty: string;
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
  qty: string;
  limit_price: string | null;
  status: "pending" | "filled" | "cancelled" | "rejected" | "expired";
  reject_reason: string | null;
  placed_at: string;
}

export interface PlaceOrderBody {
  symbol: string;
  side: "buy" | "sell";
  order_type: "market" | "limit";
  qty: string;
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
  qty: string;
  price: string;
  commission: string;
  realized_pnl: string | null;
  filled_at: string;
  note: string | null;
  account_mode: "paper" | "live" | "replay";
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

export interface ReplaySession {
  id: number;
  name: string;
  symbols: string[];
  start_date: string; // YYYY-MM-DD
  cursor_date: string;
  end_date: string;
  exhausted: boolean;
  created_at: string;
}

export interface ReplayAccount {
  id: number;
  name: string;
  role: string; // "manual" or the strategy name
}

export interface ReplayCoverage {
  symbol: string;
  first_date: string;
  last_date: string;
}

export interface ReplaySessionDetail extends ReplaySession {
  accounts: ReplayAccount[];
  coverage: ReplayCoverage[];
}

export interface StepFill {
  order_id: number;
  symbol: string;
  side: "buy" | "sell";
  qty: string;
  price: string;
}

export interface StepResult {
  cursor_date: string;
  fills: StepFill[];
  expired: number[];
  cancelled_at_exhaustion: number[];
  strategy_errors: Record<string, string>;
  exhausted: boolean;
}

export interface CreateReplaySessionBody {
  symbols: string[];
  start_date: string;
  strategies: string[];
  starting_cash?: string;
  name?: string;
}
