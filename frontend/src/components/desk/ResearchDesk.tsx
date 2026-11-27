"use client";
import dynamic from "next/dynamic";
import { AlertTriangle, Bot, CheckCircle2, ChevronDown, ChevronRight, Circle, FastForward, Loader2, MousePointer2, PanelLeft, PanelRight, Play, Search, Wrench } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import type { ToXY } from "@/components/chart/TradingChart";
import { SignalBadge } from "@/components/SignalCard";
import MachineBar from "@/components/MachineBar";
import BestSignal from "./BestSignal";
import ModelForecast from "./ModelForecast";
import PatternCard from "./PatternCard";
import TakeTrade, { LeverageOptions, type Plan, PlanCells, PlanHowTo, PlanNotes, type TradeDraft } from "@/components/TakeTrade";
import { api } from "@/lib/api";
import { fmtPrice, pair } from "@/lib/format";
import { useStore } from "@/lib/store";
import type { ChartObject, Snapshot } from "@/lib/types";
import LiveCursors, { type DeskEvent, type DrawPhase, useCursorPlayer } from "./LiveCursors";
import ValidationPanel, { FactBadge, type FactCheck, type Validation } from "./ValidationPanel";

const TradingChart = dynamic(() => import("@/components/chart/TradingChart"), { ssr: false });
const TFS = [["15m", "Scalp"], ["1h", "Intraday"], ["4h", "Swing"], ["1d", "Position"]] as const;
const KEY = "research_desk_settings_v2";

type Tool = { name: string; args: Record<string, unknown>; ok: boolean };
type Status = "waiting" | "working" | "done" | "skipped";
type SubView = { id: string; name: string; focus: string; status: Status; text: string | null; view: string | null;
  confidence: number | null; key_points: string[]; tools_used: Tool[]; error: string | null };
type AgentView = { id: string; name: string; title?: string; role: string; stage: string; color: string; status: Status;
  text: string | null; view: string | null; confidence: number | null; key_points: string[]; tools_used: Tool[];
  error: string | null; by?: "ai" | "validators"; kind: "ai" | "checks"; rebuttal?: string; revised?: string; subs?: SubView[] };
type Readiness = { verdict: "ready" | "forming" | "not_now"; score: number; side: "LONG" | "SHORT" | null; summary: string;
  parts: { name: string; points: number; of: number; detail: string }[]; blocks: string[]; wait_for: string[] };
type Decision = { action: "LONG" | "SHORT" | "WAIT"; confidence: number | null; summary: string; conditions: string[];
  confidence_calibrated?: number; calibration?: string; votes?: { votes: string[]; action: string; note: string };
  invalidation: string | null; guard?: string; plan_side: "LONG" | "SHORT"; plan_is: string; by?: string };
type Session = { budget?: number; id: string; status: "running" | "done" | "error" | "unavailable" | "not_ready"; symbol: string | null; tf: string;
  readiness?: Readiness | null;
  margin: number | null; mode: string; venue: "spot" | "futures"; model: string | null;
  stages: { key: string; title: string; status: string }[]; agents: AgentView[]; events: DeskEvent[];
  decision: Decision | null; plan: Plan | null; validation: Validation | null; chart_objects: ChartObject[];
  error: string | null; tool_calls: number; seconds: number | null };
type AIStatus = { ready: boolean; configured: boolean; provider: string | null; model?: string; error?: string | null };
type Coin = { symbol: string; base: string; price: number };
type Settings = { budget: number | null; venue: "spot" | "futures"; side: "auto" | "long" | "short" };
type Short = { symbol: string; price: number; action: "BUY" | "SELL" | "WAIT"; why: string; reasons: string[] };
type Shortlist = { status: string; done?: number; total?: number; rows: Short[]; note: string };
type Tab = "plan" | "models" | "patterns" | "leverage" | "validation" | "howto";

const TEAM: [string, string, string, string | null, string][] = [
  ["Atlas", "Research Lead", "Plans the questions, then gathers the facts.", "Horizon, Compass, Archive", "#0a84ff"],
  ["Vega", "Technical Strategist", "Structure, momentum and levels.", "Keystone, Pulse, Chronos, Bastion", "#7d5cf6"],
  ["Tide", "Flow & Derivatives", "Funding, open interest, liquidations, news.", "Torque, Undertow, Echo", "#e8890c"],
  ["Ledger", "Fundamentals", "Supply, usage, valuation, strength vs BTC.", "Mint, Bedrock, Beacon", "#2f9e44"],
  ["Raven", "Red Team", "Attacks the idea with base rates.", "Odds, Mirror, Fracture", "#e5487f"],
  ["Sentinel", "Risk Officer", "Stop, target, leverage and size.", "Anchor, Ballast", "#0e9f84"],
  ["Verity", "Validator", "Checks the data and every number (code, no AI).", null, "#0b93b8"],
  ["Apex", "Head Trader", "Decides LONG, SHORT or WAIT.", "Bull Case, Bear Case", "#c98a0b"],
];
const VIEW: Record<string, string> = { bullish: "bg-emerald-500/15 text-emerald-300", bearish: "bg-red-500/15 text-red-300", neutral: "bg-slate-500/20 text-slate-300" };
const ACTION: Record<string, string> = { LONG: "bg-emerald-500 text-emerald-950", SHORT: "bg-red-500 text-red-950", WAIT: "bg-slate-600 text-slate-100" };
const TOOL_LABEL: Record<string, string> = {
  chart_analysis: "chart", market_regime: "market regime", derivatives_and_news: "positioning & news", method_views: "10 methods",
  coin_profile: "coin profile", strategy_evidence: "backtests", research_facts: "research", search_knowledge: "knowledge",
  key_levels: "key levels", trade_plan: "trade plan", desk_record: "desk record", validation_report: "validation",
  tokenomics: "tokenomics", onchain_usage: "TVL & fees", market_standing: "standing vs BTC", market_macro: "whole market",
  order_flow: "order flow", funding_across: "funding · 3 exchanges", options_market: "options", depth_slippage: "slippage",
};
const VERDICT: Record<Readiness["verdict"], [string, string]> = {
  ready: ["Ready", "bg-emerald-500/15 text-emerald-300"], forming: ["Forming", "bg-amber-500/15 text-amber-300"],
  not_now: ["Not now", "bg-slate-900 text-slate-400"],
};

