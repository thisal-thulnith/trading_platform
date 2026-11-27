"use client";
import { AlertTriangle, Check, Loader2, Minus, Search, ShieldAlert, X, Zap } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { fmtPrice, pair } from "@/lib/format";
import { Card, More, Note, Page, PageHead, Tile } from "@/components/ui/Page";
import ThreeViews, { type AgentVerdict, type Final, type Simulation } from "@/components/signal/ThreeViews";
import TakeSignal from "@/components/portfolio/TakeSignal";

type Stats = { trades?: number; avg_r?: number; median_r?: number; win_rate?: number; outcomes?: Record<string, number>; avg_days?: number };
type LevRow = { leverage: number; position_usd: number; liquidation: number; status: "safe" | "tight" | "liquidated_before_stop";
  stop_usd: number; tp1_usd: number; tp2_usd?: number; plan_usd?: number };
type PlanPart = { entry: number; stop: number; stop_pct: number; tp1: number; tp1_pct: number; tp2: number | null; tp2_pct: number | null;
  days: number; label: string; leverage: { safe_max: number; stop_pct: number; rows: LevRow[] };
  tested: Tested_;
  expected_usd_per_trade_at_safe: number | null; predicted_r?: number; stop_atr?: number; target_r?: number };
type Tested_ = { recipe: string; filter: string; confidence: number | null; verdict: "tested_edge" | "weak" | "no_edge";
  holdout: Stats; select: Stats; baseline_holdout: Stats; baseline_select: Stats; beats_average_coin: boolean; yearly_avg_r: Record<string, number>;
  range_90?: [number, number] | null; this_side?: Stats | null };
type Why = { group: string; r: number; features: { name: string; what: string; value: number | null; r: number }[] };
type Ranked = { symbol: string; plan: string; score: number; pred_r: number; rank: number };
type Market = { btc_close: number; btc_ma50: number; trend: "up" | "down"; date: string };
type Ev = { source: string; text: string; value: number | null; usual: number | null; supports: boolean | null; tested: string; status: string };
type Plan = { available: boolean; covered?: boolean; note?: string; symbol: string; side: "LONG" | "SHORT"; tier: string; as_of: string;
  model_close: number; price: number; atr: number; atr_pct: number; entry_type: "limit" | "market"; score: number; rank: number; of: number;
  scores: Record<string, number>; plans: { swing: PlanPart; fast: PlanPart }; evidence: Ev[]; notes: string[]; risks: string[]; cancel_if: string;
  margin: number; status: "trade" | "watch"; gates: string[]; fast_gates: string[]; market: Market | null;
  engine?: string; plan_id?: string; why?: Why[]; clear?: boolean; unclear_note?: string | null;
  live_since_signal?: { move_pct: number | null; r: number | null; from: number; at: string } | null; simulation?: Simulation | null; agents?: AgentVerdict | null; final?: Final | null; live: { price: number; high_24h: number; low_24h: number; high_7d: number; low_7d: number; change_24h_pct: number } | null };
type Side = Tested_ & { status: "trade" | "watch"; gates: string[]; top: { symbol: string; score: number }[] };
type Today = { available: boolean; note?: string; as_of?: string; pick_side?: "LONG" | "SHORT"; pick_symbol?: string; strong?: boolean;
  status?: "trade" | "no_trade"; market?: Market | null; rule?: string;
  sides?: Record<"LONG" | "SHORT", Side>; plan?: Plan;
  engine?: string; top?: Ranked[]; picks?: Ranked[]; by_side?: Record<"LONG" | "SHORT", Ranked[]>;
  simulation?: Simulation | null; agents?: AgentVerdict | null; final?: Final | null };
type Candle = { time: number; open: number; high: number; low: number; close: number };

const money = (v?: number | null) => (v === undefined || v === null ? "—" : `${v < 0 ? "−" : "+"}$${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: v !== 0 && Math.abs(v) < 10 ? 2 : 0 })}`);
const pc = (v?: number | null, d = 0) => (v === undefined || v === null ? "—" : `${(v * 100).toFixed(d)}%`);
const sgn = (v?: number | null) => (v === undefined || v === null ? "—" : `${v > 0 ? "+" : ""}${v}%`);
const VERDICT: Record<string, [string, string]> = {
  tested_edge: ["Tested edge", "bg-emerald-500/15 text-emerald-300"],
  weak: ["Weak edge", "bg-sky-500/15 text-sky-300"],
  no_edge: ["No tested edge", "bg-amber-500/15 text-amber-300"],
};

