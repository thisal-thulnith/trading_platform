"use client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import BudgetPlan from "@/components/BudgetPlan";
import DeskPanel, { useDesk } from "@/components/DeskPanel";
import TakeTrade, { type TradeDraft } from "@/components/TakeTrade";
import { Stat } from "@/components/ui/Stats";
import { api } from "@/lib/api";
import { fmtPrice, pair } from "@/lib/format";

const usd = (v: number | null | undefined, sign = false) =>
  v === null || v === undefined || !isFinite(v) ? "—" : `${sign && v > 0 ? "+" : ""}${v < 0 ? "-" : ""}$${Math.abs(v).toFixed(2)}`;
const pct = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}%`);
const tone = (v: number | null | undefined) => (v === null || v === undefined ? undefined : v > 0 ? "up" : v < 0 ? "down" : undefined);
const LEVERAGES = [1, 2, 3, 5, 10, 20];
const KEY = "my_portfolio_settings";

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return <div className="flex justify-between gap-2"><span className="text-slate-500">{k}</span><span className="num text-slate-200">{v}</span></div>;
}

/** Binance-style position card for one open trade. */
export function OpenCard({ c, onPartial, onClose, onRemove }: { c: any; onPartial: () => void; onClose: () => void; onRemove: () => void }) {
  const up = (c.pnl_usd ?? 0) >= 0;
  return (
    <div className={`card border-l-2 p-3 text-[12px] ${up ? "border-l-emerald-500/60" : "border-l-red-500/60"}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold text-slate-100">{pair(c.symbol)}</span>
        <span className="chip bg-white/5 text-slate-300">{c.venue === "futures" ? `Perp · ${c.margin_mode} ${c.leverage}×` : "Spot"}</span>
        <span className="chip bg-emerald-500/10 text-emerald-300">{c.side}</span>
        {c.partial_done && <span className="chip bg-sky-500/10 text-sky-300">third sold at 2R</span>}
        <span className="ml-auto text-slate-500">day {c.days} · {c.source === "system" ? "system ticket" : "manual"}</span>
      </div>
      <div className={`num mt-2 text-xl ${up ? "text-up" : "text-down"}`}>
        {usd(c.pnl_usd, true)} <span className="text-sm">({pct(c.roe_pct)} ROE · {c.r === null ? "—" : `${c.r > 0 ? "+" : ""}${c.r}R`})</span>
      </div>
      <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 md:grid-cols-4">
        <Row k="Size" v={`${c.qty_open} (${usd(c.size_usd)})`} />
        <Row k="Entry" v={fmtPrice(c.entry)} />
        <Row k="Mark" v={c.mark ? `${fmtPrice(c.mark)} (${pct(c.pnl_pct)})` : "—"} />
        {c.venue === "futures" ? <Row k="Liq. price" v={c.liquidation ? `${fmtPrice(c.liquidation)} (${c.liq_distance_pct}% away)` : "—"} /> : <Row k="Margin" v={usd(c.margin_usd)} />}
        {c.venue === "futures" && <Row k="Margin" v={usd(c.margin_usd)} />}
        <Row k="Stop" v={<span className={c.recommended_stop > c.stop ? "text-amber-200" : ""}>{fmtPrice(c.stop)}{c.recommended_stop > c.stop ? ` → ${fmtPrice(c.recommended_stop)}` : ""}</span>} />
        <Row k="TP (2R, a third)" v={fmtPrice(c.target)} />
        <Row k="Risk at stop" v={`${usd(c.open_risk_usd)} of ${usd(c.risk_usd)}`} />
        {c.banked_usd > 0 && <Row k="Banked" v={usd(c.banked_usd, true)} />}
      </div>
      <div className={`mt-2 rounded p-2 ${c.level === "warn" ? "bg-amber-500/10 text-amber-200" : c.level === "good" ? "bg-emerald-500/10 text-emerald-200" : "bg-white/5 text-slate-300"}`}>{c.plan}</div>
      <div className="mt-2 flex flex-wrap gap-2">
        {!c.partial_done && <button className="btn" onClick={onPartial}>I sold a third at 2R</button>}
        <button className="btn" onClick={onClose}>I closed it</button>
        <button className="btn ml-auto text-slate-500" onClick={onRemove}>Remove</button>
      </div>
      {c.note && <div className="mt-1 text-slate-500">{c.note}</div>}
    </div>
  );
}

