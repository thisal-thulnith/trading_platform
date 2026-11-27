"use client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Pct } from "@/components/ui/Pnl";
import Spark from "@/components/ui/Spark";
import { Stat } from "@/components/ui/Stats";
import { api } from "@/lib/api";
import { ago, dirColor, fmtPrice, pair } from "@/lib/format";

const usd = (v: number | null | undefined, sign = false) =>
  v === null || v === undefined || !isFinite(v) ? "—" : `${sign && v > 0 ? "+" : ""}${v < 0 ? "-" : ""}$${Math.abs(v).toFixed(2)}`;
const tone = (v: number) => (v > 0 ? "up" : v < 0 ? "down" : undefined);

export default function FollowAll() {
  const [capital, setCapital] = useState(100);
  const [risk, setRisk] = useState(1);
  const [d, setD] = useState<any>(null);
  const [err, setErr] = useState("");
  const load = useCallback(
    () => api(`/follow_all?capital=${capital}&risk_pct=${risk}`).then((r) => { setD(r); setErr(""); }).catch((e) => setErr(e.message)),
    [capital, risk],
  );
  useEffect(() => { load(); const i = setInterval(load, 3000); return () => clearInterval(i); }, [load]);

  return (
    <div className="h-full space-y-3 overflow-auto p-4">
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <h1 className="text-lg font-semibold text-slate-100">If you took every trade</h1>
          <p className="text-[12px] text-slate-500">
            A {usd(capital)} account that entered every signal the platform filled, risking {risk}% of the balance per trade.
            Closed trades include fees and slippage; open trades update live.
          </p>
        </div>
        <label className="ml-auto flex flex-col text-[11px] text-slate-400">Starting balance ($)
          <input type="number" min={1} className="input w-28" value={capital} onChange={(e) => setCapital(Math.max(1, +e.target.value || 100))} />
        </label>
        <label className="flex flex-col text-[11px] text-slate-400">Risk per trade
          <select className="input" value={risk} onChange={(e) => setRisk(+e.target.value)}>
            {[0.5, 1, 2, 3, 5].map((r) => <option key={r} value={r}>{r}%</option>)}
          </select>
        </label>
      </div>
      {err && <div className="text-red-300">{err}</div>}
      {!d ? <div className="text-slate-500">Loading…</div> : (
        <>
          <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-8">
            <Stat label="Balance now" value={usd(d.equity)} tone={tone(d.total_pnl)} />
            <Stat label="Total gain / loss" value={<>{usd(d.total_pnl, true)} <Pct v={d.return_pct} className="text-[12px]" /></>} tone={tone(d.total_pnl)} />
            <Stat label="Closed trades (banked)" value={usd(d.realized_pnl, true)} tone={tone(d.realized_pnl)} />
            <Stat label="Open trades (live)" value={usd(d.unrealized_pnl, true)} tone={tone(d.unrealized_pnl)} />
            <Stat label="Trades" value={`${d.closed} closed · ${d.open} open`} />
            <Stat label="Win rate" value={d.win_rate === null ? "—" : `${(d.win_rate * 100).toFixed(0)}% (${d.wins}W/${d.losses}L)`} />
            <Stat label="Worst drop" value={`-${d.max_drawdown_pct.toFixed(1)}%`} tone={d.max_drawdown_pct > 0 ? "down" : undefined} />
            <Stat label="Peak leverage needed" value={`${d.peak_leverage.toFixed(1)}x`} tone={d.peak_leverage > 10 ? "down" : undefined} />
          </div>
          <div className="card p-3">
            <div className="mb-1 flex text-[12px] text-slate-400">Balance over time <span className="ml-auto text-slate-500">last point = live, incl. open trades</span></div>
            <Spark values={d.equity_curve.map((p: any) => p.equity)} height={140} />
          </div>
          {d.peak_leverage > 10 && (
            <div className="card border-amber-500/30 p-3 text-[12px] text-amber-200">
              Warning: at the busiest moment {d.peak_open_positions} trades were open at once, which needed about {d.peak_leverage.toFixed(0)}x total
              leverage on this balance. Many exchanges would reject this, or the account would be close to liquidation. In practice you would need to skip trades or risk less.
            </div>
          )}
          <div className="card overflow-auto">
            <table className="w-full text-[12px]">
              <thead className="text-left text-[10px] uppercase tracking-wider text-slate-500">
                <tr>{["Entered", "Market", "Side", "Strategy", "Status", "Entry", "Now / exit", "Risked", "Size", "Price move", "Gain / loss", "% of account"].map((h) => <th key={h} className="px-2 py-1.5">{h}</th>)}</tr>
              </thead>
              <tbody>
                {d.trades.map((t: any) => (
                  <tr key={t.id} className="border-t border-line/60 hover:bg-white/5">
                    <td className="px-2 py-1 text-slate-500">{ago(t.entered_at)}</td>
                    <td className="px-2 py-1"><Link className="text-sky-300 hover:underline" href={`/signals/${t.id}`}>{pair(t.symbol)}</Link> <span className="text-slate-500">{t.timeframe}</span></td>
                    <td className={`px-2 py-1 ${dirColor(t.direction)}`}>{t.direction}</td>
                    <td className="px-2 py-1 text-slate-400">{t.strategy.replaceAll("_", " ")}</td>
                    <td className="px-2 py-1">{t.status === "OPEN" ? <span className="chip bg-sky-500/15 text-sky-300">open · live</span> : <span className="chip bg-slate-500/20 text-slate-300">{t.outcome.replaceAll("_", " ").toLowerCase()}</span>}</td>
                    <td className="num px-2 py-1">{fmtPrice(t.fill)}</td>
                    <td className="num px-2 py-1">{t.status === "OPEN" ? fmtPrice(t.last_price) : "closed"}</td>
                    <td className="num px-2 py-1 text-slate-400">{usd(t.risk_usd)}</td>
                    <td className="num px-2 py-1 text-slate-400" title="position size and leverage on the balance at entry">{usd(t.notional_usd)} · {t.leverage.toFixed(1)}x</td>
                    <td className="px-2 py-1"><Pct v={t.price_move_pct} r={t.r} /></td>
                    <td className={`num px-2 py-1 font-semibold ${t.pnl_usd > 0 ? "text-up" : t.pnl_usd < 0 ? "text-down" : "text-slate-400"}`}>{usd(t.pnl_usd, true)}</td>
                    <td className="px-2 py-1"><Pct v={t.pnl_pct_account} /></td>
                  </tr>
                ))}
                {!d.trades.length && <tr><td colSpan={12} className="p-3 text-slate-500">No signals have filled yet.</td></tr>}
              </tbody>
            </table>
          </div>
          <p className="text-[11px] text-slate-500">{d.disclaimer} Only signals whose entry price was actually reached count; expired or invalidated setups were never entered. Open-trade values do not yet include the exit fee.</p>
        </>
      )}
    </div>
  );
}
