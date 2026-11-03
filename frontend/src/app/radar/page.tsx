"use client";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { dirColor, fmtPrice, pair } from "@/lib/format";
import { useStore } from "@/lib/store";
import { live } from "@/lib/ws";

export default function Radar() {
  const [rows, setRows] = useState<any[]>([]);
  const [tf, setTf] = useState<string>("all");
  const router = useRouter();
  const { setSymbol, setTimeframe } = useStore();
  useEffect(() => {
    const load = () => api(`/radar${tf === "all" ? "" : `?tf=${tf}`}`).then((r) => setRows(r.rows)).catch(() => {});
    load();
    const off = live.on((t) => t === "scan" && load());
    const i = setInterval(load, 30000);
    return () => { off(); clearInterval(i); };
  }, [tf]);
  return (
    <div className="h-full overflow-auto p-4">
      <div className="mb-3 flex items-center gap-3">
        <h1 className="text-lg font-semibold text-slate-100">AI Opportunity Radar</h1>
        <span className="text-[12px] text-slate-500">Ranked by validated setup stage &amp; quality — not indicator count. Updated on every closed candle.</span>
        <div className="ml-auto flex gap-1">{["all", "15m", "1h", "4h"].map((t) => <button key={t} onClick={() => setTf(t)} className={`btn py-0.5 ${tf === t ? "btn-primary" : ""}`}>{t}</button>)}</div>
      </div>
      <div className="card overflow-hidden">
        <table className="grid-t">
          <thead><tr><th>#</th><th>Market</th><th>TF</th><th>Score</th><th>Decision</th><th>Best setup</th><th>Stage</th><th>Quality</th><th>Missing</th><th>Regime</th><th>Bias</th><th>Families ↑/↓</th><th>Price</th></tr></thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={r.symbol + r.timeframe} className="cursor-pointer" onClick={() => { setSymbol(r.symbol); setTimeframe(r.timeframe); router.push("/"); }}>
                <td className="text-slate-500">{i + 1}</td><td className="font-medium text-slate-100">{pair(r.symbol)}</td><td>{r.timeframe}</td>
                <td><div className="flex items-center gap-1"><div className="h-1.5 w-16 rounded bg-slate-800"><div className="h-full rounded bg-sky-500" style={{ width: `${Math.min(r.score, 100)}%` }} /></div><span className="num">{r.score}</span></div></td>
                <td className={r.decision === "LONG" ? "text-up" : r.decision === "SHORT" ? "text-down" : "text-slate-400"}>{r.decision.replace("_", " ")}</td>
                <td>{r.best ? <span className={dirColor(r.best.direction)}>{r.best.direction} <span className="text-slate-300">{r.best.strategy}</span></span> : "—"}</td>
                <td>{r.best ? <span className={`chip ${r.best.stage === "confirmed" ? (r.best.passed ? "bg-violet-500/20 text-violet-300" : "bg-red-500/15 text-red-300") : r.best.stage === "pre_signal" ? "bg-amber-500/15 text-amber-300" : "bg-sky-500/15 text-sky-300"}`}>{r.best.stage === "confirmed" && !r.best.passed ? "blocked" : r.best.stage}</span> : ""}</td>
                <td className="num">{r.best?.quality ?? "—"}</td>
                <td className="max-w-[220px] truncate text-slate-500">{r.best?.missing?.join(", ")}</td>
                <td className="text-slate-400">{r.regime.replaceAll("_", " ")}</td>
                <td className={`num ${dirColor(r.bias)}`}>{r.bias.toFixed(2)}</td>
                <td className="num"><span className="text-up">{r.families_bull}</span>/<span className="text-down">{r.families_bear}</span></td>
                <td className="num">{fmtPrice(r.price)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length === 0 && <div className="p-4 text-slate-500">Radar is warming up (first scans run right after start-up).</div>}
      </div>
    </div>
  );
}
