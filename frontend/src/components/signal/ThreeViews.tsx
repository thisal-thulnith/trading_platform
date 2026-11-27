"use client";
import { Bot, Cpu, Dices, Loader2, Play } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { fmtPrice, pair } from "@/lib/format";
import { Card, Note } from "@/components/ui/Page";
import { aiReason, useAi } from "@/lib/useAi";

export type SimStats = { n: number; win_rate?: number; avg_r?: number; since?: string;
  unseen?: { n: number; win_rate: number | null; avg_r: number | null } };
export type McRow = { leverage: number; liquidated_before_stop: boolean; risk_per_trade?: number; median?: number; p5?: number; p95?: number; chance_down?: number };
export type Simulation = { available: boolean; note?: string; plan: string; side: string; margin: number; n_trades: number;
  coin_plan: SimStats; engine: SimStats; side_stats: SimStats; plan_stats: SimStats; coin: SimStats; mc_sample: string; monte_carlo: McRow[] };
export type AgentVerdict = { action: string; side: string | null; confidence: number | null; summary: string; conditions: string[];
  invalidation: string | null; by: string | null; entry: number | null; stop: number | null; target: number | null; tf: string;
  session: string; model: string | null; at: string; age_hours?: number; stale?: boolean };
export type Final = { stance: "agree" | "wait" | "against" | "none"; tone: "good" | "warn" | "bad" | "neutral"; label: string; text: string };

type MachineIn = { symbol: string; side: "LONG" | "SHORT"; status: "trade" | "watch"; plan_id?: string; predicted_r?: number;
  unseen_avg_r?: number | null; unseen_trades?: number | null; why?: { group: string; r: number }[] };

