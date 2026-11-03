"use client";
import { X } from "lucide-react";
import { fmtPrice } from "@/lib/format";
import { useStore } from "@/lib/store";
import type { Snapshot } from "@/lib/types";

export default function Inspector({ snap }: { snap: Snapshot | null }) {
  const { selectedId, select, toggleHidden, hiddenAi } = useStore();
  if (!selectedId || !snap) return null;
  const a = snap.annotations.find((x) => x.id === selectedId);
  if (!a) return (
    <div className="card m-2 p-2 text-xs text-slate-400">
      Selected: your drawing / signal overlay. Drag handles to edit, Del to remove, Ctrl+Z to undo.
    </div>
  );
  const ex = a.explain || {};
  const ts = (s?: number) => (s ? new Date(s * 1000).toISOString().replace("T", " ").slice(0, 16) + "Z" : "—");
  const related = snap.candidates.filter((c) => !c.error && c.anchor_id === a.id);
  return (
    <div className="card m-2 p-2 text-xs">
      <div className="mb-1 flex items-center">
        <span className="font-medium text-slate-100">{a.label}</span>
        <span className="chip ml-2 bg-slate-700/60 text-slate-300">{a.origin === "ai" ? "AI" : "user-edited"}</span>
        <button onClick={() => select(null)} className="ml-auto text-slate-500"><X size={14} /></button>
      </div>
      <dl className="grid grid-cols-[110px_1fr] gap-x-2 gap-y-0.5">
        <dt className="text-slate-500">Concept</dt><dd>{a.concept?.replaceAll("_", " ")}</dd>
        <dt className="text-slate-500">Status</dt><dd>{a.status}</dd>
        {a.price1 !== undefined && (<><dt className="text-slate-500">Price</dt><dd className="num">{fmtPrice(a.price1)}{a.price2 !== undefined ? ` – ${fmtPrice(a.price2)}` : ""}</dd></>)}
        {ex.criteria && (<><dt className="text-slate-500">Detection criteria</dt><dd>{ex.criteria}</dd></>)}
        {ex.timeframe && (<><dt className="text-slate-500">Timeframe</dt><dd>{ex.timeframe}</dd></>)}
        {ex.detected_at && (<><dt className="text-slate-500">Detected</dt><dd>{ts(ex.detected_at)}</dd></>)}
        {ex.supporting_candles && (<><dt className="text-slate-500">Supporting candles</dt><dd>{ex.supporting_candles.map(ts).join(", ")}</dd></>)}
        {ex.invalidation !== undefined && (<><dt className="text-slate-500">Invalidation</dt><dd className="num">{fmtPrice(ex.invalidation)}</dd></>)}
        {ex.relevance && (<><dt className="text-slate-500">Relevance</dt><dd>{ex.relevance}</dd></>)}
        {Object.entries(ex).filter(([k]) => !["criteria", "timeframe", "detected_at", "supporting_candles", "invalidation", "relevance", "limitations"].includes(k))
          .map(([k, v]) => (<span key={k} className="contents"><dt className="text-slate-500">{k.replaceAll("_", " ")}</dt><dd className="break-words">{typeof v === "object" ? JSON.stringify(v) : String(v)}</dd></span>))}
        {related.length > 0 && (<><dt className="text-slate-500">Related setup</dt><dd>{related.map((c) => `${c.strategy} ${c.direction} (${c.stage})`).join("; ")}</dd></>)}
      </dl>
      {ex.limitations && <div className="mt-1 rounded bg-amber-500/10 p-1 text-[11px] text-amber-200">⚠ {ex.limitations}</div>}
      <div className="mt-2 flex gap-2">
        <button className="btn py-0.5" onClick={() => toggleHidden(a.id)}>{hiddenAi[a.id] ? "Show" : "Hide"}</button>
        <span className="self-center text-[10px] text-slate-500">Drag to edit — your edits are saved separately and never overwrite the AI evidence.</span>
      </div>
    </div>
  );
}
