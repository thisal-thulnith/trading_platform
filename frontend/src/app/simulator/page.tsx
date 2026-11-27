"use client";
import { Play } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Area, ComposedChart, Legend, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import CarryBooks from "@/components/sim/CarryBooks";
import Compare from "@/components/sim/Compare";
import { api } from "@/lib/api";
import { pair } from "@/lib/format";
import { LabBanner, More, Page, PageHead } from "@/components/ui/Page";

type Stats = { return_pct?: number; cagr_pct?: number; sharpe?: number | null; max_dd_pct?: number; days?: number; by_year?: Record<string, number | null> };
type MC = { days: number; paths: number; step: number; bands: Record<"p5" | "p25" | "p50" | "p75" | "p95", number[]>; p_loss: number; p_dd30: number; p_ruin: number; median_return_pct: number } | null;
type Trade = { side: string; entry: string; exit: string | null; entry_px: number; exit_px: number; lev: number; why: string; ret_pct: number; cost: number; funding: number; days: number };
type Coin = {
  symbol: string; from: string; to: string; weights: Record<string, number>; notes: string[];
  sim: Stats & { trades: number; win_rate?: number; avg_trade_pct?: number; profit_factor?: number | null; stops?: number; liquidations?: number;
    costs: number; funding: number; ruined: boolean; exposure_pct: number };
  hold: Stats; test: { p: number | null; obs: number | null; null_mean: number | null; verdict: string }; mc: MC;
  today: { f: number | null; components: Record<string, number | null>; position: number };
  curve: { t: number; sim: number; hold: number | null; f: number | null; pos: number }[]; trades: Trade[];
};
type Row = { symbol: string; from: string; to: string; return_pct: number; hold_pct: number | null; excess_pct: number | null; sharpe: number | null;
  hold_sharpe: number | null; max_dd_pct: number; trades: number; win_rate: number | null; ruined: boolean; p: number | null; fdr: boolean; position: number; f: number | null };
type All = {
  from: string; to: string; weights: Record<string, number>; notes: string[]; generated: string; signs: Record<string, Record<string, number>>;
  summary: { coins: number; tested: number; p05: number; expected_by_luck: number; fdr_pass: number; beat_hold: number; median_excess_pct: number | null;
    median_sharpe: number | null; ruined: number; holding_now: number };
  pooled: Stats & { avg_coins_held: number }; ew_hold: Stats; mc: MC; rows: Row[];
  curve: { t: number; sim: number; hold: number; n: number }[];
  config: Cfg;
};
type Cfg = { start: string; entry: number; exit: number; shorts: boolean; leverage: number; stop_atr: number | null; weights: "live" | "prior"; take_profit: number | null };