function ReadinessCard({ r, onForce, canForce }: { r: Readiness; onForce?: () => void; canForce: boolean }) {
  const bar = r.verdict === "ready" ? "bg-emerald-500" : r.verdict === "forming" ? "bg-amber-500" : "bg-slate-600";
  return (
    <div className="rounded-2xl border border-line bg-panel">
      <div className="flex flex-wrap items-center gap-2.5 border-b border-line px-5 py-4">
        <span className={`chip ${VERDICT[r.verdict][1]}`}>{VERDICT[r.verdict][0]}</span>
        <h3 className="text-[14px] font-bold text-slate-100">Signal readiness{r.side ? ` · ${r.side}` : ""}</h3>
        <div className="ml-auto flex items-center gap-2">
          <div className="h-1.5 w-28 overflow-hidden rounded-full bg-slate-800"><div className={`h-full ${bar}`} style={{ width: `${r.score}%` }} /></div>
          <b className="num text-[13px] text-slate-100">{r.score}</b><span className="text-[12px] text-slate-500">/ 100</span>
        </div>
      </div>
      <div className="px-5 pt-3.5 text-[14px] text-slate-200">{r.summary}</div>
      <div className="grid gap-5 px-5 py-4 md:grid-cols-[1.3fr_1fr]">
        <div className="space-y-1.5">
          {r.parts.map((p) => (
            <div key={p.name} className="grid grid-cols-[110px_60px_1fr] items-center gap-2 text-[12px]">
              <span className="font-medium text-slate-300">{p.name}</span>
              <div className="h-1.5 overflow-hidden rounded-full bg-slate-800"><div className={`h-full ${p.points >= p.of * 0.7 ? "bg-emerald-500" : p.points > 0 ? "bg-amber-500" : "bg-slate-600"}`} style={{ width: `${(p.points / p.of) * 100}%` }} /></div>
              <span className="truncate text-slate-500" title={p.detail}>{p.detail}</span>
            </div>
          ))}
        </div>
        <div className="space-y-2 text-[12px]">
          {r.blocks.length > 0 && <div className="rounded-md bg-red-500/10 px-2.5 py-2 text-red-300"><b className="font-semibold">Blocked: </b>{r.blocks.join("; ")}</div>}
          {r.wait_for.length > 0 && (
            <div><div className="mb-1 font-semibold text-slate-300">{r.verdict === "ready" ? "Watch" : "Wait for"}</div>
              <ul className="space-y-1">{r.wait_for.map((w) => <li key={w} className="flex gap-1.5 text-slate-400"><span className="text-slate-600">•</span>{w}</li>)}</ul></div>
          )}
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-3 border-t border-line px-5 py-3 text-[12.5px] text-slate-500">
        <span title="The AI desk uses about 30 AI calls">Free check, no AI. The AI desk runs when this says Ready, and you get an alert.</span>
        {r.verdict !== "ready" && onForce && <button className="btn ml-auto" disabled={!canForce} onClick={onForce}>Analyse anyway (uses AI)</button>}
      </div>
    </div>
  );
}
const toolText = (t: Tool) => {
  const a = Object.values(t.args || {}).filter((v) => v !== null && v !== undefined && v !== "").map(String).join(", ");
  return `${TOOL_LABEL[t.name] ?? t.name}${a ? `: ${a.length > 34 ? a.slice(0, 34) + "…" : a}` : ""}`;
};
const firstSentence = (t: string) => { const c = t.indexOf(". "); return c > 30 && c < 220 ? t.slice(0, c + 1) : t.slice(0, 220); };
const shortErr = (e?: string | null) => (e ?? "").replace(/^Error code: \d+ - /, "").split(". ")[0];

function Label({ children }: { children: React.ReactNode }) {
  return <span className="text-[11px] font-medium text-slate-500">{children}</span>;
}

const STATUS: Record<Status, [string, string]> = {
  waiting: ["Waiting", "bg-slate-900 text-slate-400"], working: ["Working", "bg-sky-500/15 text-sky-300"],
  done: ["Done", "bg-emerald-500/15 text-emerald-300"], skipped: ["Not run", "bg-red-500/10 text-red-300"],
};
const StatusChip = ({ s }: { s: Status }) => (
  <span className={`chip shrink-0 ${STATUS[s][1]}`}>{s === "working" && <Loader2 size={10} className="animate-spin" />}{STATUS[s][0]}</span>
);
const ViewChip = ({ view, conf }: { view: string; conf: number | null }) => (
  <span className={`chip shrink-0 ${VIEW[view]}`}>{view}{conf !== null ? ` · ${Math.round(conf * 100)}%` : ""}</span>
);
function Avatar({ name, color, size = 28 }: { name: string; color: string; size?: number }) {
  return <span className="flex shrink-0 items-center justify-center rounded-full font-semibold text-[#fff]"
    style={{ background: color, width: size, height: size, fontSize: size * 0.42 }}>{name[0]}</span>;
}

