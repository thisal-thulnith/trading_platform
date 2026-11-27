"use client";
import { Bot, Loader2, Pencil } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { fmtPrice, pair } from "@/lib/format";
import ValidationPanel, { type Validation } from "./desk/ValidationPanel";

export type Side = "LONG" | "SHORT";
export type TradeDraft = {
  symbol: string; side?: Side; venue: "spot" | "futures"; leverage: number; entry: number; qty: number; stop: number;
  target: number; budget_usd: number; mode: string; source: "system" | "manual" | "agent"; opened_at?: string;
  note?: string; tf?: string;
  /** an agent plan the research desk already computed (skips the request) */
  plan?: Plan;
  /** which leverage option to start with (key from plan.leverage_options) */
  option?: string;
};
export type LevOption = {
  key: string; title: string; leverage: number; qty: number; notional_usd: number; margin_usd: number; liquidation: number;
  liquidation_pct: number; liquidated_before_stop: boolean; loss_at_stop_usd: number; loss_pct_of_budget: number;
  profit_at_target_usd: number; profit_pct_of_budget: number; roe_at_target_pct: number; roe_at_stop_pct: number;
  risk_level: "safe" | "risky" | "very risky" | "extreme"; note: string;
};
export type Plan = {
  ok: boolean; error?: string | null; symbol: string; side: Side; venue: string; entry: number; stop: number; target: number;
  stop_pct: number; target_pct: number; qty: number; notional_usd: number; leverage: number; leverage_safe_max: number;
  margin_usd: number; liquidation: number | null; liquidation_pct: number | null; risk_usd: number; risk_pct_of_budget: number | null;
  reward_usd: number; rr_net: number | null; fees_usd: number; orders: string[]; explain: string[]; notes: string[];
  warnings: string[]; entry_type?: string; leverage_options?: LevOption[]; sizing?: "margin" | "budget";
  margin_input?: number | null; risk_pct_of_margin?: number | null; reward_pct_of_margin?: number | null; margin_risk_limit_pct?: number | null;
};
type Quick = { symbol: string; side: Side; why_side: string; opinion: string; agrees_with_read: boolean; plan: Plan; validation?: Validation;
  by?: string; model?: string; confidence?: number | null };

/** Next UTC midnight — the daily open where a system ticket is meant to be filled. */
export const nextDailyOpen = () => { const d = new Date(); return new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate() + 1)).toISOString(); };

const usd = (v: number | null | undefined) => (v === null || v === undefined || !isFinite(v) ? "—" : `$${v.toFixed(2)}`);

function Cell({ k, v, sub, tone }: { k: string; v: React.ReactNode; sub?: React.ReactNode; tone?: string }) {
  return (
    <div className={`rounded p-2 ${tone ?? "bg-white/5"}`}>
      <div className="text-[11px] font-medium text-slate-500">{k}</div>
      <div className="num text-[13px] font-semibold text-slate-100">{v}</div>
      {sub && <div className="text-[10px] text-slate-500">{sub}</div>}
    </div>
  );
}

/** The agents' plan as a Binance-style ticket: every number comes from the server's plan, none from the user. */
const LEVEL: Record<string, string> = { safe: "bg-emerald-500/15 text-emerald-300", risky: "bg-amber-500/15 text-amber-300",
  "very risky": "bg-orange-500/20 text-orange-300", extreme: "bg-red-500/20 text-red-300" };

