"use client";
import { Check, Pencil, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { fmtPrice, pair } from "@/lib/format";
import { Card, Note } from "@/components/ui/Page";
import type { AddDraft } from "./AddTrade";

export type Account = {
  set: boolean;
  account?: { balance: number; set_at: string; realized_usd: number; unrealized_usd: number; equity: number; peak: number;
    drawdown_pct: number; loss_streak: number; closed_since: number; open: number; open_risk_usd: number; margin_in_use_usd: number };
  risk?: { mode: "conservative" | "growth" | "aggressive"; risk_pct: number; size: number; trade: boolean; reasons: string[] };
  suggestion?: { ok: boolean; reason?: string; symbol?: string; side?: "LONG" | "SHORT"; entry_type?: "limit" | "market"; entry?: number;
    stop?: number; target?: number; stop_pct?: number; target_pct?: number; days?: number; leverage?: number; margin_usd?: number;
    notional_usd?: number; qty?: number; risk_usd?: number; reward_usd?: number; liquidation?: number; note?: string | null;
    mode?: string; risk_pct?: number; as_of?: string; safe_max?: number } | null;
};

const usd = (v?: number | null, sign = false) => (v === undefined || v === null ? "—"
  : `${sign ? (v > 0 ? "+" : v < 0 ? "−" : "") : v < 0 ? "−" : ""}$${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: Math.abs(v) < 100 ? 2 : 0 })}`);
const tone = (v?: number | null) => (!v ? "text-slate-100" : v > 0 ? "text-emerald-300" : "text-red-300");
const MODE: Record<string, [string, string]> = {
  conservative: ["Conservative", "bg-sky-500/15 text-sky-300"], growth: ["Growth", "bg-emerald-500/15 text-emerald-300"],
  aggressive: ["Aggressive", "bg-amber-500/15 text-amber-300"] };

/** Step 1 when nothing is set: the balance, once. */
export function SetBalance({ onSaved, initial }: { onSaved: (a: Account) => void; initial?: number }) {
  const [v, setV] = useState(initial ? String(initial) : "");
  const [err, setErr] = useState("");
  const save = async (e: React.FormEvent) => {
    e.preventDefault(); setErr("");
    const n = Number(v);
    if (!(n >= 10)) { setErr("Enter your balance in USDT (at least 10)."); return; }
    try { onSaved(await api<Account>("/my/account", { method: "POST", json: { balance: n } })); } catch (e2) { setErr(String((e2 as Error).message || e2)); }
  };
  return (
    <form onSubmit={save} className="flex flex-wrap items-end gap-3">
      <label className="flex flex-col gap-1 text-[12.5px] text-slate-500">Your futures account balance (USDT)
        <span className="relative"><span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-500">$</span>
          <input autoFocus className="input num w-44 pl-6 text-[15px]" type="number" min={10} step="0.01" placeholder="1000" value={v} onChange={(e) => setV(e.target.value)} /></span>
      </label>
      <button className="btn btn-primary" type="submit">Save</button>
      {err && <span className="text-[12.5px] text-red-300">{err}</span>}
    </form>
  );
}

/** Your account: how it is doing, and the Risk manager's choice with its reasons. */
export function AccountCard({ a, onSaved }: { a: Account; onSaved: (a: Account) => void }) {
  const [edit, setEdit] = useState(false);
  if (!a.set || !a.account || !a.risk) {
    return (
      <Card title="Set up your account" sub="Enter your balance once. The platform then sizes every trade for it and tracks your account.">
        <SetBalance onSaved={onSaved} />
      </Card>
    );
  }
  const ac = a.account, rk = a.risk;
  const pnl = ac.equity - ac.balance;
  return (
    <Card title="Your account" right={<button className="text-slate-500 hover:text-slate-200" title="Change your balance" onClick={() => setEdit(!edit)}><Pencil size={15} /></button>}>
      {edit && <div className="mb-5"><SetBalance initial={ac.balance} onSaved={(x) => { setEdit(false); onSaved(x); }} /></div>}
      <div className="flex flex-wrap items-end gap-x-10 gap-y-4">
        <div>
          <div className="text-[12.5px] text-slate-500">Account value now</div>
          <div className="num text-[34px] font-semibold leading-tight text-slate-100">{usd(ac.equity)}</div>
          <div className={`num text-[13px] ${tone(pnl)}`}>{usd(pnl, true)} ({((pnl / ac.balance) * 100).toFixed(1)}%) since you started with {usd(ac.balance)}</div>
        </div>
        <div className="grid flex-1 grid-cols-2 gap-3 md:grid-cols-4">
          {[["Open trades", String(ac.open)], ["Margin in use", usd(ac.margin_in_use_usd)], ["Lose if all stops hit", usd(ac.open_risk_usd)],
            ["Below the peak", `${ac.drawdown_pct.toFixed(1)}%`]].map(([k, v]) => (
            <div key={k} className="rounded-xl bg-fill px-4 py-3"><div className="text-[12px] text-slate-500">{k}</div><div className="num text-[16px] font-semibold text-slate-100">{v}</div></div>
          ))}
        </div>
      </div>
      <div className="mt-5 rounded-xl border border-line px-4 py-3">
        <div className="flex flex-wrap items-center gap-2 text-[13.5px]">
          <ShieldCheck size={16} className="text-accent" />
          <b className="text-slate-100">Risk manager:</b>
          <span className={`chip ${MODE[rk.mode][1]}`}>{MODE[rk.mode][0]}</span>
          <span className="text-slate-300">{rk.risk_pct}% of your account at risk per trade</span>
          {!rk.trade && <span className="chip bg-amber-500/15 text-amber-300">no new trade today</span>}
        </div>
        <ul className="mt-2 list-disc space-y-0.5 pl-6 text-[12.5px] text-slate-400">{rk.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
        <p className="mt-2 text-[11.5px] text-slate-500" title="Conservative 1% to start. Growth 2% after 10+ finished live signals averaging +0.10R or better. Aggressive 3% only after 30+ averaging +0.20R with the AI agents' test passed and agreeing. Half size after 3 losses in a row, a 10% drop, or when the agents say wait. Pause at a 20% drop.">
          The mode is chosen from the evidence, not by you, and it changes as the live record grows. Hover for the rules.</p>
      </div>
    </Card>
  );
}

/** Today's plan: the main signal sized for your account. Accept after you placed it on Binance. */
export function TodayPlan({ a, onAccepted, onCustom }: { a: Account; onAccepted: () => void; onCustom?: (d: AddDraft) => void }) {
  const s = a.suggestion;
  const key = s?.symbol ? `skip_${s.as_of}_${s.symbol}_${s.side}` : "";
  const [skipped, setSkipped] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [done, setDone] = useState(false);
  useEffect(() => { setDone(false); }, [s?.ok, s?.symbol, s?.as_of, a.account?.open]);   // a new plan (or a removed trade): fresh buttons
  useEffect(() => { try { setSkipped(!!key && localStorage.getItem(key) === "1"); } catch { /* private mode */ } }, [key]);
  if (!a.set || !s) return null;
  if (!s.ok) {
    return (
      <Card title="Today's plan" right={<Link href="/signal" className="text-[13px] text-accent hover:underline">Today&apos;s signal →</Link>}>
        <Note tone="info">{s.reason}{s.symbol ? ` (${s.side} ${pair(s.symbol)})` : ""}</Note>
      </Card>
    );
  }
  const accept = async () => {
    setBusy(true); setErr("");
    try { await api("/my/account/accept", { method: "POST" }); setDone(true); onAccepted(); } catch (e) { setErr(String((e as Error).message || e)); }
    setBusy(false);
  };
  const skip = () => { try { localStorage.setItem(key, "1"); } catch { /* private mode */ } setSkipped(true); };
  if (skipped) return <Card title="Today's plan"><div className="text-[13px] text-slate-500">You skipped today&apos;s plan ({s.side} {pair(s.symbol)}). <button className="text-accent hover:underline" onClick={() => { try { localStorage.removeItem(key); } catch { /* */ } setSkipped(false); }}>Show it again</button></div></Card>;
  const long = s.side === "LONG";
  return (
    <Card title="Today's plan" sub={`Today's main signal, sized for your account (${s.risk_pct}% at risk)`}
      right={<Link href="/signal" className="text-[13px] text-accent hover:underline">Why this trade →</Link>}>
      <div className="flex flex-wrap items-center gap-3">
        <span className={`rounded-xl px-3.5 py-1 text-[18px] font-black ${long ? "bg-emerald-500/15 text-emerald-300" : "bg-red-500/15 text-red-300"}`}>{s.side}</span>
        <b className="text-[20px] text-slate-100">{pair(s.symbol)}</b>
      </div>
      <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-4">
        {[[s.entry_type === "limit" ? (long ? "Buy limit" : "Sell limit") : (long ? "Buy at" : "Sell at"), fmtPrice(s.entry!), s.entry_type === "limit" ? "limit order" : "market price", "text-slate-100"],
          ["Stop loss", fmtPrice(s.stop!), `${(s.stop_pct ?? 0) > 0 ? "+" : ""}${s.stop_pct}%`, "text-red-300"],
          ["Take profit", fmtPrice(s.target!), `${(s.target_pct ?? 0) > 0 ? "+" : ""}${s.target_pct}%`, "text-emerald-300"],
          ["Leverage", `${s.leverage}x isolated`, `liquidation ${fmtPrice(s.liquidation!)}`, "text-slate-100"]].map(([k, v, sub, c]) => (
          <div key={k} className="rounded-xl bg-fill px-4 py-3"><div className="text-[12px] text-slate-500">{k}</div>
            <div className={`num text-[17px] font-semibold ${c}`}>{v}</div><div className="text-[11.5px] text-slate-500">{sub}</div></div>
        ))}
      </div>
      <div className="mt-4 grid gap-3 md:grid-cols-3">
        <div className="rounded-xl border border-line px-4 py-3"><div className="text-[12px] text-slate-500">Put in (margin)</div><div className="num text-[20px] font-semibold text-slate-100">{usd(s.margin_usd)}</div>
          <div className="text-[11.5px] text-slate-500">position {usd(s.notional_usd)} · quantity {s.qty}</div></div>
        <div className="rounded-xl border border-line px-4 py-3"><div className="text-[12px] text-slate-500">If the stop hits</div><div className="num text-[20px] font-semibold text-red-300">{usd(-(s.risk_usd ?? 0), true)}</div>
          <div className="text-[11.5px] text-slate-500">{s.risk_pct}% of your account</div></div>
        <div className="rounded-xl border border-line px-4 py-3"><div className="text-[12px] text-slate-500">If take profit hits</div><div className="num text-[20px] font-semibold text-emerald-300">{usd(s.reward_usd, true)}</div>
          <div className="text-[11.5px] text-slate-500">fees included · hold up to {s.days} days</div></div>
      </div>
      {s.safe_max && s.leverage && s.safe_max > s.leverage && s.notional_usd && (
        <p className="mt-2 text-[12.5px] text-slate-400">Leverage only changes the margin you lock, not what you can lose: the same trade at {Math.min(s.safe_max, 5)}x needs {usd(s.notional_usd / Math.min(s.safe_max, 5))} margin
          {s.safe_max > 5 ? ` (safe up to ${s.safe_max}x)` : ""}. The loss at the stop stays {usd(s.risk_usd)}.</p>)}
      {s.note && <p className="mt-2 text-[12px] text-amber-300">{s.note}</p>}
      <div className="mt-5 flex flex-wrap items-center gap-3">
        {done ? <span className="flex items-center gap-1.5 text-[13.5px] text-emerald-300"><Check size={16} /> Added to your trades below. The platform tracks it from now on.</span> : <>
          <button className="btn btn-primary" disabled={busy} onClick={accept}>{busy ? "Adding…" : "Accept as suggested"}</button>
          {onCustom && <button className="btn btn-ghost" onClick={() => onCustom({ symbol: s.symbol!, side: s.side!, entry: s.entry!, stop: s.stop!, target: s.target!,
            margin: s.margin_usd ?? 0, leverage: s.leverage ?? 1, source: "planner", mode: s.mode })}>Accept with my numbers</button>}
          <button className="btn btn-ghost" onClick={skip}>Skip today</button>
          <span className="text-[12px] text-slate-500">The platform never places orders. Place it on Binance first (isolated, {s.leverage}x, with the stop loss and take profit), then press Accept.</span>
        </>}
      </div>
      {err && <p className="mt-2 text-[12.5px] text-red-300">{err}</p>}
    </Card>
  );
}