/** Last 7 days of 1h closes with the plan's entry, stop and targets drawn as lines. */
function PlanChart({ symbol, p }: { symbol: string; p: PlanPart }) {
  const [c, setC] = useState<Candle[]>([]);
  useEffect(() => {
    let stop = false;
    api<{ candles: Candle[] }>(`/candles/${symbol}?tf=1h&limit=168`).then((x) => !stop && setC(x.candles)).catch(() => {});
    return () => { stop = true; };
  }, [symbol]);
  if (c.length < 5) return <div className="flex h-[240px] items-center justify-center text-[12px] text-slate-500">Loading chart…</div>;
  const W = 1000, H = 240, padR = 120;
  const lines = [
    { y: p.tp2, label: "TP2", color: "#16a34a" }, { y: p.tp1, label: "TP1", color: "#22c55e" },
    { y: p.entry, label: "Entry", color: "#0a84ff" }, { y: p.stop, label: "Stop", color: "#dc2626" },
  ].filter((l) => l.y !== null) as { y: number; label: string; color: string }[];
  const vals = [...c.flatMap((x) => [x.high, x.low]), ...lines.map((l) => l.y)];
  const lo = Math.min(...vals), hi = Math.max(...vals), r = hi - lo || 1;
  const Y = (v: number) => 8 + (1 - (v - lo) / r) * (H - 16);
  const X = (i: number) => (i / (c.length - 1)) * (W - padR);
  const path = c.map((x, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(x.close).toFixed(1)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full">
      {p.tp2 !== null && <rect x={0} width={W - padR} y={Math.min(Y(p.entry), Y(p.tp2))} height={Math.abs(Y(p.tp2) - Y(p.entry))} fill="#16a34a" opacity={0.07} />}
      <rect x={0} width={W - padR} y={Math.min(Y(p.entry), Y(p.stop))} height={Math.abs(Y(p.stop) - Y(p.entry))} fill="#dc2626" opacity={0.07} />
      <path d={path} fill="none" stroke="#94a3b8" strokeWidth={1.4} />
      {lines.map((l) => (
        <g key={l.label}>
          <line x1={0} x2={W - padR} y1={Y(l.y)} y2={Y(l.y)} stroke={l.color} strokeDasharray={l.label === "Entry" ? "" : "5 4"} strokeWidth={1.2} />
          <text x={W - padR + 6} y={Y(l.y) + 4} fontSize={11} fill={l.color} fontWeight={600}>{l.label} {fmtPrice(l.y)}</text>
        </g>
      ))}
      <circle cx={X(c.length - 1)} cy={Y(c[c.length - 1].close)} r={3} fill="#e2e8f0" />
    </svg>
  );
}

function Levels({ p, side }: { p: PlanPart; side: string }) {
  const cells: [string, string, string, string][] = [
    [side === "LONG" ? "Buy at" : "Sell at", fmtPrice(p.entry), "entry", "text-sky-300"],
    ["Stop loss", fmtPrice(p.stop), sgn(p.stop_pct), "text-red-300"],
    [p.tp2 !== null ? "Take profit 1" : "Take profit", fmtPrice(p.tp1), `${sgn(p.tp1_pct)}${p.tp2 !== null ? " · close half" : ""}`, "text-emerald-300"],
    ...(p.tp2 !== null ? [["Take profit 2", fmtPrice(p.tp2), `${sgn(p.tp2_pct)} · close the rest`, "text-emerald-300"] as [string, string, string, string]] : []),
    ["Hold up to", `${p.days} days`, "then close", "text-slate-100"],
  ];
  return (
    <div className={`grid grid-cols-2 gap-3 ${cells.length === 5 ? "md:grid-cols-5" : "md:grid-cols-4"}`}>
      {cells.map(([k, v, sub, tone]) => <Tile key={k} label={k} value={v} sub={sub} tone={tone} />)}
    </div>
  );
}

function LevTable({ p, margin, only }: { p: PlanPart; margin: number; only?: number[] }) {
  const rows = p.leverage.rows.filter((r) => !only || only.includes(r.leverage));
  const half = p.tp2 !== null;
  // the same "safe up to" number as every other page (the API's safe_max); the highlighted row is the highest at or under it
  const safeCap = Math.min(p.leverage.safe_max, 20);
  const safeMax = Math.max(...p.leverage.rows.filter((x) => x.leverage <= safeCap && x.status !== "liquidated_before_stop").map((x) => x.leverage), 0);
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] text-[13px]">
        <thead className="text-left text-[12px] text-slate-500">
          <tr className="border-b border-line"><th className="py-2 font-medium">Leverage</th><th className="font-medium">Position</th><th className="font-medium">Liquidation</th>
            <th className="font-medium">If stop hits</th><th className="font-medium">{half ? "At take profit 1" : "At take profit"}</th>{half && <th className="font-medium">Both targets</th>}</tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const dead = r.status === "liquidated_before_stop";
            const best = r.leverage === safeMax;
            return (
              <tr key={r.leverage} className={`border-b border-line/60 last:border-0 ${dead ? "text-slate-500" : ""} ${best ? "bg-emerald-500/5" : ""}`}>
                <td className="py-2.5 pl-1 font-semibold text-slate-100">
                  {r.leverage}x
                  {best && <span className="chip ml-2 bg-emerald-500/15 text-[10.5px] text-emerald-300">{r.leverage === safeCap ? "max safe" : `safe (max ${safeCap}x)`}</span>}
                  {dead && <span className="chip ml-2 bg-red-500/10 text-[10.5px] text-red-300">liquidated before stop</span>}
                  {r.status === "tight" && <span className="chip ml-2 bg-amber-500/10 text-[10.5px] text-amber-300">tight</span>}
                </td>
                <td className="num">${r.position_usd.toLocaleString()}</td>
                <td className="num">{fmtPrice(r.liquidation)}</td>
                <td className="num text-red-300">{dead ? `−$${margin} (all)` : money(r.stop_usd)}</td>
                <td className="num text-emerald-300">{dead ? "—" : money(r.tp1_usd)}</td>
                {half && <td className="num font-semibold text-emerald-300">{dead ? "—" : money(r.plan_usd)}</td>}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Tested({ t }: { t: PlanPart["tested"] }) {
  const h = t.holdout, b = t.baseline_holdout, oc = h.outcomes ?? {};
  return (
    <div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Tile label="Trades tested" value={h.trades ?? "—"} sub="2025–26, never seen" />
        <Tile label="Made money" value={pc(h.win_rate)} sub={`random coin ${pc(b.win_rate)}`} />
        <Tile label="Average result" value={`${(h.avg_r ?? 0) > 0 ? "+" : ""}${h.avg_r ?? "—"}R`} sub={`random coin ${b.avg_r ?? "—"}R`}
          tone={(h.avg_r ?? 0) > 0 ? "text-emerald-300" : "text-red-300"} />
        {h.avg_days !== undefined
          ? <Tile label="Held" value={`${h.avg_days}d`} sub="on average" />
          : <Tile label="Total" value={`${(h as { sum_r?: number }).sum_r ?? "—"}R`} sub="over all trades" />}
      </div>
      {(t.range_90 || t.this_side) && (
        <p className="mt-3 text-[12.5px] text-slate-400">
          {t.range_90 && <span title="90% range, from resampling whole weeks">Likely true average: <b>{t.range_90[0] > 0 ? "+" : ""}{t.range_90[0]}R</b> to <b>{t.range_90[1] > 0 ? "+" : ""}{t.range_90[1]}R</b>. </span>}
          {t.this_side?.trades ? <>This side: {t.this_side.trades} trades, {pc(t.this_side.win_rate)} made money, {t.this_side.avg_r}R a trade.</> : null}
        </p>
      )}
      {Object.keys(oc).length > 0 && <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[12.5px] text-slate-400">
        {oc.tp2 !== undefined && <span>TP2 {pc(oc.tp2)}</span>}
        {oc.tp1_then_entry !== undefined && <span>TP1 then entry {pc(oc.tp1_then_entry)}</span>}
        {oc.target !== undefined && <span>Target {pc(oc.target)}</span>}
        <span>Stop {pc(oc.stop)}</span>
        <span>Time limit {pc(oc.timeout)}</span>
      </div>}
      <details className="mt-2 text-[12px] text-slate-500">
        <summary className="cursor-pointer hover:text-slate-300">How it was tested</summary>
        <p className="mt-1">The same daily pick{t.filter === "trend" ? ", only with the market trend" : ""}{t.confidence ? `, only on its top ${Math.round((1 - t.confidence) * 100)}% confidence days` : ""}, traded on 2025–26 candles the models never saw. R = result in units of the risk (−1R = the stop), after fees. &quot;Random coin&quot; = an average liquid coin traded the same way on the same days{t.beats_average_coin ? "" : ": it did as well as the pick, so the market trend did most of the work"}.</p>
        <p className="mt-1">2020–24 (used to choose the method): {t.select.trades} trades, {t.select.avg_r}R a trade. By year: {Object.entries(t.yearly_avg_r ?? {}).map(([y, v]) => `${y} ${v > 0 ? "+" : ""}${v}R`).join(" · ")}.</p>
      </details>
    </div>
  );
}

/** The model's own reasons (TreeSHAP contributions in R), grouped by concept: green helps the trade, red hurts it. */
function WhyCard({ why }: { why: Why[] }) {
  const shown = why.filter((g) => Math.abs(g.r) >= 0.005).slice(0, 9);
  const mx = Math.max(...shown.map((g) => Math.abs(g.r)), 0.01);
  return (
    <Card title="Why this trade" sub="What moves the expected result (R). Green helps, red hurts.">
      <ul className="space-y-3.5">
        {shown.map((g) => (
          <li key={g.group}>
            <div className="flex items-center gap-3 text-[13px]">
              <span className="w-48 shrink-0 font-medium text-slate-200">{g.group}</span>
              <div className="h-2 flex-1 overflow-hidden rounded-full bg-fill">
                <div className={`h-full rounded-full ${g.r >= 0 ? "bg-emerald-500" : "bg-red-500"}`} style={{ width: `${Math.max(4, (Math.abs(g.r) / mx) * 100)}%` }} />
              </div>
              <span className={`num w-16 text-right font-semibold ${g.r >= 0 ? "text-emerald-300" : "text-red-300"}`}>{g.r > 0 ? "+" : ""}{g.r.toFixed(2)}R</span>
            </div>
            {g.features.length > 0 && (
              <div className="mt-1 pl-[204px] text-[12px] text-slate-500">
                {g.features.map((f) => <div key={f.name}>{f.what}{f.value !== null ? `: ${Math.abs(f.value) >= 100 ? f.value.toFixed(0) : f.value}` : ""} <span className={f.r >= 0 ? "text-emerald-300" : "text-red-300"}>({f.r > 0 ? "+" : ""}{f.r}R)</span></div>)}
              </div>
            )}
          </li>
        ))}
      </ul>
    </Card>
  );
}

/** Today's signal, in one view: the coin, the side, entry, stop, targets, the leverage table for your margin with the
 *  dollars at each level, the fast high-leverage option, the evidence, how the same daily pick did on unseen years, the
 *  risks and when not to trade. Made by the trained models and the learned chart patterns; no AI key needed. */
export default function SignalPage() {
  const [margin, setMargin] = useState(100);
  const [mText, setMText] = useState("100");
  const [today, setToday] = useState<Today | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [custom, setCustom] = useState<{ symbol: string; side?: string } | null>(null);
  const [testMsg, setTestMsg] = useState("");
  const addToTest = async (symbol: string, side: string) => {
    setTestMsg("");
    try {
      await api(`/agent/signal/test/add`, { method: "POST", json: { symbol, side, margin } });
      setTestMsg("Added to the signal test");
    } catch (e) { setTestMsg(String((e as Error).message || e)); }
  };

  const load = useCallback(async () => {
    setBusy(true); setErr("");
    try {
      if (custom) {
        const p = await api<Plan>(`/agent/signal/plan?symbol=${custom.symbol}&margin=${margin}${custom.side ? `&side=${custom.side}` : ""}`);
        setPlan(p);
        if (!today) setToday(await api<Today>(`/agent/signal/today?margin=${margin}`));
      } else {
        const t = await api<Today>(`/agent/signal/today?margin=${margin}`);
        setToday(t); setPlan(t.plan ? { ...t.plan, simulation: t.simulation, agents: t.agents, final: t.final } : null);
      }
    } catch (e) { setErr(String((e as Error).message || e)); }
    setBusy(false);
  }, [custom, margin]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {   // /signal?symbol=SOLUSDT opens that coin's plan (linked from the research desk)
    const s = new URLSearchParams(window.location.search).get("symbol");
    if (s) setCustom({ symbol: s.toUpperCase() });
  }, []);
  useEffect(() => { load(); const i = setInterval(load, 30_000); return () => clearInterval(i); }, [load]);   // live

  const p = plan?.available && plan.covered ? plan : null;
  const isPick = !custom && p;
  const sw = p?.plans.swing, fa = p?.plans.fast;
  const supports = useMemo(() => (p?.evidence ?? []).filter((e) => e.supports === true).length, [p]);
  const against = useMemo(() => (p?.evidence ?? []).filter((e) => e.supports === false).length, [p]);

  const applyMargin = (v: string) => { setMText(v); const n = Number(v); if (n >= 1) setMargin(n); };
  const openCoin = (symbol: string, side?: string) => { setTestMsg(""); setCustom({ symbol, side }); };
  const mk = today?.market;

  return (
    <Page>
      <PageHead
        title={custom ? `Trade plan · ${pair(custom.symbol)}` : "Today's signal"}
        sub={<>
          <span title={today?.engine === "unified" ? "One model: market data, indicators and learned chart patterns. No AI key needed." : "Trained models + chart patterns. No AI key needed."}>
            Data to {today?.as_of ?? "…"} · Paper only · you place the orders</span>
          {mk && <span className={`chip ${mk.trend === "up" ? "bg-emerald-500/15 text-emerald-300" : "bg-red-500/15 text-red-300"}`}
            title={`BTC ${mk.btc_close.toLocaleString()} vs its 50-day average ${mk.btc_ma50.toLocaleString()} (${mk.date})`}>
            Market {mk.trend === "up" ? "UP · longs allowed" : "DOWN · shorts allowed"}</span>}
        </>}>
        {busy && <Loader2 size={16} className="animate-spin text-slate-400" />}
        <label className="flex items-center gap-2 text-[13px] text-slate-500">Margin
          <span className="relative"><span className="absolute left-3 top-[9px] text-slate-500">$</span>
            <input className="input w-24 pl-6" type="number" min={1} value={mText} onChange={(e) => applyMargin(e.target.value)} /></span>
        </label>
        <form className="relative" onSubmit={(e) => { e.preventDefault(); if (q.trim()) openCoin(q.trim().toUpperCase().replace("/", "")); }}>
          <Search size={14} className="absolute left-3 top-[11px] text-slate-500" />
          <input className="input w-40 pl-8" placeholder="Any coin, e.g. SOL" value={q} onChange={(e) => setQ(e.target.value)} />
        </form>
        {custom && <button className="btn btn-ghost" onClick={() => { setCustom(null); setQ(""); }}>Back to today&apos;s pick</button>}
      </PageHead>

      {err && <Note tone="bad">{err}</Note>}
      {today && !today.available && <Note tone="warn">{today.note}</Note>}
      {plan && plan.available && !plan.covered && <Note tone="warn">{pair(plan.symbol)}: {plan.note}</Note>}
      {p && p.clear === false && <Note tone="warn"><b>No clear signal.</b> {p.unclear_note} The levels below are only for reference.</Note>}
      {isPick && today?.status === "no_trade" && (
        <Note tone="warn" className="flex items-start gap-2">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" />
          <div><b>No trade today.</b> Below is the best coin to watch. Waiting is a position too.</div>
        </Note>
      )}

      {p && sw && fa && (
        <>
          {/* the call */}
          <Card>
            <div className="flex flex-wrap items-center gap-4">
              <span className={`rounded-xl px-4 py-1.5 text-[22px] font-black tracking-wide ${p.side === "LONG" ? "bg-emerald-500/15 text-emerald-300" : "bg-red-500/15 text-red-300"}`}>{p.side}</span>
              <div className="min-w-0">
                <div className="text-[24px] font-semibold text-slate-100">{pair(p.symbol)}</div>
                <div className="text-[13px] text-slate-500" title={`Average daily move ${p.atr_pct}% · #${p.rank} of ${p.of} for a ${p.side.toLowerCase()}`}>
                  Price <b className="num text-slate-100">{fmtPrice(p.price)}</b>
                  {p.live && <> · 24h {sgn(p.live.change_24h_pct)}</>}
                  {p.live_since_signal && p.live_since_signal.move_pct !== null && <> · since the signal <b className={p.live_since_signal.move_pct > 0 ? "text-emerald-300" : p.live_since_signal.move_pct < 0 ? "text-red-300" : "text-slate-300"}
                    title={`As if taken at the model's daily close ${fmtPrice(p.live_since_signal.from)} (${p.live_since_signal.at}). Display only.`}>{p.live_since_signal.move_pct > 0 ? "+" : ""}{p.live_since_signal.move_pct}%{p.live_since_signal.r !== null ? ` (${p.live_since_signal.r > 0 ? "+" : ""}${p.live_since_signal.r.toFixed(2)}R)` : ""}</b></>}
                </div>
              </div>
              <div className="ml-auto flex flex-col items-end gap-1">
                <span className={`chip ${VERDICT[sw.tested.verdict][1]}`}>{VERDICT[sw.tested.verdict][0]}</span>
                <span className="text-[12px] text-slate-500" title="R = result in units of the risk. −1R means the stop was hit.">{sw.predicted_r !== undefined
                  ? <>Expected result <b className={sw.predicted_r >= 0 ? "text-emerald-300" : "text-red-300"}>{sw.predicted_r > 0 ? "+" : ""}{sw.predicted_r}R</b></>
                  : <>{supports} reasons for · {against} against</>}</span>
              </div>
            </div>

            {p.status === "trade"
              ? <Note tone="good" className="mt-5 flex items-center gap-2"><Check size={16} /><b>TRADE</b> — the tested rule takes it today.</Note>
              : <Note tone="warn" className="mt-5">
                  <div className="flex items-center gap-2"><AlertTriangle size={16} /><b>WATCH ONLY</b> — not a trade today</div>
                  <ul className="mt-1 list-disc pl-7 text-[13px]">{p.gates.map((g) => <li key={g}>{g}</li>)}</ul>
                </Note>}

            <div className="mt-3 rounded-xl border border-sky-500/20 px-4 py-3 text-[14px] text-slate-200">
              <b className="text-sky-300">{p.status === "trade" ? "What to do" : "If you still take it"}:</b>{" "}
              {p.entry_type === "limit"
                ? `Place a ${p.side === "LONG" ? "buy" : "sell"} LIMIT order at ${fmtPrice(sw.entry)} — don't use market.`
                : `${p.side === "LONG" ? "Buy" : "Sell"} at about ${fmtPrice(sw.entry)}.`}
              {sw.tp2 === null
                ? <>{" "}Stop {fmtPrice(sw.stop)}. Take profit at {fmtPrice(sw.tp1)}{sw.target_r ? ` (${sw.target_r}R)` : ""}. Don&apos;t move the stop.</>
                : <>{" "}Stop {fmtPrice(sw.stop)}. Take half at {fmtPrice(sw.tp1)} and move the stop to entry. Close the rest at {fmtPrice(sw.tp2)}.</>}
              {" "}Leverage isolated, safe up to {Math.min(sw.leverage.safe_max, 20)}x{Math.min(sw.leverage.safe_max, 5) >= 2 ? ` (we suggest 2x–${Math.min(sw.leverage.safe_max, 5)}x)` : ""}.
            </div>
            {p.notes.map((n) => <p key={n} className="mt-2 text-[12.5px] text-amber-300">{n}</p>)}

            <div className="mt-5"><Levels p={sw} side={p.side} /></div>
            <div className="mt-5"><PlanChart symbol={p.symbol} p={sw} /></div>
            {p.clear !== false && <div className="mt-4 flex flex-wrap items-center gap-3">
              <TakeSignal symbol={p.symbol} side={p.side} levels={{ entry: sw.entry, stop: sw.stop, target: sw.tp1 }} agents={p.agents ?? null} />
              <span className="text-[12.5px] text-slate-500">Placed it on Binance? Record it here and Portfolio tracks it live.</span>
            </div>}
            <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-[12.5px] text-slate-500">
              <span>{sw.label}</span>
              {isPick
                ? <Link href="/signal/test" className="text-accent hover:underline">Saved to the signal test automatically →</Link>
                : <button className="text-accent hover:underline" onClick={() => addToTest(p.symbol, p.side)}>{testMsg || "+ Add to signal test"}</button>}
            </div>
          </Card>

          {/* machine, simulation and agents: three separate opinions */}
          {p.engine === "unified" && (
            <ThreeViews margin={margin} onAgentsDone={load} sim={p.simulation ?? null} agents={p.agents ?? null} final={p.final ?? null}
              machine={{ symbol: p.symbol, side: p.side, status: p.status, plan_id: p.plan_id, predicted_r: sw.predicted_r,
                unseen_avg_r: sw.tested.holdout.avg_r, unseen_trades: sw.tested.holdout.trades, why: p.why }} />
          )}

          {/* money + risks */}
          <div className="grid gap-6 lg:grid-cols-[1.7fr_1fr]">
          <Card title={`Your $${margin} margin at each leverage`}
            sub={`Isolated margin, fees included. Stop is ${sw.leverage.stop_pct}% away; liquidation must be further.`}>
            <LevTable p={sw} margin={margin} />
            {sw.expected_usd_per_trade_at_safe !== null && (
              <p className="mt-3 text-[12.5px] text-slate-500">
                2025–26 test average: <b className={(sw.expected_usd_per_trade_at_safe ?? 0) >= 0 ? "text-emerald-300" : "text-red-300"}>{money(sw.expected_usd_per_trade_at_safe)}</b> a trade at max safe leverage. One trade hits the stop or a target, never the average.
              </p>
            )}
          </Card>
            <Card title={<><ShieldAlert size={17} className="text-amber-300" /> Risks · when not to trade</>}>
              <ul className="list-disc space-y-2 pl-5 text-[13px] text-slate-300">
                <li className="text-amber-200">{p.cancel_if}</li>
                {p.risks.map((r) => <li key={r}>{r}</li>)}
                <li>No signal here wins 90% of the time. Size so the stop is a loss you accept.</li>
              </ul>
            </Card>
          </div>
        </>
      )}

      {(p || today?.by_side || today?.sides) && (
        <More>
          {p && sw && fa && (
          <>
          <div className="grid gap-6 md:grid-cols-2">
            {p.why && p.why.length > 0 ? <WhyCard why={p.why} /> : <Card title="Why this coin" sub="✓ supports the trade · ✗ against it · — neutral">
              <ul className="space-y-3 text-[13px]">
                {p.evidence.map((e) => (
                  <li key={e.text} className="flex gap-2.5">
                    {e.supports === true ? <Check size={16} className="mt-0.5 shrink-0 text-emerald-400" /> : e.supports === false ? <X size={16} className="mt-0.5 shrink-0 text-red-400" /> : <Minus size={16} className="mt-0.5 shrink-0 text-slate-500" />}
                    <div>
                      <div className="text-slate-200">{e.text}{e.value !== null && <>: <b>{pc(e.value)}</b>{e.usual !== null && <span className="text-slate-500"> (usual {pc(e.usual)})</span>}</>}</div>
                      <div className="text-[11.5px] text-slate-500">{e.source} · {e.tested}</div>
                    </div>
                  </li>
                ))}
              </ul>
            </Card>}
            <Card title="How this plan did on unseen years" right={<span className={`chip ${VERDICT[sw.tested.verdict][1]}`}>{VERDICT[sw.tested.verdict][0]}</span>}>
              <Tested t={sw.tested} />
            </Card>
          </div>

          {/* risky fast option */}
          <Card className="border-red-500/25"
            title={<><Zap size={17} className="text-red-300" /> Risky option · 10x–20x {p.engine === "unified" ? "tight stop" : "fast plan"}</>}
            sub={`${fa.label}. Tight stop: it hits often.${fa.predicted_r !== undefined ? ` Expected ${fa.predicted_r > 0 ? "+" : ""}${fa.predicted_r}R a trade.` : ""}`}
            right={<span className={`chip ${VERDICT[fa.tested.verdict][1]}`}>{VERDICT[fa.tested.verdict][0]}</span>}>
            {fa.tested.verdict === "no_edge" && <Note tone="bad" className="mb-4">Not recommended: this plan lost money on the unseen years.</Note>}
            <Levels p={fa} side={p.side} />
            <div className="mt-4"><LevTable p={fa} margin={margin} only={[5, 10, 15, 20]} /></div>
            <details className="mt-4">
              <summary className="cursor-pointer text-[13px] font-medium text-slate-300 hover:text-slate-100">How the fast plan did on unseen years</summary>
              <div className="mt-3"><Tested t={fa.tested} /></div>
            </details>
          </Card>
          </>
          )}

      {/* runners-up */}
      {today?.engine === "unified" && today.by_side && (
        <Card title="Top coins today" sub="Expected result in R. Click a coin for its plan.">
          <div className="grid gap-6 md:grid-cols-2">
            {(["LONG", "SHORT"] as const).map((s) => (
              <div key={s}>
                <b className={`text-[13px] ${s === "LONG" ? "text-emerald-300" : "text-red-300"}`}>{s}</b>
                <div className="mt-2 space-y-1.5">
                  {today.by_side![s].map((t) => (
                    <button key={t.symbol} onClick={() => openCoin(t.symbol, s)}
                      className={`flex w-full items-center gap-3 rounded-xl px-3 py-2 text-left text-[13px] ${p?.symbol === t.symbol ? "bg-accent-soft text-accent" : "bg-fill text-slate-200 hover:bg-white/10"}`}>
                      <span className="w-24 font-semibold">{pair(t.symbol)}</span>
                      <span className="flex-1 text-[12px] text-slate-500">stop {t.plan.split("_")[1].replace("ATR", " ATR")} · target {t.plan.split("_")[2]}</span>
                      <span className={`num font-semibold ${t.pred_r >= 0 ? "text-emerald-300" : "text-red-300"}`}>{t.pred_r > 0 ? "+" : ""}{t.pred_r.toFixed(2)}R</span>
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}
      {today?.sides && today.engine !== "unified" && (
        <Card title="Other coins today" sub="Top coins for each side. Click a coin for its plan.">
          <div className="grid gap-6 md:grid-cols-2">
            {(["LONG", "SHORT"] as const).map((s) => {
              const sd = today.sides![s];
              return (
                <div key={s}>
                  <div className="mb-2 flex flex-wrap items-center gap-2 text-[13px]">
                    <b className={s === "LONG" ? "text-emerald-300" : "text-red-300"}>{s}</b>
                    <span className={`chip ${VERDICT[sd.verdict]?.[1] ?? ""}`}>{VERDICT[sd.verdict]?.[0] ?? sd.verdict}</span>
                    <span className={`text-[12.5px] ${sd.status === "trade" ? "text-emerald-300" : "text-amber-300"}`}>{sd.status === "trade" ? "trading today" : "not today"}</span>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {sd.top.map((t, i) => (
                      <button key={t.symbol} onClick={() => openCoin(t.symbol, s)}
                        className={`rounded-xl px-3 py-1.5 text-[13px] ${p?.symbol === t.symbol && p.side === s ? "bg-accent-soft font-semibold text-accent" : "bg-fill text-slate-200 hover:bg-white/10"}`}>
                        {i + 1}. {pair(t.symbol)}
                      </button>
                    ))}
                  </div>
                  <p className="mt-2 text-[12px] text-slate-500">2025–26: {sd.holdout.avg_r}R a trade vs {sd.baseline_holdout.avg_r}R for a random coin.{sd.gates.length > 0 && ` ${sd.gates[0]}`}</p>
                </div>
              );
            })}
          </div>
        </Card>
      )}
        </More>
      )}
      {!today && !err && <div className="flex items-center gap-2 text-[13.5px] text-slate-500"><Loader2 size={16} className="animate-spin" /> Building today&apos;s signal…</div>}
    </Page>
  );
}
