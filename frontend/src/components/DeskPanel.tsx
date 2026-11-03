"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { fmtPrice, pair } from "@/lib/format";

/** Polls an intelligent-desk endpoint while it is running (the backend runs the agents in the background). */
export function useDesk(path: string | null) {
  const [intel, setIntel] = useState<any>(null);
  useEffect(() => {
    if (!path) { setIntel(null); return; }
    let stop = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = () => api(path).then((r) => {
      if (stop) return;
      setIntel(r);
      if (r.status === "running") timer = setTimeout(tick, 8000);
    }).catch(() => { if (!stop) timer = setTimeout(tick, 15000); });
    setIntel(null);
    tick();
    return () => { stop = true; if (timer) clearTimeout(timer); };
  }, [path]);
  return intel;
}

const color = (lvl: string) => (lvl === "warn" ? "text-amber-200" : lvl === "good" ? "text-emerald-200" : "text-slate-300");
const countTools = (used: any[]) => used.reduce((acc: Record<string, number>, u) => { acc[u.name] = (acc[u.name] || 0) + 1; return acc; }, {});
const short = (e: string | null | undefined) => (e ? e.replace(/^Error code: \d+ - /, "").split(". ")[0].replace(/\.$/, "") : "");

/** One desk: the rule-based facts of every agent plus, when the AI is available, its analysis, tools used and confidence. */
export default function DeskPanel({ title, facts, intel, briefing, llm }: { title: string; facts: any[]; intel: any; briefing?: string | null; llm?: any }) {
  const byAgent = new Map<string, any>((intel?.agents ?? []).map((a: any) => [a.agent, a]));
  const status: string = !intel ? "connecting" : intel.status;
  const aiOff = status === "unavailable" || (llm && (!llm.configured || llm.error));
  const chip = status === "ready" ? `AI desk ready · ${intel.model} · ${intel.tool_calls} tool calls · ${intel.seconds}s`
    : status === "running" ? `AI desk thinking… ${intel.agents.length}/${facts.length} agents done, ${intel.tool_calls} tool calls so far`
    : status === "unavailable" ? "rule-based agents" : "connecting…";
  return (
    <div className="card p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2 text-[12px]">
        <span className="font-medium text-slate-300">{title}</span>
        <span className={`chip ${status === "ready" ? "bg-emerald-500/10 text-emerald-300" : status === "running" ? "bg-sky-500/10 text-sky-300" : "bg-white/5 text-slate-400"}`}>{chip}</span>
      </div>
      {aiOff && (
        <div className="mb-2 rounded border border-amber-500/30 bg-amber-500/10 p-2 text-[12px] text-amber-200">
          {llm && !llm.configured
            ? <>No AI key is configured, so the agents run on their rules. Add <code>AIT_OPENAI_API_KEY</code> or <code>AIT_ANTHROPIC_API_KEY</code> to <code>backend/.env</code> to switch on the AI desk (LLM agents with tools).</>
            : <>AI reasoning is paused — {llm?.provider ?? "the provider"} ({llm?.model ?? intel?.model}) says: “{short(llm?.error ?? intel?.error) || "unavailable"}”. The rule-based agents below keep working with the full analysis; the AI desk retries every 5 minutes.</>}
        </div>
      )}
      {briefing && <p className="mb-2 rounded bg-white/5 p-2 text-[12px] text-slate-200">{briefing}</p>}
      <div className="space-y-2">
        {facts.map((m: any) => {
          const a = byAgent.get(m.agent);
          return (
            <div key={m.agent} className="flex gap-2 text-[12px]">
              <span className="w-32 shrink-0 font-medium" style={{ color: m.color }} title={m.role}>{m.name}</span>
              <div className="min-w-0 flex-1">
                {a?.text ? (
                  <>
                    <div className={color(a.level)}>{a.text}</div>
                    <div className="mt-1 flex flex-wrap gap-1 text-[10px] text-slate-500">
                      <span className="mr-1">tools:</span>
                      {Object.entries(countTools(a.tools_used)).map(([n, c]: any) => <span key={n} className="chip bg-white/5">{n}{c > 1 ? ` ×${c}` : ""}</span>)}
                      {a.tools_used.length === 0 && <span className="chip bg-white/5">none</span>}
                      {a.confidence !== null && a.confidence !== undefined && <span className="chip bg-white/5">confidence {(a.confidence * 100).toFixed(0)}%</span>}
                    </div>
                    <div className="mt-1 text-[11px] text-slate-500"><span className="uppercase tracking-wider">facts</span> · {m.text}</div>
                  </>
                ) : (
                  <>
                    <div className={color(m.level)}>{m.text}</div>
                    {status === "running" && <div className="text-[10px] text-sky-300/70">AI analysis coming…</div>}
                    {status === "ready" && a?.error && <div className="text-[10px] text-amber-300/70">AI analysis failed for this agent: {short(a.error)}</div>}
                  </>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** The ranked coins with their pre-computed analysis cards (the rules rank them; the Scout agent refines when the AI is on). */
export function TopList({ rows, source }: { rows: any[]; source?: string }) {
  if (!rows?.length) return null;
  const pct = (v: number | null | undefined, sign = true) => (v === null || v === undefined ? "—" : `${sign && v > 0 ? "+" : ""}${v}%`);
  return (
    <div className="card overflow-auto p-2">
      <div className="flex items-center px-1 pb-1 text-[12px]">
        <span className="font-medium text-slate-300">Top list — already analysed, best first</span>
        {source && <span className="ml-2 text-slate-500">{source}</span>}
      </div>
      <table className="w-full text-[12px]">
        <thead className="text-left text-[10px] uppercase tracking-wider text-slate-500">
          <tr>{["#", "Coin", "Verdict today", "Price", "To breakout", "60-day", "Trend rank", "ATR · stop", "Safe lev.", "Funding /yr", "Breakout record since 2020", "Why it is here"].map((h) => <th key={h} className="px-2 py-1.5">{h}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((c: any) => {
            const q = c.quant, r = c.breakout_record;
            const live = typeof c.verdict === "string" && c.verdict.startsWith("setup live");
            return (
              <tr key={c.symbol} className="border-t border-line/60">
                <td className="px-2 py-1 text-slate-500">{c.rank}</td>
                <td className="px-2 py-1 font-medium text-slate-100">{pair(c.symbol)}</td>
                <td className={`px-2 py-1 ${live ? "text-emerald-200" : c.verdict?.startsWith("near") ? "text-sky-200" : "text-slate-400"}`}>{c.verdict}</td>
                <td className="num px-2 py-1">{c.price ? fmtPrice(c.price) : "—"}</td>
                <td className="num px-2 py-1">{c.distance_to_breakout_pct === null || c.distance_to_breakout_pct === undefined ? "—" : `${c.distance_to_breakout_pct}%`}</td>
                <td className={`num px-2 py-1 ${(c.momentum_60d_pct ?? 0) >= 0 ? "text-up" : "text-down"}`}>{pct(c.momentum_60d_pct)}</td>
                <td className="num px-2 py-1">{q ? `${q.rank} of ${q.of} (${q.trend})` : "—"}</td>
                <td className="num px-2 py-1">{c.atr_pct !== undefined ? `${c.atr_pct}% · ${c.stop_pct}%` : "—"}</td>
                <td className="num px-2 py-1">{c.safe_leverage ? `${c.safe_leverage}×` : "—"}</td>
                <td className="num px-2 py-1">{c.funding_apr_pct === null || c.funding_apr_pct === undefined ? "—" : pct(c.funding_apr_pct)}</td>
                <td className="num px-2 py-1">{r ? `${r.trades} trades · ${(r.win_rate * 100).toFixed(0)}% won · ${r.avg_r > 0 ? "+" : ""}${r.avg_r}R` : "—"}</td>
                <td className="px-2 py-1 text-slate-300">{c.reason}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
