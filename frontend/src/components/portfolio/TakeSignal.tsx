"use client";
import { AlertTriangle, Bot, Check, CircleCheck, CircleX, Loader2, Pencil, ShieldCheck, X } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { api } from "@/lib/api";
import { fmtPrice, pair } from "@/lib/format";
import AddTrade, { type AddDraft } from "./AddTrade";
import Modal from "@/components/ui/Modal";

type Levels = { entry: number; stop: number; target: number };
type Agents = { action: string; side?: string | null; entry?: number | null; stop?: number | null; target?: number | null } | null;
type Check = {
  verdict: "take" | "careful" | "avoid"; title: string; note: string; reasons: { level: "good" | "warn" | "bad"; text: string }[];
  levels: Levels | null; account: { equity: number } | null;
  sizing: { ok: boolean; reason?: string; leverage?: number; margin_usd?: number; notional_usd?: number; qty?: number; risk_usd?: number;
    reward_usd?: number; liquidation?: number; note?: string | null; mode?: string; entry?: number; stop?: number; target?: number } | null;
};
const usd = (v?: number | null) => (v === undefined || v === null ? "—" : `$${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: Math.abs(v) < 100 ? 2 : 0 })}`);
const VERDICT = { take: "border-emerald-500/40 bg-emerald-500/10 text-emerald-200", careful: "border-amber-500/40 bg-amber-500/10 text-amber-200",
  avoid: "border-red-500/40 bg-red-500/10 text-red-200" };
const ICON = { good: <CircleCheck size={15} className="mt-0.5 shrink-0 text-emerald-400" />, warn: <AlertTriangle size={15} className="mt-0.5 shrink-0 text-amber-400" />,
  bad: <CircleX size={15} className="mt-0.5 shrink-0 text-red-400" /> };

/** "I took this trade", for any signal: the platform's main signal, a Lab signal or the AI agents' plan.
 *  First the Risk manager's check (take it / careful / not recommended, with reasons); you can take it anyway after
 *  confirming the warning. Then record it: sized for your account, with your own numbers, or as the agents planned.
 *  Portfolio tracks it live to the close. Nothing is ever sent to Binance. */
