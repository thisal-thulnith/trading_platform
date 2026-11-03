"use client";

const usd = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `$${v.toLocaleString(undefined, { maximumFractionDigits: 2 })}`);

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return <div className="flex justify-between gap-2"><span className="text-slate-500">{k}</span><span className="num text-slate-200">{v}</span></div>;
}

/** The settings the agents invest with for the user's own budget (backend: app/portfolio/budget.py). */
export default function BudgetPlan({ p }: { p: any }) {
  return (
    <div className="card p-3 text-[12px] text-slate-300">
      <div className="mb-1 font-medium">
        Your budget plan — {usd(p.budget_usd)} in {p.mode} mode on {p.venue === "futures" ? "Binance Futures" : "spot"}
      </div>
      <div className="grid grid-cols-2 gap-x-6 gap-y-1 md:grid-cols-4">
        <Row k="Risk per trade" v={`${usd(p.risk_per_trade_usd)} (${p.risk_pct}%)`} />
        <Row k="Max open trades" v={p.max_positions} />
        <Row k="Max at risk together" v={usd(p.max_open_risk_usd)} />
        <Row k="Max in one coin" v={usd(p.max_per_coin_usd)} />
        <Row k="Typical position" v={usd(p.typical_position_usd)} />
        {p.venue === "futures" ? <Row k="Typical margin · leverage" v={`${usd(p.typical_margin_usd)} at ${p.typical_leverage}×`} /> : <Row k="Leverage" v="none (spot)" />}
        <Row k="Typical stop" v={`${p.typical_stop_pct}% below entry`} />
        <Row k="Coins tradeable" v={`${p.coins_tradeable} of ${p.coins_total}`} />
      </div>
      <p className={`mt-2 ${p.recommendation.switch ? "text-amber-200" : "text-emerald-200"}`}>{p.recommendation.text}</p>
      {p.too_small.length > 0 && (
        <p className="mt-1 text-slate-500">
          Too small at this budget: {p.too_small.map((t: any) => `${t.symbol.replace("USDT", "")} (ticket ${usd(t.size_usd)} < ${usd(t.min_notional)} minimum, needs ${usd(t.budget_needed_usd)})`).join(", ")}
        </p>
      )}
      {p.expectations.length > 0 && (
        <table className="mt-2 w-full">
          <thead className="text-left text-[10px] uppercase tracking-wider text-slate-500">
            <tr><th className="px-2 py-1">Research period</th><th className="px-2 py-1">Return</th><th className="px-2 py-1">Your {usd(p.budget_usd)} →</th><th className="px-2 py-1">Worst drop</th></tr>
          </thead>
          <tbody>
            {p.expectations.map((e: any) => (
              <tr key={e.period} className="border-t border-line/60">
                <td className="px-2 py-1">{e.period}</td>
                <td className={`num px-2 py-1 ${e.return_pct >= 0 ? "text-up" : "text-down"}`}>{e.return_pct > 0 ? "+" : ""}{e.return_pct}%{e.yearly_pct !== undefined && e.yearly_pct !== null ? ` (${e.yearly_pct}%/yr)` : ""}</td>
                <td className="num px-2 py-1">{usd(e.end_usd)}</td>
                <td className="num px-2 py-1 text-down">-{e.max_drawdown_pct}% (−{usd(e.worst_drop_usd)})</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="mt-2 text-slate-500">No promise: these are the research results scaled to your budget. The system decides the coin, the size, the stop, the target and the leverage; you only choose the budget and the mode.</p>
    </div>
  );
}
