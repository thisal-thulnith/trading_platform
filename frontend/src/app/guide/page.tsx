"use client";
import { BookOpenText, Download, Loader2, Search } from "lucide-react";
import { Children, isValidElement, useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Card, Note, Page, PageHead } from "@/components/ui/Page";

const SRC = "/guide/PLATFORM_GUIDE.md";
const PDF = "/guide/Platform-Guide.pdf";

const slug = (t: string) => t.toLowerCase().replace(/&amp;/g, "and").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
const text = (n: React.ReactNode): string =>
  Children.toArray(n).map((c) => (typeof c === "string" || typeof c === "number" ? String(c)
    : isValidElement<{ children?: React.ReactNode }>(c) ? text(c.props.children) : "")).join("");

type Toc = { level: 1 | 2; title: string; id: string };

function heading(Tag: "h1" | "h2" | "h3") {
  return function H({ children }: { children?: React.ReactNode }) {
    return <Tag id={slug(text(children))}>{children}</Tag>;
  };
}

/** The platform guide inside the app: the same text as the PDF (docs/guide, copied in at build time), with a
 *  searchable table of contents and a PDF download. */
export default function GuidePage() {
  const [md, setMd] = useState<string | null>(null);
  const [err, setErr] = useState("");
  const [q, setQ] = useState("");

  useEffect(() => {
    fetch(SRC).then((r) => (r.ok ? r.text() : Promise.reject(new Error(`${r.status}`))))
      .then(setMd).catch(() => setErr("The guide is not available in this build."));
  }, []);
  useEffect(() => {   // open a section from a link like /guide#8-risk-and-leverage-rules-for-a-20x-trader
    if (!md || !window.location.hash) return;
    const t = setTimeout(() => document.getElementById(decodeURIComponent(window.location.hash.slice(1)))?.scrollIntoView(), 50);
    return () => clearTimeout(t);
  }, [md]);

  // the title line is the page header, not part of the body
  const body = useMemo(() => (md ?? "").replace(/^# .*\n/, "").replace(/^\[TOC\]\s*$/gm, ""), [md]);
  const toc = useMemo<Toc[]>(() => body.split("\n").flatMap((l) => {
    const m = /^(#{1,2}) (.+)$/.exec(l);
    return m ? [{ level: m[1].length as 1 | 2, title: m[2].replace(/\*\*/g, ""), id: slug(m[2].replace(/\*\*/g, "")) }] : [];
  }), [body]);
  const shown = useMemo(() => {
    const t = q.trim().toLowerCase();
    if (!t) return toc.filter((x) => x.level === 1 || toc.length < 40);
    return toc.filter((x) => x.title.toLowerCase().includes(t));
  }, [toc, q]);
  // a search also finds sections whose TEXT mentions the word (e.g. "funding" finds the glossary section)
  const inText = useMemo(() => {
    const t = q.trim().toLowerCase();
    if (t.length < 3) return [];
    const out: Toc[] = [];
    let cur: Toc | null = null;
    for (const l of body.split("\n")) {
      const m = /^(#{1,2}) (.+)$/.exec(l);
      if (m) { cur = { level: m[1].length as 1 | 2, title: m[2], id: slug(m[2]) }; continue; }
      if (cur && l.toLowerCase().includes(t) && !out.some((x) => x.id === cur!.id) && !shown.some((x) => x.id === cur!.id)) out.push(cur);
    }
    return out.slice(0, 12);
  }, [body, q, shown]);

  return (
    <Page>
      <PageHead title="Guide" sub="Every page, number and chart, in simple English">
        <a href={PDF} download className="btn btn-primary"><Download size={15} /> Download PDF</a>
      </PageHead>
      {err && <Note tone="warn">{err}</Note>}
      {!md && !err && <div className="flex items-center gap-2 text-[13.5px] text-slate-500"><Loader2 size={16} className="animate-spin" /> Loading the guide…</div>}
      {md && (
        <div className="grid items-start gap-6 lg:grid-cols-[270px_minmax(0,1fr)]">
          <aside className="min-w-0 lg:sticky lg:top-4">
            <Card className="p-4">
              <div className="relative">
                <Search size={14} className="absolute left-3 top-[11px] text-slate-500" />
                <input className="input w-full pl-8" placeholder="Search, e.g. liquidation" value={q} onChange={(e) => setQ(e.target.value)} />
              </div>
              <nav className="mt-3 max-h-[70vh] space-y-0.5 overflow-auto text-[13px]">
                {shown.map((x) => (
                  <a key={x.id} href={`#${x.id}`} className={`block rounded-lg px-2 py-1.5 hover:bg-fill ${x.level === 1 ? "font-medium text-slate-200" : "pl-5 text-slate-400"}`}>{x.title}</a>
                ))}
                {inText.length > 0 && <div className="px-2 pb-1 pt-3 text-[11px] font-semibold uppercase tracking-wider text-slate-500">Also mentioned in</div>}
                {inText.map((x) => <a key={`t-${x.id}`} href={`#${x.id}`} className="block rounded-lg px-2 py-1.5 pl-5 text-slate-400 hover:bg-fill">{x.title}</a>)}
                {q && !shown.length && !inText.length && <div className="px-2 py-2 text-slate-500">Nothing found.</div>}
              </nav>
              {!q && <p className="mt-3 flex items-center gap-1.5 border-t border-line pt-3 text-[12px] text-slate-500"><BookOpenText size={13} /> Search shows sub-sections too.</p>}
            </Card>
          </aside>
          <Card className="px-8 py-7">
            <article className="doc">
              <ReactMarkdown remarkPlugins={[remarkGfm]}
                urlTransform={(u) => (u.startsWith("images/") ? `/guide/${u}` : u)}
                components={{ h1: heading("h1"), h2: heading("h2"), h3: heading("h3") }}>
                {body}
              </ReactMarkdown>
            </article>
          </Card>
        </div>
      )}
    </Page>
  );
}
