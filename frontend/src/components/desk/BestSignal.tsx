"use client";
import { ArrowRight, Loader2 } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { fmtPrice, pair } from "@/lib/format";
import type { AgentVerdict, Final, Simulation } from "@/components/signal/ThreeViews";
import TakeSignal from "@/components/portfolio/TakeSignal";

type Row = { leverage: number; stop_usd: number; tp1_usd: number; status: string };
type Part = { entry: number; stop: number; stop_pct: number; tp1: number; tp1_pct: number; predicted_r?: number;
  leverage: { safe_max: number; rows: Row[] } };
type CoinPlan = { available: boolean; covered?: boolean; engine?: string; note?: string; symbol: string; side: "LONG" | "SHORT";
  status: "trade" | "watch"; entry_type: "limit" | "market"; plans?: { swing: Part }; simulation?: Simulation | null;
  clear?: boolean; unclear_note?: string | null; live_state?: { state: "valid" | "stop_hit" | "target_hit" | "ran_away" | "unknown"; text: string } | null; live_since_signal?: { move_pct: number | null; r: number | null; from: number; at: string } | null;
  agents?: AgentVerdict | null; final?: Final | null };

const TONE: Record<string, string> = { good: "border-emerald-500/30 bg-emerald-500/10 text-emerald-200", warn: "border-amber-500/30 bg-amber-500/10 text-amber-200",
  bad: "border-red-500/30 bg-red-500/10 text-red-200", neutral: "border-line bg-fill text-slate-300" };
const side = (s?: string | null) => (s === "LONG" ? "bg-emerald-500/15 text-emerald-300" : s === "SHORT" ? "bg-red-500/15 text-red-300" : "bg-slate-500/20 text-slate-300");
const money = (v: number) => `${v < 0 ? "−" : "+"}$${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: Math.abs(v) < 10 ? 2 : 0 })}`;

/** The best signal for one coin: the trained model's plan, how its past picks like this did, and the AI agents' latest
 *  verdict, side by side with one plain reading. The model decides; the agents advise (until Live results says
 *  otherwise). Refetches when `refresh` changes, e.g. after a desk run finishes and its verdict is saved. */
