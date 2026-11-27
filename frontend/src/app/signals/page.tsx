"use client";
import { ArrowRight, Bot, Check, Loader2, Minus, Play, X } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import BestSignal from "@/components/desk/BestSignal";
import { api } from "@/lib/api";
import { fmtPrice, pair } from "@/lib/format";
import { Card, More, Note, Page, PageHead } from "@/components/ui/Page";
import { aiReason, useAi } from "@/lib/useAi";

type Conf = { label: "High" | "Medium" | "Low" | "Unknown"; win_rate?: number | null; avg_r?: number | null; n?: number; basis?: string; vs_rest_r?: number | null };
type Row = { rank: number; symbol: string; side: "LONG" | "SHORT"; plan: string; pick: boolean; clear: boolean; live_state?: { state: "valid" | "stop_hit" | "target_hit" | "ran_away" | "unknown"; text: string } | null; live_pct: number | null; live_r: number | null; expected_r: number; price: number;
  move_since_model_pct: number | null; entry: number; stop: number; target: number; stop_pct: number; target_pct: number;
  confidence: Conf; trend: { view: string | null; label: string | null }; setup_4h: { view: string | null; action: string | null };
  agree: number; disagree: number; checked: number; agents: { action: string; confidence: number | null; age_hours: number } | null };
type Bucket = { label: string; unseen: { n: number; win_rate?: number; avg_r?: number }; by_side: Record<string, { n: number; win_rate?: number; avg_r?: number }> };
type Board = { available: boolean; note?: string; as_of?: string; market?: { trend: "up" | "down" } | null; rows?: Row[];
  calibration?: { buckets: Bucket[] } | null; setups_ready?: boolean };

const CONF: Record<Conf["label"], string> = { High: "bg-emerald-500/15 text-emerald-300", Medium: "bg-sky-500/15 text-sky-300",
  Low: "bg-red-500/15 text-red-300", Unknown: "bg-slate-500/15 text-slate-400" };
