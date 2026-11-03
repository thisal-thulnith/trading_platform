"use client";
import { BookOpen, Search } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useStore } from "@/lib/store";
import { CHAPTERS, GLOSSARY } from "./content";
import { Diagram } from "./diagrams";

const LEVEL: Record<string, string> = { start: "bg-sky-500/15 text-sky-300", beginner: "bg-emerald-500/15 text-emerald-300",
  intermediate: "bg-amber-500/15 text-amber-300", advanced: "bg-violet-500/15 text-violet-300" };

export default function Learn() {
  const [q, setQ] = useState("");
  const router = useRouter();
  const requestWalkthrough = useStore((s) => s.requestWalkthrough);
  const filtered = useMemo(() => {
    if (!q) return CHAPTERS;
    const s = q.toLowerCase();
    return CHAPTERS.map((c) => ({ ...c, lessons: c.lessons.filter((l) => (l.title + l.body).toLowerCase().includes(s)) })).filter((c) => c.lessons.length);
  }, [q]);
  const gloss = GLOSSARY.filter(([t, d]) => !q || (t + d).toLowerCase().includes(q.toLowerCase()));
  const openWalk = () => { requestWalkthrough(); router.push("/terminal"); };
  return (
    <div className="grid h-full grid-cols-[260px_1fr] overflow-hidden">
      <nav className="overflow-auto border-r border-line bg-panel p-3 text-[12.5px]">
        <div className="mb-2 flex items-center gap-2 font-semibold text-slate-100"><BookOpen size={15} className="text-sky-400" /> Learn</div>
        <div className="relative mb-3"><Search size={12} className="absolute left-2 top-2 text-slate-500" />
          <input className="input w-full pl-6 text-[12px]" placeholder="Search lessons & glossary" value={q} onChange={(e) => setQ(e.target.value)} /></div>
        {filtered.map((c) => (
          <div key={c.id} className="mb-3">
            <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">{c.title}</div>
            {c.lessons.map((l) => <a key={l.slug} href={`#${l.slug}`} className="block rounded px-2 py-0.5 text-slate-300 hover:bg-white/5">{l.title}</a>)}
          </div>
        ))}
        <a href="#glossary" className="block rounded px-2 py-0.5 font-medium text-slate-300 hover:bg-white/5">Glossary A → Z</a>
      </nav>
      <div className="overflow-auto scroll-smooth">
        <div className="mx-auto max-w-3xl px-6 py-6">
          <h1 className="text-2xl font-semibold text-slate-100">Learn trading — and how to use this platform</h1>
          <p className="mt-1 text-slate-400">From zero to reading a chart like an analyst. Each concept links back to the live chart, where the AI shows it on real data.</p>
          <div className="mt-3 flex flex-wrap gap-2">
            <button onClick={openWalk} className="btn btn-primary">▶ Watch the AI analyse a live chart step by step</button>
            <Link href="#getting-started" className="btn">Start here</Link>
            <Link href="#risk-management" className="btn">Risk management first</Link>
          </div>
          <div className="mt-3 rounded border border-amber-500/30 bg-amber-500/10 p-3 text-[12.5px] text-amber-100">
            Educational content, not financial advice. Most short-term traders lose money. Practise with paper trading, risk small amounts, and never trade money you can&apos;t afford to lose.
          </div>
          {filtered.map((c) => (
            <section key={c.id} className="mt-10">
              <h2 className="border-b border-line pb-1 text-lg font-semibold text-sky-300">{c.title}</h2>
              {c.lessons.map((l) => (
                <article key={l.slug} id={l.slug} className="scroll-mt-4 pt-6">
                  <div className="flex items-center gap-2">
                    <h3 className="text-[17px] font-semibold text-slate-100">{l.title}</h3>
                    <span className={`chip ${LEVEL[l.level]}`}>{l.level === "start" ? "platform" : l.level}</span>
                  </div>
                  {l.diagram && <Diagram name={l.diagram} />}
                  <div className="lesson mt-2 text-[14px] leading-relaxed text-slate-300"><ReactMarkdown remarkPlugins={[remarkGfm]}>{l.body}</ReactMarkdown></div>
                  {l.tryIt === "walk" && <button onClick={openWalk} className="btn mt-2 text-[12px]">See it on the live chart →</button>}
                </article>
              ))}
            </section>
          ))}
          <section id="glossary" className="mt-10 scroll-mt-4 pb-16">
            <h2 className="border-b border-line pb-1 text-lg font-semibold text-sky-300">Glossary A → Z</h2>
            <dl className="mt-3 grid grid-cols-[170px_1fr] gap-x-4 gap-y-1.5 text-[13px]">
              {gloss.map(([t, d]) => (<span key={t} className="contents"><dt className="font-medium text-slate-100">{t}</dt><dd className="text-slate-400">{d}</dd></span>))}
            </dl>
          </section>
        </div>
      </div>
    </div>
  );
}