export default function TakeSignal({ symbol, side, levels, agents, compact, origin = "main", label }: {
  symbol: string; side: "LONG" | "SHORT"; levels: Levels; agents?: Agents; compact?: boolean; origin?: "main" | "lab"; label?: string;
}) {
  const [open, setOpen] = useState(false);
  const [c, setC] = useState<Check | null>(null);
  const [ack, setAck] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [done, setDone] = useState("");
  const [form, setForm] = useState<AddDraft | null>(null);
  const agentPlan = agents && (agents.action === "LONG" || agents.action === "SHORT") && agents.entry && agents.stop && agents.target ? agents : null;
  const source = origin === "main" ? "planner" : "lab";

  const start = async () => {
    setOpen(true); setErr(""); setDone(""); setC(null); setAck(false);
    try {
      setC(await api<Check>(`/my/account/check?symbol=${symbol}&side=${side}&entry=${levels.entry}&stop=${levels.stop}&target=${levels.target}`));
    } catch (e) { setErr(String((e as Error).message || e)); }
  };
  const blocked = !!c && c.verdict !== "take" && !ack;
  const equity = c?.account?.equity ?? 0;
  const sz = c?.sizing;

  const recordSized = async () => {
    if (!sz?.ok || !c) return;
    setBusy(true); setErr("");
    try {
      await api("/my/trades", { method: "POST", json: {
        symbol, side, venue: "futures", leverage: sz.leverage, entry: sz.entry, qty: sz.qty, stop: sz.stop, target: sz.target,
        budget_usd: equity || 100, mode: sz.mode ?? "conservative", source,
        note: `${origin === "main" ? "Main signal" : "Lab signal"}, sized by the Risk manager (${c.title}${c.verdict !== "take" ? ", taken anyway" : ""})` } });
      setDone("Added to your portfolio, sized for your account.");
    } catch (e) { setErr(String((e as Error).message || e)); }
    setBusy(false);
  };
  const draft = (lv: Levels, src: AddDraft["source"], s: "LONG" | "SHORT" = side): AddDraft => ({ symbol, side: s, entry: lv.entry, stop: lv.stop, target: lv.target,
    margin: sz?.margin_usd ?? Math.max(10, Math.round(equity * 0.05)), leverage: sz?.leverage ?? 1, source: src, mode: sz?.mode });

  return (
    <>
      <button className={`btn ${compact ? "btn-ghost" : "btn-primary"}`} onClick={start} title="Record that you placed this trade on Binance; the Risk manager checks it first">
        <Check size={15} /> {label ?? "I took this trade"}
      </button>
      {open && (
        <Modal onClose={() => setOpen(false)} width={580}>
            <div className="flex items-center gap-2">
              <h2 className="text-[17px] font-semibold text-slate-100">I took {side} {pair(symbol)}</h2>
              {origin === "lab" && <span className="chip bg-violet-500/20 text-[10.5px] text-violet-200">Lab signal</span>}
              <button className="ml-auto text-slate-500 hover:text-slate-200" onClick={() => setOpen(false)}><X size={18} /></button>
            </div>
            {!c && !err && <div className="mt-4 flex items-center gap-2 text-[13px] text-slate-500"><Loader2 size={15} className="animate-spin" /> The Risk manager is checking it…</div>}
            {err && <p className="mt-3 text-[12.5px] text-red-300">{err}</p>}

            {c && !done && (
              <>
                {/* the check first: are you sure? */}
                <div className={`mt-4 rounded-xl border px-4 py-3 ${VERDICT[c.verdict]}`}>
                  <div className="flex items-center gap-2 text-[15px] font-semibold"><ShieldCheck size={17} /> Risk manager: {c.title}</div>
                  <ul className="mt-2 space-y-1.5 text-[12.5px] text-slate-300">
                    {c.reasons.map((x) => <li key={x.text} className="flex gap-2">{ICON[x.level]}<span>{x.text}</span></li>)}
                  </ul>
                  <p className="mt-2 text-[11.5px] text-slate-500">{c.note}</p>
                  {c.verdict !== "take" && (
                    <label className="mt-3 flex items-start gap-2 text-[13px] text-slate-200">
                      <input type="checkbox" className="mt-0.5" checked={ack} onChange={(e) => setAck(e.target.checked)} />
                      I understand the warning and I take it anyway.
                    </label>
                  )}
                </div>

                <div className={`mt-4 space-y-3 ${blocked ? "pointer-events-none opacity-40" : ""}`}>
                  <div className="rounded-xl border border-line p-4">
                    <div className="flex items-center gap-2 text-[14px] font-semibold text-slate-100"><ShieldCheck size={16} className="text-accent" /> Sized for my account</div>
                    {!c.account ? (
                      <p className="mt-1 text-[12.5px] text-slate-400">Set your balance first: <Link href="/portfolio" className="text-accent hover:underline">Portfolio → Set up your account</Link>.</p>
                    ) : sz?.ok ? (
                      <>
                        <div className="mt-2 grid grid-cols-2 gap-2 text-[12.5px] sm:grid-cols-4">
                          {[["Margin", usd(sz.margin_usd)], ["Leverage", `${sz.leverage}x`], ["If stopped", `−${usd(sz.risk_usd)}`], ["At take profit", `+${usd(sz.reward_usd)}`]].map(([k, v]) => (
                            <div key={k} className="rounded-lg bg-fill px-3 py-2"><div className="text-slate-500">{k}</div><div className="num font-semibold text-slate-100">{v}</div></div>))}
                        </div>
                        <div className="mt-1 text-[11.5px] text-slate-500">Entry {fmtPrice(sz.entry!)} · stop {fmtPrice(sz.stop!)} · take profit {fmtPrice(sz.target!)} · liquidation {fmtPrice(sz.liquidation!)}</div>
                        {sz.note && <div className="mt-1 text-[11.5px] text-amber-300">{sz.note}</div>}
                        <button className="btn btn-primary mt-3" disabled={busy || blocked} onClick={recordSized}>{busy ? "Adding…" : "I placed it like this"}</button>
                      </>
                    ) : <p className="mt-1 text-[12.5px] text-amber-300">{sz?.reason ?? "No size: the levels are missing or invalid."}</p>}
                  </div>
                  <div className="rounded-xl border border-line p-4">
                    <div className="flex items-center gap-2 text-[14px] font-semibold text-slate-100"><Pencil size={15} className="text-slate-400" /> With my own margin and leverage</div>
                    <p className="mt-1 text-[12.5px] text-slate-400">Your fill, margin, leverage and the time you opened it.</p>
                    <button className="btn btn-ghost mt-2" disabled={blocked} onClick={() => setForm(draft(c.levels ?? levels, source))}>Enter my numbers</button>
                  </div>
                  <div className="rounded-xl border border-line p-4">
                    <div className="flex items-center gap-2 text-[14px] font-semibold text-slate-100"><Bot size={15} className="text-amber-300" /> As the AI agents suggested</div>
                    {agentPlan ? <>
                      <p className="mt-1 text-[12.5px] text-slate-400">{agentPlan.action} · entry {fmtPrice(agentPlan.entry!)} · stop {fmtPrice(agentPlan.stop!)} · take profit {fmtPrice(agentPlan.target!)}</p>
                      <button className="btn btn-ghost mt-2" disabled={blocked} onClick={() => setForm(draft({ entry: agentPlan.entry!, stop: agentPlan.stop!, target: agentPlan.target! }, "agent", agentPlan.action as "LONG" | "SHORT"))}>Use the agents&apos; plan</button>
                    </> : <p className="mt-1 text-[12.5px] text-slate-500">{agents?.action === "WAIT" ? "The agents said WAIT: there is no plan of theirs to take." : "Ask the AI agents first (menu → AI agents) to get their plan."}</p>}
                  </div>
                </div>
              </>
            )}
            {done && (
              <div className="mt-4 flex flex-wrap items-center gap-3 rounded-xl bg-emerald-500/10 px-4 py-3 text-[13.5px] text-emerald-200">
                <Check size={16} /> {done} <Link href="/portfolio" className="ml-auto font-medium text-accent hover:underline">Open Portfolio →</Link>
              </div>
            )}
        </Modal>
      )}
      {form && <AddTrade draft={form} equity={equity} onClose={() => setForm(null)} onAdded={() => { setForm(null); setDone("Added to your portfolio with your numbers."); }} />}
    </>
  );
}