/** The safe plan first, then the 10x / 20x ways to place the same trade, with the dollars each one loses and makes. */
export function LeverageOptions({ p, selected, onPick }: { p: Plan; selected?: string; onPick?: (o: LevOption) => void }) {
  const opts = p.leverage_options ?? [];
  const byMargin = p.sizing === "margin";
  if (opts.length < 2) return null;
  return (
    <div className="overflow-x-auto rounded border border-line">
      <div className="border-b border-line px-2 py-1 text-[11px] font-medium text-slate-300">Leverage options — same entry, SL and TP. {byMargin ? "The agents' pick first; other leverages below." : "Safe first; risky below."}
        <span className="font-normal text-slate-500">{byMargin ? " Same margin: more leverage is a bigger position, so profit, loss and liquidation risk all grow." : " “Less margin” keeps the same loss; “bigger” keeps the margin, so profit and loss grow."}{onPick ? " Click a row to take that option." : ""}</span></div>
      <table className="w-full whitespace-nowrap text-[11px]">
        <thead className="text-left text-[11px] font-medium text-slate-500">
          <tr><th className="px-2 py-1">Option</th><th className="px-1">{byMargin ? "Position" : "Margin"}</th><th className="px-1">Liquidation</th>
            <th className="px-1">SL loss · TP profit</th><th className="px-1">Risk</th></tr>
        </thead>
        <tbody>
          {opts.map((o) => (
            <tr key={o.key} title={o.note} onClick={onPick ? () => onPick(o) : undefined}
              className={`border-t border-line/60 ${onPick ? "cursor-pointer hover:bg-white/5" : ""} ${selected === o.key ? "bg-sky-500/15 outline outline-1 outline-sky-500/50" : ""}`}>
              <td className="px-2 py-1 text-slate-200">{selected === o.key ? "✓ " : ""}{o.title}</td>
              <td className="num px-1" title={byMargin ? `margin ${usd(o.margin_usd)}` : `position ${usd(o.notional_usd)}`}>{usd(byMargin ? o.notional_usd : o.margin_usd)}</td>
              <td className={`num px-1 ${o.liquidated_before_stop ? "text-red-300" : ""}`} title={o.liquidated_before_stop ? "liquidated before the stop" : `${o.liquidation_pct}% away`}>
                {fmtPrice(o.liquidation)}<span className="text-slate-500"> {o.liquidated_before_stop ? "before SL" : `${o.liquidation_pct}%`}</span></td>
              <td className="num px-1" title={`${o.loss_pct_of_budget}% of the ${byMargin ? "margin" : "budget"} at the stop · ${o.roe_at_target_pct}% return on margin at the target`}>
                <span className="text-red-300">−{usd(o.loss_at_stop_usd)}</span> · <span className="text-emerald-300">+{usd(o.profit_at_target_usd)}</span></td>
              <td className="px-1"><span className={`chip ${LEVEL[o.risk_level]}`}>{o.risk_level}</span></td>
            </tr>
          ))}
        </tbody>
      </table>
      {opts.filter((o) => o.key !== "safe" && o.risk_level !== "safe").slice(-1).map((o) => (
        <div key={o.key} className="border-t border-line px-2 py-1 text-[10px] text-slate-500">{o.note} Research: 20x with 20% margin per trade took $100 to about $4 (2020-26).</div>
      ))}
    </div>
  );
}

/** The plan's numbers as a ticket grid: side, entry, leverage, SL with the $ loss, TP with the $ profit, margin, size,
 *  risk and liquidation. */
