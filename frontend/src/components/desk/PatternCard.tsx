"use client";
import { Shapes } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { pair } from "@/lib/format";

type Rates = Record<"direction_7d" | "long_tp_first" | "short_tp_first" | "drop_risk_7d", number>;
type Ex = { symbol: string; date: string; fwd7_pct: number; path: number[]; split: number };
type P = { available: boolean; covered?: boolean; note?: string; as_of?: string; charts_learned?: number;
  base_rates?: Rates; labels?: Record<string, string>; analog?: Rates; analog_up_share?: number; analog_median_fwd7_pct?: number;
  analog_p10_p90_fwd7_pct?: [number, number]; path?: number[]; examples?: Ex[];
  pattern?: { name: string; count: number; rates: Rates; shape: number[]; median_fwd7_pct: number };
  accuracy?: Record<string, { analog_auc: number; pattern_auc: number; analog_top_decile: number; analog_bottom_decile: number }> };

const ORDER = ["direction_7d", "long_tp_first", "short_tp_first", "drop_risk_7d"] as const;
const SHORT: Record<string, string> = { direction_7d: "Up after 7 days", long_tp_first: "Long hit 2R first",
  short_tp_first: "Short hit 2R first", drop_risk_7d: "2-ATR drop in 7 days" };

/** A small line chart; `split` marks where "today" is (the rest is what happened after). */
function Spark({ v, split, w = 150, h = 40, color = "#0a84ff" }: { v: number[]; split?: number; w?: number; h?: number; color?: string }) {
  if (!v?.length) return null;
  const lo = Math.min(...v), hi = Math.max(...v), r = hi - lo || 1;
  const pts = v.map((y, i) => [(i / (v.length - 1)) * w, h - 2 - ((y - lo) / r) * (h - 4)] as const);
  const cut = split && split < v.length ? split : v.length;
  const d = (a: readonly (readonly [number, number])[]) => a.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  return (
    <svg width={w} height={h} className="block">
      <path d={d(pts.slice(0, cut))} fill="none" stroke={color} strokeWidth={1.5} />
      {cut < v.length && <>
        <line x1={pts[cut - 1][0]} x2={pts[cut - 1][0]} y1={0} y2={h} stroke="#94a3b8" strokeDasharray="2 2" strokeWidth={1} />
        <path d={d(pts.slice(cut - 1))} fill="none" stroke={v[v.length - 1] >= v[cut - 1] ? "#16a34a" : "#dc2626"} strokeWidth={1.5} />
      </>}
    </svg>
  );
}

const pct = (x?: number) => (x === undefined || x === null ? "—" : `${Math.round(x * 100)}%`);

/** Patterns learned from every coin's chart since 2019: the closest past charts and what followed, and the learned
 *  pattern this chart belongs to. Each rate is shown against how often it happens for any chart. */
export default function PatternCard({ symbol }: { symbol: string | null }) {
  const [p, setP] = useState<P | null>(null);
  useEffect(() => {
    if (!symbol) return;
    let stop = false;
    setP(null);
    api<P>(`/agent/patterns?symbol=${symbol}`).then((x) => !stop && setP(x)).catch(() => !stop && setP({ available: false, note: "patterns unavailable" }));
    return () => { stop = true; };
  }, [symbol]);
  if (!symbol) return null;
  return (
    <div className="rounded-lg border border-line bg-white/[0.02] p-3 text-[12px]">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <Shapes size={14} className="text-violet-400" />
        <b className="text-slate-100">Learned chart patterns · {pair(symbol)}</b>
        <span className="text-[11px] text-slate-500">from {p?.charts_learned ? `${(p.charts_learned / 1000).toFixed(0)}k` : "every"} past charts of every coin{p?.as_of ? ` · data to ${p.as_of}` : ""} · no AI key needed</span>
      </div>
      {!p && <div className="text-slate-500">Searching past charts…</div>}
      {p && (!p.available || !p.covered) && <div className="text-slate-400">{p.note ?? "No pattern data for this coin."}</div>}
      {p?.available && p.covered && p.pattern && p.analog && p.base_rates && (
        <div className="space-y-2">
          <div className="grid gap-2 md:grid-cols-2">
            <div className="rounded-md bg-white/5 p-2">
              <div className="text-[10px] uppercase tracking-wider text-slate-500">This chart · last 30 days</div>
              <Spark v={p.path ?? []} w={220} h={44} />
              <div className="mt-1 text-[11px] text-slate-400">Learned pattern: <b className="text-slate-200">{p.pattern.name}</b> · seen {p.pattern.count.toLocaleString()} times</div>
            </div>
            <div className="rounded-md bg-white/5 p-2">
              <div className="text-[10px] uppercase tracking-wider text-slate-500">After the 50 most similar past charts</div>
              <div className="mt-1 text-[13px] text-slate-200">{pct(p.analog_up_share)} went up within 7 days · median {p.analog_median_fwd7_pct! >= 0 ? "+" : ""}{p.analog_median_fwd7_pct}%</div>
              <div className="text-[11px] text-slate-400">most moves between {p.analog_p10_p90_fwd7_pct?.[0]}% and +{p.analog_p10_p90_fwd7_pct?.[1]}%</div>
            </div>
          </div>
          <table className="w-full text-[11px]">
            <thead className="text-left text-[10px] uppercase tracking-wider text-slate-500">
              <tr><th className="py-1">Outcome</th><th>50 similar charts</th><th>Its pattern</th><th>Any chart</th><th title="AUC of the analogs on years they never saw">Tested</th></tr>
            </thead>
            <tbody>
              {ORDER.map((k) => {
                const a = p.analog![k], b = p.base_rates![k], pt = p.pattern!.rates[k];
                const better = k === "drop_risk_7d" ? a < b - 0.03 : a > b + 0.03;
                const worse = k === "drop_risk_7d" ? a > b + 0.03 : a < b - 0.03;
                return (
                  <tr key={k} className="border-t border-line/60">
                    <td className="py-1 text-slate-300">{SHORT[k]}</td>
                    <td className={better ? "font-semibold text-emerald-400" : worse ? "font-semibold text-red-400" : "text-slate-200"}>{pct(a)}</td>
                    <td className="text-slate-300">{pct(pt)}</td>
                    <td className="text-slate-500">{pct(b)}</td>
                    <td className="text-slate-500">AUC {p.accuracy?.[k]?.analog_auc ?? "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {p.examples && p.examples.length > 0 && (
            <div>
              <div className="mb-1 text-[10px] uppercase tracking-wider text-slate-500">The closest past charts — and what happened next (after the dashed line)</div>
              <div className="flex flex-wrap gap-2">
                {p.examples.map((e) => (
                  <div key={`${e.symbol}${e.date}`} className="rounded-md bg-white/5 p-1.5">
                    <Spark v={e.path} split={e.split} w={120} h={34} color="#7d5cf6" />
                    <div className="text-[10px] text-slate-400">{pair(e.symbol)} · {e.date} · <span className={e.fwd7_pct >= 0 ? "text-emerald-400" : "text-red-400"}>{e.fwd7_pct >= 0 ? "+" : ""}{e.fwd7_pct}% in 7d</span></div>
                  </div>
                ))}
              </div>
            </div>
          )}
          <div className="text-[10px] text-slate-500">Learned, not hand-written: shapes and outcomes come from the data, and each rate was tested on years the model never saw. Similar charts are evidence of what usually followed, not a promise.</div>
        </div>
      )}
    </div>
  );
}
