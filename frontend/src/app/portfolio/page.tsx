"use client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import MyTrades from "@/components/MyTrades";
import Spark from "@/components/ui/Spark";
import { Stat } from "@/components/ui/Stats";
import { api } from "@/lib/api";
import { dirColor, fmtNum, fmtPct, fmtPrice, pair } from "@/lib/format";
import { useStore } from "@/lib/store";
import { Pct } from "@/components/ui/Pnl";
import { Page } from "@/components/ui/Page";

function Onboarding({ onDone, initial }: { onDone: () => void; initial?: any }) {
  const [f, setF] = useState<any>({ starting_capital: 1000, base_currency: "USDT", exchange: "binance", market: "perp", risk_per_trade_pct: 1,
    max_drawdown_pct: 20, timeframes: ["1h", "4h"], max_open_positions: 5, max_leverage: 3, max_portfolio_risk_pct: 5, auto_follow_signals: false, ...initial });
  const [err, setErr] = useState("");
  const set = (k: string, v: any) => setF({ ...f, [k]: v });
  const num = (k: string, label: string, step = 1) => (
    <label className="flex flex-col text-[12px] text-slate-400">{label}<input type="number" step={step} className="input" value={f[k]} onChange={(e) => set(k, +e.target.value)} /></label>
  );
  const submit = async (reset = false) => {
    try { await api("/portfolio/onboarding", { method: "POST", json: { ...f, reset } }); onDone(); } catch (e: any) { setErr(e.message); }
  };
  return (
    <div className="card max-w-3xl space-y-3 p-4">
      <h2 className="font-semibold text-slate-100">Account and risk settings</h2>
      <p className="text-[12px] text-slate-500">Paper only. Sizes come from your stop, fees and these limits, never all-in.</p>
      <div className="grid grid-cols-3 gap-3">
        {num("starting_capital", "Starting capital")}
        <label className="flex flex-col text-[12px] text-slate-400">Market<select className="input" value={f.market} onChange={(e) => set("market", e.target.value)}><option value="perp">USDⓈ-M perpetual</option><option value="spot">Spot</option></select></label>
        <label className="flex flex-col text-[12px] text-slate-400">Base currency<input className="input" value={f.base_currency} onChange={(e) => set("base_currency", e.target.value)} /></label>
        {num("risk_per_trade_pct", "Max risk per trade %", 0.1)}{num("max_drawdown_pct", "Max portfolio drawdown %")}{num("max_portfolio_risk_pct", "Max total open risk %", 0.5)}
        {num("max_open_positions", "Max simultaneous positions")}{num("max_leverage", "Max leverage", 0.5)}
        <label className="flex flex-col text-[12px] text-slate-400">Preferred timeframes<input className="input" value={f.timeframes.join(",")} onChange={(e) => set("timeframes", e.target.value.split(",").map((x: string) => x.trim()))} /></label>
      </div>
      <p className="text-[11px] text-slate-500">Leverage changes the margin, not the odds. Liquidation prices are estimates.</p>
      {err && <div className="text-red-300">{err}</div>}
      <div className="flex gap-2"><button className="btn btn-primary" onClick={() => submit(false)}>Save</button>{initial && <button className="btn" onClick={() => confirm("Reset paper account? Open positions are closed.") && submit(true)}>Reset account</button>}</div>
    </div>
  );
}

function Sizer() {
  const [f, setF] = useState({ symbol: "BTCUSDT", side: "LONG", entry: 0, stop: 0 });
  const [r, setR] = useState<any>(null);
  const calc = async () => setR(await api("/portfolio/sizing", { method: "POST", json: f }).catch((e) => ({ ok: false, reason: e.message })));
  return (
    <div className="card p-3 text-[12px]">
      <div className="mb-2 font-medium text-slate-200">Position size calculator</div>
      <div className="flex flex-wrap gap-2">
        <input className="input w-28" value={f.symbol} onChange={(e) => setF({ ...f, symbol: e.target.value.toUpperCase() })} />
        <select className="input" value={f.side} onChange={(e) => setF({ ...f, side: e.target.value })}><option>LONG</option><option>SHORT</option></select>
        <input className="input w-28" type="number" placeholder="entry" onChange={(e) => setF({ ...f, entry: +e.target.value })} />
        <input className="input w-28" type="number" placeholder="stop" onChange={(e) => setF({ ...f, stop: +e.target.value })} />
        <button className="btn" onClick={calc}>Calculate</button>
      </div>
      {r && (r.ok ? (
        <div className="num mt-2 grid grid-cols-3 gap-1">
          <span>qty {fmtNum(r.qty, 6)}</span><span>notional {fmtNum(r.notional)}</span><span>leverage {fmtNum(r.leverage_used)}x</span>
          <span>risk {fmtNum(r.risk_amount)} ({fmtNum(r.risk_pct)}%)</span><span>fees ≈ {fmtNum(r.fees_estimate)}</span><span>liq ≈ {fmtPrice(r.liquidation_estimate)}</span>
          <span className="col-span-3 font-sans text-[11px] text-slate-500">{r.formula}</span>
          {r.notes?.map((n: string) => <span key={n} className="col-span-3 font-sans text-amber-300">{n}</span>)}
        </div>) : <div className="mt-2 text-red-300">{r.reason}</div>)}
    </div>
  );
}

