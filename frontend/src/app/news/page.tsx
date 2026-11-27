"use client";
import { ExternalLink, Loader2, Newspaper, RefreshCw, Search } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { ago } from "@/lib/format";
import { Card, More, Note, Page, PageHead } from "@/components/ui/Page";

type Item = { id: string; source: string; url: string; title: string; summary: string | null; published_at: string | null; coins: string[];
  sentiment: number; event_type: string; credibility: number; impact: "high" | "medium" | "low"; corroborations: number };
type Feed = { items: Item[]; fear_greed: { value: number; label: string } | null; method: string };
type Today = { plan?: { symbol?: string; side?: string } };

const IMPACT: Record<Item["impact"], [string, string]> = {
  high: ["Big news", "bg-red-500/15 text-red-300"], medium: ["Worth knowing", "bg-amber-500/15 text-amber-300"], low: ["Minor", "bg-slate-500/15 text-slate-400"] };
const KIND: Record<string, string> = { regulation: "Regulation", hack_exploit: "Hack / exploit", etf: "ETF", upgrade: "Upgrade", macro: "Economy",
  legal: "Court case", market_move: "Big price move", listing: "Listing", delisting: "Delisting", partnership: "Partnership", general: "General" };
const decode = (t: string | null) => (t ?? "").replace(/&#(\d+);/g, (_, n) => String.fromCharCode(+n)).replace(/&amp;/g, "&").replace(/&quot;/g, '"').replace(/&#39;|&apos;/g, "'").replace(/&lt;/g, "<").replace(/&gt;/g, ">");
const mood = (s: number) => (s > 0.2 ? ["Positive", "text-emerald-300"] : s < -0.2 ? ["Negative", "text-red-300"] : ["Neutral", "text-slate-500"]);
const FILTERS = [["all", "All"], ["high", "Big news"], ["good", "Positive"], ["bad", "Negative"], ["signal", "Today's coin"]] as const;

/** News: the crypto headlines the platform reads, in plain words. Context only: headlines never trigger a trade. */
export default function NewsPage() {
  const [d, setD] = useState<Feed | null>(null);
  const [today, setToday] = useState<Today | null>(null);
  const [f, setF] = useState<(typeof FILTERS)[number][0]>("all");
  const [q, setQ] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    setBusy(true);
    api<Feed>("/news?limit=300").then((x) => { setD(x); setErr(""); }).catch((e) => setErr(String(e.message || e))).finally(() => setBusy(false));
  }, []);
  useEffect(() => { load(); const i = setInterval(load, 5 * 60_000); return () => clearInterval(i); }, [load]);
  useEffect(() => { api<Today>("/agent/signal/today?margin=100").then(setToday).catch(() => undefined); }, []);

  const coin = (today?.plan?.symbol ?? "").replace(/USDT$/, "");
  const items = useMemo(() => {
    const term = q.trim().toUpperCase();
    return (d?.items ?? []).filter((n) =>
      (f === "all" || (f === "high" && n.impact === "high") || (f === "good" && n.sentiment > 0.2) || (f === "bad" && n.sentiment < -0.2)
        || (f === "signal" && !!coin && n.coins.includes(coin)))
      && (!term || n.coins.includes(term) || n.title.toUpperCase().includes(term)));
  }, [d, f, q, coin]);
  const big = (d?.items ?? []).filter((n) => n.impact === "high").slice(0, 3);
  const pos = (d?.items ?? []).filter((n) => n.sentiment > 0.2).length, neg = (d?.items ?? []).filter((n) => n.sentiment < -0.2).length;

  return (
    <Page>
      <PageHead title="News" sub="Crypto headlines in plain words · context only, never a trade trigger">
        <button className="btn btn-ghost" onClick={load} disabled={busy}>{busy ? <Loader2 size={15} className="animate-spin" /> : <RefreshCw size={15} />} Refresh</button>
      </PageHead>
      {err && <Note tone="bad">{err}</Note>}
      {!d && !err && <div className="flex items-center gap-2 text-[13.5px] text-slate-500"><Loader2 size={16} className="animate-spin" /> Loading the news…</div>}
      {d && (
        <>
          {/* the answer: the mood and the few headlines that matter */}
          <div className="grid gap-6 lg:grid-cols-[1fr_2fr]">
            <Card title="Market mood">
              <div className="num text-[34px] font-semibold leading-tight text-slate-100">{d.fear_greed ? d.fear_greed.value : "—"}
                <span className="ml-2 text-[16px] font-normal text-slate-400">{d.fear_greed?.label ?? ""}</span></div>
              <div className="mt-1 text-[12.5px] text-slate-500" title="The Fear & Greed index: 0 = extreme fear, 100 = extreme greed">Fear &amp; greed, 0 to 100</div>
              <div className="mt-4 flex gap-4 text-[13px]">
                <span><b className="text-emerald-300">{pos}</b> <span className="text-slate-500">positive</span></span>
                <span><b className="text-red-300">{neg}</b> <span className="text-slate-500">negative</span></span>
                <span><b className="text-slate-200">{d.items.length}</b> <span className="text-slate-500">headlines</span></span>
              </div>
            </Card>
            <Card title={<><Newspaper size={17} className="text-accent" /> Big news now</>}>
              {big.length === 0 ? <div className="text-[13px] text-slate-500">No big news right now.</div> : (
                <ul className="space-y-3">{big.map((n) => (
                  <li key={n.id}><a href={n.url} target="_blank" rel="noreferrer" className="group block">
                    <div className="text-[14px] font-medium text-slate-100 group-hover:text-accent">{decode(n.title)}</div>
                    <div className="mt-0.5 text-[12px] text-slate-500">{n.source} · {ago(n.published_at)}{n.coins.length ? ` · ${n.coins.slice(0, 4).join(", ")}` : ""}</div>
                  </a></li>))}</ul>
              )}
            </Card>
          </div>

          {/* every headline */}
          <Card title="All headlines" right={
            <div className="relative"><Search size={14} className="absolute left-3 top-[11px] text-slate-500" />
              <input className="input w-48 pl-8" placeholder="Coin, e.g. BTC" value={q} onChange={(e) => setQ(e.target.value)} /></div>}>
            <div className="mb-4 flex flex-wrap gap-1.5">
              {FILTERS.map(([k, l]) => (k === "signal" && !coin) ? null : (
                <button key={k} onClick={() => setF(k)} className={`rounded-full px-3.5 py-1.5 text-[13px] font-medium ${f === k ? "bg-accent-soft text-accent" : "text-slate-400 hover:bg-fill"}`}>
                  {k === "signal" ? `${coin} (today's signal)` : l}</button>))}
            </div>
            {items.length === 0 ? <div className="text-[13px] text-slate-500">No headlines match.</div> : (
              <div className="divide-y divide-line">
                {items.slice(0, 120).map((n) => {
                  const [m, mc] = mood(n.sentiment);
                  return (
                    <a key={n.id} href={n.url} target="_blank" rel="noreferrer" className="group flex gap-4 py-3.5">
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2 text-[11.5px]">
                          <span className={`chip ${IMPACT[n.impact][1]}`}>{IMPACT[n.impact][0]}</span>
                          <span className="text-slate-500">{KIND[n.event_type] ?? n.event_type}</span>
                          <span className={mc}>{m}</span>
                          {n.coins.slice(0, 5).map((c) => <span key={c} className="chip bg-fill text-slate-300">{c}</span>)}
                        </div>
                        <div className="mt-1 text-[14px] font-medium text-slate-100 group-hover:text-accent">{decode(n.title)}</div>
                        {n.summary && <div className="mt-0.5 line-clamp-2 text-[12.5px] text-slate-500">{decode(n.summary)}</div>}
                      </div>
                      <div className="shrink-0 text-right text-[12px] text-slate-500">
                        <div>{n.source}</div><div>{ago(n.published_at)}</div>
                        <ExternalLink size={13} className="ml-auto mt-1 opacity-0 group-hover:opacity-100" />
                      </div>
                    </a>);
                })}
              </div>
            )}
          </Card>

          <More>
            <Card title="How the news is read">
              <ul className="list-disc space-y-1 pl-5 text-[13px] text-slate-400">
                <li>Headlines come from public crypto news feeds and are matched to coins by name.</li>
                <li>&quot;Positive / Negative&quot; is a simple word-based reading of the headline, not a prediction.</li>
                <li>&quot;Big news&quot; means the type of event (hack, regulation, ETF…) and how many sources report it.</li>
                <li>News never triggers a trade. The main signal comes from the trained model; the AI agents read the news as context.</li>
              </ul>
              <p className="mt-2 text-[12px] text-slate-500">{d.method}</p>
            </Card>
          </More>
        </>
      )}
    </Page>
  );
}
