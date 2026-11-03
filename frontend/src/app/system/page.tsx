"use client";
import { useCallback, useEffect, useState } from "react";
import BudgetPlan from "@/components/BudgetPlan";
import DeskPanel, { TopList, useDesk } from "@/components/DeskPanel";
import { OpenCard, useTradeActions } from "@/components/MyTrades";
import SystemSignalCard from "@/components/SystemSignalCard";
import TakeTrade, { nextDailyOpen, type TradeDraft } from "@/components/TakeTrade";
import Spark from "@/components/ui/Spark";
import { Stat } from "@/components/ui/Stats";
import { api } from "@/lib/api";
import { fmtPrice, pair } from "@/lib/format";

const usd = (v: number | null | undefined, sign = false) =>
  v === null || v === undefined || !isFinite(v) ? "—" : `${sign && v > 0 ? "+" : ""}${v < 0 ? "-" : ""}$${Math.abs(v).toFixed(2)}`;
const tone = (v: number) => (v > 0 ? "up" : v < 0 ? "down" : undefined);
const pct = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(1)}%`);
const rr = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}R`);
const PERIODS = ["train 2020-23", "valid 2024", "TEST 2025-now"];
const LEVERAGES = [1, 2, 3, 5, 10, 20, 25, 50, 75, 100, 125];
const wins = (row: any) => PERIODS.map((p) => (row[p]?.n ? `${(row[p].win * 100).toFixed(0)}%` : "—")).join(" / ");
const avgs = (row: any) => PERIODS.map((p) => (row[p]?.n ? rr(row[p].E) : "—")).join(" / ");

function Table({ head, rows }: { head: string[]; rows: React.ReactNode[][] }) {
  return (
    <table className="w-full text-[12px]">
      <thead className="text-left text-[10px] uppercase tracking-wider text-slate-500">
        <tr>{head.map((h) => <th key={h} className="px-2 py-1.5">{h}</th>)}</tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i} className="border-t border-line/60">{r.map((c, j) => <td key={j} className="px-2 py-1">{c}</td>)}</tr>
        ))}
      </tbody>
    </table>
  );
}

