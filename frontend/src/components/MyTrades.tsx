"use client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import BudgetPlan from "@/components/BudgetPlan";
import DeskPanel, { useDesk } from "@/components/DeskPanel";
import TakeTrade, { type TradeDraft } from "@/components/TakeTrade";
import { Card, More, Note, PageHead } from "@/components/ui/Page";
import { Stat } from "@/components/ui/Stats";
import { api } from "@/lib/api";
import { AccountCard, TodayPlan, type Account } from "@/components/portfolio/AccountPlan";
import AddTrade, { type AddDraft } from "@/components/portfolio/AddTrade";
import { fmtPrice, pair } from "@/lib/format";

const usd = (v: number | null | undefined, sign = false) =>
  v === null || v === undefined || !isFinite(v) ? "—" : `${sign && v > 0 ? "+" : ""}${v < 0 ? "-" : ""}$${Math.abs(v).toFixed(2)}`;
const pct = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}%`);
const tone = (v: number | null | undefined) => (v === null || v === undefined ? undefined : v > 0 ? "up" : v < 0 ? "down" : undefined);

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return <div className="flex justify-between gap-2"><span className="text-slate-500">{k}</span><span className="num text-slate-200">{v}</span></div>;
}

/** Binance-style position card for one open trade. */
const SOURCE: Record<string, string> = { planner: "platform signal", manual: "my trade", agent: "agents' plan", lab: "Lab signal", system: "system ticket" };

/** Where the price is between the stop and the take profit (0 = stop, 1 = take profit), and where the entry sits. */
function Progress({ c }: { c: any }) {
  const d = c.side === "SHORT" ? -1 : 1;
  const span = (c.target - c.stop) * d;
  if (!c.mark || !(span > 0)) return null;
  const at = Math.max(0, Math.min(1, ((c.mark - c.stop) * d) / span));
  const ent = Math.max(0, Math.min(1, ((c.entry - c.stop) * d) / span));
  return (
    <div className="mt-3" title={`Stop ${fmtPrice(c.stop)} · entry ${fmtPrice(c.entry)} · now ${fmtPrice(c.mark)} · take profit ${fmtPrice(c.target)}`}>
      <div className="relative h-2 rounded-full bg-gradient-to-r from-red-500/40 via-slate-500/20 to-emerald-500/40">
        <span className="absolute top-[-3px] h-[14px] w-px bg-slate-400" style={{ left: `${ent * 100}%` }} />
        <span className={`absolute top-[-4px] h-4 w-4 -translate-x-1/2 rounded-full border-2 border-white ${at >= ent ? "bg-emerald-500" : "bg-red-500"}`} style={{ left: `${at * 100}%` }} />
      </div>
      <div className="mt-1 flex justify-between text-[10.5px] text-slate-500"><span>stop {fmtPrice(c.stop)}</span><span>entry</span><span>take profit {fmtPrice(c.target)}</span></div>
    </div>
  );
}

export function OpenCard({ c, onPartial, onClose, onRemove, onCloseAt }: { c: any; onPartial: () => void; onClose: () => void; onRemove: () => void;
  onCloseAt?: (price: number, reason: string) => void }) {
  const up = (c.pnl_usd ?? 0) >= 0;
  const sys = c.source === "system";
  const moved = c.side === "SHORT" ? c.recommended_stop < c.stop : c.recommended_stop > c.stop;
  return (
    <div className={`card border-l-2 p-3 text-[12px] ${up ? "border-l-emerald-500/60" : "border-l-red-500/60"}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold text-slate-100">{pair(c.symbol)}</span>
        <span className="chip bg-white/5 text-slate-300">{c.venue === "futures" ? `Perp · ${c.margin_mode} ${c.leverage}×` : "Spot"}</span>
        <span className={`chip ${c.side === "SHORT" ? "bg-red-500/10 text-red-300" : "bg-emerald-500/10 text-emerald-300"}`}>{c.side}</span>
        {c.partial_done && <span className="chip bg-sky-500/10 text-sky-300">third taken at 2R</span>}
        <span className="ml-auto text-slate-500" title={c.opened_at ? `opened ${new Date(c.opened_at).toLocaleString()}` : ""}>day {c.days} · {SOURCE[c.source] ?? c.source}</span>
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
        <Row k="Stop" v={<span className={moved ? "text-amber-200" : ""}>{fmtPrice(c.stop)}{moved ? ` → ${fmtPrice(c.recommended_stop)}` : ""}</span>} />
        <Row k={sys ? "TP (2R, a third)" : "Take profit"} v={fmtPrice(c.target)} />
        <Row k="Risk at stop" v={`${usd(c.open_risk_usd)} of ${usd(c.risk_usd)}`} />
        {c.banked_usd > 0 && <Row k="Banked" v={usd(c.banked_usd, true)} />}
      </div>
      <Progress c={c} />
      <div className={`mt-2 rounded p-2 ${c.level === "warn" ? "bg-amber-500/10 text-amber-200" : c.level === "good" ? "bg-emerald-500/10 text-emerald-200" : "bg-white/5 text-slate-300"}`}>{c.plan}</div>
      <div className="mt-2 flex flex-wrap gap-2">
        {!c.partial_done && sys && <button className="btn" onClick={onPartial}>{c.side === "SHORT" ? "I bought back a third at 2R" : "I sold a third at 2R"}</button>}
        {onCloseAt && <button className="btn" onClick={() => onCloseAt(c.target, "target")} title={`Record the close at the take profit ${fmtPrice(c.target)}`}>Take profit hit</button>}
        {onCloseAt && <button className="btn" onClick={() => onCloseAt(c.stop, "stop")} title={`Record the close at the stop ${fmtPrice(c.stop)}`}>Stop hit</button>}
        <button className="btn" onClick={onClose}>{onCloseAt ? "Closed at another price" : "I closed it"}</button>
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
    const price = prompt(`${pair(c.symbol)}: price you ${c.side === "SHORT" ? "bought back" : "sold"} a third at`, String(c.target));
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
  const closeAt = async (c: any, price: number, reason: string) => {
    if (!confirm(`${pair(c.symbol)}: record the close at ${fmtPrice(price)} (${reason === "target" ? "take profit" : "stop"})?`)) return;
    await api(`/my/trades/${c.id}/close`, { method: "POST", json: { price, reason } }).catch((e) => alert(e.message));
    reload();
  };
  const remove = async (c: any) => {
    if (!confirm(`Remove ${pair(c.symbol)} from your portfolio? This deletes the record.`)) return;
    await api(`/my/trades/${c.id}`, { method: "DELETE" }).catch((e) => alert(e.message));
    reload();
  };
  return { partial, close, remove, closeAt };
}

