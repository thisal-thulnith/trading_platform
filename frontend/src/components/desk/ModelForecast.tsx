"use client";
import { Cpu } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { pair } from "@/lib/format";

type M = { target: string; label: string; what: string; status: "accepted" | "weak" | "rejected"; why: string; accuracy: string;
  base_rate: number | null; value: number | null; kind: "binary" | "regression" };
type F = { available: boolean; covered?: boolean; symbol?: string; as_of?: string; trained_at?: string; note?: string | null;
  tier?: "core" | "wide"; tier_note?: string | null;
  atr_pct?: number | null; plan?: { stop_atr: number; target_atr: number; days: number }; models?: M[] };

const STATUS: Record<string, string> = { accepted: "bg-emerald-500/15 text-emerald-300", weak: "bg-amber-500/15 text-amber-300",
  rejected: "bg-white/5 text-slate-500" };
// for these a higher number is bad news for the trader
const BAD_HIGH = new Set(["drop_risk_7d"]);

function value(m: M) {
  if (m.value === null || m.status === "rejected") return { txt: "—", sub: m.status === "rejected" ? "no edge: not shown" : "", tone: "text-slate-500" };
  if (m.kind === "regression") return { txt: `±${(m.value / 2).toFixed(1)}%`, sub: `${m.value.toFixed(1)}% high-to-low`, tone: "text-slate-100" };
  const v = m.value, b = m.base_rate ?? 0.5, diff = v - b;
  const good = BAD_HIGH.has(m.target) ? diff < 0 : diff > 0;
  const big = Math.abs(diff) >= 0.03;
  return { txt: `${Math.round(v * 100)}%`, sub: `usually ${Math.round(b * 100)}%`, tone: !big ? "text-slate-200" : good ? "text-emerald-300" : "text-red-300" };
}

/** The platform's own trained models for one coin: no AI key, no cost. Each forecast is shown against how often the
 *  outcome usually happens, with the model's status and accuracy measured on years it never saw. */
export default function ModelForecast({ symbol, compact }: { symbol: string | null; compact?: boolean }) {
  const [f, setF] = useState<F | null>(null);
  useEffect(() => {
    if (!symbol) return;
    let stop = false;
    setF(null);
    api<F>(`/agent/forecast?symbol=${symbol}`).then((x) => !stop && setF(x)).catch(() => !stop && setF({ available: false, note: "forecast unavailable" }));
    return () => { stop = true; };
  }, [symbol]);
  if (!symbol) return null;
  return (
    <div className="rounded-lg border border-line bg-white/[0.02] p-3 text-[12px]">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <Cpu size={14} className="text-sky-300" />
        <b className="text-slate-100">Trained models · {pair(symbol)}</b>
        <span className="text-[11px] text-slate-500">no AI key needed{f?.as_of ? ` · data to ${f.as_of}` : ""}{f?.trained_at ? ` · trained ${f.trained_at.slice(0, 10)}` : ""}</span>
        <a href={`/signal?symbol=${symbol}`} className="ml-auto rounded-md bg-accent-soft px-2 py-0.5 text-[11px] font-semibold text-accent hover:opacity-80">Full trade plan →</a>
      </div>
      {!f && <div className="text-slate-500">Loading the models…</div>}
      {f && (!f.available || !f.covered) && <div className="text-slate-400">{f.note ?? "No forecast for this coin."}</div>}
      {f?.available && f.covered && f.models && (
        <>
          {f.tier_note && <div className="mb-2 rounded bg-amber-500/10 px-2 py-1 text-[11px] text-amber-300">{f.tier_note}</div>}
          <div className={`grid gap-1.5 ${"grid-cols-2 md:grid-cols-3"}`}>
            {f.models.map((m) => {
              const v = value(m);
              return (
                <div key={m.target} className="rounded-md bg-white/5 p-2" title={`${m.what}. ${m.why}. ${m.accuracy}`}>
                  <div className="flex items-center gap-1">
                    <span className="truncate text-[10px] uppercase tracking-wider text-slate-500">{m.label}</span>
                    <span className={`chip ml-auto shrink-0 text-[9px] ${STATUS[m.status]}`}>{m.status}</span>
                  </div>
                  <div className={`text-[16px] font-semibold ${v.tone}`}>{v.txt}</div>
                  <div className="text-[10px] text-slate-500">{v.sub}</div>
                </div>
              );
            })}
          </div>
          {!compact && (
            <details className="mt-2 text-[11px] text-slate-400">
              <summary className="cursor-pointer text-slate-500 hover:text-slate-300">How accurate is each model? (tested on 2020–2026, years it never saw)</summary>
              <ul className="mt-1 space-y-0.5 pl-1">
                {f.models.map((m) => (
                  <li key={m.target}><b className="text-slate-300">{m.label}</b> — {m.what}. <span className={m.status === "accepted" ? "text-emerald-300/80" : m.status === "weak" ? "text-amber-300/80" : "text-slate-500"}>{m.why}.</span> {m.accuracy}.</li>
                ))}
              </ul>
              {f.plan && <div className="mt-1 text-slate-500">The target-first models test a trade with the stop {f.plan.stop_atr} ATR and the target {f.plan.target_atr} ATR away (2R), held up to {f.plan.days} days{f.atr_pct ? `; this coin's ATR is ${f.atr_pct}% of the price` : ""}.</div>}
            </details>
          )}
        </>
      )}
    </div>
  );
}
