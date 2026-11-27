"use client";
import { useEffect, useState } from "react";
import { AreaSpark, type Point } from "@/components/dashboard/charts";
import { api } from "@/lib/api";
import { fmtPrice, pair } from "@/lib/format";
import { Card, LabBanner, More, Note, Page, PageHead, Tile } from "@/components/ui/Page";

type Pick = { day: string; symbol: string; side: string; style: string; entry: number; stop: number; target: number;
  status: string; outcome: string | null; r: number | null; triggered?: boolean };
type Rec = { equity: number; start_equity: number; return_pct: number; max_drawdown_pct: number; picks: number; open: number; pending: number;
  lapsed: number; closed: number; win_rate: number | null; avg_r: number | null; target_first: number; stop_first: number; expired: number;
  curve: Point[]; note: string; recent: Pick[]; monthly: { month: string; trades: number; wins: number; r: number }[];
  shadow_15r?: { trades: number; win_rate: number | null; avg_r: number | null; note: string } };
const tone = (v?: number | null) => v == null || v === 0 ? "text-slate-500" : v > 0 ? "text-up" : "text-down";
const STATUS: Record<string, string> = { open: "Open", pending: "Waiting for trigger", lapsed: "Not triggered", closed: "Closed" };

type QSandbox = { started: string | null; last_date: string | null; equity: number; start_equity: number; return_pct: number;
  max_drawdown_pct: number; sharpe_ann: number | null; days: number; costs_paid: number; weights: Record<string, number>;
  pending: { signal_date: string; weights: Record<string, number>; model: string } | null; last_rebalance: string | null;
  settings: { rebalance_days: number; promoted: boolean } | null; curve: Point[]; note: string };

type QBookT = QSandbox & { book: string; about: string; available: boolean };

function QBook({ q }: { q: QBookT }) {
  return (
          <section className="rounded-2xl border border-line bg-panel p-6">
            <div className="flex flex-wrap items-baseline gap-2"><h2 className="text-[16px] font-semibold">Quant sandbox · {q.book === "composite" ? "composite" : `candidate ${q.book}`}</h2>
              <span className="text-[13px] text-slate-500">{q.about}{q.started ? ` · started ${q.started}` : ""}</span></div>
            <div className="mt-4 grid grid-cols-2 gap-4 md:grid-cols-5">
              {[["Return", `${q.return_pct > 0 ? "+" : ""}${q.return_pct}%`], ["Sharpe", q.sharpe_ann ?? "—"], ["Max drawdown", `${q.max_drawdown_pct}%`],
                ["Days", q.days], ["Costs paid", `$${q.costs_paid.toFixed(2)}`]].map(([k, v]) => (
                <div key={k as string}><div className="text-[12.5px] text-slate-500">{k}</div><div className="num text-[18px] font-semibold text-slate-100">{v}</div></div>))}
            </div>
            <div className="mt-4">{q.curve.length > 1 ? <AreaSpark data={q.curve} height={140} fmt={(x) => `$${Math.round(x).toLocaleString()}`} />
              : <div className="flex h-[100px] items-center justify-center rounded-xl bg-panel2 text-[13px] text-slate-500">
                  {q.pending ? `First portfolio (${Object.keys(q.pending.weights).length} coins) decided on ${q.pending.signal_date}; it fills at the next day's open.` : q.note}</div>}</div>
            <div className="mt-3 flex flex-wrap gap-2 text-[12.5px]">
              {Object.entries(Object.keys(q.weights).length ? q.weights : q.pending?.weights ?? {}).map(([sym, w]) => (
                <span key={sym} className="chip bg-fill text-slate-300">{pair(sym)} {(w * 100).toFixed(1)}%</span>))}
            </div>
            <p className="mt-3 text-[12.5px] text-slate-500">{q.note}</p>
          </section>
  );
}

