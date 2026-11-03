"use client";
import dynamic from "next/dynamic";
import { GripVertical, Pause, Play, RotateCcw, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import { fmtPrice, pair } from "@/lib/format";
import { useStore } from "@/lib/store";
import { TIMEFRAMES, type ChartObject, type Snapshot } from "@/lib/types";

const TradingChart = dynamic(() => import("@/components/chart/TradingChart"), { ssr: false });

type Step = {
  key: string; title: string; plain: string; observation: string; verdict: number; verdict_word: string;
  bullets: string[]; focus_ids: string[]; objects: ChartObject[]; caution?: string | null; timeframe: string;
};
type Rules = { direction_threshold: number; high_agreement: number; medium_agreement: number; stress_regimes: string[] };
type Walk = {
  symbol: string; timeframe: string; price: number; steps: Step[]; weights: Record<string, number>; rules: Rules;
  regime: string; higher_timeframes: string[]; disclaimer: string;
};

const ICON: Record<string, string> = {
  big_picture: "🔭", regime: "🌦", structure: "📐", levels: "📏", liquidity: "💧", zones: "🧱", imbalance: "⚡",
  premium_discount: "⚖️", elliott: "🌊", momentum: "🚀", derivatives: "📊", news: "📰",
};
const CONCEPT_KEYS = Object.keys(ICON);
const vColor = (v: number) => (v > 0 ? "text-emerald-300 border-emerald-500/40 bg-emerald-500/10" : v < 0 ? "text-red-300 border-red-500/40 bg-red-500/10" : "text-slate-300 border-slate-600/50 bg-slate-600/10");
const vWord = (v: number) => (v > 0 ? "bullish" : v < 0 ? "bearish" : "neutral");

/** Same arithmetic as backend `walkthrough.synthesize`, so dragging/weighting updates instantly. */
function synthesize(steps: Step[], w: Record<string, number>, r: Rules, regime: string) {
  const wt = (s: Step) => w[s.key] ?? 0;
  const total = steps.reduce((a, s) => a + wt(s), 0);
  const bias = total ? steps.reduce((a, s) => a + wt(s) * s.verdict, 0) / total : 0;
  const voting = steps.filter((s) => s.verdict !== 0);
  const vsum = voting.reduce((a, s) => a + wt(s), 0);
  const agree = voting.filter((s) => (s.verdict > 0) === (bias > 0));
  const disagree = voting.filter((s) => (s.verdict > 0) !== (bias > 0));
  const agreement = vsum ? agree.reduce((a, s) => a + wt(s), 0) / vsum : 0;
  const direction = bias > r.direction_threshold ? "bullish" : bias < -r.direction_threshold ? "bearish" : "neutral";
  const big = steps.find((s) => s.key === "big_picture"), st = steps.find((s) => s.key === "structure");
  const sgn = bias > 0 ? 1 : -1;
  let confidence = direction === "neutral" ? "low"
    : agreement >= r.high_agreement && big && st && big.verdict === sgn && st.verdict === sgn ? "high"
    : agreement >= r.medium_agreement ? "medium" : "low";
  if (confidence === "high" && r.stress_regimes.includes(regime)) confidence = "medium";
  return { bias, direction, confidence, agreement, agree, disagree, total };
}

export default function Board() {
  const { symbol, timeframe, setTimeframe, setSymbol, setFocus } = useStore();
  const [walk, setWalk] = useState<Walk | null>(null);
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [markets, setMarkets] = useState<string[]>([]);
  const [active, setActive] = useState<string[]>([]);
  const [weights, setWeights] = useState<Record<string, number>>({});
  const [inspect, setInspect] = useState<string | null>(null);
  const [thoughts, setThoughts] = useState<string[]>([]);
  const [playing, setPlaying] = useState(false);
  const [dragOver, setDragOver] = useState<number | "lib" | null>(null);
  const [err, setErr] = useState("");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => { api<any>("/markets").then((r) => setMarkets(r.markets.map((m: any) => m.symbol))).catch(() => {}); }, []);
  useEffect(() => {
    setWalk(null); setErr(""); setInspect(null); setThoughts([]); setPlaying(false);
    api<Walk>(`/walkthrough/${symbol}?tf=${timeframe}`).then((w) => {
      setWalk(w);
      setActive(w.steps.filter((s) => CONCEPT_KEYS.includes(s.key)).map((s) => s.key));
      setWeights({ regime: 0, ...w.weights });
    }).catch((e) => setErr(e.message));
    api<Snapshot>(`/analysis/${symbol}?tf=${timeframe}`).then(setSnap).catch(() => setSnap(null));
    return () => setFocus(null);
  }, [symbol, timeframe, setFocus]);

  // step titles arrive numbered ("3. Market structure …"); the board numbers by chain position instead
  const byKey = useMemo(() => Object.fromEntries((walk?.steps || []).map((s) => [s.key, { ...s, title: s.title.replace(/^\d+\.\s*/, "") }])), [walk]);
  const chain = useMemo(() => active.map((k) => byKey[k]).filter(Boolean) as Step[], [active, byKey]);
  const syn = walk ? synthesize(chain, weights, walk.rules, walk.regime) : null;

  const show = useCallback((k: string | null) => {
    setInspect(k);
    const s = k ? byKey[k] : null;
    setFocus(s ? { ids: s.focus_ids, objects: s.objects.map((o) => ({ ...o, origin: "ai" as const })) } : null);
  }, [byKey, setFocus]);

  // "Replay agent thinking": walk the chain in order, narrating each concept like an analyst would.
  const step = thoughts.length;
  useEffect(() => {
    if (!playing) return;
    const n = step;
    if (n >= chain.length) { setPlaying(false); show(null); return; }
    const s = chain[n];
    show(s.key);
    timer.current = setTimeout(() => setThoughts((t) => [...t, `${ICON[s.key] ?? "•"} ${s.title}: ${s.observation} → ${vWord(s.verdict)}`]), 3500);
    return () => { if (timer.current) clearTimeout(timer.current); };
  }, [playing, step, chain, show]);

  const onDragStart = (e: React.DragEvent, key: string, from: "lib" | "chain") => {
    e.dataTransfer.setData("text/plain", JSON.stringify({ key, from }));
    e.dataTransfer.effectAllowed = "move";
  };
  const read = (e: React.DragEvent) => { try { return JSON.parse(e.dataTransfer.getData("text/plain")) as { key: string; from: string }; } catch { return null; } };
  const dropAt = (e: React.DragEvent, idx: number) => {
    e.preventDefault(); setDragOver(null);
    const d = read(e); if (!d) return;
    setActive((a) => {
      const without = a.filter((k) => k !== d.key);
      const at = Math.min(idx - (a.indexOf(d.key) > -1 && a.indexOf(d.key) < idx ? 1 : 0), without.length);
      return [...without.slice(0, at), d.key, ...without.slice(at)];
    });
  };
  const dropLib = (e: React.DragEvent) => { e.preventDefault(); setDragOver(null); const d = read(e); if (d) setActive((a) => a.filter((k) => k !== d.key)); };
  const reset = () => { if (!walk) return; setActive(walk.steps.filter((s) => CONCEPT_KEYS.includes(s.key)).map((s) => s.key)); setWeights({ regime: 0, ...walk.weights }); setThoughts([]); };

  const inspected = inspect ? byKey[inspect] : null;
  const plan = byKey["plan"], scen = byKey["scenarios"];

  return (
    <div className="grid h-full grid-cols-[230px_1fr_340px] overflow-hidden">
      {/* ---------------- concept library ---------------- */}
      <aside onDragOver={(e) => { e.preventDefault(); setDragOver("lib"); }} onDragLeave={() => setDragOver(null)} onDrop={dropLib}
        className={`flex flex-col overflow-auto border-r border-line bg-panel p-2 ${dragOver === "lib" ? "bg-red-500/5" : ""}`}>
        <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-slate-400">Concept library</div>
        <p className="mb-2 text-[11px] text-slate-500">Drag a concept onto the reasoning chain to make the agent use it. Drag it back here to remove it.</p>
        {CONCEPT_KEYS.map((k) => {
          const s = byKey[k]; const used = active.includes(k);
          return (
            <div key={k} draggable={!!s} onDragStart={(e) => onDragStart(e, k, "lib")} onClick={() => s && show(k)}
              className={`mb-1.5 cursor-grab rounded border px-2 py-1.5 text-[12px] ${used ? "border-line/40 opacity-45" : "border-line hover:border-sky-500/50"} ${inspect === k ? "ring-1 ring-sky-500" : ""}`}>
              <div className="flex items-center gap-1.5"><GripVertical size={12} className="text-slate-600" />{ICON[k]} <span className="text-slate-200">{s?.title ?? k}</span>
                {s && <span className={`ml-auto rounded border px-1 text-[9px] ${vColor(s.verdict)}`}>{vWord(s.verdict)}</span>}</div>
              {used && <div className="pl-5 text-[10px] text-slate-500">in use</div>}
            </div>
          );
        })}
      </aside>

      {/* ---------------- chart + reasoning chain ---------------- */}
      <section className="flex min-w-0 flex-col">
        <div className="flex items-center gap-2 border-b border-line bg-panel px-3 py-1.5">
          <select className="input py-0.5" value={symbol} onChange={(e) => setSymbol(e.target.value)}>
            {(markets.includes(symbol) ? markets : [symbol, ...markets]).map((m) => <option key={m} value={m}>{pair(m)}</option>)}
          </select>
          <span className="num text-slate-300">{walk ? fmtPrice(walk.price) : ""}</span>
          <div className="flex gap-0.5">{TIMEFRAMES.map((t) => <button key={t} onClick={() => setTimeframe(t)} className={`rounded px-1.5 py-0.5 text-[11px] ${t === timeframe ? "bg-sky-500/20 text-sky-300" : "text-slate-500 hover:text-slate-200"}`}>{t}</button>)}</div>
          {inspected && <button className="ml-2 flex items-center gap-1 rounded bg-sky-500/15 px-2 py-0.5 text-[11px] text-sky-300" onClick={() => show(null)}>showing: {inspected.title} <X size={11} /></button>}
          {err && <span className="text-[11px] text-amber-300">{err}</span>}
          <span className="ml-auto text-[10px] text-slate-500">Analysis Board · higher timeframes {walk?.higher_timeframes.join(", ") || "—"}</span>
        </div>
        <div className="min-h-0 flex-1"><TradingChart snapshot={snap} signals={[]} /></div>
        <div className="h-[250px] shrink-0 border-t border-line bg-panel">
          <div className="flex items-center gap-2 px-3 pt-2 text-[11px]">
            <span className="font-semibold uppercase tracking-wider text-slate-400">Agent reasoning chain</span>
            <span className="text-slate-500">left → right, like an analyst working top-down · drag to reorder · slider = how much the agent trusts it</span>
          </div>
          <div className="flex h-[215px] items-stretch gap-0 overflow-x-auto px-3 py-2">
            {chain.map((s, i) => {
              const w = weights[s.key] ?? 0;
              const contrib = syn && syn.total ? (w * s.verdict) / syn.total : 0;
              return (
                <div key={s.key} className="flex items-stretch">
                  <div onDragOver={(e) => { e.preventDefault(); setDragOver(i); }} onDrop={(e) => dropAt(e, i)}
                    className={`w-2 rounded ${dragOver === i ? "bg-sky-500/60" : ""}`} />
                  <div draggable onDragStart={(e) => onDragStart(e, s.key, "chain")} onClick={() => show(s.key)}
                    className={`flex w-[170px] shrink-0 cursor-grab flex-col rounded-md border p-2 text-[11px] ${vColor(s.verdict)} ${inspect === s.key ? "ring-2 ring-sky-400" : ""} ${playing && thoughts.length === i ? "animate-pulse" : ""}`}>
                    <div className="flex items-center gap-1 text-[12px] font-medium text-slate-100"><span className="text-slate-500">{i + 1}</span> {ICON[s.key]} {s.title}</div>
                    <div className="mt-0.5 font-semibold uppercase">{vWord(s.verdict)}</div>
                    <div className="mt-1 line-clamp-4 text-slate-300">{s.observation}</div>
                    <div className="mt-auto" onClick={(e) => e.stopPropagation()}>
                      <div className="flex justify-between text-[10px] text-slate-400"><span>trust {w.toFixed(1)}</span><span className={contrib > 0 ? "text-up" : contrib < 0 ? "text-down" : ""}>{contrib >= 0 ? "+" : ""}{(contrib * 100).toFixed(0)} pts</span></div>
                      <input type="range" min={0} max={3} step={0.1} value={w} draggable={false} onDragStart={(e) => e.preventDefault()}
                        onChange={(e) => setWeights({ ...weights, [s.key]: +e.target.value })} className="w-full accent-sky-500" />
                    </div>
                  </div>
                  <div className="flex items-center px-0.5 text-slate-600">→</div>
                </div>
              );
            })}
            <div onDragOver={(e) => { e.preventDefault(); setDragOver(chain.length); }} onDrop={(e) => dropAt(e, chain.length)}
              className={`flex w-[150px] shrink-0 flex-col items-center justify-center rounded-md border-2 border-dashed p-2 text-center text-[11px] ${dragOver === chain.length ? "border-sky-400 text-sky-300" : "border-line text-slate-500"}`}>
              {syn && <div className={`text-sm font-semibold uppercase ${syn.direction === "bullish" ? "text-up" : syn.direction === "bearish" ? "text-down" : "text-slate-300"}`}>{syn.direction}</div>}
              <div>conclusion</div><div className="mt-2 text-[10px]">drop a concept here to add it</div>
            </div>
          </div>
        </div>
      </section>

      {/* ---------------- decision + thinking ---------------- */}
      <aside className="flex flex-col gap-2 overflow-auto border-l border-line bg-panel p-3 text-[12px]">
        {!walk || !syn ? <div className="text-slate-500">{err || "The agent is analysing…"}</div> : (
          <>
            <div className="card p-3">
              <div className="text-[10px] uppercase tracking-wider text-slate-500">Agent conclusion (live)</div>
              <div className={`text-xl font-semibold uppercase ${syn.direction === "bullish" ? "text-up" : syn.direction === "bearish" ? "text-down" : "text-slate-200"}`}>{syn.direction}</div>
              <div className="text-slate-400">confidence <b className="text-slate-200">{syn.confidence}</b> · agreement <b className="text-slate-200">{(syn.agreement * 100).toFixed(0)}%</b></div>
              <div className="relative mt-2 h-2 rounded bg-gradient-to-r from-red-500/50 via-slate-600/40 to-emerald-500/50">
                <div className="absolute -top-1 h-4 w-1 rounded bg-white" style={{ left: `calc(${((syn.bias + 1) / 2) * 100}% - 2px)` }} />
              </div>
              <div className="mt-0.5 flex justify-between text-[10px] text-slate-500"><span>bearish</span><span>bias {syn.bias >= 0 ? "+" : ""}{syn.bias.toFixed(2)}</span><span>bullish</span></div>
              <div className="mt-2 grid grid-cols-2 gap-2 text-[11px]">
                <div><div className="text-slate-500">Supports</div>{syn.agree.map((s) => <div key={s.key} className="text-slate-300">{ICON[s.key]} {s.title}</div>)}{!syn.agree.length && <div className="text-slate-600">—</div>}</div>
                <div><div className="text-slate-500">Against</div>{syn.disagree.map((s) => <div key={s.key} className="text-slate-300">{ICON[s.key]} {s.title}</div>)}{!syn.disagree.length && <div className="text-slate-600">—</div>}</div>
              </div>
              <div className="mt-2 flex gap-2">
                <button className="btn btn-primary flex items-center gap-1" onClick={() => { if (playing) setPlaying(false); else { setThoughts([]); setPlaying(true); } }}>
                  {playing ? <Pause size={12} /> : <Play size={12} />} {playing ? "Pause" : "Replay agent thinking"}
                </button>
                <button className="btn flex items-center gap-1" onClick={reset}><RotateCcw size={12} /> Reset</button>
              </div>
            </div>

            {(thoughts.length > 0 || playing) && (
              <div className="card p-3">
                <div className="mb-1 text-[10px] uppercase tracking-wider text-slate-500">Agent&apos;s thought process</div>
                <ol className="space-y-1.5">
                  {thoughts.map((t, i) => <li key={i} className="text-slate-300">{i + 1}. {t}</li>)}
                  {playing && chain[thoughts.length] && <li className="animate-pulse text-sky-300">{thoughts.length + 1}. Looking at {chain[thoughts.length].title.toLowerCase()} on the chart…</li>}
                  {!playing && thoughts.length === chain.length && <li className="font-medium text-slate-100">⇒ Conclusion: {syn.direction}, {syn.confidence} confidence ({(syn.agreement * 100).toFixed(0)}% of the trusted evidence agrees).</li>}
                </ol>
              </div>
            )}

            {inspected && (
              <div className="card p-3">
                <div className="flex items-center gap-1 font-medium text-slate-100">{ICON[inspected.key]} {inspected.title}<span className={`ml-auto rounded border px-1 text-[10px] ${vColor(inspected.verdict)}`}>{vWord(inspected.verdict)}</span></div>
                <div className="mt-1 text-[11px] text-slate-400"><b className="text-slate-300">Concept: </b>{inspected.plain}</div>
                <div className="mt-1 text-slate-200"><b>What I see: </b>{inspected.observation}</div>
                <ul className="mt-1 list-disc pl-4 text-[11px] text-slate-400">{inspected.bullets.slice(0, 6).map((b, i) => <li key={i}>{b}</li>)}</ul>
                {inspected.caution && <div className="mt-1 text-[11px] text-amber-300">⚠ {inspected.caution}</div>}
              </div>
            )}

            {scen && <div className="card p-3"><div className="font-medium text-slate-100">🎯 {scen.title}</div><div className="mt-1 text-slate-300">{scen.observation}</div>
              <button className="mt-1 text-[11px] text-sky-300 hover:underline" onClick={() => show("scenarios")}>show triggers on chart</button></div>}
            {plan && <div className="card p-3"><div className="font-medium text-slate-100">📝 {plan.title}</div><div className="mt-1 text-slate-300">{plan.observation}</div>
              <div className="mt-1 text-[10px] text-slate-500">The plan uses the agent&apos;s default trust settings, not your edits.</div></div>}
            <p className="text-[10px] text-slate-500">{walk.disclaimer}</p>
          </>
        )}
      </aside>
    </div>
  );
}