export function PlanCells({ p, cols = 3 }: { p: Plan; cols?: 3 | 5 }) {
  const short = p.side === "SHORT";
  if (p.entry === undefined || p.stop === undefined)
    return <div className="rounded bg-red-500/10 px-2 py-1.5 text-[12px] text-red-300">No plan for {pair(p.symbol) || "this coin"}: {p.error ?? "the inputs cannot make a sane trade"}.</div>;
  return (
    <div className={`grid gap-1.5 ${cols === 5 ? "grid-cols-3 xl:grid-cols-5" : "grid-cols-3"}`}>
      <Cell k="Side" v={<span className={short ? "text-down" : "text-up"}>{p.side}</span>} sub={p.venue === "futures" ? `Isolated ${p.leverage}×` : "Spot"} />
      <Cell k="Entry" v={fmtPrice(p.entry)} sub={p.entry_type ?? "market"} />
      <Cell k="Stop loss (SL)" v={fmtPrice(p.stop)} sub={<><span className="text-red-300">loss −{usd(p.risk_usd)}</span> · {p.stop_pct}% {short ? "above" : "below"}</>} tone="bg-red-500/10" />
      <Cell k="Take profit (TP)" v={fmtPrice(p.target)} sub={<><span className="text-emerald-300">profit +{usd(p.reward_usd)}</span> · {p.target_pct}% · {p.rr_net ?? "—"}R</>} tone="bg-emerald-500/10" />
      <Cell k="Leverage" v={`${p.leverage}×`} sub={p.venue === "futures" ? `safe max ${p.leverage_safe_max}×` : "no leverage"} />
      <Cell k="Margin" v={usd(p.margin_usd)} sub={`position ${usd(p.notional_usd)}`} />
      <Cell k="Quantity" v={p.qty} sub={pair(p.symbol).split("/")[0]} />
      <Cell k="Risk at stop" v={usd(p.risk_usd)} sub={p.sizing === "margin" ? `${p.risk_pct_of_margin}% of your margin` : `${p.risk_pct_of_budget}% of budget`} tone="bg-amber-500/10" />
      <Cell k="Return at target" v={`+${usd(p.reward_usd)}`} sub={p.sizing === "margin" ? `${p.reward_pct_of_margin}% of your margin` : `${p.rr_net ?? "—"}R after fees`} tone="bg-emerald-500/10" />
      <Cell k="Liquidation" v={p.liquidation ? fmtPrice(p.liquidation) : "none"} sub={p.liquidation_pct ? `${p.liquidation_pct}% away` : "spot"} />
    </div>
  );
}

export function PlanNotes({ p }: { p: Plan }) {
  return (
    <>
      {[...(p.notes ?? []), ...(p.warnings ?? [])].map((w) => <div key={w} className="rounded bg-amber-500/10 px-2 py-1 text-[11px] text-amber-200">{w}</div>)}
      {!p.ok && p.error && p.entry !== undefined && <div className="rounded bg-red-500/10 px-2 py-1 text-[11px] text-red-300">{p.error}</div>}
    </>
  );
}

export function PlanHowTo({ p }: { p: Plan }) {
  return (
    <div className="space-y-2 text-[11px] text-slate-400">
      {p.explain?.length > 0 && <ul className="list-disc space-y-0.5 pl-4">{p.explain.map((x) => <li key={x}>{x}</li>)}</ul>}
      {p.orders?.length > 0 && (
        <div>
          <div className="font-medium text-slate-300">Type this into Binance (you place it; nothing is sent):</div>
          <ol className="list-decimal pl-4">{p.orders.map((o) => <li key={o}>{o}</li>)}</ol>
        </div>
      )}
    </div>
  );
}

export function PlanTicket({ p, compact, selected, onPick }: { p: Plan; compact?: boolean; selected?: string; onPick?: (o: LevOption) => void }) {
  if (p.entry === undefined || p.stop === undefined) return <PlanCells p={p} />;
  return (
    <div className="space-y-2">
      <PlanCells p={p} />
      <LeverageOptions p={p} selected={selected} onPick={onPick} />
      <PlanNotes p={p} />
      {!compact && <PlanHowTo p={p} />}
    </div>
  );
}

/** "I took this trade": the agents propose the full plan (side, entry, stop, target, leverage, margin, quantity, risk);
 *  the user only confirms, or corrects it to their actual Binance fill. Saved with POST /api/my/trades. */
