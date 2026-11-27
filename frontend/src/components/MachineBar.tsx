"use client";
import { ChevronDown, ChevronRight, Cpu, FlaskConical } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

type Factor = { name: string; doc: string; value: number; percentile: number | null; ic7: number; t: number | null; favours: "long" | "short" | null };
type Machine = {
  symbol: string; signal: { score: number; direction: string; strength: number; decision?: string; decision_why?: string; agreement: number;
    contributions: { component: string; score: number; weight: number; why: string }[] } | null;
  lab_rank: { rank: number; of: number; side: string; promoted: boolean; data_through: string } | null;
  factors: Factor[]; order_flow?: { cvd_pct_of_volume: number | null; taker_buy_pct: number | null; divergence: string | null };
  funding?: { oi_weighted_annualized_pct: number | null; most_crowded: { venue: string; annualized_pct: number; paying: string } | null };
  models: Record<string, boolean | null>;
};
const LABEL: Record<string, string> = {
  rvol_30: "Volatility", corr_btc_60: "BTC correlation", amihud_30: "Illiquidity", dist_high_20: "Near 20d high", dist_high_90: "Near 90d high",
  mom_90: "90d momentum", vol_trend: "Volume trend", funding_chg_7: "Funding change",
};
const tone = (side?: string | null) => side === "long" || side === "bullish" ? "text-up" : side === "short" || side === "bearish" ? "text-down" : "text-slate-400";

function Cell({ k, v, sub, pct, side, title }: { k: string; v: React.ReactNode; sub?: string; pct?: number | null; side?: string | null; title?: string }) {
  return (
    <div className="min-w-[92px] shrink-0 border-l border-line px-3 first:border-l-0" title={title}>
      <div className="whitespace-nowrap text-[11px] text-slate-500">{k}</div>
      <div className={`num whitespace-nowrap text-[13px] font-semibold ${side ? tone(side) : "text-slate-100"}`}>{v}</div>
      {pct != null ? <div className="mt-1 h-[3px] w-full rounded-full bg-fill"><div className={`h-full rounded-full ${side === "long" ? "bg-up" : side === "short" ? "bg-down" : "bg-slate-500"}`} style={{ width: `${pct}%` }} /></div>
        : sub ? <div className="whitespace-nowrap text-[10.5px] text-slate-500">{sub}</div> : null}
    </div>
  );
}

/** The machine side for one coin in a single line: the fused signal, the measured factors (percentile in today's
 *  universe, the side their evidence favours), order flow, funding, and which models are accepted. Expand for details. */