function SubRow({ x }: { x: SubView }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`rounded-lg px-2 py-1.5 ${x.text ? "hover:bg-panel" : ""} ${x.status === "waiting" ? "opacity-60" : ""}`}>
      <button className="flex w-full items-center gap-2 text-left text-[12px]" onClick={() => x.text && setOpen(!open)} disabled={!x.text}>
        {x.text ? (open ? <ChevronDown size={12} className="shrink-0 text-slate-500" /> : <ChevronRight size={12} className="shrink-0 text-slate-500" />)
          : <Circle size={7} className={`mx-[2.5px] shrink-0 ${x.status === "working" ? "fill-sky-500 text-sky-500" : "fill-slate-600 text-slate-600"}`} />}
        <b className="shrink-0 font-semibold text-slate-200">{x.name}</b>
        <span className="min-w-0 flex-1 truncate text-slate-500">{x.focus}</span>
        {x.view ? <ViewChip view={x.view} conf={x.confidence} /> : x.status !== "done" && <StatusChip s={x.status} />}
      </button>
      {x.text && !open && <div className="ml-5 mt-0.5 truncate text-[11px] text-slate-500">{firstSentence(x.text)}</div>}
      {open && x.text && (
        <div className="ml-5 mt-1 space-y-1.5">
          <div className="text-[12px] leading-relaxed text-slate-300">{x.text}</div>
          {x.tools_used.length > 0 && <div className="flex flex-wrap gap-1">{x.tools_used.map((t, i) => (
            <span key={i} className={`chip font-medium ${t.ok ? "bg-slate-900 text-slate-400" : "bg-red-500/10 text-red-300"}`}>{toolText(t)}</span>))}</div>}
        </div>
      )}
      {x.status === "skipped" && x.error && <div className="ml-5 text-[11px] text-red-300">{shortErr(x.error)}</div>}
    </div>
  );
}

function AgentCard({ a, fact, redTeam }: { a: AgentView; fact?: FactCheck; redTeam?: string }) {
  const [open, setOpen] = useState(false);
  const subsDone = (a.subs ?? []).filter((x) => x.status === "done").length;
  return (
    <div data-agent-target={`card:${a.id}`}
      className={`rounded-2xl border bg-panel p-4 transition-colors ${a.status === "working" ? "border-sky-700" : "border-line hover:border-slate-700"} ${a.status === "waiting" ? "opacity-55" : ""}`}>
      <div className="flex items-center gap-2.5">
        <Avatar name={a.name} color={a.color} />
        <div className="min-w-0 flex-1 leading-tight">
          <div className="text-[13px] font-semibold text-slate-100">{a.name}</div>
          <div className="truncate text-[12px] text-slate-500">{a.title ?? a.role}</div>
        </div>
        {a.view ? <ViewChip view={a.view} conf={a.confidence} /> : <StatusChip s={a.status} />}
      </div>
      {(a.subs ?? []).length > 0 && (
        <div className="mt-3 rounded-xl bg-fill p-1.5">
          <div className="flex items-center px-2 py-1 text-[11px] font-medium text-slate-500">Team
            <span className="ml-auto">{subsDone}/{a.subs!.length} reported</span></div>
          {a.subs!.map((x) => <SubRow key={x.id} x={x} />)}
        </div>
      )}
      {a.status === "working" && !a.text && <div className="mt-2 text-[12px] text-slate-500">{(a.subs ?? []).length && subsDone < a.subs!.length ? "The team is researching…" : "Researching with tools…"}</div>}
      {a.text && <div className="prose-chat mt-2.5 text-[13px] leading-relaxed text-slate-200"><ReactMarkdown>{a.text}</ReactMarkdown></div>}
      {a.rebuttal && <div className="mt-2 rounded-md border-l-2 border-sky-500 bg-slate-950 px-2.5 py-1.5 text-[12px] text-slate-300"><b className="font-semibold text-slate-200">Reply to {redTeam ?? "the Red Team"}: </b>{a.rebuttal}</div>}
      {a.revised && <div className="mt-1.5 text-[11px] text-cyan-300">✎ {a.revised}</div>}
      {(fact || a.status === "done") && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {fact && <FactBadge f={fact} />}
          {a.status === "done" && a.tools_used.length > 0 && (
            <button className="flex items-center gap-1 text-[11px] font-medium text-slate-500 hover:text-slate-200" onClick={() => setOpen(!open)}>
              {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}<Wrench size={11} />
              {a.by === "validators" ? `${a.tools_used.length} gap${a.tools_used.length === 1 ? "" : "s"} researched` : `${a.tools_used.length} tool call${a.tools_used.length === 1 ? "" : "s"}`}
            </button>
          )}
        </div>
      )}
      {open && <div className="mt-1.5 flex flex-wrap gap-1">{a.tools_used.map((t, i) => (
        <span key={i} className={`chip font-medium ${t.ok ? "bg-slate-900 text-slate-400" : "bg-red-500/10 text-red-300"}`}>{toolText(t)}</span>))}</div>}
    </div>
  );
}

/** The research desk.
 *  Left: the shortlist (chosen by code, no AI). Centre: the chart and, under it, the result. Right: the AI desk's
 *  conversation. Nothing runs until the user picks a coin, enters the margin and presses Analyse; the analysis and the
 *  decision are the AI agents' own, and without a working AI the page says so instead of showing anything else. */