function ClosedCard({ c }: { c: any }) {
  const up = c.pnl_usd >= 0;
  return (
    <div className={`card flex flex-wrap items-center gap-x-4 gap-y-1 border-l-2 px-3 py-2 text-[12px] ${up ? "border-l-emerald-500/60" : "border-l-red-500/60"}`}>
      <span className="font-semibold text-slate-100">{pair(c.symbol)}</span>
      <span className="chip bg-white/5 text-slate-300">{c.venue === "futures" ? `Perp ${c.leverage}×` : "Spot"}</span>
      <span className="text-slate-500">{c.opened_at?.slice(0, 10)} → {c.closed_at?.slice(0, 10)}{c.days !== null ? ` (${c.days}d)` : ""}</span>
      <span className="num text-slate-400">{fmtPrice(c.entry)} → {fmtPrice(c.exit)}{c.partial_done ? ` · third at ${fmtPrice(c.partial_price)}` : ""}</span>
      <span className="text-slate-500">{c.reason}</span>
      <span className={`num ml-auto text-base ${up ? "text-up" : "text-down"}`}>{usd(c.pnl_usd, true)} <span className="text-xs">({pct(c.roe_pct)} ROE · {c.r > 0 ? "+" : ""}{c.r}R)</span></span>
    </div>
  );
}

/** Confirm-and-record actions for an open card; shared by the portfolio page and the system page. */
export function useTradeActions(reload: () => void) {
  const partial = async (c: any) => {
    const price = prompt(`${pair(c.symbol)}: price you sold a third at`, String(c.target));
    if (!price) return;
    await api(`/my/trades/${c.id}/partial`, { method: "POST", json: { price: +price } }).catch((e) => alert(e.message));
    reload();
  };
  const close = async (c: any) => {
    const price = prompt(`${pair(c.symbol)}: exit price`, String(c.mark ?? c.entry));
    if (!price) return;
    const reason = prompt("Why did it close? (stop, target, trail, time limit, manual)", c.action === "close" ? "stop" : "manual") || "manual";
    await api(`/my/trades/${c.id}/close`, { method: "POST", json: { price: +price, reason } }).catch((e) => alert(e.message));
    reload();
  };
  const remove = async (c: any) => {
    if (!confirm(`Remove ${pair(c.symbol)} from your portfolio? This deletes the record.`)) return;
    await api(`/my/trades/${c.id}`, { method: "DELETE" }).catch((e) => alert(e.message));
    reload();
  };
  return { partial, close, remove };
}