export default function SystemAccount() {
  const [capital, setCapital] = useState(100);
  const [mode, setMode] = useState("conservative");
  const [venue, setVenue] = useState("spot");
  const [leverage, setLeverage] = useState(20);
  const [capText, setCapText] = useState("100");
  const [take, setTake] = useState<TradeDraft | null>(null);
  const [d, setD] = useState<any>(null);
  const [mine, setMine] = useState<any>(null);
  const [err, setErr] = useState("");
  const applyBudget = () => { const v = Math.round(+capText); if (v >= 10 && v <= 1_000_000 && v !== capital) { setCapital(v); setD(null); } };
  const q = `capital=${capital}&mode=${mode}&venue=${venue}&leverage=${leverage}`;
  const load = useCallback(
    () => api(`/system?${q}`).then((r) => { setD(r); setErr(""); }).catch((e) => setErr(e.message)),
    [q],
  );
  useEffect(() => { load(); const i = setInterval(load, 60_000); return () => clearInterval(i); }, [load]);
  const intel = useDesk(d ? `/system/desk?${q}` : null);
  // the user's own running trades, shown next to the signal cards so a selected card becomes a running card here
  const loadMine = useCallback(
    () => api(`/my/portfolio?budget=${capital}&mode=${mode}&venue=${venue}&leverage=${leverage}`).then(setMine).catch(() => {}),
    [capital, mode, venue, leverage],
  );
  useEffect(() => { loadMine(); const i = setInterval(loadMine, 30_000); return () => clearInterval(i); }, [loadMine]);
  const act = useTradeActions(loadMine);
  const takeSignal = (s: any) => setTake({ symbol: s.symbol, venue: s.venue, leverage: s.venue === "futures" ? (s.leverage ?? 1) : 1, entry: s.entry_ref,
    qty: s.qty, stop: s.stop, target: s.target, budget_usd: capital, mode, source: "system", opened_at: nextDailyOpen() });

  const a = d?.account;
  const r = d?.rules;
  const fu = d?.futures;
  const ls = d?.leverage_study;
  const futures = venue === "futures";
  const liqOf = (sym: string) => fu?.open?.find((x: any) => x.symbol === sym);
  const aiTop = intel?.status === "ready" && intel.top_coins?.length > 0;
  return (
    <div className="h-full space-y-3 overflow-auto p-4">
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <h1 className="text-lg font-semibold text-slate-100">System account</h1>
          <p className="max-w-3xl text-[12px] text-slate-500">
            {futures
              ? `The same trades written as Binance USDⓈ-M tickets for a ${usd(capital)} account. The system sizes every trade by risk; leverage only sets the margin and the liquidation price, and it is capped per trade so the stop — not a liquidation — closes a loser. You place the orders yourself; nothing is sent to Binance.`
              : `The platform runs this ${usd(capital)} paper account by itself: it picks the trades, sizes them, limits the risk and pauses when the edge breaks. Every trade aims for at least 1:2. Paper only — nothing is sent to an exchange.`}
          </p>
        </div>
        <label className="ml-auto flex flex-col text-[11px] text-slate-400">Venue
          <select className="input" value={venue} onChange={(e) => { setVenue(e.target.value); setD(null); }}>
            <option value="spot">Spot — no leverage</option>
            <option value="futures">Binance Futures (USDⓈ-M)</option>
          </select>
        </label>
        {futures && (
          <label className="flex flex-col text-[11px] text-slate-400">Leverage you want
            <select className="input" value={leverage} onChange={(e) => { setLeverage(+e.target.value); setD(null); }}>
              {LEVERAGES.map((l) => <option key={l} value={l}>{l}×</option>)}
            </select>
          </label>
        )}
        <label className="flex flex-col text-[11px] text-slate-400">Risk mode
          <select className="input" value={mode} onChange={(e) => { setMode(e.target.value); setD(null); }}>
            <option value="conservative">Conservative — 1% per trade</option>
            <option value="growth">Growth — 2% per trade</option>
            <option value="aggressive">Aggressive — 3% per trade</option>
          </select>
        </label>
        <label className="flex flex-col text-[11px] text-slate-400">Your budget ($)
          <input className="input w-28" type="number" min={10} step={10} value={capText} onChange={(e) => setCapText(e.target.value)}
            onBlur={applyBudget} onKeyDown={(e) => e.key === "Enter" && applyBudget()} />
        </label>
      </div>
      {take && <TakeTrade draft={take} onClose={() => setTake(null)} onAdded={loadMine} />}
      {err && <div className="text-red-300">{err}</div>}
      {!d ? <div className="text-slate-500">Loading a year of daily candles…</div> : (
        <>
          <div className={`card p-3 text-[13px] ${d.market.btc_above_200d ? "border-emerald-500/30 text-emerald-200" : "border-amber-500/30 text-amber-200"}`}>
            {d.market.plain} <span className="text-slate-500">BTC {fmtPrice(d.market.btc_close)} · data to {d.as_of}</span>
          </div>
          <DeskPanel title="System desk — seven specialist agents" facts={d.agents} intel={intel} briefing={d.briefing} llm={d.llm} />
          <TopList rows={aiTop ? intel.top_coins : d.top_list}
            source={aiTop ? `ranked by the Scout agent (${intel.model}) over the analysis cards` : "ranked by the rules over every coin's analysis card; the AI Scout refines it when available"} />

          <div>
            <div className="mb-1 flex flex-wrap items-baseline gap-2 text-[12px]">
              <span className="font-medium text-slate-300">Signal cards — the system&apos;s candidates for the next daily open ({d.signal_cards.length})</span>
              <span className="text-slate-500">You choose: select a live card you actually place on Binance and it becomes a running trade below.</span>
            </div>
            {d.signal_cards.length === 0 ? (
              <div className="card p-3 text-[12px] text-slate-500">No candidate within 5% of a 20-day high. The system only buys a daily close above the 20-day high while BTC is above its 200-day average — waiting is part of the plan.</div>
            ) : (
              <div className="grid gap-3 lg:grid-cols-2 xl:grid-cols-3">
                {d.signal_cards.map((s: any) => <SystemSignalCard key={`${s.symbol}-${s.state}`} s={s} onSelect={() => takeSignal(s)} />)}
              </div>
            )}
          </div>

          <div>
            <div className="mb-1 flex items-baseline text-[12px]">
              <span className="font-medium text-slate-300">Your running trades ({mine?.open?.length ?? 0})</span>
              <a href="/portfolio" className="ml-auto text-[11px] text-sky-400">full portfolio, budget plan and record →</a>
            </div>
            {!mine ? <div className="text-[12px] text-slate-500">Loading…</div> : mine.open.length === 0 ? (
              <div className="card p-3 text-[12px] text-slate-500">None yet. After you place a live signal on Binance, select its card above: it shows here with live P&amp;L, margin, liquidation and the next action every day.</div>
            ) : (
              <div className="grid gap-3 lg:grid-cols-2 xl:grid-cols-3">
                {mine.open.map((c: any) => <OpenCard key={c.id} c={c} onPartial={() => act.partial(c)} onClose={() => act.close(c)} onRemove={() => act.remove(c)} />)}
              </div>
            )}
          </div>

          <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-8">
            <Stat label="System balance" value={usd(a.equity_usd)} tone={tone(a.equity_usd - a.start_usd)} />
            <Stat label={`Return since ${a.replay_from}`} value={pct(a.return_pct)} tone={tone(a.return_pct)} />
            <Stat label="Trades" value={`${a.trades}`} />
            <Stat label="Win rate" value={a.win_rate === undefined ? "—" : `${(a.win_rate * 100).toFixed(0)}%`} />
            <Stat label="Average per trade" value={a.avg_r === undefined ? "—" : `${a.avg_r > 0 ? "+" : ""}${a.avg_r.toFixed(2)}R`} tone={tone(a.avg_r ?? 0)} />
            <Stat label="Worst drop" value={`-${(a.max_drawdown_pct ?? 0).toFixed(1)}%`} tone={a.max_drawdown_pct > 0 ? "down" : undefined} />
            <Stat label="Risk per trade now" value={`${r.risk_now_pct}% (${usd(a.equity_usd * r.risk_now_pct / 100)})`} />
            <Stat label="Money at risk now" value={usd(a.open_risk_usd)} />
          </div>
          {d.budget_plan && <BudgetPlan p={d.budget_plan} />}
          <div className="card p-3">
            <div className="mb-1 flex text-[12px] text-slate-400">System balance over time
              <span className="ml-auto text-slate-500">daily, includes fees and slippage</span></div>
            <Spark values={d.equity_curve.map((p: any) => p.equity)} height={140} />
          </div>

          <div className="card overflow-auto p-2">
            <div className="px-1 pb-1 text-[12px] font-medium text-slate-300">System paper account — open positions</div>
            {d.open_positions.length === 0 ? <div className="px-1 text-[12px] text-slate-500">None — the system account is in cash.</div> : (
              <Table head={["Coin", "Since", "Entry", "Now", "Stop", ...(futures ? ["Leverage", "Liquidation"] : []), "Size", "P&L", "Plan"]} rows={d.open_positions.map((p: any) => [
                pair(p.symbol), p.opened, fmtPrice(p.entry), fmtPrice(p.now), fmtPrice(p.stop),
                ...(futures ? [`${liqOf(p.symbol)?.leverage_set ?? "—"}× (max safe ${liqOf(p.symbol)?.leverage_safe_max ?? "—"}×)`,
                  liqOf(p.symbol) ? fmtPrice(liqOf(p.symbol).liquidation) : "—"] : []),
                usd(p.size_usd),
                <span key="p" className={p.pnl_usd >= 0 ? "text-up" : "text-down"}>{usd(p.pnl_usd, true)} ({p.pnl_r > 0 ? "+" : ""}{p.pnl_r}R)</span>,
                <span key="x" className="text-slate-500">{p.plan}</span>])} />
            )}
          </div>

          {futures && (
            <div className="card p-3 text-[12px] text-slate-300">
              <div className="mb-1 font-medium">How to place a selected card on Binance (by hand — the platform never sends orders)</div>
              <ol className="list-decimal space-y-0.5 pl-5 text-slate-400">{fu.how_to.map((s: string) => <li key={s}>{s}</li>)}</ol>
              <p className="mt-2 text-slate-500">
                Costs used: taker {fu.rules.taker_fee_pct}% per side, liquidation fee {fu.rules.liquidation_fee_pct}%. Liquidation estimate:
                {" "}{fu.rules.liquidation_formula}, maintenance margin BTC {fu.rules.mmr_pct.BTCUSDT}%, ETH {fu.rules.mmr_pct.ETHUSDT}%, others {fu.rules.mmr_pct.others}%.
                Binance&apos;s own liquidation price (mark price, tiered margin) is the one that counts.{fu.filters_live ? " Lot sizes and minimums are live from Binance." : " Lot sizes are defaults (Binance was unreachable)."}
              </p>
            </div>
          )}

          <div className="card overflow-auto p-2">
            <div className="px-1 pb-1 text-[12px] font-medium text-slate-300">System paper account — closed trades</div>
            <Table head={["Coin", "Opened", "Closed", "Entry", "Exit", "Reached 2R", "Why closed", "Result"]} rows={d.recent_trades.map((t: any) => [
              pair(t.symbol), t.opened, t.closed, fmtPrice(t.entry), fmtPrice(t.exit), t.half_at_2r ? "yes — a third sold" : "no", t.why,
              <span key="r" className={t.pnl >= 0 ? "text-up" : "text-down"}>{usd(t.pnl, true)} ({t.r > 0 ? "+" : ""}{t.r}R)</span>])} />
          </div>

          <div className="grid gap-3 lg:grid-cols-2">
            <div className="card p-3 text-[12px] text-slate-300">
              <div className="mb-1 font-medium">Risk rules (the system enforces these, not you)</div>
              <ul className="list-disc space-y-0.5 pl-4 text-slate-400">
                <li>Each trade risks {r.risk_pct}% of the balance at the stop, fees and slippage included.</li>
                <li>At most {r.max_positions} open trades and {r.max_open_risk_pct}% total risk — coins fall together.</li>
                <li>At most {r.max_position_pct}% of the balance in one coin.{futures
                  ? ` Leverage is capped per trade so the liquidation sits at least ${fu.rules.liq_buffer}× the stop distance away — the stop closes a loser, never Binance.`
                  : " No leverage, so no liquidation."}</li>
                <li>Risk is halved after a {r.dd_half}% drop and quartered after {r.dd_quarter}%.</li>
                <li>New trades pause if the last {r.pause_window} trades average below {r.pause_below_r}R.</li>
                <li>Every trade: stop 2.5 ATR, a third sold at 2R, the rest trails 3 ATR below the close.</li>
              </ul>
            </div>
            <div className="card p-3 text-[12px] text-slate-300">
              <div className="mb-1 font-medium">Honest notes</div>
              <ul className="list-disc space-y-0.5 pl-4 text-slate-400">{d.honest_notes.map((n: string) => <li key={n}>{n}</li>)}</ul>
              {d.research_proof && (
                <Table head={["Mode", "Period ($100 start)", "End", "Per year", "Worst drop", "Trades"]}
                  rows={Object.entries(d.research_proof).flatMap(([m, per]: any) => Object.entries(per).filter(([k]) => k !== "rules").map(([k, v]: any) => [
                    <span key="m" className={m === mode ? "text-slate-100" : "text-slate-500"}>{m}</span>, k, usd(v.end_usd),
                    v.yearly_pct === undefined ? "—" : pct(v.yearly_pct), `-${v.max_drawdown_pct}%`, v.trades]))} />
              )}
            </div>
          </div>

          {ls && (
            <div className="grid gap-3 lg:grid-cols-2">
              <div className="card overflow-auto p-3 text-[12px] text-slate-300">
                <div className="mb-1 font-medium">What leverage does to this edge — the system&apos;s own {ls.liquidation.trades} trades, 2020-now</div>
                <p className="mb-2 text-slate-400">
                  Stops sit a median {ls.liquidation.median_stop_pct}% below entry; the median winner first fell {ls.liquidation.median_mae_of_winners_pct}% before it paid.
                  An isolated long is liquidated about 1/leverage below entry, so high leverage turns most winners into liquidations.
                </p>
                <Table head={["Leverage", "Liquidated at", "Trades liquidated", "Winners liquidated", "Avg per trade", "$100 at 2% risk →"]}
                  rows={Object.entries(ls.liquidation.by_leverage).map(([l, v]: any) => [
                    <span key="l" className={l === "20x" ? "text-amber-200" : undefined}>{l}</span>, `-${v.liq_distance_alt_pct}%`, `${v.liquidated_pct}%`,
                    `${v.winners_liquidated_pct ?? 0}%`, <span key="e" className={v.E_risk_sized > 0.4 ? "text-up" : "text-amber-200"}>{rr(v.E_risk_sized)}</span>, usd(v.end_usd_risk_sized_2pct)])} />
                <p className="mt-2 text-amber-200">
                  Traded the way most 20× traders do — 20% of the account as margin on every trade — $100 ended at {usd(ls.liquidation.by_leverage["20x"].end_usd_margin20pct)} (worst drop {ls.liquidation.by_leverage["20x"].max_drawdown_margin20pct}%).
                  A +20% trade is +10R at 2% risk: it happened {ls.liquidation.big_wins.ge_10R} times in {ls.liquidation.trades} trades (best {ls.liquidation.big_wins.best_R}R).
                </p>
              </div>
              <div className="card overflow-auto p-3 text-[12px] text-slate-300">
                <div className="mb-1 font-medium">Win rate vs payoff — same signal and stop, different targets</div>
                <p className="mb-2 text-slate-400">Win rates and average result per trade after costs, in the 2020-23 / 2024 / 2025-26 periods. No target reaches 90%; the most accurate one earns the least.</p>
                <Table head={["Target", "Win rate", "Avg per trade", "All periods"]} rows={ls.winrate_vs_target.map((x: any) => [
                  <span key="t" className={x.target_R === 2 ? "text-slate-100" : undefined}>{x.target_R}R</span>, wins(x), avgs(x),
                  x.pooled ? `${(x.pooled.win * 100).toFixed(0)}% · ${rr(x.pooled.E)}` : "—"])} />
                <div className="mb-1 mt-3 font-medium">Stops tight enough for 20× (target 2R)</div>
                <Table head={["Candles", "Stop", "Win rate", "Avg per trade", "Costs / risk"]} rows={ls.tight_stops.map((x: any) => [
                  x.tf, typeof x.stop_pct === "number" ? `${x.stop_pct}%${x.note ? " — " + x.note : ""}` : x.stop_pct, wins(x), avgs(x),
                  x.cost_share_of_risk_pct !== undefined ? `${x.cost_share_of_risk_pct}%` : "—"])} />
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
