"use client";
import { ChevronLeft, ChevronRight, Loader2 } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { fmtPrice, pair } from "@/lib/format";
import { Card, More, Note, Page, PageHead } from "@/components/ui/Page";

type Live = { id: string; symbol: string; side: "LONG" | "SHORT"; entry: number; stop: number; tp1: number; state: string; outcome: string | null;
  r: number | null; created_at: string; source: string; payload: { engine?: string } };
type M = { rank: number; symbol: string; side: "LONG" | "SHORT"; plan: string; pick: boolean; clear: boolean; expected_r: number;
  result_r: number | null; outcome: "won" | "lost" | null; of: number; now_r?: number | null; source?: string };
type Day = { date: string; live: Live[]; machine: M[]; record_from: string | null; record_to: string | null; note: string };

const iso = (d: Date) => d.toISOString().slice(0, 10);
const localToday = () => { const d = new Date(); return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10); };
const shift = (s: string, n: number) => { const d = new Date(`${s}T00:00:00Z`); d.setUTCDate(d.getUTCDate() + n); return iso(d); };
const r2 = (v: number | null) => (v === null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}R`);
const STATE: Record<string, string> = { waiting: "waiting to fill", open: "open", closed: "closed", not_filled: "not filled" };
const plan = (p: string) => { const [, s, r] = p.split("_"); return `stop ${s.replace("ATR", " ATR")} · ${r} target`; };

/** Signal history: pick a day and see what the platform said, and how it turned out. */
export default function HistoryPage() {
  const yesterday = shift(iso(new Date()), -1);
  const [on, setOn] = useState(yesterday);
  const [d, setD] = useState<Day | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const load = useCallback((x: string) => {
    setBusy(true); setErr("");
    api<Day>(`/agent/signal/history?date=${x}`).then(setD).catch((e) => setErr(String(e.message || e))).finally(() => setBusy(false));
  }, []);
  useEffect(() => { load(on); }, [on, load]);
  useEffect(() => {   // today's live signals keep updating while you look at them
    if (on < shift(iso(new Date()), -2)) return;
    const i = setInterval(() => load(on), 60_000);
    return () => clearInterval(i);
  }, [on, load]);
  const pick = d?.machine.find((m) => m.pick);

  return (
    <Page>
      <PageHead title="History" sub="Pick a day: what the platform said, and how it turned out">
        <div className="flex items-center gap-1">
          <button className="btn btn-ghost h-9 w-9 p-0" onClick={() => setOn(shift(on, -1))} title="Day before"><ChevronLeft size={16} /></button>
          <input className="input w-40" type="date" value={on} max={localToday()} min={d?.record_from ?? "2021-01-01"} onChange={(e) => e.target.value && setOn(e.target.value)} />
          <button className="btn btn-ghost h-9 w-9 p-0" onClick={() => setOn(shift(on, 1))} disabled={on >= localToday()} title="Day after"><ChevronRight size={16} /></button>
          {busy && <Loader2 size={16} className="ml-2 animate-spin text-slate-400" />}
        </div>
      </PageHead>
      {err && <Note tone="bad">{err}</Note>}
      {d && (
        <>
          {/* the answer: the machine's pick that day */}
          <Card title={`The machine's trade on ${d.date}`}>
            {pick ? (
              <div className="flex flex-wrap items-center gap-4">
                <span className={`rounded-xl px-3.5 py-1 text-[18px] font-black ${pick.side === "LONG" ? "bg-emerald-500/15 text-emerald-300" : "bg-red-500/15 text-red-300"}`}>{pick.side}</span>
                <b className="text-[20px] text-slate-100">{pair(pick.symbol)}</b>
                <span className="text-[13px] text-slate-500">{plan(pick.plan)} · ranked #1 of {pick.of}</span>
                <span className={`ml-auto rounded-xl px-4 py-2 text-[18px] font-semibold ${pick.outcome === "won" ? "bg-emerald-500/15 text-emerald-300" : pick.outcome === "lost" ? "bg-red-500/15 text-red-300" : "bg-fill text-slate-300"}`}>
                  {pick.outcome ? `${pick.outcome === "won" ? "Won" : "Lost"} ${r2(pick.result_r)}` : `Still running${pick.now_r != null ? ` · ${r2(pick.now_r)} now` : ""}`}</span>
              </div>
            ) : <div className="text-[13px] text-slate-500">No machine record for this day{d.record_to && d.date > d.record_to ? ` (the record runs to ${d.record_to}; newer days are in the live signals below)` : ""}.</div>}
          </Card>

          <div className="grid gap-6 lg:grid-cols-2">
            <Card title="Saved live that day" sub="Frozen when given, graded on live prices (from 23 Nov 2026)">
              {d.live.length === 0 ? <div className="text-[13px] text-slate-500">{pick ? `No new row that day: the machine's pick (${pair(pick.symbol)}) was already being tracked, or the test started later (23 Nov 2026).` : "No signal was saved on this day."}</div> : (
                <div className="divide-y divide-line text-[13px]">
                  {d.live.map((x) => (
                    <div key={x.id} className="flex flex-wrap items-center gap-3 py-2.5">
                      <span className={`rounded-md px-1.5 py-0.5 text-[10.5px] font-black ${x.side === "LONG" ? "bg-emerald-500/15 text-emerald-300" : "bg-red-500/15 text-red-300"}`}>{x.side}</span>
                      <b className="text-slate-100">{pair(x.symbol)}</b>
                      <span className="text-[12px] text-slate-500">{new Date(x.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} · entry {fmtPrice(x.entry)} · stop {fmtPrice(x.stop)} · target {fmtPrice(x.tp1)}</span>
                      <span className={`ml-auto font-semibold ${x.r === null ? "text-slate-400" : x.r > 0 ? "text-emerald-300" : "text-red-300"}`}>
                        {x.state === "closed" ? `${x.outcome} · ${r2(x.r)}` : `${STATE[x.state] ?? x.state}${x.r !== null ? ` · ${r2(x.r)} now` : ""}`}</span>
                    </div>))}
                </div>
              )}
              <Link href="/signal/test" className="mt-3 block text-[12.5px] text-accent hover:underline">All live results →</Link>
            </Card>

            <Card title="The machine's top 10 that day" sub={d.machine[0]?.source === "saved" ? "Saved that day, graded live on prices since; only #1 is a trade" : "From its out-of-sample record; only #1 is a trade"}>
              {d.machine.length === 0 ? <div className="text-[13px] text-slate-500">No record for this day.</div> : (
                <div className="divide-y divide-line text-[13px]">
                  {d.machine.map((m) => (
                    <div key={m.symbol} className={`flex items-center gap-3 py-2 ${m.clear ? "" : "opacity-60"}`}>
                      <span className="w-5 text-slate-500">{m.rank}</span>
                      {m.clear ? <span className={`rounded-md px-1.5 py-0.5 text-[10.5px] font-black ${m.side === "LONG" ? "bg-emerald-500/15 text-emerald-300" : "bg-red-500/15 text-red-300"}`}>{m.side}</span>
                        : <span className="rounded-md bg-slate-500/15 px-1.5 py-0.5 text-[10.5px] font-bold text-slate-400">NO CLEAR SIDE</span>}
                      <b className="text-slate-100">{pair(m.symbol)}</b>
                      <span className="hidden text-[11.5px] text-slate-500 md:inline">{plan(m.plan)}</span>
                      <span className={`ml-auto font-semibold ${m.result_r === null ? "text-slate-400" : m.result_r > 0 ? "text-emerald-300" : "text-red-300"}`}>{m.clear ? (m.outcome ? `${m.outcome} ${r2(m.result_r)}` : `running${m.now_r != null ? ` · ${r2(m.now_r)}` : ""}`) : "—"}</span>
                    </div>))}
                </div>
              )}
            </Card>
          </div>

          <More>
            <Card title="How to read this">
              <ul className="list-disc space-y-1 pl-5 text-[13px] text-slate-400">
                <li><b className="text-slate-300">The machine&apos;s trade</b> is the #1 coin of the day: the only one it trades. Its result is the plan&apos;s real outcome after fees, in R (1R = the loss at the stop).</li>
                <li><b className="text-slate-300">Saved live</b> signals were frozen at the moment they were given and graded on live prices: the honest forward record.</li>
                <li><b className="text-slate-300">Top 10</b> comes from the out-of-sample record ({d.record_from} to {d.record_to}): every day predicted by models that never saw it. It shows what the machine would have ranked, not a message saved that day.</li>
                <li>&quot;Running&quot; means neither the stop nor the take profit has been hit yet (up to 14 days); the R shown is where it stands now.</li>
                <li>A day&apos;s signals are made from that day&apos;s closing prices and given just after midnight UTC.</li>
                <li>Days after the last training ({d.record_to}) come from the ranking saved each day.</li>
              </ul>
            </Card>
          </More>
        </>
      )}
    </Page>
  );
}
