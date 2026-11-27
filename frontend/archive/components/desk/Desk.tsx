"use client";
import dynamic from "next/dynamic";
import { Brain, FastForward, Pause, Play, Radar, RotateCcw, Search } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import type { ChartControl, ToXY } from "@/components/chart/TradingChart";
import { type Scan, type Signal, SignalBadge, SignalLevels } from "@/components/SignalCard";
import { api } from "@/lib/api";
import { fmtPrice, pair } from "@/lib/format";
import { useStore } from "@/lib/store";
import type { ChartObject, Snapshot } from "@/lib/types";
import Cursors, { type Agent, el, type Pos } from "./Cursors";

const TradingChart = dynamic(() => import("@/components/chart/TradingChart"), { ssr: false });
const TFS = [["15m", "Scalp"], ["1h", "Intraday"], ["4h", "Swing"], ["1d", "Position"]] as const;

type Ev = { t: number; agent: string; type: "move" | "draw" | "say" | "focus" | "click" | "indicator" | "vp" | "think";
  time?: number; price?: number; object?: ChartObject; text?: string; tone?: string; reply_to?: string | null;
  ids?: string[]; target?: string; id?: string; params?: Record<string, string | number> };
type Session = { id: string; status: "running" | "done" | "error"; brain: string; model?: string; symbol: string | null;
  tf: string; agents: Agent[]; events: Ev[]; n_events: number; duration_ms: number; signal?: Signal; error?: string;
  synthesis?: { direction: string; confidence: string; agreement: number }; disclaimer: string;
  council?: { direction: string } | null };
type Msg = Ev & { key: number };
type Anim = { agent: string; obj: ChartObject; t0: number; dur: number };
type Coin = { symbol: string; base: string; price: number; change_24h_pct: number };

const TONE: Record<string, string> = { bull: "border-l-emerald-500", bear: "border-l-red-500", warn: "border-l-amber-500", neutral: "border-l-slate-600" };
const lerp = (a: number, b: number, k: number) => a + (b - a) * k;

/** The object part-way through being dragged out, and where the mouse is at that moment. */
function partial(o: ChartObject, k: number, lastTime: number): { obj: ChartObject; time: number; price: number } {
  if (o.points?.length) {
    const n = Math.max(1, Math.ceil(o.points.length * k));
    const p = o.points[n - 1];
    return { obj: { ...o, points: o.points.slice(0, n) }, time: p.time, price: p.price };
  }
  if (o.kind === "position") return { obj: o, time: o.time1 || lastTime, price: lerp(o.entry!, o.target!, k) };
  const t1 = o.time1 || lastTime;
  if (["trendline", "ray", "zone", "rect", "channel"].includes(o.kind) && o.price2 !== undefined) {
    const t2 = o.time2 || lastTime;
    const t = lerp(t1, t2, k), p = lerp(o.price1!, o.price2, k);
    return { obj: { ...o, time2: t, price2: p }, time: t, price: p };
  }
  if (o.kind === "fib" && o.price2 !== undefined) {
    const p = lerp(o.price1!, o.price2, k);
    return { obj: { ...o, price2: p }, time: t1, price: p };
  }
  if (o.kind === "hline") return { obj: o, time: lerp(t1, lastTime, k), price: o.price1! };
  return { obj: o, time: t1, price: o.price1 ?? 0 };
}

