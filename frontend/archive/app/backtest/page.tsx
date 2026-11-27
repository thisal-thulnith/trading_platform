"use client";
import { useEffect, useState } from "react";
import Spark from "@/components/ui/Spark";
import { Stat, TradeStatsTable } from "@/components/ui/Stats";
import { api } from "@/lib/api";
import { ago, fmtNum, fmtPct } from "@/lib/format";
import { useStore } from "@/lib/store";

export default function Backtest() {
  const token = useStore((s) => s.token);
  const [runs, setRuns] = useState<any[]>([]);
  const [sel, setSel] = useState<any>(null);
  const [strategies, setStrategies] = useState<any[]>([]);
  const [f, setF] = useState({ symbols: "BTCUSDT,ETHUSDT,SOLUSDT", timeframe: "1h", bars: 3000, step: 1, risk_per_trade_pct: 1, fee: 0.0005, slip_bps: 2, strategies: [] as string[] });
  const [err, setErr] = useState("");
  const load = () => api<any[]>("/backtests").then(setRuns).catch(() => {});
  useEffect(() => { load(); api("/strategies").then((r) => setStrategies(r.strategies)); const i = setInterval(load, 10000); return () => clearInterval(i); }, []);
  const open = (id: string) => api(`/backtests/${id}`).then(setSel);
  const start = async () => {
    setErr("");
    try {
      await api("/backtests", { method: "POST", json: { ...f, symbols: f.symbols.split(",").map((s) => s.trim().toUpperCase()), strategies: f.strategies.length ? f.strategies : null } });
      load();
    } catch (e: any) { setErr(e.message); }
  };
  const res = sel?.results;
  const base = res?.scenarios?.base;
  return (
    <div className="grid h-full grid-cols-[340px_1fr] overflow-hidden">
      <div className="space-y-3 overflow-auto border-r border-line p-3">
        <h1 className="text-lg font-semibold text-slate-100">Backtesting lab</h1>
        <p className="text-[11px] text-slate-500">Event-driven: at every closed bar the live analysis engine is rebuilt from past data only, setups are validated exactly as live, then simulated with fees, slippage, conservative same-bar resolution and capital limits. Parameters are fixed priors (not optimised).</p>
        {!token ? <div className="text-slate-500">Log in to run backtests.</div> : (
          <div className="card space-y-2 p-3 text-[12px]">
            <label className="flex flex-col">Symbols<input className="input" value={f.symbols} onChange={(e) => setF({ ...f, symbols: e.target.value })} /></label>
            <div className="grid grid-cols-3 gap-2">
              <label className="flex flex-col">TF<select className="input" value={f.timeframe} onChange={(e) => setF({ ...f, timeframe: e.target.value })}>{["15m", "30m", "1h", "2h", "4h", "1d"].map((t) => <option key={t}>{t}</option>)}</select></label>
              <label className="flex flex-col">Bars<input className="input" type="number" value={f.bars} onChange={(e) => setF({ ...f, bars: +e.target.value })} /></label>
              <label className="flex flex-col">Step<input className="input" type="number" value={f.step} onChange={(e) => setF({ ...f, step: +e.target.value })} /></label>
              <label className="flex flex-col">Risk %<input className="input" type="number" step={0.1} value={f.risk_per_trade_pct} onChange={(e) => setF({ ...f, risk_per_trade_pct: +e.target.value })} /></label>
              <label className="flex flex-col">Fee<input className="input" type="number" step={0.0001} value={f.fee} onChange={(e) => setF({ ...f, fee: +e.target.value })} /></label>
              <label className="flex flex-col">Slip bps<input className="input" type="number" value={f.slip_bps} onChange={(e) => setF({ ...f, slip_bps: +e.target.value })} /></label>
            </div>
            <div className="flex flex-wrap gap-1">{strategies.filter((s) => s.backtestable).map((s) => (
              <label key={s.id} className="chip border border-line"><input type="checkbox" className="mr-1" checked={f.strategies.includes(s.id)}
                onChange={(e) => setF({ ...f, strategies: e.target.checked ? [...f.strategies, s.id] : f.strategies.filter((x) => x !== s.id) })} />{s.id}</label>))}</div>
            <div className="text-[10px] text-slate-500">No strategy selected = all backtestable strategies.</div>
            {err && <div className="text-red-300">{err}</div>}
            <button className="btn btn-primary w-full" onClick={start}>Run backtest</button>
          </div>
        )}
        <div className="space-y-1">{runs.map((r) => (
          <button key={r.id} onClick={() => open(r.id)} className={`card block w-full p-2 text-left text-[12px] ${sel?.id === r.id ? "border-sky-600" : ""}`}>
            <div className="flex"><span className="text-slate-200">{r.config.symbols.join(", ")} · {r.config.timeframe}</span><span className="ml-auto text-slate-500">{ago(r.created_at)}</span></div>
            <div className="text-slate-500">{r.status}{r.summary?.trades?.n !== undefined && ` · ${r.summary.trades.n} trades · ${fmtNum(r.summary.trades.expectancy_r, 3)}R exp.`}</div>
          </button>))}</div>
      </div>
      <div className="overflow-auto p-4">
        {!sel && <div className="text-slate-500">Select a run.</div>}
        {sel && sel.status !== "done" && <div className="text-slate-400">Status: {sel.status} {res?.error}</div>}
        {base && (
          <div className="space-y-3">
            <div className="text-[12px] text-slate-500">config hash {sel.config_hash.slice(0, 16)} · {new Date(res.period.start).toISOString().slice(0, 10)} → {new Date(res.period.end).toISOString().slice(0, 10)} · runtime {res.runtime_s}s</div>
            <div className="grid grid-cols-6 gap-2">
              <Stat label="Net return" value={fmtPct(base.equity.net_return * 100)} tone={base.equity.net_return >= 0 ? "up" : "down"} />
              <Stat label="Max drawdown" value={fmtPct(base.max_drawdown * 100)} tone="down" />
              <Stat label="Sharpe (daily)" value={fmtNum(base.equity.sharpe)} /><Stat label="Sortino" value={fmtNum(base.equity.sortino)} />
              <Stat label="Calmar" value={fmtNum(base.equity.calmar)} /><Stat label="PSR" value={base.equity.psr !== null ? fmtNum(base.equity.psr * 100, 1) + "%" : "—"} />
              <Stat label="Trades" value={base.trades.n} /><Stat label="Win rate" value={base.trades.n ? fmtNum(base.trades.win_rate * 100, 1) + "%" : "—"} />
              <Stat label="Expectancy" value={`${fmtNum(base.trades.expectancy_r, 3)}R`} tone={base.trades.expectancy_r > 0 ? "up" : "down"} />
              <Stat label="Profit factor" value={fmtNum(base.trades.profit_factor)} /><Stat label="Fill rate" value={fmtNum((base.fill_rate ?? 0) * 100, 0) + "%"} />
              <Stat label="Exposure" value={fmtNum(base.exposure * 100, 1) + "%"} />
            </div>
            <div className="card p-3"><div className="mb-1 text-[12px] text-slate-400">Equity curve vs buy &amp; hold: {Object.entries(res.benchmark_buy_hold).map(([k, v]: any) => `${k} ${fmtPct(v * 100)}`).join(" · ")}</div>
              <Spark values={base.equity_curve.map((p: any) => p.value)} height={180} /></div>
            <div className="card p-3"><div className="mb-1 text-[12px] font-medium text-slate-300">Cost sensitivity / stress</div>
              <table className="grid-t text-[12px]"><thead><tr><th>Scenario</th><th>Trades</th><th>Expectancy R</th><th>Net return</th><th>Max DD</th></tr></thead>
                <tbody>{Object.entries(res.scenarios).map(([k, s]: any) => <tr key={k}><td>{k}</td><td className="num">{s.trades.n}</td><td className={`num ${s.trades.expectancy_r > 0 ? "text-up" : "text-down"}`}>{fmtNum(s.trades.expectancy_r, 3)}</td><td className="num">{fmtPct(s.equity.net_return * 100)}</td><td className="num">{fmtPct(s.max_drawdown * 100)}</td></tr>)}</tbody></table></div>
            <div className="grid grid-cols-2 gap-3">
              <div className="card p-3"><div className="mb-1 text-[12px] text-slate-300">In-sample vs out-of-sample (last 30%)</div><TradeStatsTable rows={{ "in-sample": base.in_sample, "out-of-sample": base.out_of_sample }} /></div>
              <div className="card p-3"><div className="mb-1 text-[12px] text-slate-300">By direction</div><TradeStatsTable rows={base.by_direction} /></div>
            </div>
            <div className="card p-3"><div className="mb-1 text-[12px] text-slate-300">By strategy</div><TradeStatsTable rows={base.by_strategy} /></div>
            <div className="card p-3"><div className="mb-1 text-[12px] text-slate-300">Strategy performance by regime</div><TradeStatsTable rows={base.by_regime} /></div>
            <div className="card p-3"><div className="mb-1 text-[12px] text-slate-300">By symbol</div><TradeStatsTable rows={base.by_symbol} /></div>
            <ul className="list-disc pl-5 text-[11px] text-slate-500">{res.notes.map((n: string) => <li key={n}>{n}</li>)}</ul>
          </div>
        )}
      </div>
    </div>
  );
}
