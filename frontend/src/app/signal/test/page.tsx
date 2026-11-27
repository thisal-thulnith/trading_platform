"use client";
import { Loader2, RefreshCw, Trash2 } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { fmtPrice, pair } from "@/lib/format";
import { Card, More, Note, Page, PageHead, Tile } from "@/components/ui/Page";

type G = { finished: number; open: number; waiting: number; not_filled: number; wins?: number; losses?: number; win_rate?: number;
  win_range_90?: [number, number] | null; avg_r?: number; sum_r?: number; usd?: number; outcomes?: Record<string, number>;
  expected_win_rate?: number | null; expected_avg_r?: number | null };
type Sig = { id: string; source: "auto" | "user"; as_of: string; symbol: string; side: "LONG" | "SHORT"; status: "trade" | "watch"; gates: string[];
  entry_type: "market" | "limit"; entry: number; stop: number; tp1: number; tp2: number; fast_stop: number; fast_tp: number; margin: number;
  safe_lev: number; fast_safe_lev: number; payload: { expected?: { win_rate: number; avg_r: number; verdict: string }; fast_status?: string; rank?: number; of?: number; price_at_signal?: number;
    agent_stance?: "agree" | "wait" | "against"; agents?: { action: string; confidence: number | null } };
  state: "waiting" | "open" | "closed" | "not_filled"; outcome: string | null; r: number | null; fast_state: string; fast_outcome: string | null;
  fast_r: number | null; last_price: number | null; created_at: string; filled_at: string | null; closed_at: string | null; usd: number | null;
  fast_usd: number | null; hours?: number };
type Report = { milestone: number; main_group?: string; stats: Record<string, { swing: G; fast: G; signals: number }>; verdict: { ready: boolean; text: string; word?: string };
  signals: Sig[]; updated: string | null; rules: string; agents_own?: AgentsOwn | null;
  agent_veto?: { needed: number; have: number; decided: boolean; veto_on: boolean; text: string } };
type Summ = { calls: number; graded: number; open: number; target_first: number; stop_first: number; expired: number; win_rate: number | null; avg_r: number | null };
type AgentsOwn = { all: Summ; taken: Summ; waited_counterfactual: Summ; note: string };