export default function TakeTrade({ draft, onClose, onAdded }: { draft: TradeDraft; onClose: () => void; onAdded?: () => void }) {
  const [f, setF] = useState<TradeDraft>(draft);
  const [plan, setPlan] = useState<Plan | null>(draft.plan ?? null);
  const [quick, setQuick] = useState<Quick | null>(null);
  const [pickSide, setPickSide] = useState<"auto" | "long" | "short">(draft.side ? (draft.side.toLowerCase() as "long" | "short") : "auto");
  const [tf, setTf] = useState(draft.tf ?? "4h");
  const [marginText, setMarginText] = useState("");
  const userMargin = +marginText >= 1 ? +marginText : null;
  const [coins, setCoins] = useState<string[]>([]);
  const [asking, setAsking] = useState(false);
  const [edit, setEdit] = useState(false);
  const [manual, setManual] = useState(false);
  const [opt, setOpt] = useState<string>("safe");
  const [ack, setAck] = useState(false);
  const [err, setErr] = useState("");
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);

  const apply = useCallback((p: Plan, key = "safe") => {
    const o = p.leverage_options?.find((x) => x.key === key);
    setPlan(p); setOpt(o ? key : "safe"); setAck(false);
    setF((x) => ({ ...x, symbol: p.symbol, side: p.side, leverage: o?.leverage ?? p.leverage, entry: p.entry, qty: o?.qty ?? p.qty,
      stop: p.stop, target: p.target }));
  }, []);
  const pick = (o: LevOption) => { setOpt(o.key); setAck(false); setF((x) => ({ ...x, leverage: o.leverage, qty: o.qty })); };
  const ask = useCallback(async (keepLevels: boolean) => {
    setAsking(true); setErr("");
    try {
      const r = await api<Quick>("/agent/plan", { method: "POST", json: {
        symbol: f.symbol, side: pickSide, tf, budget: f.budget_usd, mode: f.mode, venue: f.venue, margin: keepLevels ? undefined : userMargin ?? undefined,
        ...(keepLevels ? { stop: draft.stop, target: draft.target, entry: draft.entry } : {}) } });
      setQuick(r); apply(r.plan);
    } catch (e: any) { setErr(e.message); }
    setAsking(false);
  }, [f.symbol, f.budget_usd, f.mode, f.venue, pickSide, tf, draft.stop, draft.target, draft.entry, apply, userMargin]);

  // a ticket that already has its levels (System signal card): the AI sizes it only when asked (it spends AI credits)
  useEffect(() => {
    if (draft.plan) apply(draft.plan, draft.option ?? "safe");
    if (!draft.entry) api<{ coins: { symbol: string }[] }>("/coins").then((r) => setCoins(r.coins.map((c) => c.symbol))).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const submit = async () => {
    setBusy(true); setErr("");
    try {
      await api("/my/trades", { method: "POST", json: { ...f, side: f.side ?? "LONG", plan: undefined, tf: undefined } });
      setDone(true); onAdded?.();
    } catch (e: any) { setErr(e.message); }
    setBusy(false);
  };
  const num = (label: string, k: keyof TradeDraft, step = 0.0001) => (
    <label className="flex flex-col text-[11px] text-slate-400">{label}
      <input className="input" type="number" step={step} value={f[k] as number} onChange={(e) => setF({ ...f, [k]: +e.target.value })} />
    </label>
  );
  const margin = f.leverage ? (f.entry * f.qty) / f.leverage : 0;
  const risk = Math.abs(f.entry - f.stop) * f.qty;
  const needsPick = !draft.entry && !draft.plan;
  const chosen = plan?.leverage_options?.find((o) => o.key === opt);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div className="card max-h-[92vh] w-full max-w-2xl space-y-3 overflow-auto p-4 text-[12px]" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-2">
          <Bot size={16} className="text-sky-300" />
          <h2 className="font-semibold text-slate-100">
            {needsPick && !plan ? "Add a trade — the agents plan it" : `${pair(f.symbol)} ${f.side ?? ""} · ${f.venue === "futures" ? `Isolated ${f.leverage}×` : "Spot"}`}
          </h2>
          <button className="btn ml-auto" onClick={onClose}>✕</button>
        </div>
        {done ? (
          <div className="space-y-2">
            <p className="text-emerald-200">Added. It is now a running trade, marked to the live price, with the next action every day.</p>
            <div className="flex gap-2"><button className="btn btn-primary" onClick={onClose}>Back</button><Link href="/portfolio" className="btn">Open my portfolio</Link></div>
          </div>
        ) : (
          <>
            {needsPick && (
              <div className="flex flex-wrap items-end gap-2 rounded border border-line p-2">
                <label className="flex flex-col text-[11px] text-slate-400">Coin
                  <input className="input w-36" list="tt-coins" value={f.symbol} onChange={(e) => setF({ ...f, symbol: e.target.value.toUpperCase() })} />
                  <datalist id="tt-coins">{coins.slice(0, 400).map((c) => <option key={c} value={c} />)}</datalist>
                </label>
                <label className="flex flex-col text-[11px] text-slate-400">Direction
                  <select className="input" value={pickSide} onChange={(e) => setPickSide(e.target.value as any)}>
                    <option value="auto">Agents decide</option><option value="long">Long</option>
                    {f.venue === "futures" && <option value="short">Short</option>}
                  </select>
                </label>
                <label className="flex flex-col text-[11px] text-slate-400">Margin you put (USDT)
                  <input className="input w-28" type="number" min={1} placeholder="e.g. 20" value={marginText} onChange={(e) => setMarginText(e.target.value)} />
                </label>
                <label className="flex flex-col text-[11px] text-slate-400">Chart
                  <select className="input" value={tf} onChange={(e) => setTf(e.target.value)}>
                    <option value="15m">15m scalp</option><option value="1h">1h intraday</option><option value="4h">4h swing</option><option value="1d">1d position</option>
                  </select>
                </label>
                <button className="btn btn-primary flex items-center gap-1" disabled={asking || !f.symbol || !userMargin} onClick={() => ask(false)}
                  title={userMargin ? "" : "Enter your margin first"}>
                  {asking ? <Loader2 size={12} className="animate-spin" /> : <Bot size={12} />} {plan ? "Ask again" : "Get the agents' plan"}
                </button>
                <span className="w-full text-[11px] text-slate-500">
                  Enter the margin you will put on it. The agents read the chart, pick the side, stop and target from measured levels, and
                  choose the leverage and size from your margin ({f.mode} style: a stop-out loses at most {f.mode === "conservative" ? 10 : f.mode === "aggressive" ? 30 : 20}% of it).
                  The dollars you lose at the stop and make at the target come from their side.
                </span>
              </div>
            )}
            {!plan && !asking && !quick && draft.entry > 0 && draft.stop > 0 && (
              <button className="btn btn-primary flex items-center gap-1" onClick={() => ask(true)}>
                <Bot size={12} /> Ask the AI to size it <span className="text-[11px] font-normal opacity-80">(uses AI credits)</span>
              </button>
            )}
            {asking && !plan && <div className="flex items-center gap-2 text-slate-400"><Loader2 size={14} className="animate-spin" /> Pilot, the Trade Planner, is analysing {pair(f.symbol)}…</div>}
            {quick && (
              <div className="rounded border border-emerald-500/20 bg-emerald-500/5 p-2 text-[12px] text-slate-200">
                <div className="mb-1 flex items-center gap-2 text-[11px] text-slate-400">
                  <Bot size={12} className="text-emerald-300" /><b className="text-emerald-200">{quick.by ?? "Pilot · Trade Planner"}</b>
                  <span>{quick.side} · {quick.why_side}</span>
                  {quick.confidence !== null && quick.confidence !== undefined && <span className="ml-auto">{Math.round(quick.confidence * 100)}% confidence</span>}
                </div>
                <div className="whitespace-pre-line leading-snug">{quick.opinion}</div>
              </div>
            )}
            {err && !plan && !manual && (
              <div className="space-y-1.5 rounded border border-red-500/30 bg-red-500/5 p-2 text-[12px]">
                <div className="text-red-200">{err}</div>
                <button className="btn" onClick={() => { setManual(true); setErr(""); setF((x) => ({ ...x, side: pickSide === "short" ? "SHORT" : "LONG", leverage: x.venue === "futures" ? x.leverage || 1 : 1 })); }}>
                  <Pencil size={11} className="mr-1 inline" />Enter my trade by hand</button>
              </div>
            )}
            {manual && !plan && (
              <div className="space-y-2 rounded border border-line p-2">
                <div className="text-[11px] text-slate-400">Your own fill — no AI plan. The portfolio tracks it like any other trade.</div>
                <div className="grid grid-cols-2 gap-2 md:grid-cols-3">
                  <label className="flex flex-col text-[11px] text-slate-400">Side
                    <select className="input" value={f.side ?? "LONG"} onChange={(e) => setF({ ...f, side: e.target.value as Side })}>
                      <option value="LONG">Long</option>{f.venue === "futures" && <option value="SHORT">Short</option>}
                    </select></label>
                  {num("Entry (your fill)", "entry")}
                  {num("Quantity", "qty", 0.000001)}
                  {f.venue === "futures" && num("Leverage set", "leverage", 1)}
                  {num("Stop", "stop")}
                  {num("Target", "target")}
                </div>
              </div>
            )}
            {quick?.validation && <ValidationPanel v={quick.validation} compact />}
            {plan && <PlanTicket p={plan} selected={opt} onPick={plan.ok ? pick : undefined} />}
            {chosen && chosen.key !== "safe" && (
              <label className={`flex items-start gap-2 rounded p-2 text-[12px] ${chosen.risk_level === "safe" ? "bg-white/5 text-slate-300" : "bg-red-500/10 text-red-200"}`}>
                <input type="checkbox" className="mt-0.5" checked={ack} onChange={(e) => setAck(e.target.checked)} />
                <span>I choose <b>{chosen.title}</b>: margin {usd(chosen.margin_usd)}, liquidation {fmtPrice(chosen.liquidation)}.
                  At the stop I lose <b>{usd(chosen.loss_at_stop_usd)}</b> ({chosen.loss_pct_of_budget}% of my budget); at the target I make {usd(chosen.profit_at_target_usd)}. {chosen.note}</span>
              </label>
            )}
            {plan && plan.ok && (
              <>
                <button className="flex items-center gap-1 text-[11px] text-slate-400 hover:text-slate-200" onClick={() => setEdit(!edit)}>
                  <Pencil size={11} /> {edit ? "Use the agents' numbers" : "My Binance fill was different — correct it"}
                </button>
                {edit && (
                  <div className="grid grid-cols-2 gap-2 md:grid-cols-3">
                    {num("Entry (your fill)", "entry")}
                    {num("Quantity", "qty", 0.000001)}
                    {f.venue === "futures" && num("Leverage set", "leverage", 1)}
                    {num("Stop", "stop")}
                    {num("Target", "target")}
                    <label className="flex flex-col text-[11px] text-slate-400">Note
                      <input className="input" value={f.note ?? ""} onChange={(e) => setF({ ...f, note: e.target.value })} placeholder="optional" /></label>
                    <div className="num col-span-full text-slate-400">Margin ${margin.toFixed(2)} · risk at the stop ${risk.toFixed(2)} ({(risk / f.budget_usd * 100).toFixed(1)}% of ${f.budget_usd})</div>
                  </div>
                )}
              </>
            )}
            {err && (plan || manual) && <div className="text-red-300">{err}</div>}
            <div className="flex gap-2">
              <button className="btn btn-primary" onClick={submit}
                disabled={busy || (manual && !plan ? !(f.entry > 0 && f.qty > 0 && f.stop > 0) : (!plan || !plan.ok || (!!chosen && chosen.key !== "safe" && !ack)))}>{busy ? "Saving…" : "I placed it — add as a running trade"}</button>
              <button className="btn" onClick={onClose}>Cancel</button>
            </div>
            <p className="text-slate-600">Paper record only: nothing is sent to Binance. You place the orders; the platform tracks and manages them.</p>
          </>
        )}
      </div>
    </div>
  );
}
