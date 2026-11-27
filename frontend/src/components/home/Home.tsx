"use client";
import { ArrowRight, Coins, Crosshair, FlaskConical, Loader2 } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { fmtPrice, pair } from "@/lib/format";
import { AreaSpark, GaugeLine, type Point } from "@/components/dashboard/charts";
import { Card, Note, Page, PageHead } from "@/components/ui/Page";
import type { AgentVerdict, Final, Simulation } from "@/components/signal/ThreeViews";
import TakeSignal from "@/components/portfolio/TakeSignal";

const MARGIN = 100;  // the money line on Home is for $100 margin

type LevRow = { leverage: number; stop_usd: number; tp1_usd: number; status?: string };
type Lev = { safe_max: number; stop_pct: number; rows?: LevRow[] };
type Part = { entry: number; stop: number; stop_pct: number; tp1: number; tp1_pct: number; predicted_r?: number; leverage: Lev;
  tested: { holdout: { trades?: number; avg_r?: number; win_rate?: number } } };
type Plan = { available: boolean; covered?: boolean; symbol: string; side: "LONG" | "SHORT"; status: "trade" | "watch"; price: number;
  live_since_signal?: { move_pct: number | null; r: number | null; from: number; at: string } | null;
  live_state?: { state: "valid" | "stop_hit" | "target_hit" | "ran_away" | "unknown"; text: string } | null;
  entry_type: "limit" | "market"; plan_id?: string; plans: { swing: Part } };
type Today = { available: boolean; note?: string; as_of?: string; status?: string; market?: { trend: "up" | "down"; btc_close: number; btc_ma50: number } | null;
  plan?: Plan; simulation?: Simulation | null; agents?: AgentVerdict | null; final?: Final | null };
type G = { finished: number; open: number; waiting: number; wins?: number; losses?: number; win_rate?: number; avg_r?: number; sum_r?: number; usd?: number };
type Sig = { id: string; symbol: string; side: string; state: string; r: number | null; created_at: string };
type Test = { milestone: number; main_group?: string; stats: Record<string, { swing: G; signals: number }>; verdict: { ready: boolean; text: string };
  signals: Sig[] };
type Market = { regime: string | null; fear_greed: { value: number; label: string } | null; btc_dominance_pct?: number | null;
  series: { btc: Point[]; fear_greed: Point[] } };
type Credit = { credit: { left: number; desk_runs_left: number | null } | null };

/** Whole dollars, with cents only under $10: "+$101", "−$34", "+$4.50". */
const money = (v?: number | null) => (v === undefined || v === null ? "—"
  : `${v < 0 ? "−" : "+"}$${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: v !== 0 && Math.abs(v) < 10 ? 2 : 0 })}`);
const sgn = (v: number) => `${v > 0 ? "+" : ""}${v}%`;
const word = (s?: string | null) => (s ? s.charAt(0) + s.slice(1).toLowerCase() : "");
const sideTone = (s?: string) => (s === "LONG" ? "bg-emerald-500/15 text-emerald-300" : "bg-red-500/15 text-red-300");

/** The map of the app: what each page is for, so the different systems are never confused with the main signal. */

function Level({ k, v, sub, cls = "text-slate-100" }: { k: string; v: string; sub?: string; cls?: string }) {
  return (
    <div className="rounded-xl bg-fill px-4 py-3">
      <div className="text-[12px] text-slate-500">{k}</div>
      <div className={`num text-[18px] font-semibold ${cls}`}>{v}</div>
      {sub && <div className="text-[12px] text-slate-500">{sub}</div>}
    </div>
  );
}