const R = (v?: number | null) => (v === undefined || v === null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}R`);
const usd = (v?: number | null) => (v === undefined || v === null ? "—" : `${v < 0 ? "−" : "+"}$${Math.abs(v).toFixed(2)}`);
const pc = (v?: number | null) => (v === undefined || v === null ? "—" : `${Math.round(v * 100)}%`);
const tone = (v?: number | null) => (v === undefined || v === null ? "text-slate-400" : v > 0 ? "text-emerald-300" : v < 0 ? "text-red-300" : "text-slate-300");
const OUT: Record<string, string> = { stop: "Stop hit", tp1_then_entry: "TP1, then entry", tp2: "TP2 hit", target: "Target hit", timeout: "Time limit",
  not_filled: "Not filled", tp1_hit: "TP1 hit, running" };
const when = (s: string | null) => (s ? new Date(s).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—");
const GROUPS: [string, string][] = [["unified", "Unified engine (from Nov 24)"], ["fixed", "Old fixed plan (Nov 23–24)"], ["platform", "All platform signals"], ["trade", "Said TRADE"], ["watch", "Said WATCH ONLY"],
  ["long", "Longs"], ["short", "Shorts"], ["yours", "Coins you added"]];
// the machine's signals split by what the AI agents said about the same coin (asked before the fill: no hindsight)
const AGENT_GROUPS: [string, string][] = [["unified", "Machine alone (every signal)"], ["agents_agree", "Machine + agents agreed"],
  ["agents_wait", "Machine said trade, agents said wait"], ["agents_against", "Machine and agents disagreed"], ["agents_none", "Agents not asked"]];
const STANCE: Record<string, [string, string]> = { agree: ["agents agree", "bg-emerald-500/15 text-emerald-300"],
  wait: ["agents: wait", "bg-amber-500/15 text-amber-300"], against: ["agents disagree", "bg-red-500/15 text-red-300"] };

function State({ s }: { s: Sig }) {
  if (s.state === "closed") return <span className={tone(s.r)}>{OUT[s.outcome ?? ""] ?? s.outcome}</span>;
  if (s.state === "not_filled") return <span className="text-slate-500">Not filled (48h)</span>;
  if (s.state === "waiting") return <span className="text-sky-300">Waiting for limit</span>;
  return <span className="text-amber-300">{s.outcome === "tp1_hit" ? "Open · TP1 hit" : "Open"}</span>;
}

/** Signal test: every signal the platform gives is saved at the moment it is given and graded on the live candles after
 *  it, so after 10 finished signals you can see how accurate it really is, next to what the backtest promised. */
export default function SignalTestPage() {
  const [rep, setRep] = useState<Report | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [sym, setSym] = useState("");
  const [side, setSide] = useState("");
  const [margin, setMargin] = useState("100");
  const [msg, setMsg] = useState("");

  const load = useCallback(async (refresh = false) => {
    setBusy(true); setErr("");
    try {
      if (refresh) await api(`/agent/signal/test/refresh`, { method: "POST" });
      setRep(await api<Report>(`/agent/signal/test`));
    } catch (e) { setErr(String((e as Error).message || e)); }
    setBusy(false);
  }, []);
  useEffect(() => { load(); const t = setInterval(() => load(), 30_000); return () => clearInterval(t); }, [load]);

  const add = async (e: React.FormEvent) => {
    e.preventDefault(); setMsg("");
    if (!sym.trim()) return;
    try {
      await api(`/agent/signal/test/add`, { method: "POST", json: { symbol: sym.trim().toUpperCase().replace("/", ""), side: side || null, margin: Number(margin) || 100 } });
      setMsg(`${sym.toUpperCase()} added to the test.`); setSym("");
      load();
    } catch (e2) { setMsg(String((e2 as Error).message || e2)); }
  };
  const remove = async (id: string) => { await api(`/agent/signal/test/${id}`, { method: "DELETE" }).catch(() => {}); load(); };

  const st = rep?.stats.all?.swing ?? rep?.stats[rep.main_group ?? "platform"]?.swing;   // the same rows the table lists
  const n = rep?.milestone ?? 10;

  return (
    <Page>
      <PageHead title="Results"
        sub={<span>Every <Link href="/signal" className="text-accent hover:underline">main signal</Link>, graded on live prices · Paper only</span>}>
        <button className="btn btn-ghost" onClick={() => load(true)} disabled={busy}>
          {busy ? <Loader2 size={15} className="animate-spin" /> : <RefreshCw size={15} />} Update now
        </button>
      </PageHead>
      {err && <Note tone="bad">{err}</Note>}

      {rep && st && (
        <>
          {/* the score */}
          <Card>
            <div className="flex flex-wrap items-center gap-6">
              <div>
                <div className="text-[12.5px] text-slate-500">Finished signals</div>
                <div className="text-[34px] font-semibold leading-tight text-slate-100">{st.finished}<span className="text-[18px] font-normal text-slate-500"> / {n}</span></div>
              </div>
              <div className="min-w-[220px] flex-1">
                <div className="h-3 overflow-hidden rounded-full bg-fill">
                  <div className="h-full rounded-full bg-accent transition-all" style={{ width: `${Math.max(2, Math.min(100, (st.finished / n) * 100))}%` }} />
                </div>
                <div className="mt-2 flex flex-wrap gap-x-4 text-[12.5px] text-slate-500">
                  <span><b className="text-slate-200">{st.open + st.waiting}</b> open or waiting</span>
                  <span>one new signal a day</span>
                </div>
              </div>
            </div>
            <Note tone={!rep.verdict.ready ? "info" : (st.avg_r ?? 0) > 0 ? "good" : "bad"} className="mt-5">
              {rep.verdict.ready && <b className="mr-1 capitalize">{rep.verdict.word}.</b>}{rep.verdict.text}
            </Note>
            {st.finished > 0 && (
              <div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-3">
                <Tile label="Made money" value={pc(st.win_rate)} sub={`${st.wins} won · ${st.losses} lost`} tone={tone((st.win_rate ?? 0) - 0.5)} />
                <Tile label="Dollars" value={usd(st.usd)} sub="at the safe leverage" tone={tone(st.usd)} />
                <Tile label={<span title="R = result in units of the risk. −1R means the stop was hit.">Average per trade</span>} value={R(st.avg_r)} sub={`backtest said ${R(st.expected_avg_r)}`} tone={tone(st.avg_r)} />
              </div>
            )}
          </Card>

          {/* every signal */}
          <Card title="Every signal" sub={`Newest first${rep.updated ? ` · checked ${when(rep.updated)}` : ""}. Losses stay: only coins you added can be removed.`}>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[900px] text-[13px]">
                <thead className="text-left text-[12px] text-slate-500">
                  <tr className="border-b border-line"><th className="py-2 font-medium">Coin</th><th className="font-medium">Given</th><th className="font-medium">Entry</th>
                    <th className="font-medium">Stop</th><th className="font-medium">TP1 · TP2</th><th className="font-medium">Now</th>
                    <th className="font-medium">Safe plan</th><th className="font-medium">Risky plan</th><th /></tr>
                </thead>
                <tbody>
                  {rep.signals.length === 0 && <tr><td colSpan={9} className="py-4 text-slate-500">No signals saved yet. The first ones are saved a few minutes after the models&apos; daily update.</td></tr>}
                  {rep.signals.map((s) => (
                    <tr key={s.id} className="border-b border-line/60 align-top last:border-0">
                      <td className="py-3">
                        <div className="flex items-center gap-2">
                          <Link href={`/signal?symbol=${s.symbol}`} className="font-semibold text-slate-100 hover:text-accent">{pair(s.symbol)}</Link>
                          <span className={`text-[11.5px] font-bold ${s.side === "LONG" ? "text-emerald-300" : "text-red-300"}`}>{s.side}</span>
                        </div>
                        <div className="mt-1 flex gap-1.5">
                          <span title={s.gates.join("\n")} className={`chip text-[10.5px] ${s.status === "trade" ? "bg-emerald-500/15 text-emerald-300" : "bg-amber-500/15 text-amber-300"}`}>{s.status === "trade" ? "TRADE" : "WATCH"}</span>
                          {s.source === "user" && <span className="chip bg-sky-500/15 text-[10.5px] text-sky-300">you added</span>}
                          {s.source !== "user" && (s.payload as { engine?: string }).engine !== "unified" && <span className="chip bg-slate-500/15 text-[10.5px] text-slate-400" title="Saved by the older fixed plan (23–24 Nov), before the main signal engine; kept for the honest record">old engine</span>}
                          {s.payload.agent_stance && <span className={`chip text-[10.5px] ${STANCE[s.payload.agent_stance][1]}`}>{STANCE[s.payload.agent_stance][0]}</span>}
                        </div>
                      </td>
                      <td className="py-3 text-slate-400">{when(s.created_at)}</td>
                      <td className="num py-3">{fmtPrice(s.entry)}<div className="text-[11.5px] text-slate-500">{s.entry_type}{s.filled_at && s.entry_type === "limit" ? ` · filled ${when(s.filled_at)}` : ""}</div></td>
                      <td className="num py-3 text-red-300">{fmtPrice(s.stop)}</td>
                      <td className="num py-3 text-emerald-300">{fmtPrice(s.tp1)}{s.tp2 > 0 && s.tp2 !== s.tp1 ? ` · ${fmtPrice(s.tp2)}` : ""}</td>
                      <td className="num py-3 text-slate-300">{s.last_price ? fmtPrice(s.last_price) : "—"}</td>
                      <td className="py-3">
                        <State s={s} />
                        <div className={`num text-[12px] ${tone(s.r)}`}>{s.r !== null ? `${R(s.r)}${s.state === "open" ? " now" : ""}` : ""}{s.usd !== null ? ` · ${usd(s.usd)} @${s.safe_lev}x` : ""}{s.hours !== undefined ? ` · ${s.hours < 48 ? `${s.hours}h` : `${(s.hours / 24).toFixed(1)}d`}` : ""}</div>
                      </td>
                      <td className="py-3">
                        <span className={s.fast_state === "closed" ? tone(s.fast_r) : "text-slate-400"}>{s.fast_state === "closed" ? OUT[s.fast_outcome ?? ""] ?? s.fast_outcome : s.fast_state === "open" ? "Open" : s.fast_state === "not_filled" ? "Not filled" : "Waiting"}</span>
                        <div className={`num text-[12px] ${tone(s.fast_r)}`}>{s.fast_r !== null ? `${R(s.fast_r)}${s.fast_state === "open" ? " now" : ""}` : ""}{s.fast_usd !== null ? ` · ${usd(s.fast_usd)} @${s.fast_safe_lev}x` : ""}</div>
                      </td>
                      <td className="py-3">{s.source === "user" && <button title="Remove (only coins you added)" onClick={() => remove(s.id)} className="text-slate-500 hover:text-red-300"><Trash2 size={14} /></button>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          {/* add a coin */}
          <Card title="Add a coin to the test" sub="Frozen at today's price, then graded the same way.">
            <form onSubmit={add} className="flex flex-wrap items-center gap-2 text-[13px]">
              <input className="input w-36" placeholder="Coin, e.g. SOL" value={sym} onChange={(e) => setSym(e.target.value)} />
              <select className="input" value={side} onChange={(e) => setSide(e.target.value)}>
                <option value="">Side: follow the trend</option><option value="LONG">LONG</option><option value="SHORT">SHORT</option>
              </select>
              <label className="flex items-center gap-2 text-slate-500">Margin
                <span className="relative"><span className="absolute left-3 top-[9px]">$</span><input className="input w-24 pl-6" type="number" min={1} value={margin} onChange={(e) => setMargin(e.target.value)} /></span>
              </label>
              <button className="btn btn-primary" type="submit">Add to test</button>
              {msg && <span className="text-[13px] text-slate-400">{msg}</span>}
            </form>
          </Card>

          <More>
          <Card title="Score details">
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <Tile label="Total" value={R(st.sum_r)} sub="1R = loss at the stop" tone={tone(st.sum_r)} />
              <Tile label="Likely win rate range" value={st.win_range_90 ? `${pc(st.win_range_90[0])}–${pc(st.win_range_90[1])}` : "—"} sub="small samples swing a lot" />
              <Tile label="Waiting for limit" value={st.waiting} />
              <Tile label="Not filled" value={st.not_filled} sub="no win, no loss" />
            </div>
          </Card>

          {/* by kind of signal */}
          <Card title="Results by kind of signal"
            sub={<span title="Safe: the main plan. Risky: the tight-stop option (0.75 ATR), also held up to 14 days; the old fixed plan's rows used 0.5 ATR and 5 days. ATR = the coin's average daily move.">Safe = the main plan. Risky = the tight-stop 10–20x option. Both up to 14 days.</span>}>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[720px] text-[13px]">
                <thead className="text-left text-[12px] text-slate-500">
                  <tr className="border-b border-line"><th className="py-2 font-medium">Kind</th><th className="font-medium">Signals</th><th className="font-medium">Finished</th>
                    <th className="font-medium">Safe: made money</th><th className="font-medium">Safe: avg</th><th className="font-medium">Safe: $</th>
                    <th className="font-medium">Risky: made money</th><th className="font-medium">Risky: avg</th></tr>
                </thead>
                <tbody>
                  {GROUPS.map(([k, label]) => {
                    const g = rep.stats[k];
                    if (!g) return null;
                    return (
                      <tr key={k} className="border-b border-line/60 last:border-0">
                        <td className="py-2.5 text-slate-200">{label}</td>
                        <td className="num">{g.signals}</td>
                        <td className="num">{g.swing.finished}</td>
                        <td className="num">{g.swing.finished ? `${pc(g.swing.win_rate)} (${g.swing.wins}/${g.swing.finished})` : "—"}</td>
                        <td className={`num ${tone(g.swing.avg_r)}`}>{R(g.swing.avg_r)}</td>
                        <td className={`num ${tone(g.swing.usd)}`}>{g.swing.finished ? usd(g.swing.usd) : "—"}</td>
                        <td className="num">{g.fast.finished ? `${pc(g.fast.win_rate)} (${g.fast.wins}/${g.fast.finished})` : "—"}</td>
                        <td className={`num ${tone(g.fast.avg_r)}`}>{R(g.fast.avg_r)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <p className="mt-3 text-[12.5px] text-slate-500">Backtest on unseen 2025–26: +0.08R a trade, 38% made money (shorts +0.46R, longs −0.05R).</p>
          </Card>

          {/* machine vs agents */}
          <Card title="Machine vs AI agents" sub="Does listening to the agents help? Split by what they said before the fill.">
            <div className="overflow-x-auto">
              <table className="w-full min-w-[620px] text-[13px]">
                <thead className="text-left text-[12px] text-slate-500">
                  <tr className="border-b border-line"><th className="py-2 font-medium">Group</th><th className="font-medium">Signals</th><th className="font-medium">Finished</th>
                    <th className="font-medium">Made money</th><th className="font-medium">Avg per trade</th><th className="font-medium">$</th></tr>
                </thead>
                <tbody>
                  {AGENT_GROUPS.map(([k, label]) => {
                    const g = rep.stats[k];
                    if (!g) return null;
                    return (
                      <tr key={k} className="border-b border-line/60 last:border-0">
                        <td className="py-2.5 text-slate-200">{label}</td>
                        <td className="num">{g.signals}</td>
                        <td className="num">{g.swing.finished}</td>
                        <td className="num">{g.swing.finished ? `${pc(g.swing.win_rate)} (${g.swing.wins}/${g.swing.finished})` : "—"}</td>
                        <td className={`num ${tone(g.swing.avg_r)}`}>{R(g.swing.avg_r)}</td>
                        <td className={`num ${tone(g.swing.usd)}`}>{g.swing.finished ? usd(g.swing.usd) : "—"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {rep.agent_veto && (
              <Note tone={rep.agent_veto.veto_on ? "good" : "info"} className="mt-4">
                <b title="Rule written before any data: after 30 finished signals with an agent verdict, the agents get a veto only if their 'agree' group beats the machine alone by 0.15R a trade and the trades they skipped lost money.">Agents as a veto: </b>{rep.agent_veto.decided ? rep.agent_veto.text : `${rep.agent_veto.have} of ${rep.agent_veto.needed} checked. Until then the machine decides; the agents only advise.`}
              </Note>
            )}
            {rep.agents_own && (
              <div className="mt-5">
                <div className="text-[13px] font-semibold text-slate-200">The AI agents&apos; own calls</div>
                <p className="text-[12px] text-slate-500">Each desk run, graded on its own entry, stop and target. {rep.agents_own.note}</p>
                <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-4">
                  <Tile label="Desk runs" value={rep.agents_own.all.calls} sub={`${rep.agents_own.all.graded} graded · ${rep.agents_own.all.open} open`} />
                  <Tile label="When they said trade" value={pc(rep.agents_own.taken.win_rate)} sub={`${rep.agents_own.taken.graded} graded · avg ${R(rep.agents_own.taken.avg_r)}`} tone={tone(rep.agents_own.taken.avg_r)} />
                  <Tile label={<span title="Red is good for the agents here: the trades they skipped would have lost.">When they said wait</span>} value={pc(rep.agents_own.waited_counterfactual.win_rate)} sub={`if taken anyway · avg ${R(rep.agents_own.waited_counterfactual.avg_r)}`} tone={tone(rep.agents_own.waited_counterfactual.avg_r === null ? null : -rep.agents_own.waited_counterfactual.avg_r)} />
                  <Tile label="Targets · stops" value={`${rep.agents_own.all.target_first} · ${rep.agents_own.all.stop_first}`} sub={`${rep.agents_own.all.expired} ran out of time`} />
                </div>
              </div>
            )}
          </Card>

          <Card title="How each signal is graded">
            <div className="text-[13px] text-slate-400">
              <ul className="list-disc space-y-1.5 pl-5">
                <li>Saved at the moment it is given, with entry, stop and targets frozen. Only candles that start after that count.</li>
                <li>Market entry fills at the saved price. A limit entry must be reached within 48 hours, or it counts as &quot;not filled&quot; (no win, no loss).</li>
                <li>Safe plan: the stop is −1R. At TP1, half is closed and the stop moves to entry. Then it ends at entry or at TP2. Time limit 14 days.</li>
                <li>Risky plan (the tight-stop option): one stop and one target, up to 14 days (the old fixed plan&apos;s rows: 5 days).</li>
                <li>If one 1h candle touches both the stop and a target, it counts as the stop. Fees of 0.1% are included.</li>
                <li>Each day the platform saves its top LONG and top SHORT, TRADE or WATCH alike, so you can see whether the rules help.</li>
                <li>Dollars are at the safe leverage (no liquidation before the stop) for the saved margin. Nothing is ever sent to an exchange.</li>
              </ul>
            </div>
          </Card>
          </More>
        </>
      )}
      {!rep && !err && <div className="flex items-center gap-2 text-[13.5px] text-slate-500"><Loader2 size={16} className="animate-spin" /> Loading…</div>}
    </Page>
  );
}
