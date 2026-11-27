export function fmtPrice(v: number | null | undefined): string {
  if (v === null || v === undefined || !isFinite(v)) return "—";
  const a = Math.abs(v);
  const d = a >= 1000 ? 2 : a >= 1 ? 4 : a >= 0.01 ? 5 : 8;
  return v.toLocaleString(undefined, { minimumFractionDigits: Math.min(d, 2), maximumFractionDigits: d });
}
export const fmtPct = (v: number | null | undefined, d = 2) =>
  v === null || v === undefined || !isFinite(v) ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(d)}%`;
export const fmtNum = (v: number | null | undefined, d = 2) =>
  v === null || v === undefined || !isFinite(v) ? "—" : v.toLocaleString(undefined, { maximumFractionDigits: d });
export const fmtCompact = (v: number | null | undefined) =>
  v === null || v === undefined || !isFinite(v) ? "—" : Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 2 }).format(v);
export function ago(ts: string | number | null | undefined): string {
  if (!ts) return "—";
  const t = typeof ts === "number" ? ts : Date.parse(ts.endsWith("Z") || ts.includes("+") ? ts : ts + "Z");
  const s = (Date.now() - t) / 1000;
  if (s < 60) return `${Math.max(0, Math.floor(s))}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}
export const pair = (s?: string | null) => (!s ? "" : s.endsWith("USDT") ? `${s.slice(0, -4)}/USDT` : s);
export const dirColor = (d: string | number) =>
  d === "LONG" || d === "bullish" || (typeof d === "number" && d > 0) ? "text-up" : d === "SHORT" || d === "bearish" || (typeof d === "number" && d < 0) ? "text-down" : "text-muted";
export const stateColor = (s: string) =>
  ({
    FORMING: "bg-sky-500/15 text-sky-300",
    PRE_SIGNAL: "bg-amber-500/15 text-amber-300",
    CONFIRMED: "bg-violet-500/20 text-violet-300",
    ACTIVE: "bg-blue-500/20 text-blue-300",
    TARGET_HIT: "bg-emerald-500/20 text-emerald-300",
    TP1_THEN_STOP: "bg-emerald-500/10 text-emerald-200",
    TP2_THEN_STOP: "bg-emerald-500/15 text-emerald-200",
    STOP_HIT: "bg-red-500/20 text-red-300",
    INVALIDATED: "bg-zinc-500/20 text-zinc-300",
    EXPIRED: "bg-zinc-500/20 text-zinc-400",
    TIME_EXIT: "bg-zinc-500/20 text-zinc-300",
  } as Record<string, string>)[s] || "bg-zinc-500/20 text-zinc-300";