export default function ResearchDesk() {
  const { symbol, timeframe, setTimeframe, setSymbol, setFocus } = useStore();
  const [st, setSt] = useState<Settings>({ budget: null, venue: "futures", side: "auto" });
  const [budgetText, setBudgetText] = useState("");
  const [sel, setSel] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [sess, setSess] = useState<Session | null>(null);
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [scan, setScan] = useState<Shortlist | null>(null);
  const [coins, setCoins] = useState<Coin[]>([]);
  const [ai, setAi] = useState<AIStatus | null>(null);
  const [q, setQ] = useState("");
  const [err, setErr] = useState("");
  const [run, setRun] = useState<{ symbol: string; key: number; refresh?: boolean; force?: boolean } | null>(null);
  const [rd, setRd] = useState<Readiness | null>(null);
  const [take, setTake] = useState<TradeDraft | null>(null);
  const [tab, setTab] = useState<Tab>("plan");
  const [cursorsOn, setCursorsOn] = useState(false);
  const [panes, setPanes] = useState({ list: true, desk: true });  // hide the coin list or the AI desk to give the chart room
  useEffect(() => { try { const v = JSON.parse(localStorage.getItem("desk_panes") || "null"); if (v) setPanes(v); } catch {} }, []);
  const togglePane = (k: "list" | "desk") => setPanes((p) => {
    const v = { ...p, [k]: !p[k] };
    try { localStorage.setItem("desk_panes", JSON.stringify(v)); } catch {}
    return v;
  });  // agent cursors are opt-in: a clean screen by default
  const [drawn, setDrawn] = useState<ChartObject[]>([]);
  const geo = useRef<ToXY | null>(null);
  const anchor = useRef<HTMLDivElement>(null);
  const tf = TFS.some(([t]) => t === timeframe) ? timeframe : "4h";

  // ------------------------------------------------------------------ settings, coins, AI status
  useEffect(() => {
    try {
      const raw = localStorage.getItem(KEY);
      if (raw) { const v = JSON.parse(raw); setSt((s) => ({ ...s, ...v, budget: v.budget ?? null })); setBudgetText(v.budget ? String(v.budget) : ""); }
    } catch {}
    const coin = new URLSearchParams(window.location.search).get("coin");  // a link straight to one coin: /?coin=SOLUSDT
    if (coin) setSel(coin.toUpperCase().endsWith("USDT") ? coin.toUpperCase() : `${coin.toUpperCase()}USDT`);
    setLoaded(true);
  }, []);
  useEffect(() => { if (loaded) { try { localStorage.setItem(KEY, JSON.stringify(st)); } catch {} } }, [st, loaded]);
  useEffect(() => { if (timeframe !== tf) setTimeframe(tf); }, [timeframe, tf, setTimeframe]);
  useEffect(() => { api<{ coins: Coin[] }>("/coins").then((r) => setCoins(r.coins)).catch(() => {}); }, []);
  useEffect(() => () => setFocus(null), [setFocus]);
  const loadAi = useCallback(() => api<AIStatus>("/agent/status").then(setAi).catch(() => {}), []);
  useEffect(() => { loadAi(); const i = setInterval(loadAi, 60_000); return () => clearInterval(i); }, [loadAi]);

  // the shortlist: chosen by code only (no AI), refreshed by the server every 15 minutes
  useEffect(() => {
    let stop = false;
    let id: ReturnType<typeof setTimeout> | undefined;
    const poll = () => api<Shortlist>(`/agent/shortlist?tf=${tf}`).then((s) => {
      if (stop) return;
      setScan(s);
      id = setTimeout(poll, s.status === "running" ? 3000 : 60000);
    }).catch(() => { id = setTimeout(poll, 5000); });
    poll();
    return () => { stop = true; clearTimeout(id); };
  }, [tf]);
  useEffect(() => { if (loaded && !sel) setSel(symbol || scan?.rows?.[0]?.symbol || null); }, [loaded, sel, scan, symbol]);   // start on the coin you were looking at
  useEffect(() => { if (sel && sel !== symbol) setSymbol(sel); }, [sel]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!sel) return;
    let stop = false;
    api<Snapshot>(`/analysis/${sel}?tf=${tf}`).then((x) => !stop && setSnap(x)).catch(() => {});
    return () => { stop = true; };
  }, [sel, tf]);

  // ------------------------------------------------------------------ the analysis (only on Analyse)
  useEffect(() => {
    if (!run || !loaded) return;
    let stop = false;
    let id: ReturnType<typeof setTimeout> | undefined;
    setErr(""); setSess(null); setTab("plan");
    const take_ = (s: Session) => {
      if (stop) return;
      setSess(s);
      if (s.status === "running") id = setTimeout(() => api<Session>(`/agent/research/${s.id}`).then(take_).catch(() => { id = setTimeout(() => take_(s), 3000); }), 2000);
      else loadAi();
    };
    api<Session>("/agent/research", { method: "POST", json: { symbol: run.symbol, tf, side: st.side, margin: null, budget: st.budget ?? 100, mode: "auto", venue: st.venue, refresh: !!run.refresh, force: !!run.force } })
      .then(take_).catch((e) => setErr(e.message));
    return () => { stop = true; clearTimeout(id); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run, loaded]);
  useEffect(() => { if (sess && (sess.symbol !== sel || sess.tf !== tf) && sess.status !== "running") { setSess(null); setRun(null); setFocus(null); } },
    [sel, tf]); // eslint-disable-line react-hooks/exhaustive-deps
  // the free readiness check for the selected coin (code only, no AI)
  useEffect(() => {
    setRd(null);
    if (!sel) return;
    let stop = false;
    const get = () => api<Readiness>(`/agent/readiness?symbol=${sel}&tf=${tf}&side=${st.side}&venue=${st.venue}`)
      .then((r) => { if (!stop) setRd(r); }).catch(() => undefined);
    get();
    const id = setInterval(get, 120_000);
    return () => { stop = true; clearInterval(id); };
  }, [sel, tf, st.side, st.venue]);

  // ------------------------------------------------------------------ cursors and the plan on the chart
  const onDraw = useCallback((agent: string, d: NonNullable<DeskEvent["draw"]>, phase: DrawPhase) => {
    const who = sess?.agents.find((x) => x.id === agent)?.name ?? agent;
    const base = { origin: "ai" as const, concept: "plan", time1: d.time };
    const objs: ChartObject[] = [{ ...base, id: "cur_entry", kind: "hline", price1: d.entry, color: "#e2e8f0", style: "solid", label: `ENTRY ${fmtPrice(d.entry)} · ${who}` }];
    if (phase !== "entry") objs.push({ ...base, id: "cur_sl", kind: "hline", price1: d.stop, color: "#ef4444", style: "dashed", label: `SL ${fmtPrice(d.stop)}` });
    if (phase === "target") {
      objs.push({ ...base, id: "cur_tp", kind: "hline", price1: d.target, color: "#22c55e", style: "dashed", label: `TP ${fmtPrice(d.target)}` });
      objs.push({ ...base, id: "cur_box", kind: "position", direction: d.side, entry: d.entry, stop: d.stop, target: d.target, label: `${d.side} · ${who}` });
    }
    setDrawn(objs);
  }, [sess?.agents]);
  const player = useCursorPlayer(sess?.events ?? [], sess?.id ?? null, onDraw, cursorsOn);
  useEffect(() => { setDrawn([]); }, [sess?.id]);
  const deskSymbol = sess?.symbol ?? null;
  const caughtUp = !cursorsOn || (player.behind === 0 && sess?.status !== "running");
  useEffect(() => {
    if (deskSymbol !== symbol) return;
    const objs = caughtUp && sess?.chart_objects?.length ? sess.chart_objects : drawn;
    if (objs.length) setFocus({ ids: [], objects: objs.map((o) => ({ ...o, origin: "ai" as const })) });
  }, [caughtUp, sess?.chart_objects, drawn, deskSymbol, symbol, setFocus]);

  // ------------------------------------------------------------------ derived
  const short = useMemo(() => new Map((scan?.rows || []).map((r) => [r.symbol, r])), [scan]);
  const list = useMemo(() => {
    const term = q.trim().toUpperCase();
    if (term) return coins.filter((c) => c.base.includes(term) || c.symbol.includes(term)).slice(0, 60).map((c) => ({ symbol: c.symbol, price: c.price, row: short.get(c.symbol) }));
    return (scan?.rows || []).map((r) => ({ symbol: r.symbol, price: r.price, row: r as Short | undefined }));
  }, [q, coins, scan, short]);
  const pick = (s: string) => { setQ(""); setSel(s); };
  const applyBudget = (txt: string) => { const v = +txt; setSt((x) => ({ ...x, budget: v >= 10 && v <= 1_000_000 ? Math.round(v * 100) / 100 : null })); };
  const aiReady = ai?.ready ?? true;
  const ready = !!sel && !!st.budget;
  const running = sess?.status === "running";
  const analyse = (refresh = false, force = false) => { if (ready && sel) setRun({ symbol: sel, key: Date.now(), refresh, force }); };
  const stale = !!sess && !running && (sess.budget !== st.budget || sess.venue !== st.venue);
  const d = sess?.decision;
  const plan = sess?.plan;
  const shown = (sess?.agents ?? []).filter((a) => a.status !== "skipped");
  const doneAgents = shown.filter((a) => a.status === "done").length;
  const allSubs = (sess?.agents ?? []).flatMap((a) => a.subs ?? []);
  const subsDone = allSubs.filter((x) => x.status === "done").length;
  const subsTotal = allSubs.length;
  const redTeam = sess?.agents.find((a) => a.id === "skeptic")?.name;
  const lastEv = sess?.events?.length ? sess.events[sess.events.length - 1] : null;
  const lastWho = lastEv ? sess?.agents.find((a) => a.id === lastEv.agent)?.name : null;
  const takeIt = (option = "safe") => plan && setTake({ symbol: plan.symbol, side: plan.side, venue: st.venue, leverage: plan.leverage, entry: plan.entry,
    qty: plan.qty, stop: plan.stop, target: plan.target, budget_usd: st.budget ?? 100, mode: (plan as Plan & { risk_style?: string }).risk_style ?? "conservative", source: "agent", plan, tf, option });
  const unavailable = sess?.status === "unavailable" || (!sess && ai && !ai.ready);
  const aiMsg = sess?.status === "unavailable" ? sess.error : ai && !ai.configured ? "No AI key is set (backend/.env)." : ai?.error ? `The AI provider says: ${shortErr(ai.error)}.` : null;

  return (
    <div className="grid h-full grid-rows-[auto_1fr] overflow-hidden"
      style={{ gridTemplateColumns: `${panes.list ? "240px " : ""}minmax(0,1fr)${panes.desk ? " 380px" : ""}` }}>
      {/* ================================================================ control bar */}
      <div style={{ gridColumn: "1 / -1" }} className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line bg-panel px-5 py-3 text-[13px]">
        <div className="flex items-center gap-2">
          <Label>Coin</Label>
          <b className="min-w-[84px] text-[14px] text-slate-100">{pair(sel) || "—"}</b>
          <div className="flex rounded-full bg-fill p-1">{TFS.map(([t, label]) => (
            <button key={t} data-agent-target={`tf:${t}`} onClick={() => setTimeframe(t)} title={label}
              className={`rounded-full px-3.5 py-1 font-semibold transition-colors ${t === tf ? "bg-panel text-slate-100 shadow-[0_1px_3px_rgba(28,43,51,0.12)]" : "text-slate-400 hover:text-slate-200"}`}>{t}</button>
          ))}</div>
        </div>
        <span className="h-6 w-px bg-line" />
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-1.5 text-slate-400" title="Your trading budget in USDT. Sentinel, the Risk Officer, decides how much of it each trade risks and how big the position is.">
            Budget $
            <input className="input w-24 py-0.5" type="number" min={10} step={10} placeholder="e.g. 500" value={budgetText}
              onChange={(e) => { setBudgetText(e.target.value); applyBudget(e.target.value); }} onKeyDown={(e) => e.key === "Enter" && analyse(!!sess)} />
          </label>
          <span className="whitespace-nowrap text-[12px] text-slate-500" title="Sentinel, the Risk Officer, sets the risk and size: conservative (1% of the budget at risk) by default; growth (2%) or aggressive (3%) only when the evidence is strong">Risk: by Sentinel</span>
          <select className="input py-0.5" value={st.venue} onChange={(e) => setSt({ ...st, venue: e.target.value as Settings["venue"], side: e.target.value === "spot" ? "auto" : st.side })}>
            <option value="futures">Futures</option><option value="spot">Spot</option>
          </select>
          <select className="input py-0.5" value={st.side} onChange={(e) => setSt({ ...st, side: e.target.value as Settings["side"] })}>
            <option value="auto">Agents choose side</option><option value="long">Long only</option>{st.venue === "futures" && <option value="short">Short only</option>}
          </select>
        </div>
        <span className="h-6 w-px bg-line" />
        <button className="btn btn-primary flex items-center gap-1.5 px-3 py-1 text-[13px]" disabled={!ready || running || !aiReady} onClick={() => analyse(!!sess)}
          title={!aiReady ? "The AI is unavailable" : !sel ? "Pick a coin first" : !st.budget ? "Enter your budget first" : "The AI agents research this coin now"}>
          {running ? <Loader2 size={13} className="animate-spin" /> : <Play size={12} />}
          {running ? "Agents working…" : `Analyse ${pair(sel) || ""}`}{sess && !running && sess.symbol === sel ? " again" : ""}
        </button>
        {stale && <span className="text-[11px] text-amber-300">settings changed — analyse again</span>}
        {err && <span className="text-amber-300">{err}</span>}
        <div className="ml-auto flex items-center gap-1">
          <button className={`btn btn-ghost h-8 w-8 p-0 ${panes.list ? "text-slate-200" : ""}`} onClick={() => togglePane("list")} title={panes.list ? "Hide the coin list" : "Show the coin list"}><PanelLeft size={16} /></button>
          <button className={`btn btn-ghost h-8 w-8 p-0 ${panes.desk ? "text-slate-200" : ""}`} onClick={() => togglePane("desk")} title={panes.desk ? "Hide the AI desk" : "Show the AI desk"}><PanelRight size={16} /></button>
        </div>
      </div>

      {/* ================================================================ shortlist */}
      {panes.list && (
      <aside data-agent-target="coins" className="flex min-h-0 flex-col border-r border-line bg-panel">
        <div className="relative p-2">
          <Search size={13} className="absolute left-[18px] top-[17px] text-slate-500" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={`Search any of ${coins.length || ""} coins`} className="input w-full py-1 pl-7" />
        </div>
        {!q && <div className="px-3 pb-1" title="These notes come from the 4h chart-setup scanner (a Lab system). The trained model's call for a coin is the Best signal under the chart."><Label>Chart setups (4h scan) · not the main signal</Label></div>}
        <div className="min-h-0 flex-1 overflow-auto px-1">
          {list.map((c) => (
            <button key={c.symbol} onClick={() => pick(c.symbol)} title={c.row?.reasons.join(" · ")}
              className={`block w-full rounded-xl px-3 py-2 text-left text-[12.5px] hover:bg-fill ${sel === c.symbol ? "bg-accent-soft" : ""}`}>
              <div className="flex items-center gap-1.5">
                <b className="w-[84px] truncate text-slate-100">{pair(c.symbol)}</b>
                <span className="num flex-1 truncate text-right text-[11px] text-slate-400">{fmtPrice(c.price)}</span>
                {c.row ? <SignalBadge action={c.row.action} /> : <span className="text-[9px] text-slate-600">any coin</span>}
              </div>
              {c.row && !q && <div className="mt-0.5 line-clamp-2 text-[10px] leading-tight text-slate-500">{c.row.why}</div>}
            </button>
          ))}
          {!list.length && <div className="p-2 text-[11px] text-slate-500">{scan?.status === "running" ? `Building the shortlist… ${scan.done ?? 0}/${scan.total ?? "?"}` : "No coins match."}</div>}
        </div>
        <div className="border-t border-line p-2 text-[10px] leading-snug text-slate-500">{scan?.note ?? "Building the shortlist with code only (no AI)…"}</div>
      </aside>

      )}

      {/* ================================================================ chart + result */}
      <section className="flex min-h-0 flex-col">
        {sel && <MachineBar symbol={sel} tf={tf} />}
        <div className="relative min-h-0 flex-1">
          <TradingChart snapshot={snap} signals={[]} indicatorPreset={[]} geoRef={geo}
            overlay={<div ref={anchor} className="pointer-events-none absolute inset-0" />} />
        </div>
        <section data-agent-target="panel:decision" className="flex h-[42%] min-h-[250px] flex-col border-t border-line bg-panel">
          {/* ---- before any analysis */}
          {sess?.status === "not_ready" && sess.readiness && !running && (
            <div className="h-full overflow-auto p-4"><ReadinessCard r={sess.readiness} canForce={ready && aiReady} onForce={() => analyse(true, true)} /></div>
          )}
          {!sess && !run && rd && (
            <div className="h-full space-y-2 overflow-auto p-4">
              {!aiReady && <div className="flex items-center gap-2 rounded-md bg-red-500/10 px-3 py-2 text-[12px] text-red-300">
                <AlertTriangle size={14} className="shrink-0" />AI unavailable — {aiMsg} The readiness check below is free and still works.</div>}
              {sel && <BestSignal symbol={sel} margin={st.budget ?? 100} />}
              <ReadinessCard r={rd} canForce={ready && aiReady} onForce={() => analyse(true, true)} />
              <details className="rounded-2xl border border-line bg-panel px-4 py-2"><summary className="cursor-pointer text-[12.5px] text-slate-400 hover:text-slate-200">Details: model forecasts and chart patterns</summary><div className="mt-2 space-y-2"><ModelForecast symbol={sel} /><PatternCard symbol={sel} /></div></details>
            </div>
          )}
          {!sess && !run && !unavailable && !rd && (
            <div className="flex h-full flex-col justify-center gap-3 overflow-auto p-4">
              {sel && <BestSignal symbol={sel} margin={st.budget ?? 100} />}
              <div className="grid gap-2 md:grid-cols-3">
                {[
                  { ok: !!sel, n: 1, t: "Pick a coin", s: sel ? `${pair(sel)} — ${short.get(sel)?.why ?? "chosen from search"}` : "From the list on the left, or search any coin." },
                  { ok: !!st.budget, n: 2, t: "Set your budget", s: st.budget ? `$${st.budget} budget · 1–3% risked per trade` : "Sentinel sets the risk and size of each trade." },
                  { ok: false, n: 3, t: "Analyse", s: "The AI agents give the side, entry, stop, target, leverage and size." },
                ].map((x) => (
                  <div key={x.n} className={`rounded-lg border p-3 ${x.ok ? "border-emerald-500/40 bg-emerald-500/5" : "border-line bg-white/[0.02]"}`}>
                    <div className="flex items-center gap-2 text-[13px] font-medium text-slate-100">
                      <span className={`flex h-5 w-5 items-center justify-center rounded-full text-[11px] ${x.ok ? "bg-emerald-500 text-emerald-950" : "bg-white/10 text-slate-300"}`}>{x.ok ? "✓" : x.n}</span>{x.t}
                    </div>
                    <div className="mt-1 text-[11px] leading-snug text-slate-400">{x.s}</div>
                  </div>
                ))}
              </div>
              <button className="btn btn-primary mx-auto flex items-center gap-1.5 px-4 py-1.5 text-[13px]" disabled={!ready} onClick={() => analyse()}>
                <Play size={12} /> {ready ? `Analyse ${pair(sel)} · $${st.budget} budget` : !sel ? "Pick a coin first" : "Enter your budget first"}
              </button>
            </div>
          )}

          {/* ---- the AI cannot run */}
          {unavailable && !running && !(rd && !sess) && (
            <div className="h-full space-y-2 overflow-auto p-4">
              <div className="flex items-start gap-3 rounded-lg border border-red-500/30 bg-red-500/5 p-3 text-[12px]">
                <AlertTriangle size={16} className="mt-0.5 shrink-0 text-red-300" />
                <div className="space-y-0.5">
                  <div className="text-[13px] font-medium text-red-200">AI agents unavailable — {aiMsg}</div>
                  <div className="text-slate-400">The trained models below still work without AI. Renew the key or add credits in <code>backend/.env</code>.</div>
                </div>
              </div>
              {sel ? <><BestSignal symbol={sel} margin={st.budget ?? 100} /><details className="rounded-2xl border border-line bg-panel px-4 py-2"><summary className="cursor-pointer text-[12.5px] text-slate-400 hover:text-slate-200">Details: model forecasts and chart patterns</summary><div className="mt-2 space-y-2"><ModelForecast symbol={sel} /><PatternCard symbol={sel} /></div></details></> : <div className="text-[12px] text-slate-500">Pick a coin to see the trained model&apos;s signal.</div>}
            </div>
          )}

          {/* ---- agents working */}
          {(running || (run && !sess)) && (
            <div className="flex h-full flex-col items-center justify-center gap-2 p-4 text-[12px] text-slate-300">
              <Loader2 size={20} className="animate-spin text-sky-300" />
              <div>{lastWho && lastEv ? <><b className="text-slate-100">{lastWho}</b>: {lastEv.label || lastEv.text || "thinking"}</> : "Starting the desk…"}</div>
              <div className="text-[11px] text-slate-500">{doneAgents}/{shown.length || 9} leads done · {subsDone}/{subsTotal} specialists · {sess?.tool_calls ?? 0} tool calls · the plan shows here when Apex decides</div>
            </div>
          )}

          {/* ---- the result */}
          {sess && !running && sess.status === "done" && (
            <>
              <div className="flex items-center gap-3 border-b border-line px-3 py-2">
                {d ? <span className={`rounded px-3 py-1 text-lg font-black tracking-wide ${ACTION[d.action]}`}>{d.action}</span>
                  : <span className="rounded bg-amber-500/20 px-3 py-1 text-[13px] font-semibold text-amber-200">NO DECISION</span>}
                <div className="min-w-0 flex-1 text-[12px]">
                  <div className="truncate text-slate-100">{d ? firstSentence(d.summary) : sess.error}</div>
                  <div className="text-[11px] text-slate-500">
                    {d ? `${d.by ?? "Head Trader"}${d.confidence !== null && d.confidence !== undefined ? ` · ${Math.round(d.confidence * 100)}% confidence` : ""} · plan: ${d.action === "WAIT" ? `${d.plan_side} only if the trigger prints` : "the trade"}` : "the AI stopped before the decision"}
                    {d?.confidence_calibrated !== undefined && d.confidence_calibrated !== d.confidence && ` · record-calibrated ${Math.round(d.confidence_calibrated * 100)}%`}
                    {d?.votes && ` · ${d.votes.note}`}
                    {(plan as (Plan & { risk_style?: string }) | null)?.risk_style && ` · ${(plan as Plan & { risk_style?: string }).risk_style} risk, chosen by Sentinel`}                  </div>
                </div>
                {plan?.ok && <button className="btn btn-primary shrink-0" onClick={() => takeIt()}>I&apos;m taking it</button>}
              </div>
              {d?.guard && <div className="mx-3 mt-2 rounded bg-amber-500/10 px-2 py-1 text-[11px] text-amber-200">{d.guard}</div>}
              <div className="flex gap-1 border-b border-line px-3 pt-1.5 text-[12px]">
                {([["plan", "Plan"], ["models", "Trained models"], ["patterns", "Chart patterns"], ["leverage", "Leverage options"], ["validation", "Validation"], ["howto", "How to place it"]] as [Tab, string][]).map(([k, label]) => (
                  <button key={k} onClick={() => setTab(k)} className={`rounded-t px-3 py-1 ${tab === k ? "bg-white/10 text-slate-100" : "text-slate-400 hover:text-slate-200"}`}>
                    {label}{k === "validation" && sess.validation ? <span className={`ml-1 text-[10px] ${sess.validation.gate === "blocked" ? "text-red-300" : "text-emerald-300"}`}>{sess.validation.gate === "blocked" ? "blocked" : "ok"}</span> : null}
                  </button>
                ))}
              </div>
              <div className="min-h-0 flex-1 space-y-2 overflow-auto p-3">
                {tab === "plan" && sess.symbol && <BestSignal symbol={sess.symbol} margin={st.budget ?? 100} refresh={sess.id} />}
                {tab === "plan" && plan && (
                  <>
                    <div className="pt-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">The AI agents&apos; own plan</div>
                    <PlanCells p={plan} cols={5} />
                    {d && (d.conditions?.length > 0 || d.invalidation) && (
                      <div className="grid gap-2 text-[11px] md:grid-cols-2">
                        {d.conditions?.length > 0 && <div className="rounded bg-white/5 p-2 text-slate-300"><b className="text-slate-400">{d.action === "WAIT" ? "It becomes a trade when" : "Holds while"}: </b>{d.conditions.join(" · ")}</div>}
                        {d.invalidation && <div className="rounded bg-red-500/5 p-2 text-red-200/90"><b>Wrong if: </b>{d.invalidation}</div>}
                      </div>
                    )}
                    <PlanNotes p={plan} />
                  </>
                )}
                {tab === "plan" && !plan && <div className="text-[12px] text-slate-500">No plan: the agents stopped before the trade was planned.</div>}
                {tab === "models" && <ModelForecast symbol={sess.symbol} />}
                {tab === "patterns" && <PatternCard symbol={sess.symbol} />}
                {tab === "leverage" && plan && <LeverageOptions p={plan} onPick={plan.ok ? (o) => takeIt(o.key) : undefined} />}
                {tab === "validation" && sess.validation && <div data-agent-target="panel:validators"><ValidationPanel v={sess.validation} /></div>}
                {tab === "howto" && plan && <PlanHowTo p={plan} />}
              </div>
            </>
          )}
          {sess && !running && sess.status === "error" && <div className="m-4 rounded bg-red-500/10 p-3 text-[12px] text-red-300">The desk stopped: {sess.error}</div>}
        </section>
      </section>

      {/* ================================================================ the AI desk */}
      {panes.desk && (
      <aside className="flex min-h-0 flex-col border-l border-line bg-panel">
        <div className="border-b border-line px-5 pb-4 pt-4">
          <div className="flex items-center gap-2">
            <h2 className="text-[17px]">AI agents</h2>
            <span className="chip bg-slate-900 text-slate-400">{sess ? `${pair(sess.symbol)} · ${sess.tf}` : "9 leads · 23 specialists"}</span>
            <div className="ml-auto flex items-center gap-1">
              {cursorsOn && player.behind > 2 && <button className="btn btn-ghost h-7 px-2 text-[12px]" onClick={player.skip} title="Jump to the present"><FastForward size={13} /> {player.behind}</button>}
              <button className={`btn btn-ghost h-7 px-2 ${cursorsOn ? "text-sky-300" : ""}`} onClick={() => setCursorsOn(!cursorsOn)}
                title={cursorsOn ? "Agent cursors on: each agent's mouse shows its real work" : "Agent cursors off"}><MousePointer2 size={14} /></button>
              <span className={`chip ${aiReady ? "bg-emerald-500/15 text-emerald-300" : "bg-red-500/10 text-red-300"}`} title={aiReady ? `AI ready${ai?.model ? ` · ${ai.model}` : ""}` : aiMsg ?? ""}>
                <span className={`h-1.5 w-1.5 rounded-full ${aiReady ? "bg-emerald-500" : "bg-red-500"}`} />{aiReady ? "AI ready" : "AI off"}</span>
            </div>
          </div>
          {sess?.status === "done" && <div className="mt-1 text-[11px] text-slate-500">{sess.tool_calls} tool calls · {sess.seconds}s</div>}
          <div className="mt-3 grid grid-cols-6 gap-1">
            {(sess?.stages ?? ["Research", "Analysis", "Debate", "Plan", "Validate", "Decision"].map((t) => ({ key: t, title: t, status: "pending" }))).map((s) => (
              <div key={s.key} title={`${s.title}: ${s.status}`}>
                <div className={`h-1.5 rounded-full ${s.status === "done" ? "bg-sky-500" : s.status === "running" ? "animate-pulse bg-sky-700" : s.status === "skipped" ? "bg-red-700" : "bg-slate-800"}`} />
                <div className={`mt-1 truncate text-[10px] ${s.status === "running" ? "font-semibold text-sky-300" : "text-slate-500"}`}>{s.title}</div>
              </div>
            ))}
          </div>
        </div>
        <div className="min-h-0 flex-1 space-y-3 overflow-auto bg-panel2 p-4">
          {!sess && (
            <div className="overflow-hidden rounded-2xl border border-line bg-panel">
              <div className="flex items-center border-b border-line px-4 py-3 text-[13px] font-semibold text-slate-200">The team
                <span className="ml-auto font-normal text-slate-500">works in this order</span></div>
              {TEAM.map(([n, t, r, subs, color], i) => (
                <div key={n} className="flex gap-3 border-b border-line/70 px-4 py-3 last:border-0">
                  <Avatar name={n} color={color} size={24} />
                  <div className="min-w-0 text-[12px] leading-snug">
                    <div><b className="font-semibold text-slate-100">{n}</b> <span className="text-slate-500">· {t}</span></div>
                    <div className="text-slate-400">{r}</div>
                    {subs && <div className="mt-0.5 text-[11px] text-slate-500">Team: {subs}</div>}
                  </div>
                  <span className="ml-auto shrink-0 text-[11px] text-slate-600">{i + 1}</span>
                </div>
              ))}
            </div>
          )}
          {shown.map((a) => <AgentCard key={a.id} a={a} fact={sess?.validation?.fact_check?.[a.id]} redTeam={redTeam} />)}
          {sess && sess.status !== "running" && (sess.agents ?? []).some((a) => a.status === "skipped") && (
            <div className="flex items-start gap-2 rounded border border-dashed border-red-500/30 px-2.5 py-2 text-[11px] text-slate-400">
              <Circle size={11} className="mt-0.5 shrink-0 text-red-300" />
              <div><b className="text-slate-300">Not run:</b> {(sess.agents ?? []).filter((a) => a.status === "skipped").map((a) => a.name).join(", ")} — the AI is unavailable.</div>
            </div>
          )}
        </div>
      </aside>
      )}
      {take && <TakeTrade draft={take} onClose={() => setTake(null)} />}
      {cursorsOn && sess && <LiveCursors agents={sess.agents.map((a) => ({ id: a.id, name: a.name, color: a.color }))} player={player} anchor={anchor} geo={geo} />}
    </div>
  );
}