export default function BestSignal({ symbol, margin, refresh }: { symbol: string; margin: number; refresh?: string | number | null }) {
  const [p, setP] = useState<CoinPlan | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    let stop = false;
    setErr("");
    const get = () => api<CoinPlan>(`/agent/signal/plan?symbol=${symbol}&margin=${margin}`).then((x) => !stop && setP(x)).catch((e) => !stop && setErr(String(e.message || e)));
    get();
    const i = setInterval(get, 30_000);   // live: price, levels, since-signal result and the live check
    return () => { stop = true; clearInterval(i); };
  }, [symbol, margin, refresh]);

  if (err) return null;
  if (!p || p.symbol !== symbol) return <div className="flex items-center gap-2 rounded-2xl border border-line bg-panel px-4 py-3 text-[12.5px] text-slate-500"><Loader2 size={14} className="animate-spin" /> Best signal for {pair(symbol)}…</div>;
  if (!p.available || !p.covered || p.engine !== "unified" || !p.plans) {
    return (
      <div className="rounded-2xl border border-line bg-panel px-4 py-3 text-[12.5px] text-slate-400">
        <b className="text-slate-200">Best signal · {pair(symbol)}</b> — the trained model covers liquid coins only, so here the AI agents are the only opinion. Press Analyse.
      </div>
    );
  }
  if (p.clear === false) {
    return (
      <div className="rounded-2xl border border-line bg-panel p-4">
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-[12px] font-semibold uppercase tracking-wider text-slate-500">Best signal</span>
          <span className="rounded-lg bg-slate-500/15 px-2.5 py-0.5 text-[13px] font-bold text-slate-400">NO CLEAR SIDE</span>
          <b className="text-[15px] text-slate-100">{pair(symbol)}</b>
        </div>
        <p className="mt-2 text-[12.5px] text-slate-400">{p.unclear_note}</p>
        <p className="mt-1 text-[12px] text-slate-500">The AI agents can still give their own view: ask them below.</p>
      </div>
    );
  }
  const sw = p.plans.swing;
  const lv = p.live_since_signal;
  const safe = Math.min(sw.leverage.safe_max, 20);
  const row = [...sw.leverage.rows].filter((r) => r.leverage <= safe).sort((a, b) => b.leverage - a.leverage)[0];
  const f = p.final;
  const v = p.agents && !p.agents.stale ? p.agents : null;
  const past = p.simulation?.side_stats?.unseen;
  return (
    <div className="rounded-2xl border border-line bg-panel p-4">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-[12px] font-semibold uppercase tracking-wider text-slate-500">Best signal</span>
        <span className={`rounded-lg px-2.5 py-0.5 text-[15px] font-black ${side(p.side)}`}>{p.side}</span>
        <b className="text-[15px] text-slate-100">{pair(symbol)}</b>
        <span className={`chip ${p.status === "trade" ? "bg-emerald-500/15 text-emerald-300" : "bg-amber-500/15 text-amber-300"}`}>{p.status === "trade" ? "good to trade" : "watch only"}</span>
        <Link href={`/signal?symbol=${symbol}`} className="ml-auto flex items-center gap-1 text-[12.5px] text-accent hover:underline">Full plan <ArrowRight size={13} /></Link>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-x-6 gap-y-1 text-[12.5px] sm:grid-cols-4">
        <div><div className="text-slate-500">{p.entry_type === "limit" ? (p.side === "LONG" ? "Buy limit" : "Sell limit") : (p.side === "LONG" ? "Buy at" : "Sell at")}</div><div className="num font-semibold text-slate-100">{fmtPrice(sw.entry)}</div></div>
        <div><div className="text-slate-500">Stop loss</div><div className="num font-semibold text-red-300">{fmtPrice(sw.stop)} <span className="text-[11px] font-normal text-slate-500">{sw.stop_pct > 0 ? "+" : ""}{sw.stop_pct}%</span></div></div>
        <div><div className="text-slate-500">Take profit</div><div className="num font-semibold text-emerald-300">{fmtPrice(sw.tp1)} <span className="text-[11px] font-normal text-slate-500">{sw.tp1_pct > 0 ? "+" : ""}{sw.tp1_pct}%</span></div></div>
        <div><div className="text-slate-500">Leverage</div><div className="num font-semibold text-slate-100">up to {safe}x</div></div>
      </div>
      {lv && lv.move_pct !== null && <div className="mt-2 text-[12.5px] text-slate-400" title={`As if taken at the model's daily close ${fmtPrice(lv.from)} (${lv.at}). Display only.`}>
        Since the signal: <b className={lv.move_pct > 0 ? "text-emerald-300" : lv.move_pct < 0 ? "text-red-300" : "text-slate-200"}>{lv.move_pct > 0 ? "+" : ""}{lv.move_pct}%</b>
        {lv.r !== null && <span className="text-slate-500"> ({lv.r > 0 ? "+" : ""}{lv.r.toFixed(2)}R)</span>}
        {row && lv.r !== null && <span className="text-slate-500"> · on ${margin} at {row.leverage}x about <b className={lv.r >= 0 ? "text-emerald-300" : "text-red-300"}>{money(lv.r * Math.abs(row.stop_usd))}</b></span>}</div>}
      {row && <div className="mt-1 text-[12.5px] text-slate-400">On ${margin} at {row.leverage}x: <b className="text-emerald-300">{money(row.tp1_usd)}</b> if it works, <b className="text-red-300">{money(row.stop_usd)}</b> if stopped.</div>}
      <div className="mt-3 grid gap-2 sm:grid-cols-3">
        <div className="rounded-xl bg-fill px-3 py-2 text-[12px]"><div className="text-slate-500">Trained model</div><div className="font-semibold text-slate-100">{p.side === "LONG" ? "Long" : "Short"} · {p.status === "trade" ? "take it" : "watch"}</div></div>
        <div className="rounded-xl bg-fill px-3 py-2 text-[12px]" title="The model's past picks on this side, on years it never saw (2025–26)"><div className="text-slate-500">Past similar trades</div>
          <div className="font-semibold text-slate-100">{past?.n ? `won ${Math.round((past.win_rate ?? 0) * 100)}% of ${past.n}` : "—"}</div></div>
        <div className="rounded-xl bg-fill px-3 py-2 text-[12px]"><div className="text-slate-500">AI agents</div>
          <div className="font-semibold text-slate-100">{v ? `${v.action === "WAIT" ? "Wait" : v.action === "LONG" ? "Long" : v.action === "SHORT" ? "Short" : v.action}${v.confidence !== null ? ` · ${Math.round(v.confidence * 100)}% sure` : ""}` : p.agents?.stale ? "old — analyse again" : "not asked yet"}</div></div>
      </div>
      {p.live_state && ["stop_hit", "target_hit", "ran_away"].includes(p.live_state.state) &&
        <div className="mt-3 rounded-xl border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[12.5px] text-amber-200"><b>Not tradeable now.</b> {p.live_state.text}</div>}
      {f && <div className={`mt-3 rounded-xl border px-3 py-2 text-[12.5px] ${TONE[f.tone] ?? TONE.neutral}`}><b>{f.label}.</b> {f.text}</div>}
      <div className="mt-3"><TakeSignal symbol={symbol} side={p.side} levels={{ entry: sw.entry, stop: sw.stop, target: sw.tp1 }} agents={p.agents ?? null} compact /></div>
    </div>
  );
}
