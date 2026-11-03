"use client";
import { useEffect, useState } from "react";
import Spark from "@/components/ui/Spark";
import { Stat, TradeStatsTable } from "@/components/ui/Stats";
import { api } from "@/lib/api";
import { fmtNum } from "@/lib/format";

function Reliability({ rows }: { rows: { p_mean: number; freq: number; n: number }[] }) {
  const S = 220;
  return (
    <svg viewBox={`0 0 ${S} ${S}`} className="h-[220px] w-[220px] rounded bg-panel2">
      <line x1={0} y1={S} x2={S} y2={0} stroke="#334155" strokeDasharray="4 4" />
      {rows.map((r, i) => <circle key={i} cx={r.p_mean * S} cy={S - r.freq * S} r={Math.max(2.5, Math.min(8, Math.sqrt(r.n) / 8))} fill="#38bdf8" opacity={0.8} />)}
      <text x={4} y={12} fill="#64748b" fontSize={9}>observed freq</text><text x={S - 70} y={S - 4} fill="#64748b" fontSize={9}>predicted p</text>
    </svg>
  );
}

export default function Research() {
  const [models, setModels] = useState<any[]>([]);
  const [perf, setPerf] = useState<any>(null);
  const [strats, setStrats] = useState<any>(null);
  const [sel, setSel] = useState<any>(null);
  useEffect(() => {
    api<any[]>("/models").then((m) => { setModels(m); setSel(m[0]); });
    api("/performance").then(setPerf);
    api("/strategies").then(setStrats);
  }, []);
  return (
    <div className="h-full space-y-4 overflow-auto p-4">
      <h1 className="text-lg font-semibold text-slate-100">Research laboratory</h1>
      <section className="space-y-2">
        <h2 className="font-medium text-slate-200">Forward test (live paper signals)</h2>
        {perf && (<>
          <div className="grid grid-cols-6 gap-2">
            <Stat label="Confirmed signals" value={perf.confirmed} /><Stat label="Resolved" value={perf.resolved} />
            <Stat label="Expectancy" value={perf.stats.n ? `${fmtNum(perf.stats.expectancy_r, 3)}R` : "—"} tone={perf.stats.expectancy_r > 0 ? "up" : "down"} />
            <Stat label="Win rate" value={perf.stats.n ? fmtNum(perf.stats.win_rate * 100, 1) + "%" : "—"} />
            <Stat label="Profit factor" value={fmtNum(perf.stats.profit_factor)} />
            <Stat label="Median forming→confirm" value={perf.median_forming_to_confirm_s ? `${fmtNum(perf.median_forming_to_confirm_s / 60, 0)} min` : "—"} />
          </div>
          <div className="text-[11px] text-slate-500">{perf.disclaimer} Outcomes: {Object.entries(perf.outcomes).map(([k, v]) => `${k} ${v}`).join(" · ")}</div>
          <div className="card p-3"><div className="mb-1 text-[12px] text-slate-400">Cumulative R (all resolved signals, chronological)</div><Spark values={[0, ...perf.equity_r]} zero height={120} /></div>
          <div className="grid grid-cols-2 gap-3">
            <div className="card p-3"><div className="mb-1 text-[12px] text-slate-300">By strategy</div><TradeStatsTable rows={perf.by_strategy} /></div>
            <div className="card p-3"><div className="mb-1 text-[12px] text-slate-300">Strategy performance by regime</div><TradeStatsTable rows={perf.by_regime} /></div>
          </div>
          <div className="card p-3 text-[12px]"><div className="mb-1 text-slate-300">Signal quality-score calibration (does a higher score actually win more?)</div>
            {perf.quality_calibration.length ? perf.quality_calibration.map((b: any) => <div key={b.score_range[0]} className="num">score {b.score_range[0]}–{b.score_range[1]}: n={b.n}, win {fmtNum(b.win_rate * 100, 0)}%, avg {fmtNum(b.avg_r, 2)}R</div>) : <span className="text-slate-500">needs resolved signals</span>}</div>
        </>)}
      </section>
      <section className="space-y-2">
        <h2 className="font-medium text-slate-200">Model registry &amp; confidence calibration</h2>
        <p className="text-[12px] text-slate-500">Models must beat the class-prior and logistic baselines on purged walk-forward folds and show positive net-of-cost economics; otherwise they are rejected and never served.</p>
        <div className="flex gap-2">{models.map((m) => <button key={m.id} onClick={() => setSel(m)} className={`btn ${sel?.id === m.id ? "btn-primary" : ""}`}>{m.name} v{m.version} <span className={m.status === "accepted" ? "text-up" : "text-down"}>{m.status}</span></button>)}</div>
        {models.length === 0 && <div className="text-[12px] text-slate-500">No models trained yet. Run <code>uv run python -m app.ml.train --tf 1h --horizon 24</code>.</div>}
        {sel && (
          <div className="grid grid-cols-3 gap-3">
            <div className="card p-3 text-[12px]">
              <div className="mb-1 font-medium">Acceptance criteria</div>
              {Object.entries(sel.metrics.criteria || {}).map(([k, v]: any) => <div key={k} className={v ? "text-up" : "text-down"}>{v ? "✓" : "✗"} {k.replaceAll("_", " ")}</div>)}
              <div className="mt-2 font-medium">Summary</div>
              {Object.entries(sel.metrics.summary || {}).map(([k, v]: any) => <div key={k} className="num">{k}: {fmtNum(v, 4)}</div>)}
              <div className="mt-2 font-medium">Economic test (thresholded, net of costs)</div>
              {Object.entries(sel.metrics.economic || {}).map(([k, v]: any) => <div key={k} className="num">{k}: {fmtNum(v, 5)}</div>)}
              <div className="mt-1 text-[10px] text-slate-500">Note: overlapping horizons make samples autocorrelated; CI is optimistic.</div>
            </div>
            <div className="card p-3"><div className="mb-1 text-[12px]">Reliability diagram (out-of-sample)</div><Reliability rows={sel.metrics.reliability || []} /></div>
            <div className="card p-3 text-[12px]"><div className="mb-1">Top features (gain) — model attribution, not causality</div>
              {(sel.metrics.feature_importance || []).slice(0, 14).map((f: any) => <div key={f.feature} className="num flex justify-between"><span>{f.feature}</span><span>{fmtNum(f.gain, 0)}</span></div>)}</div>
            <div className="card col-span-3 p-3"><div className="mb-1 text-[12px]">Walk-forward folds</div>
              <table className="grid-t text-[12px]"><thead><tr><th>Fold</th><th>Test period</th><th>n train/test</th><th>Base rate</th><th>LogLoss prior / LR / GBM</th><th>AUC LR / GBM</th><th>Brier prior / GBM</th></tr></thead>
                <tbody>{(sel.metrics.folds || []).map((f: any) => <tr key={f.fold}><td>{f.fold}</td><td>{new Date(f.test_start).toISOString().slice(0, 10)} → {new Date(f.test_end).toISOString().slice(0, 10)}</td>
                  <td className="num">{f.n_train}/{f.n_test}</td><td className="num">{fmtNum(f.base_rate, 3)}</td>
                  <td className="num">{fmtNum(f.logloss_prior, 4)} / {fmtNum(f.logloss_lr, 4)} / <b className={f.logloss_gbm < f.logloss_prior ? "text-up" : "text-down"}>{fmtNum(f.logloss_gbm, 4)}</b></td>
                  <td className="num">{fmtNum(f.auc_lr, 3)} / {fmtNum(f.auc_gbm, 3)}</td><td className="num">{fmtNum(f.brier_prior, 4)} / {fmtNum(f.brier_gbm, 4)}</td></tr>)}</tbody></table></div>
          </div>
        )}
      </section>
      <section className="space-y-2">
        <h2 className="font-medium text-slate-200">Strategy specifications</h2>
        <div className="grid grid-cols-2 gap-3">{strats?.strategies.map((s: any) => (
          <div key={s.id} className="card p-3 text-[12px]">
            <div className="flex"><span className="font-medium text-slate-100">{s.name}</span><span className="chip ml-2 bg-slate-700/60">evidence grade {s.evidence_grade}</span>
              {strats.live_stats?.[s.id] && <span className="ml-auto text-slate-400">live: {strats.live_stats[s.id].n} trades, {fmtNum(strats.live_stats[s.id].expectancy_r, 2)}R</span>}</div>
            <div className="text-slate-400">{s.description}</div>
            <div className="mt-1 grid grid-cols-[90px_1fr] gap-x-2 text-[11px]">
              <span className="text-slate-500">Entry</span><span>{s.entry}</span><span className="text-slate-500">Exit</span><span>{s.exit}</span>
              <span className="text-slate-500">Invalidation</span><span>{s.invalidation}</span><span className="text-slate-500">Stop</span><span>{s.stop_rule}</span>
              <span className="text-slate-500">Regimes</span><span>{s.regimes.join(", ")}</span><span className="text-slate-500">Data</span><span>{s.required_data.join(", ")}{!s.backtestable && " (live-only: insufficient history)"}</span>
            </div>
          </div>))}</div>
      </section>
    </div>
  );
}
