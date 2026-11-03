"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { ago, dirColor, fmtNum, fmtPrice, pair, stateColor } from "@/lib/format";
import type { SignalRow } from "@/lib/types";
import { live } from "@/lib/ws";
import { SignalPnl } from "@/components/ui/Pnl";

const FILTERS: Record<string, string> = {
  "Pre-signal alerts": "FORMING,PRE_SIGNAL", Confirmed: "CONFIRMED,ACTIVE",
  Resolved: "TARGET_HIT,STOP_HIT,TP1_THEN_STOP,TP2_THEN_STOP,TIME_EXIT", "Expired / invalidated": "EXPIRED,INVALIDATED,CANCELLED,DATA_UNAVAILABLE", All: "",
};

export default function Signals() {
  const [f, setF] = useState("Confirmed");
  const [rows, setRows] = useState<SignalRow[]>([]);
  useEffect(() => {
    const load = () => api<SignalRow[]>(`/signals?limit=300${FILTERS[f] ? `&state=${FILTERS[f]}` : ""}`).then(setRows).catch(() => {});
    load();
    return live.on((t) => t === "signal" && load());
  }, [f]);
  return (
    <div className="h-full overflow-auto p-4">
      <div className="mb-3 flex items-center gap-2">
        <h1 className="text-lg font-semibold text-slate-100">Signal feed</h1>
        <span className="text-[12px] text-slate-500">Every signal is permanent — losers, expiries and invalidations are never deleted.</span>
        <div className="ml-auto flex gap-1">{Object.keys(FILTERS).map((k) => <button key={k} className={`btn py-0.5 ${f === k ? "btn-primary" : ""}`} onClick={() => setF(k)}>{k}</button>)}</div>
      </div>
      <div className="card overflow-hidden">
        <table className="grid-t">
          <thead><tr><th>Created</th><th>Market</th><th>TF</th><th>Strategy</th><th>Dir</th><th>State</th><th>Ref</th><th>Entry zone</th><th>SL</th><th>TP1/2/3</th><th>Quality</th><th>P&amp;L (live / final)</th><th>Detail</th></tr></thead>
          <tbody>
            {rows.map((s) => (
              <tr key={s.id}>
                <td className="text-slate-500">{ago(s.created_at)}</td><td className="text-slate-100">{pair(s.symbol)}</td><td>{s.timeframe}</td>
                <td>{s.strategy}</td><td className={dirColor(s.direction)}>{s.direction}</td>
                <td><span className={`chip ${stateColor(s.state)}`}>{s.state}</span></td>
                <td className="num">{fmtPrice(s.reference_price)}</td><td className="num">{fmtPrice(s.entry_low)}–{fmtPrice(s.entry_high)}</td>
                <td className="num text-down">{fmtPrice(s.stop)}</td>
                <td className="num text-up">{fmtPrice(s.tp1)} / {fmtPrice(s.tp2)} / {fmtPrice(s.tp3)}</td>
                <td className="num">{fmtNum(s.score, 0)}</td>
                <td><SignalPnl s={s} /></td>
                <td><Link className="text-sky-400" href={`/signals/${s.id}`}>timeline →</Link></td>
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length === 0 && <div className="p-4 text-slate-500">No signals in this category yet.</div>}
      </div>
    </div>
  );
}