const C = { accent: "#0064e0", up: "#1e9e5a", down: "#e0413b", muted: "#9aa7b3", band: "#0064e0" };
const sgn = (v?: number | null, d = 1) => v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(d)}`;
const pct = (v?: number | null) => v == null ? "—" : `${sgn(v)}%`;
const tone = (v?: number | null) => v == null || v === 0 ? "text-slate-400" : v > 0 ? "text-up" : "text-down";
const usd = (v: number): string => v < 0 ? `−${usd(-v)}` : v >= 1e6 ? `$${(v / 1e6).toFixed(1)}M` : v >= 1e4 ? `$${(v / 1e3).toFixed(0)}k` : `$${Math.round(v).toLocaleString()}`;
const day = (t: number) => new Date(t).toLocaleDateString([], { year: "2-digit", month: "short" });
const LABEL: Record<string, string> = { momentum: "Momentum", carry: "Carry (funding)", regime: "BTC regime", order_flow: "Order flow", quant_factors: "Factor composite" };
const DEFAULT: Cfg = { start: "2021-01-01", entry: 0.1, exit: 0, shorts: true, leverage: 1, stop_atr: 3, weights: "live", take_profit: null };

function Stat({ k, v, sub, cls }: { k: string; v: React.ReactNode; sub?: React.ReactNode; cls?: string }) {
  return (
    <div className="min-w-0">
      <div className="text-[12.5px] text-slate-500">{k}</div>
      <div className={`num text-[20px] font-semibold ${cls ?? "text-slate-100"}`}>{v}</div>
      {sub != null && <div className="text-[12px] leading-snug text-slate-500">{sub}</div>}
    </div>
  );
}

function EquityChart({ data, a, b }: { data: { t: number; sim: number; hold: number | null }[]; a: string; b: string }) {
  const positive = data.every((d) => d.sim > 0 && (d.hold == null || d.hold > 0));
  return (
    <ResponsiveContainer width="100%" height={260}>
      <ComposedChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
        <XAxis dataKey="t" tickFormatter={day} tick={{ fontSize: 11, fill: C.muted }} axisLine={false} tickLine={false} minTickGap={40} />
        <YAxis scale={positive ? "log" : "auto"} domain={["auto", "auto"]} tickFormatter={usd} tick={{ fontSize: 11, fill: C.muted }} axisLine={false} tickLine={false} width={56} allowDataOverflow />
        <Tooltip formatter={(v: number) => usd(v)} labelFormatter={(t: number) => new Date(t).toLocaleDateString()} contentStyle={{ fontSize: 12, borderRadius: 10 }} />
        <Legend iconType="plainline" wrapperStyle={{ fontSize: 12 }} />
        <Line name={a} dataKey="sim" stroke={C.accent} strokeWidth={1.75} dot={false} isAnimationActive={false} />
        <Line name={b} dataKey="hold" stroke={C.muted} strokeWidth={1.25} dot={false} isAnimationActive={false} connectNulls />
      </ComposedChart>
    </ResponsiveContainer>
  );
}

function MonteCarlo({ mc }: { mc: MC }) {
  if (!mc) return <p className="text-[13px] text-slate-500">Not enough simulated days to bootstrap.</p>;
  const data = mc.bands.p50.map((_, i) => ({ d: i * mc.step, outer: [mc.bands.p5[i], mc.bands.p95[i]], inner: [mc.bands.p25[i], mc.bands.p75[i]], mid: mc.bands.p50[i] }));
  return (
    <>
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <Stat k="Median after a year" v={pct(mc.median_return_pct)} cls={tone(mc.median_return_pct)} />
        <Stat k="Chance of a loss" v={`${mc.p_loss}%`} />
        <Stat k="Chance of a 30% drawdown" v={`${mc.p_dd30}%`} />
        <Stat k="Chance of losing 95%" v={`${mc.p_ruin}%`} />
      </div>
      <ResponsiveContainer width="100%" height={200}>
        <ComposedChart data={data} margin={{ top: 12, right: 8, bottom: 0, left: 0 }}>
          <XAxis dataKey="d" type="number" domain={[0, mc.days]} ticks={[0, 91, 182, 273, 365]} tickFormatter={(d: number) => d ? `${Math.round(d / 30.4)} mo` : "now"} tick={{ fontSize: 11, fill: C.muted }} axisLine={false} tickLine={false} />
          <YAxis tickFormatter={usd} tick={{ fontSize: 11, fill: C.muted }} axisLine={false} tickLine={false} width={56} domain={["auto", "auto"]} />
          <Tooltip formatter={(v: number | number[]) => Array.isArray(v) ? `${usd(v[0])} – ${usd(v[1])}` : usd(v)} labelFormatter={(d: number) => `day ${d}`} contentStyle={{ fontSize: 12, borderRadius: 10 }} />
          <Area name="5–95%" dataKey="outer" stroke="none" fill={C.band} fillOpacity={0.08} isAnimationActive={false} />
          <Area name="25–75%" dataKey="inner" stroke="none" fill={C.band} fillOpacity={0.16} isAnimationActive={false} />
          <Line name="median" dataKey="mid" stroke={C.accent} strokeWidth={1.75} dot={false} isAnimationActive={false} />
        </ComposedChart>
      </ResponsiveContainer>
      <p className="text-[12.5px] text-slate-500">{mc.paths.toLocaleString()} one-year futures resampled in 10-day blocks from the simulated daily returns, starting from $10,000.</p>
    </>
  );
}

function Years({ a, b, la, lb }: { a?: Record<string, number | null>; b?: Record<string, number | null>; la: string; lb: string }) {
  const ys = Object.keys(a ?? {});
  if (!ys.length) return null;
  return (
    <table className="w-full text-[13px]">
      <thead><tr className="text-left text-slate-500"><th className="py-1 font-medium">Year</th>{ys.map((y) => <th key={y} className="py-1 text-right font-medium">{y}</th>)}</tr></thead>
      <tbody>
        {[[la, a], [lb, b]].map(([k, v]) => (
          <tr key={k as string} className="border-t border-line">
            <td className="py-1.5 text-slate-300">{k as string}</td>
            {ys.map((y) => { const x = (v as Record<string, number | null> | undefined)?.[y]; return <td key={y} className={`num py-1.5 text-right ${tone(x)}`}>{pct(x)}</td>; })}
          </tr>))}
      </tbody>
    </table>
  );
}

function Section({ title, sub, children }: { title: string; sub?: string; children: React.ReactNode }) {
  return (
    <section className="card p-5">
      <div className="mb-3 flex flex-wrap items-baseline gap-2"><h2 className="text-[15px] font-semibold">{title}</h2>{sub && <span className="text-[12.5px] text-slate-500">{sub}</span>}</div>
      {children}
    </section>
  );
}

function CoinResult({ r }: { r: Coin }) {
  const s = r.sim;
  const verdictTone = r.test.p != null && r.test.p < 0.05 ? "text-up" : "text-slate-300";
  return (
    <div className="space-y-4">
      <section className="card p-5">
        <div className="mb-4 flex flex-wrap items-baseline gap-2"><h2 className="text-[17px] font-semibold">{pair(r.symbol)}</h2>
          <span className="text-[13px] text-slate-500">{r.from} → {r.to} · $10,000 start</span>
          {s.ruined && <span className="chip bg-down/10 text-down">liquidated — run ended</span>}</div>
        <div className="grid grid-cols-2 gap-5 md:grid-cols-6">
          <Stat k="Simulated" v={pct(s.return_pct)} cls={tone(s.return_pct)} sub={`${pct(s.cagr_pct)} a year`} />
          <Stat k="Buy and hold" v={pct(r.hold.return_pct)} cls={tone(r.hold.return_pct)} sub={`${pct(r.hold.cagr_pct)} a year`} />
          <Stat k="Sharpe" v={s.sharpe ?? "—"} sub={`hold ${r.hold.sharpe ?? "—"}`} />
          <Stat k="Max drawdown" v={`${s.max_dd_pct}%`} sub={`hold ${r.hold.max_dd_pct ?? "—"}%`} />
          <Stat k="Trades" v={s.trades} sub={`${s.win_rate ?? "—"}% won · ${s.exposure_pct}% of days in a trade`} />
          <Stat k="Costs + funding" v={usd(s.costs + s.funding)} sub={`${usd(s.costs)} fees · ${usd(s.funding)} funding`} />
        </div>
        <div className="mt-5"><EquityChart data={r.curve} a="Simulated" b="Buy and hold" /></div>
        <div className="mt-4"><Years a={s.by_year} b={r.hold.by_year} la="Simulated" lb="Buy and hold" /></div>
      </section>
      <div className="grid gap-4 lg:grid-cols-2">
        <Section title="Test against luck" sub="the same trades placed at random times, 200 times">
          <div className={`text-[15px] font-semibold ${verdictTone}`}>{r.test.verdict}</div>
          <div className="mt-3 grid grid-cols-3 gap-4">
            <Stat k="p-value" v={r.test.p != null ? r.test.p.toFixed(3) : "—"} sub="share of random books as good" />
            <Stat k="Daily edge" v={r.test.obs != null ? `${(r.test.obs * 1e4).toFixed(1)} bp` : "—"} sub="after costs" />
            <Stat k="Random timing" v={r.test.null_mean != null ? `${(r.test.null_mean * 1e4).toFixed(1)} bp` : "—"} sub="average" />
          </div>
        </Section>
        <Section title="Signal today" sub={`fused score ${sgn(r.today.f, 2)} · ${r.today.position > 0 ? "long" : r.today.position < 0 ? "short" : "flat"}`}>
          {Object.entries(r.today.components).map(([k, v]) => (
            <div key={k} className="grid grid-cols-[130px_1fr_120px] items-center gap-3 py-1 text-[13px]">
              <span className="text-slate-300">{LABEL[k] ?? k}</span>
              <div className="relative h-1.5 rounded-full bg-fill">
                {v != null && <span className="absolute top-0 h-1.5 rounded-full" style={{ left: v >= 0 ? "50%" : `${50 + v * 50}%`, width: `${Math.abs(v) * 50}%`, background: v >= 0 ? C.up : C.down }} />}
                <span className="absolute left-1/2 top-[-2px] h-2.5 w-px bg-slate-600" />
              </div>
              <span className="num whitespace-nowrap text-right text-slate-500">{v == null ? "no data" : `${sgn(v, 2)} · w ${(r.weights[k] ?? 0).toFixed(2)}`}</span>
            </div>))}
        </Section>
      </div>
      <More title="More details: next-year simulation and every trade">
      <Section title="Monte Carlo — the next year" sub="if the future looks like this coin's simulated past"><MonteCarlo mc={r.mc} /></Section>
      <Section title="Trades" sub={`${r.trades.length} shown, newest first${s.stops ? ` · ${s.stops} stopped` : ""}${s.liquidations ? ` · ${s.liquidations} liquidated` : ""}`}>
        <div className="max-h-[420px] overflow-auto">
          <table className="w-full text-[13px]">
            <thead className="sticky top-0 bg-panel"><tr className="text-left text-slate-500">
              {["Side", "Entry", "Exit", "Days", "Entry price", "Exit price", "Lev", "Exit reason", "Result", "Fees", "Funding"].map((h, i) => <th key={h} className={`py-1.5 font-medium ${i > 2 ? "text-right" : ""}`}>{h}</th>)}</tr></thead>
            <tbody>
              {r.trades.map((t, i) => (
                <tr key={i} className="border-t border-line">
                  <td className={`py-1.5 capitalize ${t.side === "long" ? "text-up" : "text-down"}`}>{t.side}</td>
                  <td className="py-1.5 text-slate-300">{t.entry}</td><td className="py-1.5 text-slate-300">{t.exit ?? "open"}</td>
                  <td className="num py-1.5 text-right">{t.days}</td>
                  <td className="num py-1.5 text-right">{t.entry_px.toPrecision(5)}</td><td className="num py-1.5 text-right">{t.exit_px.toPrecision(5)}</td>
                  <td className="num py-1.5 text-right">{t.lev}x</td><td className="py-1.5 text-right text-slate-400">{t.why}</td>
                  <td className={`num py-1.5 text-right font-medium ${tone(t.ret_pct)}`}>{pct(t.ret_pct)}</td>
                  <td className="num py-1.5 text-right text-slate-500">{usd(t.cost)}</td><td className="num py-1.5 text-right text-slate-500">{usd(t.funding)}</td>
                </tr>))}
            </tbody>
          </table>
        </div>
      </Section>
      </More>
    </div>
  );
}

type SortKey = "p" | "return_pct" | "excess_pct" | "sharpe" | "max_dd_pct" | "trades";

function AllResult({ r, open }: { r: All; open: (s: string) => void }) {
  const s = r.summary;
  const [sort, setSort] = useState<SortKey>("p");
  const [q, setQ] = useState("");
  const rows = useMemo(() => {
    const dir = sort === "p" || sort === "max_dd_pct" ? 1 : -1;
    return r.rows.filter((x) => !q || x.symbol.includes(q.toUpperCase()))
      .sort((a, b) => ((a[sort] ?? (dir > 0 ? 9e9 : -9e9)) - (b[sort] ?? (dir > 0 ? 9e9 : -9e9))) * dir);
  }, [r.rows, sort, q]);
  const luck = s.p05 <= s.expected_by_luck * 1.5 && s.fdr_pass === 0;
  return (
    <div className="space-y-4">
      <section className="card p-5">
        <div className="mb-4 flex flex-wrap items-baseline gap-2"><h2 className="text-[17px] font-semibold">Every coin</h2>
          <span className="text-[13px] text-slate-500">{s.coins} coins (delisted ones included) · {r.from} → {r.to} · run {r.generated.slice(0, 10)}</span></div>
        <div className="grid grid-cols-2 gap-5 md:grid-cols-6">
          <Stat k="Pooled book" v={pct(r.pooled.return_pct)} cls={tone(r.pooled.return_pct)} sub={`Sharpe ${r.pooled.sharpe ?? "—"} · DD ${r.pooled.max_dd_pct}%`} />
          <Stat k="Hold every coin" v={pct(r.ew_hold.return_pct)} cls={tone(r.ew_hold.return_pct)} sub={`Sharpe ${r.ew_hold.sharpe ?? "—"} · DD ${r.ew_hold.max_dd_pct}%`} />
          <Stat k="Beat buy-and-hold" v={`${s.beat_hold} / ${s.coins}`} sub={`median ${pct(s.median_excess_pct)}`} />
          <Stat k="Median Sharpe" v={s.median_sharpe != null ? s.median_sharpe.toFixed(2) : "—"} sub="per coin" />
          <Stat k="Beat random timing" v={`${s.p05}`} sub={`p < 0.05 · ${s.expected_by_luck} expected by luck`} />
          <Stat k="Pass correction" v={`${s.fdr_pass}`} cls={s.fdr_pass ? "text-up" : "text-slate-100"} sub={`10% false discovery · ${s.ruined} liquidated`} />
        </div>
        <p className={`mt-4 rounded-xl px-4 py-3 text-[13px] ${luck ? "bg-panel2 text-slate-300" : "bg-up/10 text-slate-200"}`}>
          {luck ? `About as many coins beat random timing as luck alone would give (${s.p05} vs ${s.expected_by_luck}), and none pass the correction: the timing is not proven. Beating buy-and-hold mostly reflects how many coins fell.`
            : `${s.fdr_pass} coins beat random timing after the correction for testing ${s.tested} coins.`}
        </p>
        <div className="mt-5"><EquityChart data={r.curve} a="Pooled book (equal capital across the coins held)" b="Hold every tradeable coin" /></div>
        <div className="mt-4"><Years a={r.pooled.by_year} b={r.ew_hold.by_year} la="Pooled book" lb="Hold every coin" /></div>
      </section>
      <Section title="Coins" sub="click a coin to simulate it in detail">
        <input className="input mb-3 w-full max-w-[240px]" placeholder="Filter coins" value={q} onChange={(e) => setQ(e.target.value)} />
        <div className="max-h-[560px] overflow-auto">
          <table className="w-full text-[13px]">
            <thead className="sticky top-0 bg-panel"><tr className="text-left text-slate-500">
              <th className="py-1.5 font-medium">Coin</th>
              {([["return_pct", "Simulated"], ["excess_pct", "vs hold"], ["sharpe", "Sharpe"], ["max_dd_pct", "Max DD"], ["trades", "Trades"], ["p", "p-value"]] as [SortKey, string][]).map(([k, l]) => (
                <th key={k} className="py-1.5 text-right font-medium"><button onClick={() => setSort(k)} className={sort === k ? "text-slate-100" : "hover:text-slate-200"}>{l}</button></th>))}
              <th className="py-1.5 text-right font-medium">Now</th></tr></thead>
            <tbody>
              {rows.map((x) => (
                <tr key={x.symbol} onClick={() => open(x.symbol)} className="cursor-pointer border-t border-line hover:bg-panel2">
                  <td className="py-1.5 font-medium text-slate-200">{pair(x.symbol)}{x.ruined && <span className="ml-2 text-[11px] text-down">liquidated</span>}{Date.parse(r.to) - Date.parse(x.to) > 5 * 864e5 && <span className="ml-2 text-[11px] text-slate-500">delisted {x.to}</span>}</td>
                  <td className={`num py-1.5 text-right ${tone(x.return_pct)}`}>{pct(x.return_pct)}</td>
                  <td className={`num py-1.5 text-right ${tone(x.excess_pct)}`}>{pct(x.excess_pct)}</td>
                  <td className="num py-1.5 text-right">{x.sharpe ?? "—"}</td>
                  <td className="num py-1.5 text-right text-slate-400">{x.max_dd_pct}%</td>
                  <td className="num py-1.5 text-right text-slate-400">{x.trades}</td>
                  <td className={`num py-1.5 text-right ${x.fdr ? "font-semibold text-up" : x.p != null && x.p < 0.05 ? "text-slate-200" : "text-slate-500"}`}>{x.p != null ? x.p.toFixed(3) : "—"}</td>
                  <td className={`py-1.5 text-right ${x.position > 0 ? "text-up" : x.position < 0 ? "text-down" : "text-slate-500"}`}>{x.position > 0 ? "long" : x.position < 0 ? "short" : "flat"}</td>
                </tr>))}
            </tbody>
          </table>
        </div>
      </Section>
      <More title="More details: next-year simulation and factor choice">
      <Section title="Monte Carlo — the pooled book's next year"><MonteCarlo mc={r.mc} /></Section>
      <Section title="Factor composite, chosen each year from earlier data only">
        <div className="grid gap-2 text-[12.5px] md:grid-cols-2">
          {Object.entries(r.signs).map(([y, sg]) => (
            <div key={y}><span className="font-medium text-slate-300">{y}</span> <span className="text-slate-500">{Object.keys(sg).length ? Object.entries(sg).map(([f, v]) => `${v > 0 ? "+" : "−"}${f}`).join(", ") : "no factor passed yet"}</span></div>))}
        </div>
      </Section>
      </More>
    </div>
  );
}

/** Replay the platform's systematic signal on any coin, or on every coin, then test it against luck and simulate its future. */
export default function SimulatorPage() {
  const [coins, setCoins] = useState<string[]>([]);
  const [symbol, setSymbol] = useState("ALL");
  const [cfg, setCfg] = useState<Cfg>(DEFAULT);
  const [coin, setCoin] = useState<Coin | null>(null);
  const [all, setAll] = useState<All | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState("");
  const [tab, setTab] = useState<"sim" | "compare" | "carry">("sim");
  const set = <K extends keyof Cfg>(k: K, v: Cfg[K]) => setCfg((c) => ({ ...c, [k]: v }));

  useEffect(() => {
    api<{ coins: { symbol: string }[] }>("/sim/coins").then((d) => setCoins(d.coins.map((c) => c.symbol))).catch(() => undefined);
    api<{ result: All | null }>("/sim/latest").then((d) => d.result && setAll(d.result)).catch(() => undefined);
    const qs = new URLSearchParams(window.location.search);
    const tq = qs.get("tab");
    if (tq === "compare" || tq === "carry") setTab(tq);
    const c = qs.get("coin");  // /simulator?coin=SOLUSDT opens that coin
    if (c) { setSymbol(c.toUpperCase()); run(c.toUpperCase()); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function run(sym = symbol, c = cfg) {
    setErr(""); setBusy("Simulating…");
    const s = sym.trim().toUpperCase() || "ALL";
    try {
      const r = await api<{ status: string; result?: Coin | All; job?: string }>("/sim/run", { method: "POST", json: { symbol: s, ...c } });
      if (s !== "ALL") { setCoin(r.result as Coin); setBusy(null); window.scrollTo({ top: 0 }); return; }
      setCoin(null);
      if (r.result) { setAll(r.result as All); setBusy(null); return; }
      for (;;) {
        await new Promise((res) => setTimeout(res, 1000));
        const j = await api<{ status: string; progress: number; result: All | null; error: string | null }>(`/sim/job/${r.job}`);
        if (j.status === "done" && j.result) { setAll(j.result); break; }
        if (j.status === "error") throw new Error(j.error ?? "simulation failed");
        setBusy(`Simulating every coin… ${Math.round(j.progress * 100)}%`);
      }
      setBusy(null);
    } catch (e) { setErr((e as Error).message); setBusy(null); }
  }

  const openCoin = (s: string) => { setSymbol(s); run(s); };

  return (
    <Page>
        <PageHead title="Simulator" sub={`Any coin, or all ${coins.length || 591} · real costs, funding and liquidation · tested against luck`} />
        <LabBanner what="Replays the quant factor ranking (momentum, trend, funding) on any coin." />

        <div className="flex gap-1">
          {([["sim", "Simulate"], ["compare", "vs competitors"], ["carry", "Steady: carry"]] as const).map(([k, l]) => (
            <button key={k} onClick={() => setTab(k)} className={`rounded-full px-3.5 py-1.5 text-[13px] font-medium ${tab === k ? "bg-accent/10 text-accent" : "text-slate-400 hover:bg-fill"}`}>{l}</button>))}
        </div>

        {tab === "compare" ? <Compare /> : tab === "carry" ? <CarryBooks /> : (<>
        <section className="card flex flex-wrap items-end gap-3 p-4">
          <label className="text-[12px] text-slate-500">Coin
            <input className="input mt-1 block w-[170px]" list="sim-coins" value={symbol} onChange={(e) => setSymbol(e.target.value)} onFocus={(e) => e.target.select()} />
            <datalist id="sim-coins"><option value="ALL">Every coin</option>{coins.map((s) => <option key={s} value={s} />)}</datalist>
          </label>
          <label className="text-[12px] text-slate-500">From
            <select className="input mt-1 block" value={cfg.start} onChange={(e) => set("start", e.target.value)}>
              {["2020", "2021", "2022", "2023", "2024", "2025"].map((y) => <option key={y} value={`${y}-01-01`}>{y}</option>)}</select>
          </label>
          <label className="text-[12px] text-slate-500" title="Open a trade when the fused score is at least this far from zero">Entry
            <select className="input mt-1 block" value={cfg.entry} onChange={(e) => set("entry", +e.target.value)}>
              {[0.05, 0.1, 0.2, 0.3, 0.4].map((v) => <option key={v} value={v}>±{v.toFixed(2)}</option>)}</select>
          </label>
          <label className="text-[12px] text-slate-500">Leverage
            <select className="input mt-1 block" value={cfg.leverage} onChange={(e) => set("leverage", +e.target.value)}>
              {[1, 2, 3, 5].map((v) => <option key={v} value={v}>{v}x</option>)}</select>
          </label>
          <label className="text-[12px] text-slate-500">Stop
            <select className="input mt-1 block" value={cfg.stop_atr ?? 0} onChange={(e) => set("stop_atr", +e.target.value || null)}>
              <option value={0}>None</option>{[2, 3, 4, 6].map((v) => <option key={v} value={v}>{v} × ATR</option>)}</select>
          </label>
          <label className="text-[12px] text-slate-500" title="A close take profit raises the win rate and lowers the average win — watch the return, not the win rate">Take profit
            <select className="input mt-1 block" value={cfg.take_profit ?? 0} onChange={(e) => set("take_profit", +e.target.value || null)}>
              <option value={0}>Signal exit</option>{[0.02, 0.03, 0.05, 0.1, 0.2].map((v) => <option key={v} value={v}>+{v * 100}%</option>)}</select>
          </label>
          <label className="text-[12px] text-slate-500">Sides
            <select className="input mt-1 block" value={cfg.shorts ? "both" : "long"} onChange={(e) => set("shorts", e.target.value === "both")}>
              <option value="both">Long and short</option><option value="long">Long only</option></select>
          </label>
          <label className="text-[12px] text-slate-500" title="Live: today's evidence weights (the factor composite earned its weight on this history, so partly in-sample). Prior: literature priors only.">Weights
            <select className="input mt-1 block" value={cfg.weights} onChange={(e) => set("weights", e.target.value as Cfg["weights"])}>
              <option value="live">Live (today)</option><option value="prior">Prior only</option></select>
          </label>
          <button className="btn btn-primary" disabled={!!busy} onClick={() => run()}><Play size={15} />{busy ?? "Run"}</button>
          {(symbol.toUpperCase() !== "ALL" || coin) && <button className="btn btn-ghost" disabled={!!busy} onClick={() => { setSymbol("ALL"); setCoin(null); }}>Back to every coin</button>}
        </section>

        {err && <div className="card p-4 text-[13px] text-down">{err}</div>}
        {coin ? <CoinResult r={coin} /> : all ? <AllResult r={all} open={openCoin} />
          : !busy && <div className="card p-8 text-center text-[13.5px] text-slate-500">Pick a coin, or keep “ALL” to simulate every coin, and press Run.</div>}

        {(coin ?? all) && (
          <More title="How the simulation works">
            <Section title="Method and costs">
              <ul className="list-disc space-y-1 pl-5 text-[12.5px] text-slate-500">{(coin ?? all)!.notes.map((n) => <li key={n}>{n}</li>)}</ul>
            </Section>
          </More>)}
        </>)}
    </Page>
  );
}
