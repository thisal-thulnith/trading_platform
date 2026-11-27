"use client";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import ConfluenceMap from "@/components/terminal/ConfluenceMap";
import { api } from "@/lib/api";
import { fmtPrice, pair } from "@/lib/format";
import type { Snapshot } from "@/lib/types";
import { Page as Layout, PageHead } from "@/components/ui/Page";

function Inner() {
  const { id } = useParams<{ id: string }>();
  const cmp = useSearchParams().get("compare");
  const [s, setS] = useState<(Snapshot & { created_at: string }) | null>(null);
  const [diff, setDiff] = useState<any>(null);
  useEffect(() => {
    api(`/snapshots/${id}`).then(setS).catch(() => {});
    if (cmp) api(`/snapshots/${cmp}/compare/${id}`).then(setDiff).catch(() => {});
  }, [id, cmp]);
  if (!s) return <Layout><div className="text-[13.5px] text-slate-500">Loading snapshot…</div></Layout>;
  return (
    <Layout>
      <PageHead title={`Analysis snapshot · ${pair(s.symbol)} ${s.timeframe}`}
        sub={<span title={`As-of candle ${new Date(s.as_of).toISOString()} · engine ${s.engine_version} · data hash ${s.data_hash.slice(0, 16)}`}>Saved {s.created_at} · cannot be changed</span>} />
      {diff && (
        <div className="card mb-3 p-3 text-[12px]">
          <div className="mb-1 font-medium text-slate-200">Version comparison (previous → this)</div>
          <div>Decision: {diff.a.decision?.action} → {diff.b.decision?.action} · bias {diff.a.bias.toFixed(2)} → {diff.b.bias.toFixed(2)} · regime {diff.a.regime} → {diff.b.regime}</div>
          <div className="text-up">+ {diff.annotations_added.length} new objects: {diff.annotations_added.slice(0, 8).map((a: any) => a.label).join(", ")}</div>
          <div className="text-down">− {diff.annotations_removed.length} removed: {diff.annotations_removed.slice(0, 8).map((a: any) => a.label).join(", ")}</div>
          <div className="text-slate-400">{diff.annotations_changed.length} status changes</div>
        </div>
      )}
      <div className="grid grid-cols-3 gap-3">
        <div className="card p-3"><div className="mb-1 font-medium">Decision: {s.decision?.action}</div><ul className="list-disc pl-4 text-[12px] text-slate-400">{s.decision?.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
          <div className="mt-2 text-[12px]">Price {fmtPrice(s.price)} · regime {s.regime.regime}</div></div>
        <div className="card p-3"><div className="mb-1 font-medium">Confluence</div><ConfluenceMap snap={s} /></div>
        <div className="card p-3 text-[12px]"><div className="mb-1 font-medium">Setups</div>{s.candidates?.filter((c) => !c.error).map((c, i) => <div key={i}>{c.direction} {c.strategy} — {c.stage} {c.validation.passed ? "✓" : ""}</div>)}</div>
        <div className="card col-span-3 p-3 text-[12px]"><div className="mb-1 font-medium">Evidence ({s.evidence.length})</div>
          {s.evidence.map((e, i) => <div key={i} className="flex gap-2"><span className="w-28 text-slate-500">{e.family}</span><span className={e.direction > 0 ? "text-up" : e.direction < 0 ? "text-down" : "text-slate-400"}>{e.name}</span><span className="text-slate-500">{e.detail}</span><span className="ml-auto text-slate-600">{e.basis}</span></div>)}</div>
      </div>
      <Link href="/terminal" className="inline-block text-accent hover:underline">← Back to chart</Link>
    </Layout>
  );
}
export default function Page() { return <Suspense><Inner /></Suspense>; }
