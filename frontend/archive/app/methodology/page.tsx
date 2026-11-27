"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export default function Methodology() {
  const [m, setM] = useState<any>(null);
  useEffect(() => { api("/methodology").then(setM); }, []);
  return (
    <div className="h-full max-w-4xl space-y-3 overflow-auto p-6">
      <h1 className="text-xl font-semibold text-slate-100">Methodology &amp; performance calculation</h1>
      <p className="text-slate-400">How signals are produced, tracked and measured. This platform is a research and decision-support tool. It does not place orders, and no result shown is a promise of future performance.</p>
      {m?.sections.map((s: any) => <div key={s.title} className="card p-4"><h2 className="mb-1 font-medium text-slate-100">{s.title}</h2><p className="text-slate-300">{s.body}</p></div>)}
      <div className="card p-4 text-slate-300"><h2 className="mb-1 font-medium text-slate-100">Architecture principle</h2>
        Deterministic code computes every price, indicator and pattern. Machine-learning models provide probabilities only after passing walk-forward validation. Language models only explain structured evidence and operate chart tools through validated schemas — they never invent numbers.</div>
      <p className="text-[11px] text-slate-600">Charts by TradingView Lightweight Charts™ (Apache-2.0). Market data © Binance public API. News via publisher RSS feeds. Fear &amp; Greed index by alternative.me.</p>
    </div>
  );
}