const pct = (v?: number | null) => (v === undefined || v === null ? "—" : `${Math.round(v * 100)}%`);
const r2 = (v?: number | null) => (v === undefined || v === null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}R`);
const LIVE: Record<string, [string, string]> = { stop_hit: ["stop already hit", "bg-red-500/15 text-red-300"],
  target_hit: ["target already hit", "bg-emerald-500/15 text-emerald-300"], ran_away: ["moved away · too late", "bg-amber-500/15 text-amber-300"] };
const over = (r: Row) => !!r.live_state && r.live_state.state in LIVE;
const WIDE = 20;   // a stop more than 20% away is too wide to trade with leverage
const wide = (r: Row) => Math.abs(r.stop_pct) > WIDE;
const FILTERS = [["all", "All"], ["clear", "Clear signals"], ["LONG", "Longs"], ["SHORT", "Shorts"], ["high", "High confidence"], ["agree", "Methods agree"]] as const;

function View({ v, side, label }: { v: string | null; side: string; label: string }) {
  const icon = !v ? <Minus size={12} className="text-slate-600" /> : v === side ? <Check size={12} className="text-emerald-400" /> : <X size={12} className="text-red-400" />;
  return <span className="flex items-center gap-0.5" title={`${label}: ${v ? v.toLowerCase() : "no view"}`}><span className="text-[10.5px] text-slate-500">{label}</span>{icon}</span>;
}

/** One signal as a card: side and coin, live price and result since the signal, the levels, how much to trust it,
 *  what the other methods and the AI agents say. Click to open it in the panel. */
function SignalCardView({ r, on, flash, onPick }: { r: Row; on: boolean; flash?: "up" | "down"; onPick: () => void }) {
  const long = r.side === "LONG";
  return (
    <button onClick={onPick} className={`group flex h-full flex-col rounded-2xl border p-4 text-left transition-all hover:-translate-y-0.5 hover:shadow-md ${on ? "border-accent bg-accent-soft/40" : "border-line bg-panel"} ${r.clear ? "" : "opacity-70"}`}>
      <div className="flex items-center gap-2">
        <span className="text-[12px] font-medium text-slate-500">#{r.rank}</span>
        {r.clear ? <span className={`rounded-md px-2 py-0.5 text-[11px] font-black ${long ? "bg-emerald-500/15 text-emerald-300" : "bg-red-500/15 text-red-300"}`}>{r.side}</span>
          : <span className="rounded-md bg-slate-500/15 px-2 py-0.5 text-[11px] font-bold text-slate-400">NO CLEAR SIDE</span>}
        <b className="text-[16px] text-slate-100">{pair(r.symbol)}</b>
        {r.pick && <span className="chip ml-auto bg-accent-soft text-[10.5px] text-accent">today&apos;s pick</span>}
      </div>
      <div className="mt-3 flex items-end justify-between gap-3">
        <div>
          <div className="text-[11.5px] text-slate-500">Live price</div>
          <div className={`num text-[20px] font-semibold transition-colors duration-700 ${flash === "up" ? "text-emerald-300" : flash === "down" ? "text-red-300" : "text-slate-100"}`}>{fmtPrice(r.price)}</div>
        </div>
        {r.clear && r.live_pct !== null && (
          <div className="text-right" title="As if the signal was taken at the model's daily close">
            <div className="text-[11.5px] text-slate-500">Since signal</div>
            <div className={`num text-[16px] font-semibold ${r.live_pct > 0 ? "text-emerald-300" : r.live_pct < 0 ? "text-red-300" : "text-slate-300"}`}>{r.live_pct > 0 ? "+" : ""}{r.live_pct}% <span className="text-[12px] font-normal text-slate-500">{r2(r.live_r)}</span></div>
          </div>
        )}
      </div>
      {r.clear ? (
        <div className="mt-3 grid grid-cols-2 gap-2 text-[12px]">
          <div className="rounded-xl bg-fill px-3 py-2"><div className="text-slate-500">Stop loss</div><div className="num font-semibold text-red-300">{fmtPrice(r.stop)} <span className="font-normal text-slate-500">{r.stop_pct > 0 ? "+" : ""}{r.stop_pct}%</span></div></div>
          <div className="rounded-xl bg-fill px-3 py-2"><div className="text-slate-500">Take profit</div><div className="num font-semibold text-emerald-300">{fmtPrice(r.target)} <span className="font-normal text-slate-500">{r.target_pct > 0 ? "+" : ""}{r.target_pct}%</span></div></div>
        </div>
      ) : <p className="mt-3 text-[12px] text-slate-500">The model has no reliable side for this coin today.</p>}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {r.clear && <span className={`chip ${CONF[r.confidence.label]}`} title={`${r.confidence.basis ?? ""}: ${r2(r.confidence.avg_r)} a trade on 2025–26; ${r2(r.confidence.vs_rest_r)} vs the rest`}>{r.confidence.label} confidence</span>}
        {r.clear && <span className="text-[11.5px] text-slate-500">won {pct(r.confidence.win_rate)} of similar</span>}
        {wide(r) && <span className="chip bg-amber-500/15 text-[10.5px] text-amber-300">very wide stop</span>}
        {over(r) && <span className={`chip text-[10.5px] ${LIVE[r.live_state!.state][1]}`} title={r.live_state!.text}>{LIVE[r.live_state!.state][0]}</span>}
      </div>
      <div className="mt-auto flex items-center gap-3 border-t border-line/60 pt-3 text-[12px]">
        <View v={r.trend.view} side={r.side} label="Trend" /><View v={r.setup_4h.view} side={r.side} label="4h" />
        <span className="ml-auto">{r.agents ? <span className={r.agents.action === r.side ? "text-emerald-300" : r.agents.action === "WAIT" ? "text-amber-300" : "text-red-300"}>AI: {r.agents.action === "WAIT" ? "wait" : r.agents.action.toLowerCase()}</span> : <span className="text-slate-500">AI: not asked</span>}</span>
      </div>
    </button>
  );
}

/** Ask the AI agents about one coin (only on the button: it spends AI credits); refresh the board when they finish. */
function AskAgents({ symbol, onDone }: { symbol: string; onDone: () => void }) {
  const [run, setRun] = useState<{ id: string; t0: number } | null>(null);
  const [err, setErr] = useState("");
  const [, tick] = useState(0);
  const ai = useAi();
  useEffect(() => { setRun(null); setErr(""); }, [symbol]);
  useEffect(() => {
    if (!run) return;
    const t = setInterval(async () => {
      tick((x) => x + 1);
      try {
        const s = await api<{ status: string; error?: string | null }>(`/agent/research/${run.id}`);
        if (s.status !== "running") { clearInterval(t); setRun(null); if (s.error) setErr(s.error); onDone(); }
      } catch { /* keep polling */ }
    }, 4000);
    return () => clearInterval(t);
  }, [run, onDone]);
  const start = async () => {
    setErr("");
    try {
      const s = await api<{ id: string }>("/agent/research", { method: "POST", json: { symbol, tf: "1d", side: "auto", budget: 100, margin: 100, mode: "auto", venue: "futures", force: true } });
      setRun({ id: s.id, t0: Date.now() });
    } catch (e) { setErr(String((e as Error).message || e)); }
  };
  return (
    <div className="rounded-2xl border border-line bg-panel p-4">
      <div className="flex items-center gap-2 text-[13px] font-semibold text-slate-200"><Bot size={15} className="text-amber-300" /> AI agents on {pair(symbol)}</div>
      <p className="mt-1 text-[12px] text-slate-500">9 AI agents check the chart, structure, news, flow and risk, then give their own call. About 3–5 minutes; uses AI credits.</p>
      {ai && !ai.ready && <p className="mt-2 rounded-lg bg-amber-500/10 px-3 py-2 text-[12px] text-amber-200">AI agents are off: {aiReason(ai)} Add credits on your AI provider&apos;s billing page to turn them on.</p>}
      <button className="btn btn-primary mt-3" disabled={!!run || (!!ai && !ai.ready)} onClick={start}>
        {run ? <><Loader2 size={14} className="animate-spin" /> Agents working… {Math.round((Date.now() - run.t0) / 1000)}s</> : <><Play size={14} /> Ask the AI agents</>}
      </button>
      {err && <p className="mt-2 text-[12px] text-red-300">{err}</p>}
      <Link href={`/desk?coin=${symbol}`} className="mt-3 block text-[12.5px] text-accent hover:underline">Open the AI agents page with the chart →</Link>
    </div>
  );
}

/** Signal board: every coin the machine scores today, live, ranked; the other methods' view; a confidence level from
 *  how signals ranked like it did on years the machine never saw. The AI agents run only on the coin you pick. */
export default function SignalBoard() {
  const [b, setB] = useState<Board | null>(null);
  const [err, setErr] = useState("");
  const [f, setF] = useState<(typeof FILTERS)[number][0]>("all");
  const [view, setView] = useState<"cards" | "table">("cards");
  useEffect(() => { try { const v = localStorage.getItem("board_view"); if (v === "table" || v === "cards") setView(v); } catch { /* private mode */ } }, []);
  const pickView = (v: "cards" | "table") => { setView(v); try { localStorage.setItem("board_view", v); } catch { /* private mode */ } };
  const [sel, setSel] = useState<string | null>(null);
  const [updated, setUpdated] = useState<number | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const prev = useRef<Record<string, number>>({});
  const [flash, setFlash] = useState<Record<string, "up" | "down">>({});
  const [, now] = useState(0);

  const load = useCallback(() => api<Board>("/agent/signal/board").then((x) => {
    const fl: Record<string, "up" | "down"> = {};
    for (const r of x.rows ?? []) {
      const p0 = prev.current[r.symbol];
      if (p0 !== undefined && r.price !== p0) fl[r.symbol] = r.price > p0 ? "up" : "down";
      prev.current[r.symbol] = r.price;
    }
    setFlash(fl); setB(x); setErr(""); setUpdated(Date.now());
    setTimeout(() => setFlash({}), 1200);
  }).catch((e) => setErr(String(e.message || e))), []);
  useEffect(() => { load(); const i = setInterval(load, 15_000); const c = setInterval(() => now((x) => x + 1), 1000); return () => { clearInterval(i); clearInterval(c); }; }, [load]);
  useEffect(() => { if (!sel && b?.rows?.length) setSel(b.rows[0].symbol); }, [b, sel]);

  const rows = useMemo(() => (b?.rows ?? []).filter((r) => f === "all" || (f === "clear" && r.clear) || (r.clear && r.side === f)
    || (f === "high" && r.clear && r.confidence.label === "High" && !wide(r) && !over(r))
    || (f === "agree" && r.clear && r.checked > 0 && r.disagree === 0 && r.agree > 0)), [b, f]);
  const longs = (b?.rows ?? []).filter((r) => r.clear && r.side === "LONG").length;
  const shorts = (b?.rows ?? []).filter((r) => r.clear && r.side === "SHORT").length;
  const unclear = (b?.rows ?? []).length - longs - shorts;
  const age = updated ? Math.round((Date.now() - updated) / 1000) : null;
  const onAgentsDone = useCallback(() => { load(); setRefreshKey((k) => k + 1); }, [load]);

  return (
    <Page>
      <PageHead title="All coins today" sub="Every coin scored by the machine · live prices · pick one to ask the AI agents">
        <span className="flex items-center gap-2 text-[12.5px] text-slate-500"><span className="relative flex h-2 w-2"><span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-60" /><span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500" /></span>
          Live{age !== null ? ` · updated ${age}s ago` : ""}</span>
      </PageHead>
      {err && <Note tone="bad">{err}</Note>}
      {b && !b.available && <Note tone="warn">{b.note}</Note>}
      {!b && !err && <div className="flex items-center gap-2 text-[13.5px] text-slate-500"><Loader2 size={16} className="animate-spin" /> Scoring every coin…</div>}
      {b?.available && b.rows && (
        <>
          <div className="grid gap-4 md:grid-cols-3">
            <div className="rounded-2xl border border-line bg-panel px-5 py-4"><div className="text-[12.5px] text-slate-500">Coins scored today</div>
              <div className="num text-[24px] font-semibold text-slate-100">{b.rows.length}</div>
              <div className="text-[12px] text-slate-500"><span className="text-emerald-300">{longs} long</span> · <span className="text-red-300">{shorts} short</span>{unclear > 0 ? <span title="The model's own forecast is negative for these coins, and past tests can't say which side is right: no side is claimed"> · {unclear} no clear side</span> : null} · data to {b.as_of}</div></div>
            <div className="rounded-2xl border border-line bg-panel px-5 py-4"><div className="text-[12.5px] text-slate-500">Market</div>
              <div className={`text-[20px] font-semibold ${b.market?.trend === "up" ? "text-emerald-300" : "text-red-300"}`}>{b.market?.trend === "up" ? "Rising" : "Falling"}</div>
              <div className="text-[12px] text-slate-500">BTC {b.market?.trend === "up" ? "above" : "below"} its 50-day average</div></div>
            <div className="rounded-2xl border border-line bg-panel px-5 py-4"><div className="text-[12.5px] text-slate-500">The machine trades only the #1 coin</div>
              <div className="text-[20px] font-semibold text-slate-100">{pair(b.rows.find((r) => r.pick)?.symbol ?? b.rows[0].symbol)}</div>
              <div className="text-[12px] text-slate-500">the others are ideas, ranked</div></div>
          </div>

          <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_340px]">
            <Card className="p-0">
              <div className="flex flex-wrap gap-1.5 border-b border-line px-5 py-3">
                {FILTERS.map(([k, l]) => (
                  <button key={k} onClick={() => setF(k)} className={`rounded-full px-3.5 py-1.5 text-[13px] font-medium ${f === k ? "bg-accent-soft text-accent" : "text-slate-400 hover:bg-fill"}`}>{l}</button>))}
                <div className="ml-auto flex rounded-full bg-fill p-1">
                  {(["cards", "table"] as const).map((v) => (
                    <button key={v} onClick={() => pickView(v)} className={`rounded-full px-3 py-1 text-[12.5px] font-medium capitalize ${view === v ? "bg-panel text-slate-100 shadow-sm" : "text-slate-400"}`}>{v}</button>))}
                </div>
              </div>
              {view === "cards" ? (
                <div className="grid gap-4 p-4 sm:grid-cols-2 2xl:grid-cols-3">
                  {rows.map((r) => <SignalCardView key={r.symbol} r={r} on={sel === r.symbol} flash={flash[r.symbol]} onPick={() => setSel(r.symbol)} />)}
                  {!rows.length && <div className="text-[13px] text-slate-500">No coins match this filter.</div>}
                </div>
              ) : <div className="overflow-x-auto">
                <table className="w-full min-w-[720px] text-[13px]">
                  <thead className="text-left text-[11.5px] text-slate-500">
                    <tr className="border-b border-line">
                      <th className="py-2.5 pl-5 font-medium">#</th><th className="font-medium">Coin</th><th className="font-medium">Live price</th>
                      <th className="font-medium" title="Live result as if the signal was taken at the model's daily close (display only)">Since signal</th>
                      <th className="font-medium">Stop loss / Take profit</th><th className="font-medium" title="How signals ranked like this did on 2025–26, years the machine never saw">Confidence</th>
                      <th className="font-medium" title="Do the other methods point the same way? Trend = the factor ranking; 4h = the chart-setup screener">Other methods</th>
                      <th className="pr-5 font-medium">AI agents</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => (
                      <tr key={r.symbol} onClick={() => setSel(r.symbol)}
                        className={`cursor-pointer border-b border-line/60 last:border-0 ${sel === r.symbol ? "bg-accent-soft/60" : "hover:bg-fill"} ${r.clear ? "" : "opacity-60"}`}>
                        <td className="py-3 pl-5 text-slate-500">{r.rank}</td>
                        <td className="py-2.5">
                          <div className="flex items-center gap-2">
                            {r.clear ? <span className={`rounded-md px-1.5 py-0.5 text-[10.5px] font-black ${r.side === "LONG" ? "bg-emerald-500/15 text-emerald-300" : "bg-red-500/15 text-red-300"}`}>{r.side}</span>
                              : <span className="rounded-md bg-slate-500/15 px-1.5 py-0.5 text-[10.5px] font-bold text-slate-400" title="The model's own forecast is negative here and past tests can't say which side is right">NO CLEAR SIDE</span>}
                            <b className="whitespace-nowrap text-slate-100">{pair(r.symbol)}</b>
                          </div>
                          {(r.pick || wide(r)) && <div className="mt-1 flex gap-1">
                            {r.pick && <span className="chip bg-accent-soft text-[10px] text-accent" title="The machine's trade today">today&apos;s pick</span>}
                            {wide(r) && <span className="chip bg-amber-500/15 text-[10px] text-amber-300" title="This coin moves so much that its stop is more than 20% away: not tradeable with leverage">very wide stop</span>}
                          </div>}
                        </td>
                        <td className={`num whitespace-nowrap transition-colors duration-700 ${flash[r.symbol] === "up" ? "text-emerald-300" : flash[r.symbol] === "down" ? "text-red-300" : "text-slate-200"}`}>
                          {fmtPrice(r.price)}{r.move_since_model_pct !== null && Math.abs(r.move_since_model_pct) >= 0.05 &&
                            <div className="text-[11px] text-slate-500" title="Move since the model's daily close">{r.move_since_model_pct > 0 ? "+" : ""}{r.move_since_model_pct}%</div>}</td>
                        <td className="num whitespace-nowrap text-[12px]">{r.clear && r.live_pct !== null ? <>
                          <div className={`font-semibold ${r.live_pct > 0 ? "text-emerald-300" : r.live_pct < 0 ? "text-red-300" : "text-slate-300"}`}>{r.live_pct > 0 ? "+" : ""}{r.live_pct}%</div>
                          <div className="text-[11px] text-slate-500">{r2(r.live_r)}</div>
                          {over(r) && <span className={`chip mt-0.5 text-[10px] ${LIVE[r.live_state!.state][1]}`} title={r.live_state!.text}>{LIVE[r.live_state!.state][0]}</span>}</> : <span className="text-slate-600">—</span>}</td>
                        {!r.clear ? <td className="text-[12px] text-slate-600">—</td> : <td className="num whitespace-nowrap text-[12px] leading-5">
                          <div><span className="text-red-300">{fmtPrice(r.stop)}</span> <span className="text-slate-500">{r.stop_pct > 0 ? "+" : ""}{r.stop_pct}%</span></div>
                          <div><span className="text-emerald-300">{fmtPrice(r.target)}</span> <span className="text-slate-500">{r.target_pct > 0 ? "+" : ""}{r.target_pct}%</span></div></td>}
                        {!r.clear ? <td className="text-[12px] text-slate-600">—</td> : <td title={`${r.confidence.basis ?? ""}: won ${pct(r.confidence.win_rate)} of ${r.confidence.n ?? 0}, ${r2(r.confidence.avg_r)} a trade on 2025–26; ${r2(r.confidence.vs_rest_r)} vs the other coins on the same side`}>
                          <span className={`chip ${CONF[r.confidence.label]}`}>{r.confidence.label}</span>
                          <div className="mt-0.5 whitespace-nowrap text-[11px] text-slate-500">won {pct(r.confidence.win_rate)} · {r2(r.confidence.avg_r)}</div>
                        </td>}
                        <td><div className="flex gap-2.5"><View v={r.trend.view} side={r.side} label="Trend" /><View v={r.setup_4h.view} side={r.side} label="4h" /></div></td>
                        <td className="whitespace-nowrap pr-5 text-[12px]">{r.agents ? <span className={r.agents.action === r.side ? "text-emerald-300" : r.agents.action === "WAIT" ? "text-amber-300" : "text-red-300"}>{r.agents.action === "WAIT" ? "Wait" : r.agents.action}{r.agents.confidence !== null ? ` · ${Math.round(r.agents.confidence * 100)}%` : ""}</span> : <span className="text-slate-600">not asked</span>}</td>
                      </tr>
                    ))}
                    {!rows.length && <tr><td colSpan={8} className="px-5 py-6 text-slate-500">No coins match this filter.</td></tr>}
                  </tbody>
                </table>
              </div>}
            </Card>

            <div className="space-y-4 xl:sticky xl:top-4">
              {sel ? <>
                <BestSignal symbol={sel} margin={100} refresh={`${refreshKey}`} />
                <AskAgents symbol={sel} onDone={onAgentsDone} />
                <Link href={`/signal?symbol=${sel}`} className="flex items-center justify-center gap-1 rounded-2xl border border-line bg-panel px-4 py-3 text-[13px] text-accent hover:bg-fill">Full plan for {pair(sel)} <ArrowRight size={14} /></Link>
              </> : <div className="rounded-2xl border border-line bg-panel p-4 text-[13px] text-slate-500">Pick a coin from the list.</div>}
            </div>
          </div>

          <More title="How accurate is each rank?">
            <Card title="Signals ranked like this, on years the machine never saw (2025–26)"
              sub="Each day every coin is ranked. This is what each rank's best plan actually made. Not a promise for one trade.">
              <div className="overflow-x-auto">
                <table className="w-full min-w-[620px] text-[13px]">
                  <thead className="text-left text-[12px] text-slate-500"><tr className="border-b border-line"><th className="py-2 font-medium">Rank</th><th className="font-medium">All</th><th className="font-medium">Longs</th><th className="font-medium">Shorts</th></tr></thead>
                  <tbody>{(b.calibration?.buckets ?? []).map((k) => (
                    <tr key={k.label} className="border-b border-line/60 last:border-0">
                      <td className="py-2.5 font-medium text-slate-200">{k.label}</td>
                      {[k.unseen, k.by_side.LONG, k.by_side.SHORT].map((s, i) => (
                        <td key={i} className={`num ${(s?.avg_r ?? 0) >= 0 ? "text-emerald-300" : "text-red-300"}`}>{r2(s?.avg_r)} <span className="text-[11.5px] text-slate-500">won {pct(s?.win_rate)} · {s?.n ?? 0}</span></td>))}
                    </tr>))}</tbody>
                </table>
              </div>
              <ul className="mt-3 list-disc space-y-1 pl-5 text-[12.5px] text-slate-400">
                <li>High = signals like it made +0.10R a trade or more; Medium = 0 to +0.10R; Low = they lost money.</li>
                <li>Part of it is the market: 2025–26 fell, so even low-ranked shorts made money. The tooltip on each row shows the machine&apos;s own part (vs the other coins on the same side).</li>
                <li>&quot;No clear side&quot;: the model&apos;s own forecast for its best-ranked plan is negative. Tested on such coins, the side it ranked made money in 2021–24 and lost in 2025–26, and the opposite side did the reverse, so no side is claimed.</li>
                <li>Each signal is checked against the hourly candles since it was given: &quot;stop already hit&quot; (it failed), &quot;target already hit&quot; (too late, don&apos;t chase) or &quot;moved away&quot; (half the way to the target already: a worse entry). Those can&apos;t be traded any more.</li>
                <li>&quot;Since signal&quot;: the live result as if the signal had been taken at the model&apos;s daily close (in % of the price and in R). Display only; your own trades are in Portfolio, the graded signals in Results.</li>
                <li>Prices update every 15 seconds. The machine re-scores once a day, after the daily candle closes.</li>
                <li>The AI agents are not run on the board: they run only when you press the button for one coin.</li>
              </ul>
            </Card>
          </More>
        </>
      )}
    </Page>
  );
}