export default function Desk() {
  const { symbol, timeframe, setTimeframe, setSymbol, setFocus } = useStore();
  const [sess, setSess] = useState<Session | null>(null);
  const [events, setEvents] = useState<Ev[]>([]);
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [coins, setCoins] = useState<Coin[]>([]);
  const [scan, setScan] = useState<Scan | null>(null);
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<"ALL" | "BUY" | "SELL" | "WAIT">("ALL");
  const [err, setErr] = useState("");
  const [clock, setClock] = useState(0);
  const [playing, setPlaying] = useState(true);
  const [speed, setSpeed] = useState(1);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [talking, setTalking] = useState<Record<string, boolean>>({});
  const [thinking, setThinking] = useState<Record<string, string>>({});
  const [bubble, setBubble] = useState<Record<string, string>>({});
  const [clicks, setClicks] = useState<{ id: number; agent: string; x: number; y: number }[]>([]);
  const [spoken, setSpoken] = useState<Set<string>>(new Set());
  const [active, setActive] = useState<string | null>(null);
  const [auto, setAuto] = useState(true); // the Chief picks the coin
  const [runKey, setRunKey] = useState(0);
  const geo = useRef<ToXY | null>(null);
  const control = useRef<ChartControl | null>(null);
  const anchor = useRef<HTMLDivElement>(null);
  const pos = useRef<Record<string, Pos>>({});
  const dragging = useRef<Set<string>>(new Set());
  const anims = useRef<Anim[]>([]);
  const cursor = useRef(0);
  const drawn = useRef<{ ids: string[]; objects: ChartObject[] }>({ ids: [], objects: [] });
  const chatEnd = useRef<HTMLDivElement>(null);
  const talkTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const followSymbol = useRef<string | null>(null);
  const tf = TFS.some(([t]) => t === timeframe) ? timeframe : "4h";

  const resetPlayback = useCallback(() => {
    cursor.current = 0; drawn.current = { ids: [], objects: [] }; pos.current = {}; anims.current = []; dragging.current.clear();
    setClock(0); setMsgs([]); setTalking({}); setThinking({}); setBubble({}); setActive(null); setSpoken(new Set()); setClicks([]);
    control.current?.reset();
    setFocus({ ids: [], objects: [] });
  }, [setFocus]);

  useEffect(() => { if (timeframe !== tf) setTimeframe(tf); }, [timeframe, tf, setTimeframe]);
  useEffect(() => { api<{ coins: Coin[] }>("/coins").then((r) => setCoins(r.coins)).catch(() => {}); }, []);
  useEffect(() => () => setFocus(null), [setFocus]);

  // Scout's scan of every coin (left list)
  useEffect(() => {
    let stop = false;
    let id: ReturnType<typeof setTimeout> | undefined;
    const poll = () => api<Scan>(`/room/scan?tf=${tf}`).then((s) => {
      if (stop) return;
      setScan(s);
      id = setTimeout(poll, s.status === "running" ? 2500 : 60000);
    }).catch(() => { id = setTimeout(poll, 5000); });
    poll();
    return () => { stop = true; clearTimeout(id); };
  }, [tf]);

  // start a session (the agents keep working in the background; we poll for what they have done so far)
  useEffect(() => {
    if (!auto && followSymbol.current === symbol) return; // the chart just followed the Chief's pick
    let stop = false;
    let id: ReturnType<typeof setTimeout> | undefined;
    setSess(null); setEvents([]); setErr(""); setSnap(null); resetPlayback(); setPlaying(true);
    const qs = auto ? `tf=${tf}` : `symbol=${symbol}&tf=${tf}`;
    let n = 0;
    const take = (s: Session) => {
      if (stop) return;
      setSess(s);
      if (s.events.length) setEvents((e) => [...e, ...s.events]);
      n = s.n_events;
      if (s.symbol && s.symbol !== followSymbol.current) {
        followSymbol.current = s.symbol;
        if (s.symbol !== symbol) setSymbol(s.symbol);
        api<Snapshot>(`/analysis/${s.symbol}?tf=${tf}`).then((x) => !stop && setSnap(x)).catch(() => {});
      }
      if (s.status === "running") id = setTimeout(() => api<Session>(`/room/session/${s.id}?after=${n}`).then(take).catch(() => {
        id = setTimeout(() => take(s), 2000);
      }), 1200);
    };
    api<Session>(`/room/session?${qs}`, { method: "POST" }).then(take).catch((e) => setErr(e.message));
    return () => { stop = true; clearTimeout(id); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auto, tf, runKey, auto ? null : symbol]);

  // switching coins clears chart focus in the store; restore the agents' canvas
  useEffect(() => { setFocus({ ids: drawn.current.ids, objects: drawn.current.objects }); }, [symbol, setFocus]);

  const lastT = events.length ? events[events.length - 1].t : 0;
  const running = sess?.status === "running";
  // playback clock: never runs ahead of what the agents have actually done
  useEffect(() => {
    if (!playing || !sess) return;
    let last = performance.now(), id = 0;
    const loop = (now: number) => {
      setClock((c) => Math.min(c + (now - last) * speed, running ? lastT + 300 : lastT + 1));
      last = now;
      id = requestAnimationFrame(loop);
    };
    id = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(id);
  }, [playing, sess, speed, lastT, running]);

  const pushFocus = useCallback(() => {
    setFocus({ ids: drawn.current.ids, objects: drawn.current.objects.map((o) => ({ ...o, origin: "ai" as const })) });
  }, [setFocus]);

  // drag animations: the drawing grows from the press point while the cursor follows
  useEffect(() => {
    let id = 0;
    const loop = (now: number) => {
      if (anims.current.length) {
        const lt = snap ? Math.floor(snap.as_of / 1000) : Math.floor(Date.now() / 1000);
        anims.current = anims.current.filter((a) => {
          const k = Math.min(1, (now - a.t0) / a.dur);
          const pt = partial(a.obj, k, lt);
          drawn.current.objects = [...drawn.current.objects.filter((o) => o.id !== a.obj.id), k >= 1 ? a.obj : pt.obj];
          pos.current[a.agent] = { mode: "chart", time: pt.time, price: pt.price };
          if (k >= 1) { dragging.current.delete(a.agent); control.current?.setTool("cursor"); }
          return k < 1;
        });
        pushFocus();
      }
      id = requestAnimationFrame(loop);
    };
    id = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(id);
  }, [snap, pushFocus]);

  const ripple = useCallback((agent: string, target: string, delay: number, act?: () => void) => {
    setTimeout(() => {
      const t = el(target);
      if (t) {
        const b = t.getBoundingClientRect();
        const cid = Math.random();
        setClicks((c) => [...c, { id: cid, agent, x: b.left + b.width / 2, y: b.top + b.height / 2 }]);
        setTimeout(() => setClicks((c) => c.filter((x) => x.id !== cid)), 700);
        t.animate([{ outline: "2px solid #fde047", outlineOffset: "1px" }, { outline: "2px solid transparent" }], { duration: 700 });
      }
      act?.();
    }, delay);
  }, []);

  // apply every event whose time has come
  useEffect(() => {
    let changed = false;
    while (cursor.current < events.length && events[cursor.current].t <= clock) {
      const e = events[cursor.current++];
      const a = e.agent;
      setActive(a);
      if (e.type === "think") { setThinking((t) => ({ ...t, [a]: e.text || "thinking" })); if (!pos.current[a]) pos.current[a] = { mode: "park" }; }
      if (e.type === "move" && e.time && e.price) pos.current[a] = { mode: "chart", time: e.time, price: e.price };
      if (e.type === "click" && e.target) {
        const target = e.target;
        setTimeout(() => el(target)?.scrollIntoView({ block: "nearest" }), 50);
        pos.current[a] = { mode: "el", target };
        ripple(a, target, 600 / Math.sqrt(speed), () => {
          if (target.startsWith("tool:")) control.current?.setTool(target.slice(5) as never);
          if (target === "indicators") control.current?.openMenu("ind");
        });
      }
      if (e.type === "indicator" && e.id) { control.current?.addIndicator(e.id, e.params || {}); control.current?.openMenu(null); }
      if (e.type === "vp") control.current?.setProfile(true);
      if (e.type === "draw" && e.object) {
        dragging.current.add(a);
        anims.current.push({ agent: a, obj: e.object, t0: performance.now(), dur: 750 / speed });
      }
      if (e.type === "focus" && e.ids) { drawn.current.ids = [...new Set([...drawn.current.ids, ...e.ids])]; changed = true; }
      if (e.type === "say") {
        setThinking((t) => { const n = { ...t }; delete n[a]; return n; });
        setMsgs((m) => [...m, { ...e, key: cursor.current }]);
        setBubble((b) => ({ ...b, [a]: e.text || "" }));
        setTalking((t) => ({ ...t, [a]: true }));
        setSpoken((s) => (s.has(a) ? s : new Set([...s, a])));
        clearTimeout(talkTimers.current[a]);
        talkTimers.current[a] = setTimeout(() => setTalking((t) => ({ ...t, [a]: false })), Math.max(1600, 3000 / speed));
        if (!pos.current[a]) pos.current[a] = { mode: "park" };
      }
    }
    if (changed) pushFocus();
  }, [clock, events, speed, ripple, pushFocus]);

  const done = !!sess && sess.status !== "running" && cursor.current >= events.length && clock >= lastT;
  useEffect(() => {
    if (!done) return;
    const id = setTimeout(() => control.current?.openMenu(null), 900);
    return () => clearTimeout(id);
  }, [done]);
  useEffect(() => { chatEnd.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [msgs.length]);

  const agents = useMemo(() => sess?.agents || [], [sess]);
  const byId = useMemo(() => Object.fromEntries(agents.map((a) => [a.id, a])), [agents]);
  const sig = sess?.signal;
  const scanned = useMemo(() => new Map((scan?.rows || []).map((r) => [r.symbol, r])), [scan]);
  const list = useMemo(() => {
    const term = q.trim().toUpperCase();
    if (term) return coins.filter((c) => c.base.includes(term) || c.symbol.includes(term)).slice(0, 60)
      .map((c) => ({ symbol: c.symbol, price: c.price, row: scanned.get(c.symbol) }));
    return (scan?.rows || []).filter((r) => filter === "ALL" || r.signal.action === filter)
      .sort((a, b) => (a.signal.action === "WAIT" ? 1 : 0) - (b.signal.action === "WAIT" ? 1 : 0) || b.score - a.score)
      .map((r) => ({ symbol: r.symbol, price: r.price, row: r }));
  }, [q, coins, scan, scanned, filter]);
  const count = (a: string) => (scan?.rows || []).filter((r) => r.signal.action === a).length;
  const pick = (s: string) => { setAuto(false); followSymbol.current = null; setSymbol(s); setQ(""); };
  const thinkingNow = Object.entries(thinking).filter(([k]) => !talking[k]);

  return (
    <div className="grid h-full grid-cols-[250px_1fr_400px] grid-rows-[auto_1fr_58px] overflow-hidden">
      {/* ---------------------------------------------------------------- top bar */}
      <div className="col-span-3 flex flex-wrap items-center gap-2 border-b border-line bg-panel px-3 py-1.5">
        <div className="flex rounded border border-line">{TFS.map(([t, label]) => (
          <button key={t} onClick={() => setTimeframe(t)} title={t} className={`px-2.5 py-0.5 text-[12px] ${t === tf ? "bg-sky-500/20 text-sky-200" : "text-slate-400 hover:text-slate-200"}`}>{label} <span className="text-[10px] text-slate-500">{t}</span></button>
        ))}</div>
        <button onClick={() => { setAuto(true); followSymbol.current = null; setRunKey((k) => k + 1); }}
          className={`btn flex items-center gap-1 ${auto ? "border-yellow-400/60 bg-yellow-400/15 text-yellow-200" : ""}`}>
          <Radar size={12} /> {auto ? "Chief picks the best coin" : "Let Chief pick the coin"}
        </button>
        <span className="text-[13px] font-semibold text-slate-100">{sess?.symbol ? pair(sess.symbol) : auto ? "choosing coin…" : pair(symbol)}</span>
        <button className="btn btn-primary flex items-center gap-1" disabled={!sess} onClick={() => setPlaying(!playing)}>
          {playing ? <Pause size={12} /> : <Play size={12} />} {playing ? "Pause" : "Play"}
        </button>
        <button className="btn" title="Watch again" disabled={!done} onClick={() => { resetPlayback(); setPlaying(true); }}><RotateCcw size={12} /></button>
        <button className="btn" title="New analysis" onClick={() => { followSymbol.current = null; setRunKey((k) => k + 1); }}>New analysis</button>
        <div className="flex gap-0.5 text-[11px]">{[1, 2, 4].map((s) => <button key={s} onClick={() => setSpeed(s)} className={`rounded px-1.5 py-0.5 ${s === speed ? "bg-white/10 text-slate-100" : "text-slate-500"}`}>{s}×</button>)}</div>
        {err && <span className="text-[11px] text-amber-300">{err}</span>}
        <span className={`ml-auto flex items-center gap-1 rounded border px-2 py-0.5 text-[11px] ${sess?.brain && sess.brain !== "rules" ? "border-emerald-500/40 text-emerald-300" : "border-amber-500/40 text-amber-300"}`}
          title={sess?.brain === "rules" ? "Add AIT_OPENAI_API_KEY (or AIT_ANTHROPIC_API_KEY) to backend/.env and restart the backend to give the agents an AI brain." : "Agents reason with an LLM over measured market data"}>
          <Brain size={12} /> {!sess ? "…" : sess.brain === "rules" ? "AI brain off · rule-based agents (add an API key to switch on)" : `AI agents · ${sess.model}`}
        </span>
      </div>

      {/* ---------------------------------------------------------------- coin list */}
      <aside className="row-span-2 flex min-h-0 flex-col border-r border-line bg-panel">
        <div className="relative p-2">
          <Search size={12} className="absolute left-4 top-[18px] text-slate-500" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={`Search ${coins.length || ""} coins`} className="input w-full py-1 pl-6" />
        </div>
        <div className="flex gap-0.5 px-2 pb-1 text-[10px]">{(["ALL", "BUY", "SELL", "WAIT"] as const).map((f) => (
          <button key={f} onClick={() => setFilter(f)} className={`flex-1 rounded py-0.5 ${f === filter ? "bg-white/10 text-slate-100" : "text-slate-500 hover:text-slate-300"}`}>
            {f === "ALL" ? `All ${scan?.rows.length || ""}` : `${f} ${count(f)}`}
          </button>))}
        </div>
        <div className="min-h-0 flex-1 overflow-auto px-1">
          {list.map((c) => (
            <button key={c.symbol} data-agent-target={`scan:${c.symbol}`} onClick={() => pick(c.symbol)} title={c.row?.reasons.join(" · ")}
              className={`flex w-full items-center gap-1.5 rounded px-1.5 py-1 text-left text-[12px] hover:bg-white/5 ${sess?.symbol === c.symbol ? "bg-white/10" : ""}`}>
              <b className="w-[88px] truncate text-slate-100">{pair(c.symbol)}</b>
              <span className="num flex-1 truncate text-right text-[11px] text-slate-400">{fmtPrice(c.price)}</span>
              {c.row ? <SignalBadge action={c.row.signal.action} /> : <span className="text-[9px] text-slate-600">analyse</span>}
            </button>
          ))}
          {!list.length && <div className="p-2 text-[11px] text-slate-500">{scan?.status === "running" ? `Scout is scanning every coin… ${scan.done}/${scan.total}` : "No coins match."}</div>}
        </div>
        <div className="border-t border-line p-2 text-[10px] leading-snug text-slate-500">
          {scan ? `${scan.all_coins || scan.all || "All"} USDT coins screened, ${scan.rows.length} fully analysed${scan.status === "running" ? ` (updating ${scan.done}/${scan.total})` : ""}.` : "Loading coins…"}
        </div>
      </aside>

      {/* ---------------------------------------------------------------- chart */}
      <div className="relative min-h-0">
        <TradingChart snapshot={snap} signals={[]} geoRef={geo} control={control} indicatorPreset={[]}
          overlay={<div ref={anchor} className="pointer-events-none absolute inset-0" />} />
        {sess && <Cursors agents={agents} pos={pos} anchor={anchor} geo={geo} talking={talking} thinking={thinking} bubble={bubble}
          clicks={clicks} dragging={dragging} speed={speed} />}
      </div>

      {/* ---------------------------------------------------------------- signal + discussion */}
      <aside className="row-span-2 flex min-h-0 flex-col border-l border-line bg-panel">
        <div className="border-b border-line p-3">
          {done && sig ? (
            <div>
              <div className="flex items-center gap-2">
                <SignalBadge action={sig.action} big /><b className="text-lg text-slate-100">{sess?.symbol ? pair(sess.symbol) : ""}</b>
                <span className="text-[11px] text-slate-500">{tf}{sig.strategy ? ` · ${sig.strategy}` : ""}</span>
              </div>
              <div className="mt-1.5 text-[13px] leading-snug text-slate-200">{sig.text}</div>
              {sig.action !== "WAIT" && <SignalLevels s={sig} />}
            </div>
          ) : (
            <div className="flex items-center gap-2 text-[12px] text-slate-300">
              <span className="inline-block h-2 w-2 shrink-0 animate-pulse rounded-full bg-sky-400" />
              <span>{!sess ? "Starting the desk…" : thinkingNow.length ? `${byId[thinkingNow[0][0]]?.name}: ${thinkingNow[0][1]}` : `${spoken.size} agents have spoken`} — the signal appears when Chief makes the call.</span>
              {sess && sess.status !== "running" && <button className="btn ml-auto flex shrink-0 items-center gap-1 py-0.5 text-[11px]" onClick={() => setClock(lastT)}><FastForward size={11} /> Skip</button>}
            </div>
          )}
          {sess?.status === "error" && <div className="mt-1 text-[11px] text-amber-300">The session stopped: {sess.error}</div>}
        </div>
        <div className="px-3 pt-2 text-[11px] font-semibold uppercase tracking-wider text-slate-400">Team discussion</div>
        <div className="min-h-0 flex-1 space-y-1.5 overflow-auto p-2">
          {msgs.map((m) => {
            const a = byId[m.agent];
            return (
              <div key={m.key} className={`rounded border-l-2 bg-white/[0.03] px-2 py-1.5 text-[12px] ${TONE[m.tone || "neutral"] || TONE.neutral}`}>
                <div className="flex items-center gap-1.5 text-[11px]">
                  <span className="inline-block h-2 w-2 rounded-full" style={{ background: a?.color }} />
                  <b style={{ color: a?.color }}>{a?.name}</b><span className="truncate text-slate-500">{a?.role}</span>
                  {m.reply_to && <span className="ml-auto shrink-0 text-slate-500">↪ @{byId[m.reply_to]?.name}</span>}
                </div>
                <div className="prose-chat mt-0.5 text-slate-200"><ReactMarkdown>{m.text || ""}</ReactMarkdown></div>
              </div>
            );
          })}
          {thinkingNow.map(([k, t]) => (
            <div key={k} className="flex items-center gap-1.5 px-2 text-[11px] italic text-slate-500">
              <span className="inline-block h-2 w-2 animate-pulse rounded-full" style={{ background: byId[k]?.color }} />{byId[k]?.name} is {t.toLowerCase().replace(/…$/, "")}…
            </div>
          ))}
          {done && <div className="px-1 text-[10px] text-slate-500">{sess?.disclaimer} A trade is only possible on a setup that passed every safety check; agents can explain and decide, but can&apos;t invent levels.</div>}
          <div ref={chatEnd} />
        </div>
      </aside>

      {/* ---------------------------------------------------------------- agent roster */}
      <div className="flex items-stretch gap-1 overflow-x-auto border-t border-line bg-panel px-2 py-1.5">
        {agents.map((a) => (
          <div key={a.id} title={a.role} className={`flex w-[112px] shrink-0 flex-col justify-center rounded border px-2 text-[11px] transition ${active === a.id ? "border-white/40 bg-white/5" : "border-line"} ${spoken.has(a.id) || thinking[a.id] ? "" : "opacity-50"}`}>
            <div className="flex items-center gap-1.5"><span className={`inline-block h-2.5 w-2.5 rounded-full ${talking[a.id] || thinking[a.id] ? "animate-pulse" : ""}`} style={{ background: a.color }} /><b className="text-slate-100">{a.name}</b>
              <span className="ml-auto text-[9px] text-slate-500">{talking[a.id] ? "talking" : thinking[a.id] ? "thinking" : spoken.has(a.id) ? "✓" : ""}</span></div>
            <div className="truncate leading-tight text-slate-500">{a.role}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