export default function MachineBar({ symbol, tf = "4h" }: { symbol: string; tf?: string }) {
  const [m, setM] = useState<Machine | null>(null);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    setM(null);
    let stop = false;
    api<Machine>(`/agent/machine?symbol=${symbol}&tf=${tf}`).then((x) => { if (!stop) setM(x); }).catch(() => undefined);
    return () => { stop = true; };
  }, [symbol, tf]);
  const s = m?.signal;
  const accepted = m ? Object.values(m.models).filter(Boolean).length : 0;
  return (
    <div className="border-b border-line bg-panel">
      <div className="flex items-center overflow-x-auto py-2 pl-3 pr-2">
        <button onClick={() => setOpen(!open)} className="mr-2 flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-[12px] font-semibold text-slate-200 hover:bg-fill" title="Chart and factor readings for this chart (the older code score, not the trained model). The trained model's call is the Best signal.">
          <Cpu size={14} className="text-accent" />Chart & factors{open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        </button>
        {!m && <span className="text-[12px] text-slate-500">measuring…</span>}
        {m && (<>
          <Cell k="Chart score" v={s ? `${s.direction} ${s.strength}` : "—"} side={s?.direction} sub={s?.decision ? `setup: ${s.decision}` : undefined} title={s?.decision_why} />
          <Cell k="Factor rank" v={m.lab_rank ? `${m.lab_rank.rank} / ${m.lab_rank.of}` : "—"} side={m.lab_rank?.side}
            pct={m.lab_rank ? Math.round(100 * (1 - (m.lab_rank.rank - 1) / Math.max(m.lab_rank.of - 1, 1))) : null}
            title={m.lab_rank ? `quant lab composite, data to ${m.lab_rank.data_through}${m.lab_rank.promoted ? "" : " (not promoted: ranks coins out of sample, not yet profitable after costs)"}` : "not in the lab's universe"} />
          {m.factors.slice(0, 5).map((f) => (
            <Cell key={f.name} k={LABEL[f.name] ?? f.name} v={f.percentile != null ? `${f.percentile}th` : "—"} pct={f.percentile} side={f.favours}
              title={`${f.doc}. Percentile in today's universe; measured IC with 7-day returns ${f.ic7 > 0 ? "+" : ""}${f.ic7} (t ${f.t}) → favours ${f.favours}`} />
          ))}
          <Cell k="Order flow" v={m.order_flow?.cvd_pct_of_volume != null ? `${m.order_flow.cvd_pct_of_volume > 0 ? "+" : ""}${m.order_flow.cvd_pct_of_volume}%` : "—"}
            side={(m.order_flow?.cvd_pct_of_volume ?? 0) > 0 ? "long" : (m.order_flow?.cvd_pct_of_volume ?? 0) < 0 ? "short" : null} sub={m.order_flow?.divergence ?? undefined}
            title="taker buying minus selling over 24 candles, % of volume" />
          <Cell k="Funding / yr" v={m.funding?.oi_weighted_annualized_pct != null ? `${m.funding.oi_weighted_annualized_pct > 0 ? "+" : ""}${m.funding.oi_weighted_annualized_pct.toFixed(1)}%` : "—"}
            sub={m.funding?.most_crowded ? `${m.funding.most_crowded.paying} pay most on ${m.funding.most_crowded.venue}` : undefined} title="open-interest-weighted across Binance, Bybit and OKX" />
          <Cell k="Signal models" v={`${accepted} / ${Object.keys(m.models).length}`} sub="voting in the signal" title={"Models allowed to vote in the fused signal (the forecast models are shown separately below).\n" + Object.entries(m.models).map(([k, v]) => `${k}: ${v ? "accepted" : "not accepted"}`).join("\n")} />
          <Link href={`/simulator?coin=${symbol}`} className="ml-auto flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-[12px] font-medium text-slate-300 hover:bg-fill"
            title="Replay this signal on the coin's history, test it against luck and simulate the next year"><FlaskConical size={14} />Simulate</Link>
        </>)}
      </div>
      {open && m && (
        <div className="grid gap-6 border-t border-line bg-panel2 px-4 py-4 text-[12.5px] md:grid-cols-2">
          <div>
            <div className="mb-2 font-semibold text-slate-200">Final signal — every part, weighted by its evidence</div>
            {s?.contributions.map((c) => (
              <div key={c.component} className="grid grid-cols-[110px_1fr_70px] items-center gap-3 py-0.5">
                <span className="capitalize text-slate-300">{c.component.replace("_", " ")}</span>
                <div className="relative h-1.5 rounded-full bg-fill" title={c.why}>
                  <span className="absolute top-0 h-1.5 rounded-full" style={{ left: c.score >= 0 ? "50%" : `${50 + c.score * 50}%`, width: `${Math.abs(c.score) * 50}%`,
                    background: c.weight > 0 ? (c.score >= 0 ? "var(--color-up)" : "var(--color-down)") : "#cbd2d9" }} />
                  <span className="absolute left-1/2 top-[-2px] h-2.5 w-px bg-slate-600" />
                </div>
                <span className="num text-right text-slate-500">{c.weight > 0 ? `w ${c.weight.toFixed(2)}` : "no weight"}</span>
              </div>))}
            {s?.decision_why && <p className="mt-2 text-slate-500">{s.decision_why}</p>}
          </div>
          <div>
            <div className="mb-2 font-semibold text-slate-200">Measured factors (quant lab) — percentile in today&apos;s universe</div>
            {m.factors.map((f) => (
              <div key={f.name} className="grid grid-cols-[130px_1fr_90px] items-center gap-3 py-0.5" title={f.doc}>
                <span className="text-slate-300">{LABEL[f.name] ?? f.name}</span>
                <div className="h-1.5 rounded-full bg-fill"><div className={`h-full rounded-full ${f.favours === "long" ? "bg-up" : "bg-down"}`} style={{ width: `${f.percentile ?? 0}%` }} /></div>
                <span className={`num text-right ${tone(f.favours)}`}>{f.percentile}th · {f.favours}</span>
              </div>))}
            <p className="mt-2 text-slate-500">These rank coins against each other (rank IC with 7-day returns, out of sample). Human view: the chart and the AI desk.</p>
          </div>
        </div>
      )}
    </div>
  );
}
