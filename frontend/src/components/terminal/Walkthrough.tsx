"use client";
import { ChevronLeft, ChevronRight, GraduationCap, MessageSquareText, Pause, Play, X } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { api } from "@/lib/api";
import { fmtPrice } from "@/lib/format";
import { useStore } from "@/lib/store";
import type { ChartObject } from "@/lib/types";

type Step = {
  key: string; title: string; plain: string; observation: string; verdict: number; verdict_word: string;
  bullets: string[]; focus_ids: string[]; objects: ChartObject[]; learn: string; caution?: string | null;
};
type Walk = {
  symbol: string; timeframe: string; price: number; higher_timeframes: string[]; steps: Step[];
  synthesis: { direction: string; confidence: string; agreement: number; story: string[]; supporting: string[]; conflicting: string[] };
  decision: { action: string; reasons: string[] }; narrative: string | null; llm_configured: boolean; disclaimer: string;
};

const VERDICT_STYLE: Record<string, string> = {
  bullish: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  bearish: "bg-red-500/15 text-red-300 border-red-500/30",
  neutral: "bg-slate-600/20 text-slate-300 border-slate-600/40",
};

export default function Walkthrough({ onClose }: { onClose: () => void }) {
  const { symbol, timeframe, setFocus } = useStore();
  const [walk, setWalk] = useState<Walk | null>(null);
  const [i, setI] = useState(-1); // -1 = overview
  const [playing, setPlaying] = useState(false);
  const [basics, setBasics] = useState(true);
  const [err, setErr] = useState("");
  const [narrating, setNarrating] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    setWalk(null);
    setI(-1);
    setErr("");
    api<Walk>(`/walkthrough/${symbol}?tf=${timeframe}`).then(setWalk).catch((e) => setErr(e.message));
    return () => setFocus(null);
  }, [symbol, timeframe, setFocus]);

  const go = useCallback((n: number) => {
    if (!walk) return;
    const k = Math.max(-1, Math.min(walk.steps.length - 1, n));
    setI(k);
    if (k < 0) setFocus(null);
    else setFocus({ ids: walk.steps[k].focus_ids, objects: walk.steps[k].objects.map((o) => ({ ...o, origin: "ai" as const })) });
  }, [walk, setFocus]);

  useEffect(() => {
    if (!playing || !walk) return;
    if (i >= walk.steps.length - 1) {
      setPlaying(false);
      return;
    }
    timer.current = setTimeout(() => go(i + 1), basics ? 11000 : 7000);
    return () => { if (timer.current) clearTimeout(timer.current); };
  }, [playing, i, walk, go, basics]);

  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;
      if (e.key === "ArrowRight") go(i + 1);
      if (e.key === "ArrowLeft") go(i - 1);
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [i, go]);

  const narrate = async () => {
    setNarrating(true);
    try {
      const w = await api<Walk>(`/walkthrough/${symbol}?tf=${timeframe}&narrate=true`);
      setWalk((cur) => (cur ? { ...cur, narrative: w.narrative } : w));
    } finally {
      setNarrating(false);
    }
  };

  if (err) return <div className="p-3 text-amber-300">{err}</div>;
  if (!walk) return <div className="p-4 text-slate-400">Analysing {symbol} top-down ({timeframe} + higher timeframes)…</div>;
  const s = i >= 0 ? walk.steps[i] : null;
  const syn = walk.synthesis;
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-line px-3 py-2">
        <GraduationCap size={15} className="text-sky-400" />
        <span className="font-medium text-slate-100">Step-by-step analysis</span>
        <label className="ml-auto flex items-center gap-1 text-[11px] text-slate-400">
          <input type="checkbox" checked={basics} onChange={(e) => setBasics(e.target.checked)} /> beginner explanations
        </label>
        <button onClick={() => { setFocus(null); onClose(); }} className="text-slate-500 hover:text-slate-200"><X size={14} /></button>
      </div>
      {/* progress */}
      <div className="flex gap-0.5 px-3 pt-2">
        {walk.steps.map((st, k) => (
          <button key={st.key} title={st.title} onClick={() => go(k)}
            className={`h-1.5 flex-1 rounded ${k === i ? "bg-sky-400" : st.verdict > 0 ? "bg-emerald-600/60" : st.verdict < 0 ? "bg-red-600/60" : "bg-slate-700"}`} />
        ))}
      </div>
      <div className="min-h-0 flex-1 overflow-auto px-3 py-2">
        {!s ? (
          <div className="space-y-3">
            <div className="text-[12px] text-slate-400">How a human analyst reads {walk.symbol} — starting from {walk.higher_timeframes.join(" & ") || "this timeframe"}, then zooming in to {walk.timeframe}. The chart shows only what each step talks about.</div>
            <div className={`rounded border p-3 ${VERDICT_STYLE[syn.direction]}`}>
              <div className="text-[12px] font-medium opacity-80">Chart scanner&apos;s read (Lab, not the main signal: see the Signal tab)</div>
              <div className="text-lg font-semibold">{syn.direction.toUpperCase()} · {syn.confidence} confidence</div>
              <div className="text-[12px] opacity-90">{Math.round(syn.agreement * 100)}% of weighted evidence agrees · scanner call: <b>{walk.decision.action.replace("_", " ")}</b></div>
            </div>
            <ul className="space-y-1 text-[13px] leading-relaxed text-slate-200">{syn.story.map((x) => <li key={x}>• {x}</li>)}</ul>
            {walk.narrative && <div className="prose-sm rounded bg-panel2 p-2 text-[13px] text-slate-200"><ReactMarkdown>{walk.narrative}</ReactMarkdown></div>}
            {walk.llm_configured && !walk.narrative && (
              <button onClick={narrate} disabled={narrating} className="btn flex items-center gap-1"><MessageSquareText size={13} />{narrating ? "writing…" : "AI analyst commentary"}</button>
            )}
            <div className="grid grid-cols-1 gap-1">
              {walk.steps.map((st, k) => (
                <button key={st.key} onClick={() => go(k)} className="flex items-center gap-2 rounded px-2 py-1 text-left text-[12px] hover:bg-white/5">
                  <span className={`chip border ${VERDICT_STYLE[st.verdict_word]}`}>{st.verdict_word}</span>
                  <span className="text-slate-300">{st.title}</span>
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="flex items-start gap-2">
              <h3 className="text-[15px] font-semibold text-slate-100">{s.title}</h3>
              <span className={`chip ml-auto border ${VERDICT_STYLE[s.verdict_word]}`}>{s.verdict_word}</span>
            </div>
            {basics && (
              <div className="rounded border border-sky-500/20 bg-sky-500/5 p-2 text-[12.5px] leading-relaxed text-sky-100/90">
                <div className="mb-0.5 text-[11px] font-semibold text-sky-400">The concept</div>
                {s.plain}
                <Link href={`/learn#${s.learn}`} className="ml-1 text-sky-400 underline">Learn more →</Link>
              </div>
            )}
            <div>
              <div className="mb-0.5 text-[11px] font-semibold text-slate-500">What I see on {walk.symbol}</div>
              <p className="text-[13.5px] leading-relaxed text-slate-100">{s.observation}</p>
            </div>
            {s.bullets.length > 0 && (
              <ul className="space-y-1 text-[12.5px] text-slate-300">{s.bullets.map((b) => <li key={b} className="flex gap-1.5"><span className="text-slate-600">▸</span><span>{b}</span></li>)}</ul>
            )}
            {s.caution && <div className="rounded bg-amber-500/10 p-2 text-[11.5px] text-amber-200">⚠ {s.caution}</div>}
            {s.focus_ids.length + s.objects.length > 0
              ? <div className="text-[11px] text-slate-500">On the chart: {s.focus_ids.length + s.objects.length} highlighted object(s); everything else is hidden for this step. Click an object for its exact detection rule.</div>
              : <div className="text-[11px] text-slate-500">This step has no chart drawing — it reads numbers (shown above).</div>}
            {s.key === "plan" && <div className="text-[11px] text-slate-500">Current price {fmtPrice(walk.price)}.</div>}
          </div>
        )}
      </div>
      <div className="flex items-center gap-1 border-t border-line p-2">
        <button className="btn px-2" onClick={() => go(i - 1)} disabled={i < 0}><ChevronLeft size={14} /></button>
        <button className="btn flex items-center gap-1 px-2" onClick={() => { if (i < 0) go(0); setPlaying(!playing); }}>
          {playing ? <Pause size={13} /> : <Play size={13} />}{playing ? "Pause" : i < 0 ? "Play walkthrough" : "Auto-play"}
        </button>
        <button className="btn btn-primary flex items-center gap-1 px-3" onClick={() => go(i + 1)} disabled={i >= walk.steps.length - 1}>
          {i < 0 ? "Start" : "Next"} <ChevronRight size={14} />
        </button>
        <span className="ml-auto text-[11px] text-slate-500">{i < 0 ? "overview" : `${i + 1} / ${walk.steps.length}`} · ← → keys</span>
        {i >= 0 && <button className="text-[11px] text-sky-400" onClick={() => go(-1)}>summary</button>}
      </div>
    </div>
  );
}