/** Every daily pick and how it turned out: the method's honest, graded record. */
export default function RecordPage() {
  const [d, setD] = useState<Rec | null>(null);
  const [err, setErr] = useState("");
  const [q, setQ] = useState<(QSandbox & { books?: QBookT[] }) | null>(null);
  useEffect(() => { api<Rec>("/agent/sandbox?recent=500").then(setD).catch((e) => setErr(e.message)); }, []);
  useEffect(() => { api<QSandbox>("/agent/quant_sandbox").then(setQ).catch(() => undefined); }, []);
  const stats: [string, string, string?][] = d ? [
    ["Return", `${d.return_pct > 0 ? "+" : ""}${d.return_pct}%`, `$${d.equity.toLocaleString()} from $${d.start_equity.toLocaleString()}`],
    ["Win rate", d.win_rate != null ? `${Math.round(d.win_rate * 100)}%` : "—", `${d.target_first} targets · ${d.stop_first} stops · ${d.expired} expired`],
    ["Average R", d.avg_r != null ? `${d.avg_r > 0 ? "+" : ""}${d.avg_r}` : "—", `${d.closed} closed trades`],
    ["Max drawdown", `${d.max_drawdown_pct}%`],
    ["Picks", `${d.picks}`, `${d.open} open · ${d.pending} waiting · ${d.lapsed} not triggered`],
  ] : [];
  return (
    <Page>
        <PageHead title="Setup record"
          sub={<span title="Paper-traded at the published price (or when its trigger hit) and graded on the candles">
            Every 4h setup, graded, losses included · main signal: <a href="/signal/test" className="text-accent hover:underline">Results</a></span>} />
        <LabBanner what="The record of the 4h setup screener and the quant sandbox." />
        {err && <Note tone="bad">{err}</Note>}
        <Card title="Equity · $10,000 paper account">
          <div className="grid grid-cols-2 gap-4 md:grid-cols-5">
            {stats.map(([k, v, sub]) => <Tile key={k} label={k} value={v} sub={sub} />)}
          </div>
          <div className="mt-5">{d && d.curve.length > 1 ? <AreaSpark data={d.curve} height={180} fmt={(x) => `$${Math.round(x).toLocaleString()}`} />
            : <div className="flex h-[180px] items-center justify-center rounded-xl bg-panel2 text-[13px] text-slate-500">{d?.note ?? "Loading…"}</div>}</div>
        </Card>
        {d && d.monthly.length > 0 && (
          <section className="rounded-2xl border border-line bg-panel p-6">
            <h2 className="text-[16px] font-semibold">By month</h2>
            <table className="mt-3 w-full text-[13px]"><thead><tr className="text-left text-[12px] text-slate-500"><th className="py-2 font-medium">Month</th><th className="font-medium">Trades</th><th className="font-medium">Wins</th><th className="text-right font-medium">Total R</th></tr></thead>
              <tbody className="divide-y divide-line">{d.monthly.map((m) => (
                <tr key={m.month}><td className="py-2 text-slate-200">{m.month}</td><td className="num">{m.trades}</td><td className="num">{m.wins}</td><td className={`num text-right font-medium ${tone(m.r)}`}>{m.r > 0 ? "+" : ""}{m.r}R</td></tr>))}</tbody></table>
          </section>)}
        <section className="overflow-hidden rounded-2xl border border-line bg-panel">
          <h2 className="px-6 pb-2 pt-5 text-[16px] font-semibold">All picks</h2>
          <table className="w-full text-[13px]">
            <thead className="bg-panel2 text-left text-[12px] text-slate-500"><tr>
              <th className="px-6 py-2.5 font-medium">Day</th><th className="font-medium">Coin</th><th className="font-medium">Side</th><th className="font-medium">Entry</th>
              <th className="font-medium">Stop</th><th className="font-medium">Target</th><th className="font-medium">Risk</th><th className="font-medium">Status</th><th className="px-6 text-right font-medium">Result</th></tr></thead>
            <tbody>{(d?.recent ?? []).map((p, i) => (
              <tr key={i} className="border-t border-line">
                <td className="num px-6 py-2.5 text-slate-400">{p.day}</td><td className="font-medium text-slate-100">{pair(p.symbol)}</td>
                <td className={p.side === "LONG" ? "text-up" : "text-down"}>{p.side === "LONG" ? "Long" : "Short"}</td>
                <td className="num text-slate-300">{fmtPrice(p.entry)}</td><td className="num text-slate-400">{fmtPrice(p.stop)}</td><td className="num text-slate-400">{fmtPrice(p.target)}</td>
                <td className="text-slate-400">{p.style === "growth" ? "2%" : "1%"}</td>
                <td className="text-slate-400">{p.status === "closed" ? p.outcome : STATUS[p.status] ?? p.status}{p.triggered ? " · triggered" : ""}</td>
                <td className={`num px-6 text-right font-medium ${tone(p.r)}`}>{p.r != null ? `${p.r > 0 ? "+" : ""}${p.r}R` : ""}</td>
              </tr>))}</tbody>
          </table>
        </section>
        {((q?.books ?? []).length > 0 || d?.shadow_15r) && <More>
        {(q?.books ?? []).map((b) => b.available ? <QBook key={b.book} q={b} /> : (
          <section key={b.book} className="rounded-2xl border border-dashed border-line px-6 py-4 text-[13px] text-slate-500">
            <b className="font-semibold text-slate-300">Quant sandbox · candidate {b.book}</b> — {b.about}. Starts as soon as its code is in place.</section>))}
        {d?.shadow_15r && (
          <section className="rounded-2xl border border-line bg-panel p-6">
            <div className="flex items-baseline gap-2"><h2 className="text-[16px] font-semibold">Forward test · +1.5R target</h2>
              <span className="text-[13px] text-slate-500">same picks, closer target · shadow only</span></div>
            <div className="mt-3 flex gap-10 text-[13px]">
              <div><div className="text-slate-500">Trades</div><div className="num text-[18px] font-semibold text-slate-100">{d.shadow_15r.trades}</div></div>
              <div><div className="text-slate-500">Win rate</div><div className="num text-[18px] font-semibold text-slate-100">{d.shadow_15r.win_rate != null ? `${Math.round(d.shadow_15r.win_rate * 100)}%` : "—"}</div></div>
              <div><div className="text-slate-500">Avg R</div><div className={`num text-[18px] font-semibold ${tone(d.shadow_15r.avg_r)}`}>{d.shadow_15r.avg_r != null ? `${d.shadow_15r.avg_r > 0 ? "+" : ""}${d.shadow_15r.avg_r}` : "—"}</div></div>
              <p className="max-w-md text-[12.5px] text-slate-500">{d.shadow_15r.note} It passed on 4h but not 1h, so it is tested live before it changes any pick.</p>
            </div>
          </section>)}
        </More>}
    </Page>
  );
}
