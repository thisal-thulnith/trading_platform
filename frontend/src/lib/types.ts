export type Candle = { time: number; open: number; high: number; low: number; close: number; volume: number; taker_buy?: number };

export type ChartObject = {
  id: string;
  kind: "zone" | "hline" | "vline" | "trendline" | "ray" | "fib" | "label" | "marker" | "wave" | "position" | "rect" | "channel";
  concept?: string;
  time1?: number | null;
  time2?: number | null;
  price1?: number;
  price2?: number;
  color?: string;
  label?: string;
  status?: string;
  style?: "solid" | "dashed" | "dotted";
  extend?: boolean;
  levels?: number[];
  points?: { time: number; price: number; label: string }[];
  position?: "above" | "below";
  shape?: string;
  role?: string;
  invalidation?: number | null;
  explain?: Record<string, any>;
  origin: "ai" | "user" | "ai_chat";
  // position box
  direction?: "LONG" | "SHORT";
  entry?: number;
  stop?: number;
  target?: number;
  // persistence
  dbId?: string; // user drawing id in backend
  parentId?: string | null; // AI annotation edited by user
  hidden?: boolean;
};

export type Evidence = { family: string; name: string; direction: number; strength: number; detail: string; basis: string; ref?: string | null };

export type Candidate = {
  strategy: string;
  direction: "LONG" | "SHORT";
  stage: "forming" | "pre_signal" | "confirmed";
  entry_low: number;
  entry_high: number;
  stop: number;
  targets: number[];
  missing: string[];
  invalidation: string;
  anchor_id: string;
  conditions: { name: string; kind: string; met: boolean; detail: string }[];
  expiry_bars: number;
  expected_hold_bars: number;
  validation: {
    passed: boolean;
    checks: { name: string; passed: boolean; detail: string }[];
    counter_arguments: string[];
    rr_tp1: number;
    rr_tp2?: number;
    rr_tp3?: number;
    quality_score: number;
    supporting_families: string[];
    opposing_families: string[];
  };
  error?: string;
};

export type Snapshot = {
  symbol: string;
  timeframe: string;
  price: number;
  as_of: number;
  as_of_close_time: number;
  generated_at: number;
  engine_version: string;
  data_hash: string;
  snapshot_id?: string;
  indicators: Record<string, number | null>;
  regime: { regime: string; reasons: string[]; secondary?: string[]; suited_strategies?: string[]; features: Record<string, number> };
  confluence: {
    bias: number;
    direction: string;
    families: Record<string, { score: number; n: number; bull: number; bear: number }>;
    independent_families_bull: number;
    independent_families_bear: number;
    weights: Record<string, number>;
  };
  evidence: Evidence[];
  smc: any;
  elliott: { counts: any[]; disclaimer?: string };
  price_action: any;
  mtf: Record<string, number>;
  derivatives: any;
  orderbook: any;
  ml: any;
  news: any[];
  fear_greed: any;
  liquidations: any[];
  data_freshness: any;
  annotations: ChartObject[];
  candidates: Candidate[];
  decision: { action: string; strategy?: string; quality?: number; reasons: string[] };
  replay?: any;
};

export type SignalRow = {
  id: string;
  symbol: string;
  timeframe: string;
  strategy: string;
  direction: "LONG" | "SHORT";
  state: string;
  created_at: string;
  confirmed_at: string | null;
  reference_price: number;
  entry_low: number;
  entry_high: number;
  stop: number;
  tp1: number;
  tp2: number;
  tp3: number;
  score: number;
  outcome: string | null;
  r_multiple: number | null;
  snapshot_id: string | null;
  last_price?: number;
  summary?: any;
  payload?: any;
  events?: any[];
  entry_fill?: number | null;
  exit_price?: number | null;
  expires_at?: string | null;
  closed_at?: string | null;
  mfe_r?: number | null;
  mae_r?: number | null;
  live_pnl_pct?: number | null;
  live_r?: number | null;
  realized_pnl_pct?: number | null;
  distance_to_entry_pct?: number | null;
};

export const TIMEFRAMES = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d", "1w"];