/** The portfolio page body: your account (balance once; the Risk manager picks the risk mode), today's plan sized
 *  for it (accept after placing it on Binance), your open and closed trades. `extra` goes under "More details". */
export default function MyTrades({ extra }: { extra?: React.ReactNode }) {
  const [acc, setAcc] = useState<Account | null>(null);
  const [d, setD] = useState<any>(null);
  const [err, setErr] = useState("");
  const [adding, setAdding] = useState<TradeDraft | null>(null);
  const [runAi, setRunAi] = useState(false);
  const [mine, setMine] = useState<AddDraft | null>(null);
  useEffect(() => {   // a direct link (quick search, "Add a trade"): open the form at once
    const a = new URLSearchParams(window.location.search).get("add");
    if (a) setMine({ symbol: a === "1" ? "" : a, side: "LONG", entry: 0, stop: 0, target: 0, margin: 50, leverage: 1, source: "manual" });
  }, []);
  const loadAcc = useCallback(() => api<Account>("/my/account").then((a) => { setAcc(a); setErr(""); }).catch((e) => setErr(e.message)), []);
  useEffect(() => { loadAcc(); const i = setInterval(loadAcc, 60_000); return () => clearInterval(i); }, [loadAcc]);
  // the settings follow your account and the Risk manager; nothing to pick by hand
  const s = {
    budget: acc?.account ? Math.max(10, Math.min(1_000_000, Math.round(acc.account.equity))) : 100,
    mode: acc?.risk?.mode ?? "conservative", venue: "futures" as const, leverage: acc?.suggestion?.leverage ?? 3,
  };
  const load = useCallback(() => api(`/my/portfolio?budget=${s.budget}&mode=${s.mode}&venue=${s.venue}&leverage=${s.leverage}`)
    .then((r) => { setD(r); setErr(""); }).catch((e) => setErr(e.message)), [s.budget, s.mode, s.venue, s.leverage]);
  useEffect(() => { if (!acc) return; load(); const i = setInterval(load, 30_000); return () => clearInterval(i); }, [load, acc]);
  const reload = () => { loadAcc(); load(); };
  // the AI portfolio desk runs only when you press the button (it spends AI credits); then it re-runs on trade changes
  const sig = d ? `${d.open.length}.${d.open.filter((c: any) => c.partial_done).length}.${d.closed.length}` : "";
  const intel = useDesk(d ? `/my/desk?budget=${s.budget}&mode=${s.mode}&venue=${s.venue}&leverage=${s.leverage}&v=${sig}${runAi ? "&run=true" : ""}` : null);

  const act = useTradeActions(reload);
  const st = d?.stats;
  return (
    <>
      <PageHead title="My portfolio"
        sub={<span>Your account, today&apos;s plan and your trades · you place the orders on Binance</span>} />
      {err && <Note tone="bad">{err}</Note>}
      {!acc ? <div className="text-[13.5px] text-slate-500">Loading your account…</div> : (
        <>
          <AccountCard a={acc} onSaved={(a) => { setAcc(a); load(); }} />
          {acc.set && <TodayPlan a={acc} onAccepted={reload} onCustom={setMine} />}

          {d && <>
            <Card title={`Open trades (${d.open.length})`}
              right={<div className="flex gap-2">
                <button className="btn btn-primary" onClick={() => setMine({ symbol: "", side: "LONG", entry: 0, stop: 0, target: 0, margin: 50, leverage: 1, source: "manual", mode: s.mode })}
                  title="Record a trade you placed, with your own entry, margin, leverage, stop, take profit and time">+ Add a trade</button>
                <button className="btn btn-ghost" onClick={() => setAdding({ symbol: "BTCUSDT", venue: s.venue, leverage: 1, entry: 0, qty: 0, stop: 0, target: 0, budget_usd: s.budget, mode: s.mode, source: "agent" })}
                  title="The AI agents plan the size, stop and target (uses AI credits)">Let the AI plan one</button></div>}>
              {d.open.length === 0 ? (
                <div className="text-[13px] text-slate-500">No open trades. {acc?.set ? "Take today's plan above, or press “I took this trade” on any signal, or add a trade you placed." : "Set your balance above first; then take a signal with “I took this trade”, or add a trade you placed."}</div>
              ) : (
                <div className="grid gap-3 lg:grid-cols-2 xl:grid-cols-3">
                  {d.open.map((c: any) => <OpenCard key={c.id} c={c} onPartial={() => act.partial(c)} onClose={() => act.close(c)} onRemove={() => act.remove(c)}
                    onCloseAt={(price, reason) => act.closeAt(c, price, reason)} />)}
                </div>
              )}
            </Card>

            <Card title={`Closed trades (${d.closed.length})`}
              right={st && st.closed > 0 ? <span className="text-[12.5px] text-slate-500">won {(st.win_rate * 100).toFixed(0)}% · {st.avg_r > 0 ? "+" : ""}{st.avg_r}R average · {usd(st.realized_usd, true)}</span> : undefined}>
              {d.closed.length === 0 ? <div className="text-[13px] text-slate-500">Nothing closed yet.</div> : (
                <div className="space-y-2">{d.closed.map((c: any) => <ClosedCard key={c.id} c={c} />)}</div>
              )}
            </Card>

            <More>
              <DeskPanel title="AI portfolio desk" facts={d.agents} intel={intel} llm={d.llm} onRun={() => setRunAi(true)} />
              <BudgetPlan p={d.budget_plan} />
              {extra}
              {d.marks_note && <p className="text-[12px] text-slate-500">{d.marks_note}</p>}
            </More>
          </>}
        </>
      )}
      {adding && <TakeTrade draft={adding} onClose={() => setAdding(null)} onAdded={reload} />}
      {mine && <AddTrade draft={mine} equity={acc?.account?.equity ?? 0} onClose={() => setMine(null)} onAdded={reload} />}
    </>
  );
}