const pct = (v?: number | null) => (v === undefined || v === null ? "—" : `${Math.round(v * 100)}%`);
const r2 = (v?: number | null) => (v === undefined || v === null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}R`);
const usd = (v?: number) => (v === undefined ? "—" : `${v < 0 ? "−" : "+"}$${Math.abs(v).toLocaleString()}`);
const tone = (v?: number | null) => (v === undefined || v === null ? "text-slate-300" : v >= 0 ? "text-emerald-300" : "text-red-300");
const sideTone = (s?: string | null) => (s === "LONG" ? "bg-emerald-500/15 text-emerald-300" : s === "SHORT" ? "bg-red-500/15 text-red-300" : "bg-amber-500/15 text-amber-300");
const planLabel = (p?: string) => (p ? p.replace(/_/g, " · ").replace("ATR", " ATR stop") : "");

function Row({ k, v, sub }: { k: string; v: React.ReactNode; sub?: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-line/60 py-1.5 text-[13px] last:border-0">
      <span className="text-slate-400">{k}</span>
      <span className="text-right"><span className="num font-semibold text-slate-100">{v}</span>{sub && <span className="ml-1.5 text-[11.5px] text-slate-500">{sub}</span>}</span>
    </div>
  );
}

function Stat({ label, s, unseen }: { label: string; s: SimStats; unseen?: boolean }) {
  const x = unseen && s.unseen ? s.unseen : s;
  if (!x.n) return <Row k={label} v="no trades" />;
  return <Row k={label} v={<span className={tone(x.avg_r)}>{r2(x.avg_r)}</span>} sub={`${pct(x.win_rate)} won · ${x.n}`} />;
}

/** 1 · the machine: the unified engine's own call, nothing else mixed in. */
function MachineCol({ m }: { m: MachineIn }) {
  const top = (m.why ?? []).slice().sort((a, b) => Math.abs(b.r) - Math.abs(a.r)).slice(0, 3);
  return (
    <div className="rounded-2xl bg-fill p-4">
      <div className="mb-3 flex items-center gap-2 text-[13px] font-semibold text-slate-200"><Cpu size={15} className="text-sky-300" /> 1 · Machine model</div>
      <div className="flex items-center gap-2">
        <span className={`rounded-lg px-2.5 py-1 text-[15px] font-black ${sideTone(m.side)}`}>{m.side}</span>
        <span className={`chip ${m.status === "trade" ? "bg-emerald-500/15 text-emerald-300" : "bg-amber-500/15 text-amber-300"}`}>{m.status === "trade" ? "trade" : "watch only"}</span>
      </div>
      <div className="mt-3">
        <Row k="Plan" v={planLabel(m.plan_id)} />
        <Row k="Expected result" v={<span className={tone(m.predicted_r)}>{r2(m.predicted_r)}</span>} sub="this trade" />
        <Row k="Past test, 2025–26" v={<span className={tone(m.unseen_avg_r)}>{r2(m.unseen_avg_r)}</span>} sub={m.unseen_trades ? `${m.unseen_trades} trades` : undefined} />
      </div>
      {top.length > 0 && (
        <div className="mt-3 text-[12px] text-slate-500">Main reasons
          <ul className="mt-1 space-y-0.5">{top.map((g) => <li key={g.group} className="flex justify-between gap-3"><span className="truncate text-slate-300">{g.group}</span> <span className={`num shrink-0 ${tone(g.r)}`}>{r2(g.r)}</span></li>)}</ul></div>
      )}
      <p className="mt-3 text-[11.5px] text-slate-500" title="Market data, 21 indicators and learned chart patterns in one model">One trained model. No AI.</p>
    </div>
  );
}

/** 2 · the simulation: the engine's own past picks like this one, and a Monte Carlo of the next 20. */
function SimCol({ s }: { s: Simulation | null }) {
  if (!s || !s.available) return (
    <div className="rounded-2xl bg-fill p-4 text-[13px] text-slate-500">
      <div className="mb-3 flex items-center gap-2 text-[13px] font-semibold text-slate-200"><Dices size={15} className="text-violet-300" /> 2 · Simulation</div>
      {s?.note ?? "Not available for this coin."}
    </div>
  );
  const sideWord = s.side.toLowerCase();
  return (
    <div className="rounded-2xl bg-fill p-4">
      <div className="mb-1 flex items-center gap-2 text-[13px] font-semibold text-slate-200"><Dices size={15} className="text-violet-300" /> 2 · Simulation</div>
      <p className="mb-2 text-[11.5px] text-slate-500" title="Unseen years 2025–26: average a trade · share that made money · number of trades">The machine&apos;s past picks, 2025–26</p>
      <Stat label={`All its ${sideWord}s`} s={s.side_stats} unseen />
      <Stat label="This exact plan" s={s.plan_stats} unseen />
      <Stat label="On this coin" s={s.coin} unseen />
      <Stat label="All its trades" s={s.engine} unseen />
      <Row k="This plan, any day, no model" v={<span className={tone(s.coin_plan.avg_r)}>{r2(s.coin_plan.avg_r)}</span>} sub={s.coin_plan.n ? `${pct(s.coin_plan.win_rate)} · ${s.coin_plan.n} days` : undefined} />
      {s.monte_carlo.length > 0 && (
        <div className="mt-3">
          <div className="text-[12px] font-medium text-slate-300">Next {s.n_trades} trades like this · ${s.margin} margin each · 10,000 runs</div>
          <table className="mt-1.5 w-full text-[12.5px]">
            <thead className="text-[11px] text-slate-500"><tr><th className="py-1 text-left font-normal">Lev</th><th className="text-right font-normal">Middle</th><th className="text-right font-normal" title="90% of runs end between these two">Bad · good</th><th className="text-right font-normal">Ends down</th></tr></thead>
            <tbody>
              {s.monte_carlo.map((m) => m.liquidated_before_stop ? (
                <tr key={m.leverage} className="border-t border-line/60"><td className="py-1 font-semibold text-slate-300">{m.leverage}x</td><td colSpan={3} className="text-right text-red-300">liquidated before the stop</td></tr>
              ) : (
                <tr key={m.leverage} className="num border-t border-line/60">
                  <td className="py-1 font-semibold text-slate-300">{m.leverage}x</td>
                  <td className={`text-right font-semibold ${tone(m.median)}`}>{usd(m.median)}</td>
                  <td className="text-right text-slate-400">{usd(m.p5)} · {usd(m.p95)}</td>
                  <td className="text-right text-slate-300">{pct(m.chance_down)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-1.5 text-[11px] text-slate-500">Drawn from {s.mc_sample === "side_unseen" ? `its ${sideWord}s in 2025–26` : s.mc_sample === "side" ? `all its ${sideWord}s` : "all its trades"}. Not a promise.</p>
        </div>
      )}
    </div>
  );
}

/** 3 · the AI agents: their own separate analysis, run only when you press the button. */
function AgentsCol({ v, symbol, margin, onDone }: { v: AgentVerdict | null; symbol: string; margin: number; onDone: () => void }) {
  const [run, setRun] = useState<{ id: string; t0: number } | null>(null);
  const [err, setErr] = useState("");
  const [, tick] = useState(0);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (!run) return;
    timer.current = setInterval(async () => {
      tick((x) => x + 1);
      try {
        const s = await api<{ status: string; error?: string | null }>(`/agent/research/${run.id}`);
        if (s.status !== "running") {
          if (timer.current) clearInterval(timer.current);
          setRun(null);
          if (s.status === "error" || s.error) setErr(s.error || "The agents stopped.");
          onDone();
        }
      } catch { /* keep polling */ }
    }, 4000);
    return () => { if (timer.current) clearInterval(timer.current); };
  }, [run, onDone]);

  const start = async () => {
    setErr("");
    try {
      const s = await api<{ id: string }>("/agent/research", { method: "POST",
        json: { symbol, tf: "1d", side: "auto", budget: margin, margin, mode: "auto", venue: "futures", force: true } });
      setRun({ id: s.id, t0: Date.now() });
    } catch (e) { setErr(String((e as Error).message || e)); }
  };

  const fresh = v && !v.stale;
  const ai = useAi();
  return (
    <div className="rounded-2xl bg-fill p-4">
      <div className="mb-3 flex items-center gap-2 text-[13px] font-semibold text-slate-200"><Bot size={15} className="text-amber-300" /> 3 · AI agents</div>
      {v ? (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <span className={`rounded-lg px-2.5 py-1 text-[15px] font-black ${sideTone(v.action === "WAIT" ? null : v.action)}`}>{v.action}</span>
            {v.confidence !== null && <span className="text-[12px] text-slate-400">confidence {pct(v.confidence)}</span>}
            <span className={`text-[11.5px] ${fresh ? "text-slate-500" : "text-amber-300"}`}>{v.age_hours !== undefined ? `${v.age_hours < 1 ? "just now" : `${Math.round(v.age_hours)}h ago`}` : ""}{!fresh && " · old, run again"}</span>
          </div>
          {v.conditions.length > 0 && <ul className="mt-3 list-disc space-y-1 pl-5 text-[12.5px] text-slate-300">{v.conditions.slice(0, 3).map((c) => <li key={c}>{c}</li>)}</ul>}
          <details className="mt-2">
            <summary className="cursor-pointer text-[12px] text-slate-400 hover:text-slate-200">Head Trader&apos;s reasoning</summary>
            <p className="mt-1.5 text-[12.5px] leading-relaxed text-slate-400">{v.summary}</p>
            {v.entry !== null && v.stop !== null && <p className="mt-1.5 text-[12px] text-slate-500">Their plan ({v.side}): entry {fmtPrice(v.entry)} · stop {fmtPrice(v.stop)} · target {v.target !== null ? fmtPrice(v.target) : "—"}</p>}
            <Link href={`/desk?coin=${symbol}`} className="mt-1.5 inline-block text-[12px] text-accent hover:underline">Open the full desk →</Link>
          </details>
        </>
      ) : <p className="text-[13px] text-slate-400" title="Chart, structure, liquidity, flow, news, quant, red team, risk, head trader">Not run yet for {pair(symbol)}. 9 AI agents give their own call.</p>}
      <div className="mt-3 flex items-center gap-2">
        <button className="btn btn-ghost" disabled={!!run || (!!ai && !ai.ready)} onClick={start} title={ai && !ai.ready ? aiReason(ai) : ""}>
          {run ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
          {run ? `Agents working… ${Math.round((Date.now() - run.t0) / 1000)}s` : v ? "Run again" : "Run AI agents"}
        </button>
      </div>
      {run && <p className="mt-1.5 text-[11.5px] text-slate-500">About 3–5 minutes. Uses AI credits.</p>}
      {ai && !ai.ready && <p className="mt-1.5 text-[11.5px] text-amber-300">AI off: {aiReason(ai)}</p>}
      {err && <p className="mt-1.5 text-[12px] text-red-300">{err}</p>}
    </div>
  );
}

/** Machine, simulation and agents side by side: three separate opinions, then a plain reading of how they line up. */
export default function ThreeViews({ machine, sim, agents, final, margin, onAgentsDone }: {
  machine: MachineIn; sim: Simulation | null; agents: AgentVerdict | null; final: Final | null; margin: number; onAgentsDone: () => void;
}) {
  const t = final?.tone ?? "neutral";
  return (
    <Card title="Three separate opinions" sub="Each one decides on its own. Results scores all three.">
      {final && (
        <Note tone={t === "neutral" ? "info" : t} className="mb-4">
          <b>{final.label}.</b> {final.text}
        </Note>
      )}
      <div className="grid gap-4 lg:grid-cols-3">
        <MachineCol m={machine} />
        <SimCol s={sim} />
        <AgentsCol v={agents} symbol={machine.symbol} margin={margin} onDone={onAgentsDone} />
      </div>
    </Card>
  );
}