function SignalHero({ t }: { t: Today | null }) {
  if (!t) return <Card><div className="flex items-center gap-2 text-[13.5px] text-slate-500"><Loader2 size={16} className="animate-spin" /> Building today&apos;s signal…</div></Card>;
  const p = t.plan?.available && t.plan.covered ? t.plan : null;
  if (!t.available || !p) return <Card title="Today's signal"><Note tone="warn">{t.note ?? "Not ready yet."}</Note></Card>;
  const sw = p.plans.swing;
  const f = t.final;
  const safe = Math.min(sw.leverage.safe_max, 20);
  // the money line reads the plan's own leverage table: the largest leverage that is still safe
  const row = (sw.leverage.rows ?? []).filter((r) => r.leverage <= safe).sort((a, b) => b.leverage - a.leverage)[0];
  const verb = p.side === "LONG" ? "Buy" : "Sell";
  const won = t.simulation?.side_stats?.unseen?.win_rate;
  const ag = t.agents;
  return (
    <Card title={<><Crosshair size={17} className="text-accent" /> Today&apos;s signal</>} sub={t.as_of ? `Updated ${t.as_of}` : undefined}
      right={<div className="flex flex-wrap gap-2"><Link href="/signals" className="btn btn-ghost">All coins</Link><Link href="/signal" className="btn btn-ghost">Full plan <ArrowRight size={15} /></Link>
        <TakeSignal symbol={p.symbol} side={p.side} levels={{ entry: sw.entry, stop: sw.stop, target: sw.tp1 }} agents={t?.agents ?? null} /></div>}>
      <div className="flex flex-wrap items-center gap-4">
        <span className={`rounded-xl px-4 py-1.5 text-[26px] font-black tracking-wide ${sideTone(p.side)}`}>{p.side}</span>
        <div>
          <div className="text-[28px] font-semibold leading-tight text-slate-100">{pair(p.symbol)}</div>
          <div className="text-[13px] text-slate-500">Price now <b className="num text-slate-200">{fmtPrice(p.price)}</b>
            {p.live_since_signal && p.live_since_signal.move_pct !== null && <> · since the signal <b className={p.live_since_signal.move_pct > 0 ? "text-emerald-300" : p.live_since_signal.move_pct < 0 ? "text-red-300" : "text-slate-300"}
                    title={`As if taken at the model's daily close ${fmtPrice(p.live_since_signal.from)} (${p.live_since_signal.at}). Display only.`}>{p.live_since_signal.move_pct > 0 ? "+" : ""}{p.live_since_signal.move_pct}%{p.live_since_signal.r !== null ? ` (${p.live_since_signal.r > 0 ? "+" : ""}${p.live_since_signal.r.toFixed(2)}R)` : ""}</b></>}</div>
        </div>
        <span className={`chip ml-auto text-[13px] ${p.status === "trade" ? "bg-emerald-500/15 text-emerald-300" : "bg-amber-500/15 text-amber-300"}`}>
          {p.status === "trade" ? "Good to trade today" : "Watch only — not today"}</span>
      </div>

      <div className="mt-5 grid grid-cols-2 gap-3 md:grid-cols-4">
        <Level k={`${verb} at`} v={fmtPrice(sw.entry)} sub={p.entry_type === "limit" ? "limit order" : "market price"} />
        <Level k="Stop loss" v={fmtPrice(sw.stop)} sub={`${sgn(sw.stop_pct)} move`} cls="text-red-300" />
        <Level k="Take profit" v={fmtPrice(sw.tp1)} sub={`${sgn(sw.tp1_pct)} move`} cls="text-emerald-300" />
        <Level k="Leverage" v={`up to ${safe}x`} sub="safe" />
      </div>

      {row && (
        <p className="mt-4 text-[15px] text-slate-200">
          On ${MARGIN} at {row.leverage}x: <b className="num text-emerald-300">{money(row.tp1_usd)}</b> if it works,{" "}
          <b className="num text-red-300">−${Math.abs(row.stop_usd).toLocaleString(undefined, { maximumFractionDigits: Math.abs(row.stop_usd) < 10 ? 2 : 0 })}</b> if stopped.
        </p>
      )}

      <div className="mt-4 flex flex-wrap gap-2 text-[13px]">
        <span className="chip bg-fill text-slate-200">Model: {word(p.side)}</span>
        <span className="chip bg-fill text-slate-200">Past similar trades: {won !== undefined && won !== null ? `won ${Math.round(won * 100)}%` : "—"}</span>
        <span className="chip bg-fill text-slate-200">AI agents: {ag ? `${word(ag.action)}${ag.stale ? " (old)" : ""}` : "not asked"}</span>
      </div>
      {p.live_state && ["stop_hit", "target_hit", "ran_away"].includes(p.live_state.state) &&
        <Note tone="warn" className="mt-4"><b>Not tradeable now.</b> {p.live_state.text}</Note>}
      {f && <Note tone={f.tone === "neutral" ? "info" : f.tone} className="mt-4"><b>{f.label}.</b> {f.text}</Note>}
    </Card>
  );
}

