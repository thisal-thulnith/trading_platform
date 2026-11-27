"use client";
import { RefreshCw } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "@/lib/api";

type Yr = Record<string, number | null>;
type Row = { key: string; name: string; sold_by: string; sides: string; return_pct: number | null; cagr_pct: number | null; sharpe: number | null;
  max_dd_pct: number | null; by_year: Yr; beat_hold_pct?: number | null; median_sharpe?: number | null; p05?: number; expected_by_luck?: number;
  fdr_pass?: number; trades?: number; win_rate?: number | null; avg_trade_pct?: number | null; day_hit_rate?: number | null; curve: { t: number; v: number }[] };
type Res = { from: string; to: string; rows: Row[]; indices: Row[]; hold_all: Omit<Row, "key" | "name" | "sold_by" | "sides" | "curve">; generated: string; notes: string[] };

const PALETTE = ["#0064e0", "#1e9e5a", "#e0413b", "#b26b00", "#7a4cc2", "#0b8a8a", "#5d6c7b", "#c2185b"];
const pct = (v?: number | null) => v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(1)}%`;
const tone = (v?: number | null) => v == null || v === 0 ? "text-slate-400" : v > 0 ? "text-up" : "text-down";
const usd = (v: number) => v >= 1e6 ? `$${(v / 1e6).toFixed(1)}M` : v >= 1e4 ? `$${(v / 1e3).toFixed(0)}k` : `$${Math.round(v).toLocaleString()}`;
/** Compounded return from 2022 on: everything after the 2021 bull market that flatters any long book. */
const after2021 = (y?: Yr) => {
  const ks = Object.keys(y ?? {}).filter((k) => +k >= 2022);
  if (!ks.length) return null;
  return (ks.reduce((a, k) => a * (1 + (y![k] ?? 0) / 100), 1) - 1) * 100;
};
const ours = (r: Row) => r.key.startsWith("ours") || r.key === "composite_index";

/** The platform's signal against the strategies competitors' bots and charts sell, on every coin, with the same rules. */
export default function Compare() {
  const [d, setD] = useState<Res | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [shown, setShown] = useState<string[]>(["ours_live_ls", "ema_cross_ls", "supertrend_ls", "dca_bot_l", "rsi_l"]);
  const load = () => api<{ result: Res | null }>("/sim/compare").then((x) => setD(x.result)).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);

  async function rerun() {
    setBusy(true); setErr("");
    try {
      await api("/sim/compare", { method: "POST" });
      for (;;) {
        await new Promise((r) => setTimeout(r, 2000));
        const x = await api<{ result: Res | null; job: { status: string; error: string | null } }>("/sim/compare");
        if (x.job.status === "done") { setD(x.result); break; }
        if (x.job.status === "error") throw new Error(x.job.error ?? "failed");
      }
    } catch (e) { setErr((e as Error).message); }
    setBusy(false);
  }

  const all = useMemo(() => d ? [...d.rows, ...d.indices] : [], [d]);
  const chart = useMemo(() => {
    const sel = all.filter((r) => shown.includes(r.key));
    const byT = new Map<number, Record<string, number>>();
    sel.forEach((r) => r.curve.forEach((p) => { const o = byT.get(p.t) ?? { t: p.t }; o[r.key] = p.v; byT.set(p.t, o); }));
    return { sel, data: [...byT.values()].sort((a, b) => a.t - b.t) };
  }, [all, shown]);

  if (!d) return <div className="card p-8 text-center text-[13.5px] text-slate-500">{err || "Loading the comparison…"}</div>;
  const toggle = (k: string) => setShown((s) => s.includes(k) ? s.filter((x) => x !== k) : [...s, k]);

  return (
    <div className="space-y-4">
      <section className="card p-5">
        <div className="mb-1 flex flex-wrap items-baseline gap-2">
          <h2 className="text-[17px] font-semibold">vs what competitors sell</h2>
          <span className="text-[13px] text-slate-500">every coin, {d.from} → {d.to}, same costs and rules · run {d.generated.slice(0, 10)}</span>
          <button className="btn btn-ghost ml-auto" disabled={busy} onClick={rerun}><RefreshCw size={14} className={busy ? "animate-spin" : ""} />{busy ? "Running…" : "Re-run"}</button>
        </div>
        <p className="mb-4 text-[13px] text-slate-500">Rule-based strategies as competitors&apos; bots and charts ship them, default settings, nothing tuned. Tick a row to draw it.</p>
        <div className="overflow-auto">
          <table className="w-full text-[13px]">
            <thead><tr className="text-left text-slate-500">
              <th className="py-1.5 font-medium" />
              <th className="py-1.5 font-medium">Strategy</th>
              {["Return", "2022 → now", "Sharpe", "Max DD", "Win rate", "Day hit rate", "Beat luck", "Pass correction"].map((h) => <th key={h} className="py-1.5 text-right font-medium">{h}</th>)}
            </tr></thead>
            <tbody>
              {all.map((r) => (
                <tr key={r.key} className={`border-t border-line ${ours(r) ? "bg-accent/5" : ""}`}>
                  <td className="py-2 pr-2"><input type="checkbox" checked={shown.includes(r.key)} onChange={() => toggle(r.key)} /></td>
                  <td className="py-2">
                    <div className={`font-medium ${ours(r) ? "text-accent" : "text-slate-200"}`}>{r.name} <span className="font-normal text-slate-500">· {r.sides}</span></div>
                    <div className="text-[12px] text-slate-500">{r.sold_by}</div>
                  </td>
                  <td className={`num py-2 text-right ${tone(r.return_pct)}`}>{pct(r.return_pct)}</td>
                  <td className={`num py-2 text-right ${tone(after2021(r.by_year))}`}>{pct(after2021(r.by_year))}</td>
                  <td className="num py-2 text-right">{r.sharpe ?? "—"}</td>
                  <td className="num py-2 text-right text-slate-400">{r.max_dd_pct != null ? `${r.max_dd_pct}%` : "—"}</td>
                  <td className="num py-2 text-right" title={r.key === "dca_bot_l" ? "closed cycles only: losing cycles stay open and are not counted" : undefined}>
                    {r.win_rate != null ? `${r.win_rate}%` : "—"}{r.key === "dca_bot_l" && <span className="text-down">*</span>}</td>
                  <td className="num py-2 text-right">{r.day_hit_rate != null ? `${r.day_hit_rate}%` : "—"}</td>
                  <td className="num py-2 text-right text-slate-400">{r.p05 != null ? `${r.p05} (luck ${r.expected_by_luck})` : "—"}</td>
                  <td className={`num py-2 text-right ${r.fdr_pass ? "font-semibold text-up" : "text-slate-400"}`}>{r.fdr_pass ?? "—"}</td>
                </tr>))}
              <tr className="border-t border-line">
                <td />
                <td className="py-2"><div className="font-medium text-slate-200">Buy and hold every tradeable coin</div><div className="text-[12px] text-slate-500">the benchmark, equal weight</div></td>
                <td className={`num py-2 text-right ${tone(d.hold_all.return_pct)}`}>{pct(d.hold_all.return_pct)}</td>
                <td className={`num py-2 text-right ${tone(after2021(d.hold_all.by_year))}`}>{pct(after2021(d.hold_all.by_year))}</td>
                <td className="num py-2 text-right">{d.hold_all.sharpe ?? "—"}</td>
                <td className="num py-2 text-right text-slate-400">{d.hold_all.max_dd_pct}%</td>
                <td colSpan={4} />
              </tr>
            </tbody>
          </table>
        </div>
        <p className="mt-2 text-[12px] text-slate-500"><span className="text-down">*</span> The DCA bot&apos;s win rate counts only finished cycles; cycles stuck below their buy price never close, so they never count as losses — the reason such bots advertise 90%+ win rates.</p>
      </section>

      <section className="card p-5">
        <h2 className="mb-3 text-[15px] font-semibold">Pooled books, $10,000 start</h2>
        <ResponsiveContainer width="100%" height={300}>
          <LineChart data={chart.data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
            <XAxis dataKey="t" tickFormatter={(t: number) => new Date(t).toLocaleDateString([], { year: "2-digit", month: "short" })} tick={{ fontSize: 11, fill: "#9aa7b3" }} axisLine={false} tickLine={false} minTickGap={40} />
            <YAxis scale="log" domain={["auto", "auto"]} allowDataOverflow tickFormatter={usd} tick={{ fontSize: 11, fill: "#9aa7b3" }} axisLine={false} tickLine={false} width={56} />
            <Tooltip formatter={(v: number) => usd(v)} labelFormatter={(t: number) => new Date(t).toLocaleDateString()} contentStyle={{ fontSize: 12, borderRadius: 10 }} />
            <Legend iconType="plainline" wrapperStyle={{ fontSize: 12 }} />
            {chart.sel.map((r, i) => <Line key={r.key} name={`${r.name} (${r.sides})`} dataKey={r.key} stroke={ours(r) ? "#0064e0" : PALETTE[(i % (PALETTE.length - 1)) + 1]}
              strokeWidth={ours(r) ? 2.25 : 1.25} dot={false} isAnimationActive={false} connectNulls />)}
          </LineChart>
        </ResponsiveContainer>
      </section>

      <section className="card p-5">
        <h2 className="mb-2 text-[15px] font-semibold">How to read it</h2>
        <ul className="list-disc space-y-1 pl-5 text-[12.5px] text-slate-500">
          {d.notes.map((n) => <li key={n}>{n}</li>)}
          <li>&ldquo;2022 → now&rdquo; leaves out the 2021 bull market, which flatters every long book.</li>
          <li>&ldquo;Beat luck&rdquo;: coins where the strategy beat the same trades at random times (p &lt; 0.05), next to how many luck alone gives. &ldquo;Pass correction&rdquo;: coins still significant after correcting for testing every coin.</li>
        </ul>
      </section>
    </div>
  );
}
