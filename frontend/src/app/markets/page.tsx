"use client";
import { ArrowDown, ArrowUp, Search } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { fmtPrice, pair } from "@/lib/format";
import { Note, Page, PageHead } from "@/components/ui/Page";

type Row = { symbol: string; name: string | null; logo: string | null; price: number | null; change_24h_pct: number | null;
  market_cap: number | null; rank: number; of: number; score: number; quintile: number | null; trend: string | null;
  p_up_week: number | null; p_n: number | null; mom_7d_pct: number | null; mom_28d_pct: number | null;
  funding_ann_pct: number | null; basis_pct: number | null; funding_note: string | null };
type Data = { computed_at: number; regime: string | null; method: string; rows: Row[] };
type Key = "score" | "change_24h_pct" | "mom_7d_pct" | "mom_28d_pct" | "p_up_week" | "funding_ann_pct" | "market_cap";
const FILTERS: [string, (r: Row) => boolean][] = [
  ["All", () => true], ["Strong trend", (r) => r.score >= 80], ["Weakest", (r) => r.score <= 20],
  ["Crowded longs", (r) => (r.funding_ann_pct ?? 0) > 30], ["Crowded shorts", (r) => (r.funding_ann_pct ?? 0) < -30],
];
const pct = (v?: number | null, d = 1) => v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(d)}%`;
const tone = (v?: number | null) => v == null || v === 0 ? "text-slate-500" : v > 0 ? "text-up" : "text-down";
const cap = (v?: number | null) => v == null ? "—" : v >= 1e9 ? `$${(v / 1e9).toFixed(1)}B` : `$${(v / 1e6).toFixed(0)}M`;

/** The screener: every liquid coin ranked by the computer-side numbers (trend score, measured odds, momentum, funding). */
export default function MarketsPage() {
  const [d, setD] = useState<Data | null>(null);
  const [err, setErr] = useState("");
  const [q, setQ] = useState("");
  const [f, setF] = useState(0);
  const [sort, setSort] = useState<{ k: Key; desc: boolean }>({ k: "score", desc: true });
  useEffect(() => { api<Data>("/agent/screener").then(setD).catch((e) => setErr(e.message)); }, []);
  const rows = useMemo(() => {
    const term = q.trim().toUpperCase();
    const out = (d?.rows ?? []).filter(FILTERS[f][1]).filter((r) => !term || r.symbol.includes(term) || (r.name ?? "").toUpperCase().includes(term));
    return out.sort((a, b) => ((a[sort.k] ?? -Infinity) - (b[sort.k] ?? -Infinity)) * (sort.desc ? -1 : 1));
  }, [d, q, f, sort]);
  const H = ({ k, children, className = "" }: { k: Key; children: React.ReactNode; className?: string }) => (
    <th className={`cursor-pointer select-none whitespace-nowrap px-3 py-2.5 text-right font-medium hover:text-slate-200 ${className}`}
      onClick={() => setSort({ k, desc: sort.k === k ? !sort.desc : true })}>
      <span className="inline-flex items-center gap-1">{children}{sort.k === k && (sort.desc ? <ArrowDown size={12} /> : <ArrowUp size={12} />)}</span>
    </th>
  );
  return (
    <Page>
        <PageHead title="Trend ranking"
          sub={d ? <span>{d.rows.length} coins by trend, momentum and funding · {(d.regime ?? "").split(":")[0]} market · updated {new Date(d.computed_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} · the main signals are in <Link href="/signals" className="text-accent hover:underline">All coins</Link></span> : "Loading…"}>
          <div className="relative">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
            <input className="input w-60 pl-9" placeholder="Search coin" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
        </PageHead>
        {err && <Note tone="bad">{err}</Note>}
        <div className="flex flex-wrap gap-1">{FILTERS.map(([label], i) => (
          <button key={label} onClick={() => setF(i)} className={`rounded-full px-3.5 py-1.5 text-[13px] font-medium ${f === i ? "bg-fill text-slate-100" : "text-slate-500 hover:text-slate-200"}`}>{label}</button>))}
        </div>
        <section className="overflow-hidden rounded-2xl border border-line bg-panel">
          <table className="w-full text-[13px]">
            <thead className="bg-panel2 text-[12px] text-slate-500">
              <tr>
                <th className="px-5 py-2.5 text-left font-medium">Coin</th>
                <th className="px-3 py-2.5 text-right font-medium">Price</th>
                <H k="change_24h_pct">24h</H><H k="mom_7d_pct">7d</H><H k="mom_28d_pct">28d</H>
                <H k="score">Trend score</H><H k="p_up_week">Up next week</H><H k="funding_ann_pct">Funding / yr</H>
                <H k="market_cap" className="pr-5">Market cap</H>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.symbol} className="border-t border-line hover:bg-panel2">
                  <td className="px-5 py-2.5">
                    <Link href={`/desk?coin=${r.symbol}`} className="flex items-center gap-3">
                      {r.logo
                        // eslint-disable-next-line @next/next/no-img-element
                        ? <img src={r.logo} alt="" width={24} height={24} className="h-6 w-6 rounded-full" />
                        : <span className="flex h-6 w-6 items-center justify-center rounded-full bg-fill text-[10px] font-semibold text-slate-400">{pair(r.symbol).slice(0, 2)}</span>}
                      <span><span className="font-medium text-slate-100">{r.name ?? pair(r.symbol).split("/")[0]}</span> <span className="text-slate-500">{pair(r.symbol).split("/")[0]}</span></span>
                    </Link>
                  </td>
                  <td className="num px-3 text-right text-slate-200">{r.price != null ? fmtPrice(r.price) : "—"}</td>
                  <td className={`num px-3 text-right ${tone(r.change_24h_pct)}`}>{pct(r.change_24h_pct)}</td>
                  <td className={`num px-3 text-right ${tone(r.mom_7d_pct)}`}>{pct(r.mom_7d_pct)}</td>
                  <td className={`num px-3 text-right ${tone(r.mom_28d_pct)}`}>{pct(r.mom_28d_pct)}</td>
                  <td className="px-3 text-right">
                    <span className="inline-flex items-center gap-2"><span className="h-1 w-12 overflow-hidden rounded-full bg-fill"><span className="block h-full rounded-full bg-accent" style={{ width: `${r.score}%` }} /></span>
                      <span className="num w-8 font-semibold text-slate-100">{Math.round(r.score)}</span></span>
                  </td>
                  <td className="num px-3 text-right text-slate-300" title={r.p_n ? `measured on ${r.p_n} past weeks of coins in this quintile` : undefined}>{r.p_up_week != null ? `${Math.round(r.p_up_week * 100)}%` : "—"}</td>
                  <td className={`num px-3 text-right ${Math.abs(r.funding_ann_pct ?? 0) > 30 ? "font-semibold text-amber-300" : "text-slate-300"}`} title={r.funding_note ?? undefined}>{pct(r.funding_ann_pct, 0)}</td>
                  <td className="num px-3 pr-5 text-right text-slate-400">{cap(r.market_cap)}</td>
                </tr>))}
            </tbody>
          </table>
          {d && !rows.length && <div className="py-12 text-center text-[13px] text-slate-500">No coin matches.</div>}
        </section>
        <details className="text-[12.5px] text-slate-500">
          <summary className="cursor-pointer hover:text-slate-300">How to read this</summary>
          <ul className="mt-2 list-disc space-y-1 pl-5">
            <li>Trend score: the coin&apos;s rank on 8 trend signals (momentum, moving averages, volume). Higher = stronger.</li>
            <li>Up next week: how often coins with a similar score rose the next week.</li>
            <li>Funding above ±30% a year means too many traders on one side.</li>
          </ul>
        </details>
    </Page>
  );
}
