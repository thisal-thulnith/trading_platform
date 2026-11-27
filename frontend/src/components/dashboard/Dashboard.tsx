"use client";
import { ChevronDown, ChevronRight, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { fmtPrice, pair } from "@/lib/format";
import MachineBar from "@/components/MachineBar";
import { Card, LabBanner, More, Note, Page, PageHead } from "@/components/ui/Page";
import { AreaSpark, type Point } from "./charts";
import TakeSignal from "@/components/portfolio/TakeSignal";

type PickPlan = { ok: boolean; error: string | null; qty: number; notional_usd: number; leverage: number; margin_usd: number;
  liquidation: number | null; risk_usd: number; reward_usd: number; risk_pct_of_budget: number; stop_pct: number; target_pct: number;
  rr_net: number; fees_usd: number; orders: string[]; warnings: string[] };
type Pick = { main?: { covered: boolean; clear?: boolean; side?: string | null; rank?: number; agrees?: boolean };
  live_state?: { state: string; text: string } | null; rank: number; symbol: string; side: "LONG" | "SHORT"; price: number; strategy: string; stage: string; regime: string;
  readiness: { verdict: string; score: number; summary: string; wait_for?: string[] }; style: string; entry: number; stop: number; target: number; rr: number;
  stop_why: string; target_why: string; capacity_usd: number | null;
  validation: { score: number; passed: number; total: number; warnings: string[] };
  odds: { n: number; win_rate: number | null; avg_r: number | null; basis: string } | null; plan: PickPlan;
  signal?: { score: number; direction: string; strength: number; decision: "take" | "wait" | "veto"; decision_why: string;
    coverage: number; agreement: number; contributions: { component: string; score: number; weight: number; contribution: number; why: string }[] } };
type Daily = { day: string; tf: string; made_at: number; pool: number; with_setup: number; picks: Pick[]; dropped: { symbol: string; why: string }[];
  selector: string; ai?: Record<string, string> | null; budget: number };
type Sandbox = { equity: number; start_equity: number; return_pct: number; max_drawdown_pct: number; picks: number; open: number; closed: number;
  win_rate: number | null; avg_r: number | null; curve: Point[]; note: string; backtest: Backtest | null;
  recent: { day: string; symbol: string; side: string; style: string; status: string; outcome: string | null; r: number | null }[] };

const KEY = "research_desk_settings_v2";  // the budget is shared with the Research desk
const usd = (v?: number | null, d = 0) => v == null ? "—" : `$${v.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })}`;
const big = (v?: number | null) => v == null ? "—" : v >= 1e9 ? `$${(v / 1e9).toFixed(1)}B` : v >= 1e6 ? `$${(v / 1e6).toFixed(1)}M` : `$${(v / 1e3).toFixed(0)}K`;
const pct = (v?: number | null, d = 1) => v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(d)}%`;
const tone = (v?: number | null) => v == null || v === 0 ? "text-slate-500" : v > 0 ? "text-up" : "text-down";

function Stat({ k, v, sub, className = "" }: { k: string; v: React.ReactNode; sub?: React.ReactNode; className?: string }) {
  return <div className={className}><div className="text-[12px] text-slate-500">{k}</div><div className="num mt-0.5 text-[15px] font-semibold text-slate-100">{v}</div>
    {sub && <div className="num text-[12px] text-slate-500">{sub}</div>}</div>;
}

function TradeRow({ p }: { p: Pick }) {
  const [open, setOpen] = useState(false);
  const long = p.side === "LONG";
  const pl = p.plan;
  const gone = !!p.live_state && ["stop_hit", "target_hit", "ran_away"].includes(p.live_state.state);
  const conflict = !!p.main?.covered && !!p.main.clear && p.main.agrees === false;
  const take = !gone && !conflict && (p.signal ? p.signal.decision === "take" : p.readiness.verdict === "ready");
  return (
    <div className="border-t border-line first:border-t-0">
      <div className="grid grid-cols-1 items-center gap-4 px-4 py-4 sm:px-6 sm:py-5 xl:grid-cols-[minmax(140px,1fr)_minmax(220px,1.5fr)_minmax(270px,1.9fr)_minmax(160px,1fr)_auto] xl:gap-6">
        <div className="flex items-center gap-3">
          <span className="num w-5 text-[13px] text-slate-500">{p.rank}</span>
          <div>
            <div className="text-[16px] font-semibold text-slate-100">{pair(p.symbol)}</div>
            <div className="mt-1 flex items-center gap-2 text-[12.5px]">
              <span className={`font-semibold ${long ? "text-up" : "text-down"}`}>{long ? "Long" : "Short"}</span>
              <span className="truncate text-slate-500">{p.strategy.replaceAll("_", " ")}</span>
            </div>
            {p.main?.covered && <div className={`mt-1 text-[11.5px] ${!p.main.clear ? "text-slate-500" : p.main.agrees ? "text-emerald-300" : "text-red-300"}`}
              title="The platform's main signal (the trained model) for this coin">
              Main signal: {!p.main.clear ? "no clear side (no check either way)" : `${p.main.side} ${p.main.agrees ? "✓ agrees" : "✗ disagrees"}`}</div>}
            {p.main && !p.main.covered && <div className="mt-1 text-[11.5px] text-slate-500" title="The main signal scores liquid coins only">Main signal: not covered</div>}
            <div className={`mt-1.5 inline-flex items-center gap-1.5 text-[12px] font-medium ${take ? "text-up" : gone ? "text-slate-400" : "text-amber-300"}`}
              title={gone ? p.live_state!.text : p.signal?.decision_why ?? (take ? "Every check is in place" : `Enter only after: ${(p.readiness.wait_for ?? []).join("; ")}`)}>
              <span className={`h-1.5 w-1.5 rounded-full ${take ? "bg-up" : gone ? "bg-slate-500" : "bg-amber-500"}`} />
              {conflict ? "Main signal disagrees — skip" : gone ? (p.live_state!.state === "stop_hit" ? "Stop already hit" : p.live_state!.state === "target_hit" ? "Target already hit" : "Moved away — too late") : take ? "Take now" : "Wait for trigger"}
            </div>
          </div>
        </div>
        <div className="grid grid-cols-3 gap-4">
          <Stat k="Entry" v={fmtPrice(p.entry)} />
          <Stat k="Stop" v={<span className="text-down">{fmtPrice(p.stop)}</span>} sub={pl.stop_pct != null ? `${pl.stop_pct}%` : undefined} />
          <Stat k="Target" v={<span className="text-up">{fmtPrice(p.target)}</span>} sub={pl.target_pct != null ? `${pl.target_pct}%` : undefined} />
        </div>
        <div className="grid grid-cols-4 gap-4">
          <Stat k="Leverage" v={`${pl.leverage}x`} sub={p.style} />
          <Stat k="Margin" v={usd(pl.margin_usd, 2)} sub={`${usd(pl.notional_usd)} position`} />
          <Stat k="If stopped" v={<span className="text-down">−{usd(pl.risk_usd, 2)}</span>} sub={`${pl.risk_pct_of_budget}% of budget`} />
          <Stat k="At target" v={<span className="text-up">+{usd(pl.reward_usd, 2)}</span>} sub={`${pl.rr_net}R`} />
        </div>
        <div className="grid grid-cols-2 gap-4">
          <Stat k="Signal" v={p.signal ? <span className={p.signal.direction === "bearish" ? "text-down" : p.signal.direction === "bullish" ? "text-up" : ""}>{p.signal.strength}</span> : p.readiness.score}
            sub={p.signal ? `${p.signal.direction} · ${Math.round(p.signal.agreement * 100)}% agree` : "readiness"} />
          <Stat k="Past win rate" v={p.odds?.win_rate != null ? `${Math.round(p.odds.win_rate * 100)}%` : "—"} sub={p.odds ? `${p.odds.n} cases` : "no history"} />
        </div>
        <div className="flex items-center gap-1">
          <button className="btn btn-ghost h-9 w-9 p-0" onClick={() => setOpen(!open)} title="Details">{open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}</button>
          <Link href={`/desk?coin=${p.symbol}`} className="btn h-9">Analyse</Link>
          <TakeSignal symbol={p.symbol} side={p.side} origin="lab" compact label="I took it" levels={{ entry: p.entry, stop: p.stop, target: p.target }} />
        </div>
      </div>
      {open && <div className="border-t border-line"><MachineBar symbol={p.symbol} tf="4h" /></div>}
      {open && p.signal && (
        <div className="border-t border-line bg-panel2 px-6 pt-5 text-[13px]">
          <div className="mb-2 flex items-baseline gap-2"><span className="font-semibold text-slate-200">Final signal</span>
            <span className="text-slate-500" title="Each part is weighted by the evidence it has earned">{p.signal.decision_why}</span></div>
          <div className="grid gap-x-8 gap-y-1.5 md:grid-cols-2">
            {p.signal.contributions.map((c) => (
              <div key={c.component} className="grid grid-cols-[96px_1fr_64px] items-center gap-3">
                <span className="capitalize text-slate-300">{c.component.replace("_", " ")}</span>
                <div className="relative h-1.5 rounded-full bg-fill" title={c.why}>
                  <span className="absolute top-0 h-1.5 rounded-full" style={{ left: c.score >= 0 ? "50%" : `${50 + c.score * 50}%`, width: `${Math.abs(c.score) * 50}%`,
                    background: c.weight > 0 ? (c.score >= 0 ? "var(--color-up)" : "var(--color-down)") : "#cbd2d9" }} />
                  <span className="absolute left-1/2 top-[-2px] h-2.5 w-px bg-slate-600" />
                </div>
                <span className="num text-right text-[12px] text-slate-500" title={c.why}>{c.weight > 0 ? `w ${c.weight.toFixed(2)}` : "no weight"}</span>
              </div>))}
          </div>
        </div>
      )}
      {open && (
        <div className="grid gap-6 bg-panel2 px-6 py-5 text-[13px] md:grid-cols-3">
          <div>
            <div className="mb-2 font-semibold text-slate-200">Why these levels</div>
            <p className="text-slate-400"><b className="font-medium text-slate-300">Stop:</b> {p.stop_why}</p>
            <p className="mt-1 text-slate-400"><b className="font-medium text-slate-300">Target:</b> {p.target_why}</p>
            <p className="mt-2 text-slate-500">{p.readiness.summary}</p>
            {p.readiness.verdict !== "ready" && (p.readiness.wait_for ?? []).length > 0 && (
              <p className="mt-2 text-amber-300"><b className="font-medium">Enter only after:</b> {(p.readiness.wait_for ?? []).join("; ")}</p>)}
          </div>
          <div>
            <div className="mb-2 font-semibold text-slate-200">Checks · {p.validation.passed} of {p.validation.total} passed</div>
            {p.validation.warnings.length ? <ul className="space-y-1 text-slate-400">{p.validation.warnings.map((w) => <li key={w}>· {w}</li>)}</ul>
              : <p className="text-slate-400">No warnings.</p>}
            <p className="mt-2 text-slate-500">Capacity {big(p.capacity_usd)} · {p.odds?.basis ?? "no past cases"}{p.odds?.avg_r != null ? ` · ${p.odds.avg_r > 0 ? "+" : ""}${p.odds.avg_r}R average` : ""}</p>
          </div>
          <div>
            <div className="mb-2 font-semibold text-slate-200">Orders on Binance</div>
            <ol className="list-decimal space-y-1 pl-4 text-slate-400">{(pl.orders ?? []).map((o) => <li key={o}>{o}</li>)}</ol>
            {pl.liquidation && <p className="mt-2 text-slate-500">Liquidation {fmtPrice(pl.liquidation)} · fees {usd(pl.fees_usd, 2)}</p>}
          </div>
        </div>
      )}
    </div>
  );
}

type Book = { trades: number | null; win_rate: number | null; expectancy_r: number | null; ci95: number[] | null; total_r: number | null;
  cagr_pct: number | null; max_drawdown_pct: number | null } | null;
type Backtest = { tf: string; decision: string | null; reason: string | null; oos_from: string | null; period: (string | null)[];
  books: Record<"history" | "oos", Record<"selected" | "take_all" | "random_5", Book>>; curve_selected: Point[]; curve_take_all: Point[] };

function BacktestPanel({ report, selector }: { report: Backtest | null; selector?: string }) {
  const [win, setWin] = useState<"history" | "oos">("oos");
  if (!report?.books) return <div className="mt-4 flex h-[200px] items-center justify-center rounded-xl bg-panel2 px-6 text-center text-[13px] text-slate-500">The historical simulation of the picking method has not been run yet.</div>;
  const rows: [string, Book][] = [["Selected 5", report.books[win].selected], ["Every setup", report.books[win].take_all], ["5 at random", report.books[win].random_5]];
  return (
    <>
      <div className="mt-3 flex items-center gap-2 text-[13px]">
        {([["oos", `Unseen data${report.oos_from ? ` (from ${report.oos_from})` : ""}`], ["history", "Full history"]] as const).map(([k, l]) => (
          <button key={k} onClick={() => setWin(k)} className={`rounded-full px-3 py-1 font-medium ${win === k ? "bg-fill text-slate-100" : "text-slate-500 hover:text-slate-200"}`}>{l}</button>))}
      </div>
      <table className="mt-3 w-full text-[13px]">
        <thead><tr className="text-left text-[12px] text-slate-500"><th className="py-1.5 font-medium">Method</th><th className="font-medium">Trades</th><th className="font-medium">Win</th><th className="font-medium">Avg R</th><th className="font-medium">Per year</th><th className="font-medium">Max DD</th></tr></thead>
        <tbody className="divide-y divide-line">
          {rows.map(([name, b]) => (
            <tr key={name} className={name === "Selected 5" ? "font-semibold text-slate-100" : "text-slate-300"}>
              <td className="py-2">{name}</td><td className="num">{b?.trades != null ? Math.round(b.trades).toLocaleString() : "—"}</td>
              <td className="num">{b?.win_rate != null ? `${Math.round(b.win_rate * 100)}%` : "—"}</td>
              <td className={`num ${tone(b?.expectancy_r)}`}>{b?.expectancy_r != null ? `${b.expectancy_r > 0 ? "+" : ""}${b.expectancy_r.toFixed(3)}` : "—"}</td>
              <td className={`num ${tone(b?.cagr_pct)}`}>{pct(b?.cagr_pct)}</td><td className="num">{b?.max_drawdown_pct != null ? `${b.max_drawdown_pct}%` : "—"}</td>
            </tr>))}
        </tbody>
      </table>
      <div className="mt-4"><div className="mb-1 flex gap-4 text-[12px] text-slate-500"><span>Equity at 1% risk per trade · selected 5</span></div>
        <AreaSpark data={report.curve_selected} height={90} fmt={(x) => x.toFixed(2)} color="#5d6c7b" /></div>
      <p className="mt-3 text-[12.5px] leading-relaxed text-slate-500">
        {report.decision === "kept_incumbent" ? "No method has beaten the baselines on unseen data yet. " : ""}
        Ranking today: {selector}.</p>
    </>
  );
}

/** Lab: the 4h setup screener's 5 trades a day and its record (its live paper trades next to its backtest). Not the
 *  main signal (that is the unified engine on /signal). */
export default function Dashboard() {
  const [budget, setBudget] = useState(1000);
  const [budgetText, setBudgetText] = useState("1000");
  const [d, setD] = useState<Daily | null>(null);
  const [sb, setSb] = useState<Sandbox | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    try { const v = JSON.parse(localStorage.getItem(KEY) || "{}"); if (v.budget) { setBudget(v.budget); setBudgetText(String(v.budget)); } } catch {}
  }, []);
  const saveBudget = (txt: string) => {
    setBudgetText(txt);
    const v = +txt;
    if (v >= 10 && v <= 1_000_000) {
      setBudget(v);
      try { const s = JSON.parse(localStorage.getItem(KEY) || "{}"); localStorage.setItem(KEY, JSON.stringify({ ...s, budget: v })); } catch {}
    }
  };
  const load = useCallback((refresh = false) => {
    setBusy(true);
    Promise.all([
      api<Daily>(`/agent/daily?tf=4h&budget=${budget}${refresh ? "&refresh=true" : ""}`).then(setD),
      api<Sandbox>("/agent/sandbox").then(setSb),
    ]).then(() => setErr("")).catch((e) => setErr(e.message)).finally(() => setBusy(false));
  }, [budget]);
  useEffect(() => { load(); }, [load]);


  return (
    <Page>
        <PageHead title="4h setups"
          sub={d ? <span>{new Date(d.made_at).toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" })} · {d.pool} coins screened · {d.with_setup} setups · {d.picks.length} picks</span> : "Screening the market…"}>
          <label className="flex items-center gap-2 text-[13px] text-slate-500">Budget
            <span className="relative"><span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-500">$</span>
              <input className="input num w-28 pl-6" type="number" min={10} value={budgetText} onChange={(e) => saveBudget(e.target.value)} /></span>
          </label>
          <button className="btn btn-ghost h-9 w-9 p-0" onClick={() => load(true)} disabled={busy} title="Pick again now"><RefreshCw size={15} className={busy ? "animate-spin" : ""} /></button>
        </PageHead>
        <LabBanner what="A code screener: up to 5 chart setups a day on the 4h chart." />
        {err && <Note tone="bad">{err}</Note>}

        <section className="overflow-hidden rounded-2xl border border-line bg-panel">
          {!d && <div className="py-20 text-center text-[13px] text-slate-500">Screening the market and validating setups…</div>}
          {d && !d.picks.length && <div className="py-20 text-center text-[14px] text-slate-400">No trade passes today. Waiting is the position.</div>}
          {d?.picks.map((p) => <TradeRow key={p.symbol} p={p} />)}
          {d && (
            <div className="flex flex-wrap gap-x-6 gap-y-1 border-t border-line bg-panel2 px-6 py-3 text-[12.5px] text-slate-500">
              <span>Risk: 1% of the budget, 2% for the strongest setups.</span>
              {d.dropped.length > 0 && <span title={d.dropped.map((x) => `${pair(x.symbol)}: ${x.why}`).join("\n")}>{d.dropped.length} more dropped — see More details.</span>}
              <span className="ml-auto">Paper only · you place the orders</span>
            </div>
          )}
        </section>

        <div className="grid gap-6 lg:grid-cols-2">
          <Card title="Live paper record" sub="Every setup, paper-traded live.">
            <div className="grid grid-cols-4 gap-4">
              <Stat k="Return" v={<span className={tone(sb?.return_pct)}>{pct(sb?.return_pct, 2)}</span>} sub={sb ? usd(sb.equity) : undefined} />
              <Stat k="Win rate" v={sb?.win_rate != null ? `${Math.round(sb.win_rate * 100)}%` : "—"} sub={sb ? `${sb.closed} closed · ${sb.open} open` : undefined} />
              <Stat k="Avg R" v={sb?.avg_r != null ? `${sb.avg_r > 0 ? "+" : ""}${sb.avg_r}` : "—"} />
              <Stat k="Max drawdown" v={sb ? `${sb.max_drawdown_pct}%` : "—"} />
            </div>
            <div className="mt-4">{sb && sb.curve.length > 1 ? <AreaSpark data={sb.curve} height={110} fmt={(x) => usd(x)} />
              : <div className="flex h-[110px] items-center justify-center rounded-xl bg-panel2 text-[13px] text-slate-500">{sb?.note ?? "…"}</div>}</div>
            {sb && sb.recent.length > 0 && (
              <div className="mt-4 divide-y divide-line text-[13px]">
                {sb.recent.slice(0, 6).map((r, i) => (
                  <div key={i} className="flex items-center gap-3 py-2">
                    <span className="num w-14 text-slate-500">{r.day.slice(5)}</span><span className="w-24 font-medium text-slate-200">{pair(r.symbol)}</span>
                    <span className={r.side === "LONG" ? "text-up" : "text-down"}>{r.side === "LONG" ? "Long" : "Short"}</span>
                    <span className="ml-auto text-slate-400">{r.status === "open" ? "open" : r.outcome}</span>
                    <span className={`num w-14 text-right font-medium ${tone(r.r)}`}>{r.r != null ? `${r.r > 0 ? "+" : ""}${r.r}R` : ""}</span>
                  </div>))}
              </div>
            )}
          </Card>
          <Card title="Backtest" sub="The same picking method on past data.">
            <BacktestPanel report={sb?.backtest ?? null} selector={d?.selector} />
          </Card>
        </div>
        {d && d.dropped.length > 0 && (
          <More>
            <Card title="Not traded today" sub="Ruled out by the checks.">
              <div className="grid grid-cols-1 gap-x-8 gap-y-1 text-[13px] md:grid-cols-2">
                {d.dropped.map((x) => (
                  <div key={x.symbol} className="flex min-w-0 gap-3"><span className="w-24 shrink-0 font-medium text-slate-300">{pair(x.symbol)}</span><span className="truncate text-slate-500" title={x.why}>{x.why}</span></div>))}
              </div>
            </Card>
          </More>
        )}
    </Page>
  );
}