function ResultsCard({ r }: { r: Test | null }) {
  const g = r?.stats.all?.swing ?? r?.stats[r.main_group ?? "platform"]?.swing;   // the same signals the list shows
  const open = (r?.signals ?? []).filter((s) => s.state === "open" || s.state === "waiting").slice(0, 5);
  return (
    <Card title={<><FlaskConical size={17} className="text-sky-300" /> Results</>} sub="Every signal, checked on live prices"
      right={<Link href="/signal/test" className="text-[13px] text-accent hover:underline">All results →</Link>}>
      {!r || !g ? <div className="text-[13px] text-slate-500">Loading…</div> : (
        <>
          <div className="text-[18px] font-semibold text-slate-100">
            {g.finished > 0
              ? <>Won {g.wins ?? 0} of {g.finished} · <span className={(g.usd ?? 0) >= 0 ? "text-emerald-300" : "text-red-300"}>{money(g.usd)}</span></>
              : <>{g.open + g.waiting} open · 0 finished</>}
          </div>
          <div className="mt-1 text-[13px] text-slate-500">
            {g.finished > 0 ? `${g.open + g.waiting} open now` : `First score after ${r.milestone} finished trades`}
          </div>
          {open.length > 0 && (
            <div className="mt-3 divide-y divide-line text-[13px]">
              {open.map((s) => (
                <div key={s.id} className="flex items-center gap-3 py-1.5">
                  <span className="w-24 font-medium text-slate-200">{pair(s.symbol)}</span>
                  <span className="text-[11.5px] text-slate-500" title="The day the signal was given">{new Date(s.created_at).toLocaleDateString([], { day: "numeric", month: "short" })}</span>
                  <span className={s.side === "LONG" ? "text-emerald-300" : "text-red-300"}>{s.side}</span>
                  <span className="text-slate-500">{s.state === "waiting" ? "waiting to fill" : "open"}</span>
                  <span className={`ml-auto font-medium ${s.r === null ? "" : s.r >= 0 ? "text-emerald-300" : "text-red-300"}`}>
                    {s.r === null ? "" : s.r > 0 ? "in profit" : s.r < 0 ? "in loss" : "flat"}</span>
                </div>))}
            </div>
          )}
        </>
      )}
    </Card>
  );
}

function MarketCard({ m, trend }: { m: Market | null; trend?: Today["market"] }) {
  const s = m?.series;
  const btc = s?.btc.length ? s.btc[s.btc.length - 1].v : null;
  return (
    <Card title="Market"
      right={trend && <span title={`BTC is ${trend.trend === "up" ? "above" : "below"} its 50-day average`}
        className={`chip ${trend.trend === "up" ? "bg-emerald-500/15 text-emerald-300" : "bg-red-500/15 text-red-300"}`}>
        Market is {trend.trend === "up" ? "rising" : "falling"}</span>}>
      <div className="grid gap-5 sm:grid-cols-2">
        <div><div className="text-[12px] text-slate-500">Bitcoin</div><div className="num text-[18px] font-semibold text-slate-100">{btc ? `$${Math.round(btc).toLocaleString()}` : "—"}</div>
          <div className="mt-1"><AreaSpark data={s?.btc ?? []} height={44} fmt={(x) => `$${Math.round(x).toLocaleString()}`} /></div></div>
        <div><div className="text-[12px] text-slate-500">Fear &amp; greed</div><div className="num text-[18px] font-semibold text-slate-100">{m?.fear_greed ? `${m.fear_greed.value} ${m.fear_greed.label}` : "—"}</div>
          <div className="mt-1"><GaugeLine data={s?.fear_greed ?? []} height={44} /></div></div>
      </div>
    </Card>
  );
}

