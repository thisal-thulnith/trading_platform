"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { ago } from "@/lib/format";

export default function News() {
  const [d, setD] = useState<any>(null);
  const [coin, setCoin] = useState("");
  const [impact, setImpact] = useState("");
  useEffect(() => {
    const q = new URLSearchParams({ limit: "200", ...(coin && { coin }), ...(impact && { impact }) });
    api(`/news?${q}`).then(setD).catch(() => {});
  }, [coin, impact]);
  return (
    <div className="h-full overflow-auto p-4">
      <div className="mb-3 flex items-center gap-3">
        <h1 className="text-lg font-semibold text-slate-100">News intelligence</h1>
        {d?.fear_greed && <span className="chip bg-slate-700/60 text-slate-200">Fear &amp; Greed {d.fear_greed.value} · {d.fear_greed.label}</span>}
        <input className="input ml-auto w-24" placeholder="coin" value={coin} onChange={(e) => setCoin(e.target.value.toUpperCase())} />
        <select className="input" value={impact} onChange={(e) => setImpact(e.target.value)}><option value="">all impact</option><option>high</option><option>medium</option><option>low</option></select>
      </div>
      <div className="mb-2 text-[11px] text-slate-500">{d?.method} Correlation with price moves is not causation; headlines never trigger trades.</div>
      <div className="card divide-y divide-line">
        {d?.items.map((n: any) => (
          <a key={n.id} href={n.url} target="_blank" rel="noreferrer" className="block p-2 hover:bg-white/5">
            <div className="flex items-center gap-2 text-[11px]">
              <span className={`chip ${n.impact === "high" ? "bg-red-500/20 text-red-300" : n.impact === "medium" ? "bg-amber-500/15 text-amber-300" : "bg-slate-600/30 text-slate-400"}`}>{n.impact}</span>
              <span className="chip bg-slate-700/50">{n.event_type}</span>
              <span className={n.sentiment > 0.2 ? "text-up" : n.sentiment < -0.2 ? "text-down" : "text-slate-500"}>sent {n.sentiment.toFixed(2)}</span>
              <span className="text-slate-500">cred {n.credibility.toFixed(2)} · {n.corroborations} source(s)</span>
              {n.coins.map((c: string) => <span key={c} className="chip bg-sky-500/10 text-sky-300">{c}</span>)}
              <span className="ml-auto text-slate-500">{n.source} · {ago(n.published_at)}</span>
            </div>
            <div className="mt-0.5 text-slate-200">{n.title}</div>
            <div className="text-[12px] text-slate-500">{n.summary}</div>
          </a>
        ))}
      </div>
    </div>
  );
}