export default function MyTrades() {
  const [s, setS] = useState({ budget: 100, mode: "growth", venue: "futures" as "spot" | "futures", leverage: 3 });
  const [budgetText, setBudgetText] = useState("100");
  const [d, setD] = useState<any>(null);
  const [err, setErr] = useState("");
  const [adding, setAdding] = useState<TradeDraft | null>(null);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    try { const raw = localStorage.getItem(KEY); if (raw) { const v = JSON.parse(raw); setS(v); setBudgetText(String(v.budget)); } } catch {}
    setLoaded(true);
  }, []);
  useEffect(() => { if (loaded) { try { localStorage.setItem(KEY, JSON.stringify(s)); } catch {} } }, [s, loaded]);
  const load = useCallback(() => api(`/my/portfolio?budget=${s.budget}&mode=${s.mode}&venue=${s.venue}&leverage=${s.leverage}`)
    .then((r) => { setD(r); setErr(""); }).catch((e) => setErr(e.message)), [s]);
  useEffect(() => { if (!loaded) return; load(); const i = setInterval(load, 30_000); return () => clearInterval(i); }, [load, loaded]);
  const applyBudget = () => { const v = Math.round(+budgetText); if (v >= 10 && v <= 1_000_000 && v !== s.budget) setS({ ...s, budget: v }); };
  // the desk re-runs when the settings or the recorded trades change (the backend caches by that signature)
  const sig = d ? `${d.open.length}.${d.open.filter((c: any) => c.partial_done).length}.${d.closed.length}` : "";
  const intel = useDesk(d ? `/my/desk?budget=${s.budget}&mode=${s.mode}&venue=${s.venue}&leverage=${s.leverage}&v=${sig}` : null);

  const act = useTradeActions(load);
  const st = d?.stats;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <h1 className="text-lg font-semibold text-slate-100">My portfolio — the trades you took</h1>
          <p className="max-w-3xl text-[12px] text-slate-500">
            Type your budget; the agents turn it into the settings they invest with. Take a ticket from the <Link href="/system" className="text-sky-400">System account</Link> and
            press “I took this trade”: it appears here as a card, marked to the live price, with the next action every day. You place the orders; the platform tracks and manages.
          </p>
        </div>
        <label className="ml-auto flex flex-col text-[11px] text-slate-400">Your budget ($)
          <input className="input w-28" type="number" min={10} step={10} value={budgetText} onChange={(e) => setBudgetText(e.target.value)}
            onBlur={applyBudget} onKeyDown={(e) => e.key === "Enter" && applyBudget()} />
        </label>
        <label className="flex flex-col text-[11px] text-slate-400">Risk mode
          <select className="input" value={s.mode} onChange={(e) => setS({ ...s, mode: e.target.value })}>
            <option value="conservative">Conservative — 1% per trade</option>
            <option value="growth">Growth — 2% per trade</option>
            <option value="aggressive">Aggressive — 3% per trade</option>
          </select>
        </label>
        <label className="flex flex-col text-[11px] text-slate-400">Venue
          <select className="input" value={s.venue} onChange={(e) => setS({ ...s, venue: e.target.value as any })}>
            <option value="spot">Spot</option><option value="futures">Binance Futures (USDⓈ-M)</option>
          </select>
        </label>
        {s.venue === "futures" && (
          <label className="flex flex-col text-[11px] text-slate-400">Leverage you want
            <select className="input" value={s.leverage} onChange={(e) => setS({ ...s, leverage: +e.target.value })}>
              {LEVERAGES.map((l) => <option key={l} value={l}>{l}×</option>)}
            </select>
          </label>
        )}
      </div>
      {err && <div className="text-red-300">{err}</div>}
      {!d ? <div className="text-slate-500">Loading your portfolio…</div> : (
        <>
          <DeskPanel title="Portfolio desk — three agents" facts={d.agents} intel={intel} llm={d.llm} />
          <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-8">
            <Stat label="Budget" value={usd(s.budget)} />
            <Stat label="Open trades" value={`${st.open}`} />
            <Stat label="Margin in use" value={usd(st.margin_in_use_usd)} />
            <Stat label="Risk at stops" value={usd(st.open_risk_usd)} />
            <Stat label="Unrealised" value={usd(st.unrealized_usd, true)} tone={tone(st.unrealized_usd)} />
            <Stat label="Realised" value={usd(st.realized_usd, true)} tone={tone(st.realized_usd)} />
            <Stat label="Total P&L" value={`${usd(st.total_usd, true)} (${pct(st.total_usd / s.budget * 100)})`} tone={tone(st.total_usd)} />
            <Stat label="Win rate · avg R" value={st.win_rate === null ? `— (${st.closed} closed)` : `${(st.win_rate * 100).toFixed(0)}% · ${st.avg_r > 0 ? "+" : ""}${st.avg_r}R`} />
          </div>
          <BudgetPlan p={d.budget_plan} />
          <div className="flex items-center">
            <div className="text-[12px] font-medium text-slate-300">Open trades ({d.open.length})</div>
            <button className="btn ml-auto" onClick={() => setAdding({ symbol: "BTCUSDT", venue: s.venue, leverage: s.venue === "futures" ? Math.min(s.leverage, 3) : 1, entry: 0, qty: 0, stop: 0, target: 0, budget_usd: s.budget, mode: s.mode, source: "manual" })}>
              + Add a trade I took by hand
            </button>
          </div>
          {d.open.length === 0 ? (
            <div className="card p-3 text-[12px] text-slate-500">No open trades. Tickets appear on the System account after a daily close above a 20-day high while BTC is in an uptrend; press “I took this trade” there.</div>
          ) : (
            <div className="grid gap-3 lg:grid-cols-2 xl:grid-cols-3">
              {d.open.map((c: any) => <OpenCard key={c.id} c={c} onPartial={() => act.partial(c)} onClose={() => act.close(c)} onRemove={() => act.remove(c)} />)}
            </div>
          )}
          <div className="text-[12px] font-medium text-slate-300">Closed trades ({d.closed.length})</div>
          {d.closed.length === 0 ? <div className="card p-3 text-[12px] text-slate-500">Nothing closed yet. Closed trades stay here with their realised profit or loss.</div> : (
            <div className="space-y-2">{d.closed.map((c: any) => <ClosedCard key={c.id} c={c} />)}</div>
          )}
          <p className="text-[11px] text-slate-600">{d.marks_note}</p>
        </>
      )}
      {adding && <TakeTrade draft={adding} onClose={() => setAdding(null)} onAdded={load} />}
    </div>
  );
}