/** A small AI-credits line; the full usage card lives on Settings. */
function CreditsTile() {
  const [c, setC] = useState<Credit | null>(null);
  useEffect(() => { api<Credit>("/agent/costs").then(setC).catch(() => undefined); }, []);
  const cr = c?.credit;
  return (
    <Link href="/settings" className="flex items-center gap-3 rounded-2xl border border-line bg-panel px-6 py-4 text-[14px] hover:bg-fill">
      <Coins size={17} className="text-amber-300" />
      {!c ? <span className="text-slate-500">AI credits…</span> : cr
        ? <span className="text-slate-200">AI credits: <b className="num">${Math.max(0, cr.left).toFixed(2)}</b> left
            {cr.desk_runs_left !== null && <span className="text-slate-500"> (≈ {cr.desk_runs_left} agent runs)</span>}</span>
        : <span className="text-slate-200">Set your AI balance</span>}
      <span className="ml-auto text-[13px] text-accent">Settings →</span>
    </Link>
  );
}

/** Home: the one main signal first, in plain words and dollars, then its live score, the market and a map of every page. */
export default function Home() {
  const [t, setT] = useState<Today | null>(null);
  const [r, setR] = useState<Test | null>(null);
  const [m, setM] = useState<Market | null>(null);
  const [err, setErr] = useState("");
  const [today, setToday] = useState("");
  useEffect(() => { setToday(new Date().toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" })); }, []);
  useEffect(() => {   // a live dashboard: the signal (live price), the live results and the market refresh by themselves
    const load = () => {
      api<Today>(`/agent/signal/today?margin=${MARGIN}`).then((x) => { setT(x); setErr(""); }).catch((e) => setErr(String(e.message || e)));
      api<Test>("/agent/signal/test").then(setR).catch(() => undefined);
      api<Market>("/agent/market").then(setM).catch(() => undefined);
    };
    load();
    const i = setInterval(load, 30_000);
    return () => clearInterval(i);
  }, []);

  return (
    <Page>
      <PageHead title="Home" sub={<span>{today ? `${today} · ` : ""}Paper only · you place the orders</span>} />
      {err && <Note tone="bad">{err}</Note>}
      <SignalHero t={t} />
      <div className="grid gap-6 lg:grid-cols-2">
        <ResultsCard r={r} />
        <MarketCard m={m} trend={t?.market} />
      </div>
      <CreditsTile />
      <Card title="How it works, end to end">
        <ol className="grid gap-3 text-[13px] md:grid-cols-3 xl:grid-cols-6">
          {[["1", "The machine scores every coin", "once a day; prices and checks are live", "/signals"],
            ["2", "Read today's signal", "side, entry, stop, take profit, leverage, still valid?", "/signal"],
            ["3", "Get a second opinion", "the AI agents, only when you ask (uses credits)", "/desk"],
            ["4", "Place it on Binance yourself", "isolated, stop loss and take profit attached", "/guide#10-how-to-place-a-trade-and-manage-it"],
            ["5", "Record it in Portfolio", "accept the plan, or add it with your own numbers", "/portfolio"],
            ["6", "Tracked live to the close", "next step daily; results in Portfolio, Results and History", "/history"]].map(([n, t, sub, href]) => (
            <li key={n}><Link href={href} className="block h-full rounded-xl bg-fill px-3.5 py-3 hover:bg-white/10">
              <div className="flex items-center gap-2"><span className="flex h-5 w-5 items-center justify-center rounded-full bg-accent text-[11px] font-semibold text-[#fff]">{n}</span>
                <span className="font-medium text-slate-100">{t}</span></div>
              <div className="mt-1 text-[12px] leading-snug text-slate-500">{sub}</div></Link></li>))}
        </ol>
      </Card>

    </Page>
  );
}