/** The older auto-follow paper account for the live signals (login required). Kept below the user's own trades. */
function PaperPortfolio() {
  const token = useStore((s) => s.token);
  const [pf, setPf] = useState<any>(null);
  const [state, setState] = useState<"loading" | "none" | "ok">("loading");
  const [edit, setEdit] = useState(false);
  const [open, setOpen] = useState(false);
  const load = useCallback(() => api("/portfolio").then((p) => { setPf(p); setState("ok"); }).catch(() => setState("none")), []);
  useEffect(() => { if (token && open) { load(); const i = setInterval(load, 2000); return () => clearInterval(i); } }, [token, open, load]);
  const head = (
    <div className="flex items-center">
      <h2 className="font-semibold text-slate-100">Auto-follow paper account</h2>
      <button className="btn ml-auto" onClick={() => setOpen(!open)}>{open ? "Hide" : "Show"}</button>
    </div>
  );
  if (!open) return <div className="card p-3">{head}<p className="mt-1 text-[12px] text-slate-500">A separate paper account that follows the live signals. Login required.</p></div>;
  if (!token) return <div className="card p-3">{head}<p className="mt-1 text-[12px] text-slate-400"><Link href="/login" className="text-sky-400">Log in</Link> to configure it.</p></div>;
  if (state === "loading") return <div className="card p-3">{head}<p className="mt-1 text-[12px] text-slate-500">Loading…</p></div>;
  if (state === "none" || edit) return <div className="card space-y-3 p-3">{head}<Onboarding initial={edit ? { ...pf?.risk, starting_capital: pf?.starting_capital } : undefined} onDone={() => { setEdit(false); load(); }} /><div className="max-w-3xl"><Sizer /></div></div>;
  const close = async (id: string) => { await api(`/portfolio/positions/${id}/close`, { method: "POST" }).catch((e) => alert(e.message)); load(); };
  return (
    <div className="card space-y-3 p-3">
      {head}
      <div className="flex items-center"><span className="text-[12px] text-slate-500">{pf.disclaimer}</span><button className="btn ml-auto" onClick={() => setEdit(true)}>Risk settings</button></div>
      <div className="grid grid-cols-6 gap-2">
        <Stat label="Starting capital" value={fmtNum(pf.starting_capital)} />
        <Stat label="Equity" value={fmtNum(pf.equity)} tone={pf.equity >= pf.starting_capital ? "up" : "down"} />
        <Stat label="Total return" value={fmtPct(pf.total_return_pct)} tone={pf.total_return_pct >= 0 ? "up" : "down"} />
        <Stat label="Realized / unrealized" value={`${fmtNum(pf.realized_pnl)} / ${fmtNum(pf.unrealized_pnl)}`} />
        <Stat label="Max drawdown" value={fmtPct(pf.max_drawdown_pct)} tone="down" />
        <Stat label="Win rate / PF" value={`${pf.win_rate !== null ? fmtNum(pf.win_rate * 100, 0) + "%" : "—"} / ${fmtNum(pf.profit_factor)}`} />
        <Stat label="Trades" value={pf.trades} /><Stat label="Fees paid" value={fmtNum(pf.fees_paid)} /><Stat label="Funding" value={fmtNum(pf.funding_paid, 4)} />
        <Stat label="Gross exposure" value={fmtNum(pf.gross_exposure)} /><Stat label="Current DD" value={fmtPct(pf.current_drawdown_pct)} />
        <Stat label="Max corr." value={pf.correlation?.max !== null && pf.correlation?.max !== undefined ? fmtNum(pf.correlation.max) : "—"} />
      </div>
      {pf.correlation?.warning && <div className="rounded bg-amber-500/10 p-2 text-[12px] text-amber-300">⚠ {pf.correlation.warning}: {pf.correlation.pairs.filter((p: any) => p.high).map((p: any) => `${p.a}/${p.b} ${p.corr.toFixed(2)}`).join(", ")}</div>}
      <div className="grid grid-cols-3 gap-3">
        <div className="card col-span-2 p-3"><div className="mb-1 text-[12px] text-slate-400">Equity curve (5-min samples)</div><Spark values={pf.equity_curve.map((p: any) => p.value)} height={160} /></div>
        <div className="card p-3 text-[12px]"><div className="mb-1 text-slate-400">Exposure by coin</div>
          {Object.entries(pf.exposure).map(([k, v]: any) => <div key={k} className="num flex justify-between"><span>{pair(k)}</span><span className={dirColor(v)}>{fmtNum(v)}</span></div>)}
          {Object.keys(pf.exposure).length === 0 && <div className="text-slate-600">flat</div>}
          <div className="mt-2 text-slate-400">R distribution</div>
          <div className="num text-[11px] text-slate-500">{pf.r_distribution.map((r: number) => r.toFixed(2)).join("  ") || "—"}</div>
        </div>
      </div>
      <div className="card p-3">
        <div className="mb-1 text-[12px] text-slate-400">Open positions</div>
        <table className="grid-t text-[12px]"><thead><tr><th>Symbol</th><th>Side</th><th>Qty</th><th>Lev</th><th>Entry</th><th>Mark</th><th>Stop</th><th>TP</th><th>uPnL</th><th>P&amp;L %</th><th>ROE %</th><th>Fees</th><th></th></tr></thead>
          <tbody>{pf.open_positions.map((p: any) => (
            <tr key={p.id}><td>{pair(p.symbol)}</td><td className={dirColor(p.side)}>{p.side}</td><td className="num">{fmtNum(p.qty, 6)}</td><td className="num">{fmtNum(p.leverage, 1)}x</td>
              <td className="num">{fmtPrice(p.entry_price)}</td><td className="num">{fmtPrice(p.mark)}</td><td className="num">{fmtPrice(p.stop)}</td><td className="num">{fmtPrice(p.take_profit)}</td>
              <td className={`num ${p.unrealized_pnl >= 0 ? "text-up" : "text-down"}`}>{fmtNum(p.unrealized_pnl)}</td>
              <td><Pct v={p.unrealized_pnl_pct} /></td><td><Pct v={p.roe_pct} /></td><td className="num">{fmtNum(p.fees, 4)}</td>
              <td><button className="text-sky-400" onClick={() => close(p.id)}>close</button></td></tr>))}</tbody></table>
      </div>
      <div className="card p-3">
        <div className="mb-1 text-[12px] text-slate-400">Closed positions</div>
        <table className="grid-t text-[12px]"><thead><tr><th>Closed</th><th>Symbol</th><th>Side</th><th>Entry</th><th>Exit</th><th>Reason</th><th>Net PnL</th><th>Net %</th><th>Fees</th><th>Funding</th></tr></thead>
          <tbody>{pf.closed_positions.slice().reverse().map((p: any) => (
            <tr key={p.id}><td>{p.closed_at?.slice(0, 16)}</td><td>{pair(p.symbol)}</td><td className={dirColor(p.side)}>{p.side}</td><td className="num">{fmtPrice(p.entry_price)}</td>
              <td className="num">{fmtPrice(p.exit_price)}</td><td>{p.close_reason}</td><td className={`num ${p.realized_pnl >= 0 ? "text-up" : "text-down"}`}>{fmtNum(p.realized_pnl)}</td>
              <td><Pct v={p.realized_pnl_pct} /></td><td className="num">{fmtNum(p.fees, 4)}</td><td className="num">{fmtNum(p.funding, 4)}</td></tr>))}</tbody></table>
      </div>
      <div className="max-w-3xl"><Sizer /></div>
    </div>
  );
}

export default function Portfolio() {
  return (
    <Page>
      <MyTrades extra={<PaperPortfolio />} />
    </Page>
  );
}
