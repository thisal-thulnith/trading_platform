"use client";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { dirColor, fmtNum, fmtPrice, pair, stateColor } from "@/lib/format";
import { useStore } from "@/lib/store";
import type { SignalRow } from "@/lib/types";
import { SignalPnl } from "@/components/ui/Pnl";
import { Note, Page, PageHead } from "@/components/ui/Page";

export default function SignalDetail() {
  const { id } = useParams<{ id: string }>();
  const [s, setS] = useState<SignalRow | null>(null);
  const [err, setErr] = useState("");
  const router = useRouter();
  const { setSymbol, setTimeframe, token } = useStore();
  useEffect(() => { api<SignalRow>(`/signals/${id}`).then(setS).catch((e) => setErr(e.message)); }, [id]);
  if (err) return <Page><Note tone="bad">{err}</Note></Page>;
  if (!s) return <Page><div className="text-[13.5px] text-slate-500">Loading…</div></Page>;
  const p = s.payload || {};
  const v = p.validation || {};
  const entry = (s.entry_low + s.entry_high) / 2;
  const follow = async () => {
    try {
      const r = await api<any>("/portfolio/positions", { method: "POST", json: { symbol: s.symbol, side: s.direction, stop: s.stop, take_profit: s.tp1, signal_id: s.id } });
      alert(`Paper position opened at ${fmtPrice(r.fill)} (risk ${fmtNum(r.sizing.risk_amount)})`);
    } catch (e: any) { alert(e.message); }
  };
  return (
    <Page>
      <PageHead title={<>{pair(s.symbol)} <span className={dirColor(s.direction)}>{s.direction}</span></>}
        sub={<><Link href="/terminal" className="text-accent hover:underline">← Chart</Link> · <span className={`chip ${stateColor(s.state)}`}>{s.state}</span> {s.strategy} · {s.timeframe}</>}>
        <button className="btn" onClick={() => { setSymbol(s.symbol); setTimeframe(s.timeframe); router.push("/terminal"); }}>Open chart</button>
        {s.snapshot_id && <Link className="btn" href={`/snapshots/${s.snapshot_id}`}>Evidence snapshot</Link>}
        {token && ["CONFIRMED", "ACTIVE"].includes(s.state) && <button className="btn btn-primary" onClick={follow}>Paper-trade this signal</button>}
      </PageHead>
      <div className="grid grid-cols-3 gap-3">
        <div className="card col-span-2 p-3">
          <div className="num grid grid-cols-4 gap-3 text-[13px]">
            {[["Reference", s.reference_price], ["Entry zone", `${fmtPrice(s.entry_low)} – ${fmtPrice(s.entry_high)}`], ["Stop", s.stop], ["Fill", s.entry_fill],
              ["TP1", s.tp1], ["TP2", s.tp2], ["TP3", s.tp3], ["Exit", s.exit_price]].map(([k, val]) => (
              <div key={k as string}><div className="text-[11px] font-medium text-slate-500">{k}</div><div className="text-slate-100">{typeof val === "number" ? fmtPrice(val) : val ?? "—"}</div></div>
            ))}
            <div><div className="text-[11px] font-medium text-slate-500">R:R TP1/2/3 (net)</div><div>{fmtNum(v.rr_tp1)} / {fmtNum(v.rr_tp2)} / {fmtNum(v.rr_tp3)}</div></div>
            <div><div className="text-[11px] font-medium text-slate-500">{s.state === "ACTIVE" ? "Live P&L" : "Result"}</div><div className="text-[15px]"><SignalPnl s={s} /></div></div>
            <div><div className="text-[11px] font-medium text-slate-500">MFE / MAE</div><div>{fmtNum(s.mfe_r)}R / {fmtNum(s.mae_r)}R</div></div>
            <div><div className="text-[11px] font-medium text-slate-500">Risk/unit</div><div>{fmtPrice(Math.abs(entry - s.stop))} ({fmtNum(Math.abs(entry - s.stop) / entry * 100)}%)</div></div>
          </div>
          <h2 className="mb-1 mt-4 font-medium text-slate-200">Explainable signal timeline</h2>
          <ol className="relative ml-2 border-l border-line">
            {(s.events || []).map((e, i) => (
              <li key={i} className="mb-3 ml-4">
                <span className="absolute -left-1.5 mt-1 h-3 w-3 rounded-full border border-line bg-panel2" />
                <div className="flex items-center gap-2 text-[12px]"><span className="text-slate-500">{e.ts.replace("T", " ").slice(0, 19)}Z</span>
                  <span className={`chip ${stateColor(e.to)}`}>{e.from ? `${e.from} → ` : ""}{e.to}</span>{e.price && <span className="num text-slate-400">@ {fmtPrice(e.price)}</span>}
                  {e.snapshot_id && <Link href={`/snapshots/${e.snapshot_id}`} className="text-sky-400">evidence</Link>}</div>
                <div className="text-[12px] text-slate-300">{e.reason}</div>
              </li>
            ))}
          </ol>
        </div>
        <div className="space-y-3">
          <div className="card p-3 text-[12px]">
            <div className="mb-1 font-medium text-slate-200">Conditions</div>
            {(p.conditions || []).map((c: any) => <div key={c.name} className={c.met ? "text-slate-300" : "text-slate-500"}>{c.met ? "✓" : "○"} {c.name} <span className="text-slate-600">{c.detail}</span></div>)}
            <div className="mt-2 font-medium text-slate-200">Validation gates</div>
            {(v.checks || []).map((c: any) => <div key={c.name} className={c.passed ? "text-slate-300" : "text-red-300"}>{c.passed ? "✓" : "✗"} {c.name} <span className="text-slate-600">{c.detail}</span></div>)}
            <div className="mt-2 text-slate-400">Invalidation: {p.invalidation}</div>
            <div className="text-slate-400">Regime at detection: {p.regime}</div>
            {p.data_freshness_s !== undefined && <div className="text-slate-400">Data age at confirmation: {fmtNum(p.data_freshness_s, 0)}s</div>}
            {p.expected_costs && <div className="text-slate-400">Costs assumed: fee {p.expected_costs.fee_taker * 100}% / slippage {p.expected_costs.slippage_bps} bps per fill</div>}
          </div>
          {p.counter_arguments?.length > 0 && (
            <div className="card p-3 text-[12px]"><div className="mb-1 font-medium text-amber-300">Counter-argument review</div>
              <ul className="list-disc pl-4 text-slate-300">{p.counter_arguments.map((a: string) => <li key={a}>{a}</li>)}</ul></div>
          )}
          {p.sizing_example?.ok && (
            <div className="card p-3 text-[12px]"><div className="mb-1 font-medium text-slate-200">Position sizing (example: $1,000, 1% risk)</div>
              <div className="num">qty {fmtNum(p.sizing_example.qty, 6)} · notional {fmtNum(p.sizing_example.notional)} · leverage {fmtNum(p.sizing_example.leverage_used)}x</div>
              <div className="num">risk {fmtNum(p.sizing_example.risk_amount)} · est. liq {fmtPrice(p.sizing_example.liquidation_estimate)}</div>
              <div className="mt-1 text-[11px] text-slate-500">{p.sizing_example.formula}</div></div>
          )}
        </div>
      </div>
    </Page>
  );
}
