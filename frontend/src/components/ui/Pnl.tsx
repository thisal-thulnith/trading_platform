"use client";
import { useStore } from "@/lib/store";
import type { SignalRow } from "@/lib/types";

/** Coloured percentage, e.g. +1.24% */
export function Pct({ v, r, className = "" }: { v: number | null | undefined; r?: number | null; className?: string }) {
  if (v === null || v === undefined || !isFinite(v)) return <span className="text-slate-600">—</span>;
  const c = v > 0 ? "text-up" : v < 0 ? "text-down" : "text-slate-400";
  return (
    <span className={`num ${c} ${className}`}>
      {v > 0 ? "+" : ""}{v.toFixed(2)}%{r !== undefined && r !== null && isFinite(r) && <span className="ml-1 text-[10px] opacity-75">({r > 0 ? "+" : ""}{r.toFixed(2)}R)</span>}
    </span>
  );
}

/** Live P&L of a signal: open trades tick with the live price feed; closed trades show the realised result. */
export function SignalPnl({ s }: { s: SignalRow }) {
  const live = useStore((st) => st.prices[s.symbol]) ?? s.last_price;
  const sgn = s.direction === "LONG" ? 1 : -1;
  const fill = s.entry_fill;
  if (s.state === "ACTIVE" && fill && live) {
    const risk = Math.abs(fill - s.stop);
    return <Pct v={(sgn * (live - fill) / fill) * 100} r={risk ? (sgn * (live - fill)) / risk : null} className="font-semibold" />;
  }
  if (s.r_multiple !== null && s.r_multiple !== undefined) {
    const f = fill || (s.entry_low + s.entry_high) / 2;
    return <Pct v={(s.r_multiple * Math.abs(f - s.stop) / f) * 100} r={s.r_multiple} />;
  }
  if (["CONFIRMED", "PRE_SIGNAL", "FORMING"].includes(s.state) && live) {
    const edge = sgn > 0 ? s.entry_high : s.entry_low;
    const d = (live / edge - 1) * 100;
    return <span className="num text-[11px] text-slate-500" title="distance from current price to the entry zone">{Math.abs(d) < 0.005 ? "in zone" : `${d > 0 ? "+" : ""}${d.toFixed(2)}% to entry`}</span>;
  }
  return <span className="text-slate-600">—</span>;
}
