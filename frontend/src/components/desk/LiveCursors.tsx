"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { ToXY } from "@/components/chart/TradingChart";

/** One thing an agent really did (from the backend's tool callbacks): click/hover an element, point at prices on the
 *  chart, drag out the plan, or say something. */
export type DeskEvent = {
  i: number; t: number; agent: string; kind: "think" | "move" | "draw" | "say";
  click?: string; el?: string; points?: { price: number; time: number }[]; label?: string; text?: string;
  draw?: { side: "LONG" | "SHORT"; entry: number; stop: number; target: number; time: number };
};
export type CursorAgent = { id: string; name: string; color: string };
export type DrawPhase = "entry" | "stop" | "target";
type Pos = { mode: "chart"; time: number; price: number } | { mode: "el"; target: string };
type Motion = { x: number; y: number; sx: number; sy: number; cx: number; cy: number; tx: number; ty: number; t0: number;
  dur: number; seed: number; jx: number; jy: number };

const el = (t: string) => document.querySelector(`[data-agent-target="${t}"]`) as HTMLElement | null;
const ease = (k: number) => (k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2);
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const jitter = (n: number) => (Math.random() - 0.5) * n;

/**
 * Plays the desk's real events as mouse cursors, one per agent, in the order the agents acted. Motion imitates a hand:
 * a curved path (Bézier with a random bend), duration from distance (Fitts's law), ease-in/out, a small overshoot that
 * settles, a short hesitation before clicks, an aim that is never pixel-exact, and a faint idle drift. When the agents
 * are ahead of the playback, it speeds up (and finally skips) so the cursors never lag minutes behind the work.
 */
export function useCursorPlayer(events: DeskEvent[], session: string | null, onDraw: (a: string, d: NonNullable<DeskEvent["draw"]>, phase: DrawPhase) => void,
  enabled: boolean) {
  const pos = useRef<Record<string, Pos>>({});
  const dragging = useRef<Set<string>>(new Set());
  const evs = useRef<DeskEvent[]>(events);
  const idx = useRef(0);
  const [bubble, setBubble] = useState<Record<string, { text: string; kind: "do" | "say" | "think" }>>({});
  const [clicks, setClicks] = useState<{ id: number; agent: string; x: number; y: number }[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [played, setPlayed] = useState(0);
  const drawRef = useRef(onDraw);
  useEffect(() => { evs.current = events; }, [events]);
  useEffect(() => { drawRef.current = onDraw; }, [onDraw]);

  const ripple = useCallback((agent: string, target: string) => {
    const b = el(target)?.getBoundingClientRect();
    if (!b) return;
    const id = Math.random();
    setClicks((c) => [...c, { id, agent, x: b.left + b.width / 2, y: b.top + b.height / 2 }]);
    setTimeout(() => setClicks((c) => c.filter((x) => x.id !== id)), 700);
    el(target)?.animate([{ outline: "2px solid #fde047", outlineOffset: "1px" }, { outline: "2px solid transparent" }], { duration: 700 });
  }, []);

  const skip = useCallback(() => { idx.current = evs.current.length; setPlayed(idx.current); }, []);

  useEffect(() => {
    let stop = false;
    pos.current = {}; dragging.current.clear(); idx.current = 0;
    setBubble({}); setClicks([]); setActive(null); setPlayed(0);
    if (!session || !enabled) return () => { stop = true; };
    const say = (a: string, text: string, kind: "do" | "say" | "think") => setBubble((b) => ({ ...b, [a]: { text, kind } }));
    const goEl = (a: string, target?: string) => {
      if (!target) return;
      const node = el(target);
      if (node && (target.startsWith("card:") || target.startsWith("panel:"))) node.scrollIntoView({ block: "nearest", behavior: "smooth" });
      pos.current[a] = { mode: "el", target };
    };
    const play = async (e: DeskEvent) => {
      const backlog = evs.current.length - idx.current;
      const f = backlog > 14 ? 4 : backlog > 7 ? 2.2 : backlog > 3 ? 1.4 : 1;
      const a = e.agent;
      setActive(a);
      if (e.kind === "think") {
        goEl(a, e.el); say(a, e.label || "thinking", "think");
        await sleep(650 / f);
      } else if (e.kind === "say") {
        goEl(a, e.el); say(a, e.text || "", "say");
        await sleep(Math.min(2600, 900 + (e.text?.length || 0) * 12) / f);
      } else if (e.kind === "move") {
        if (e.click) {
          goEl(a, e.click); say(a, e.label || "", "do");
          await sleep((520 + Math.random() * 160) / f);     // travel, then a short hesitation before the press
          ripple(a, e.click);
          await sleep(420 / f);
        } else if (e.points?.length) {
          say(a, e.label || "", "do");
          for (const p of e.points.slice(0, 5)) {
            pos.current[a] = { mode: "chart", time: p.time, price: p.price };
            await sleep((480 + Math.random() * 180) / f);
          }
        } else {
          goEl(a, e.el); say(a, e.label || "", "do");
          await sleep(900 / f);
        }
      } else if (e.kind === "draw" && e.draw) {
        const d = e.draw;
        say(a, e.label || "Drawing the plan", "do");
        pos.current[a] = { mode: "chart", time: d.time, price: d.entry };
        await sleep(560 / f);
        drawRef.current(a, d, "entry");
        dragging.current.add(a);
        for (const [to, phase] of [[d.stop, "stop"], [d.target, "target"]] as const) {
          const from: number = (pos.current[a] as { price: number }).price;
          const steps = 24;
          for (let k = 1; k <= steps; k++) {           // a hand drag: fast in the middle, slow at the ends
            pos.current[a] = { mode: "chart", time: d.time, price: from + (to - from) * ease(k / steps) };
            await sleep(26 / f);
          }
          drawRef.current(a, d, phase);
          await sleep(260 / f);
        }
        dragging.current.delete(a);
        await sleep(500 / f);
      }
    };
    (async () => {
      while (!stop) {
        if (idx.current < evs.current.length) {
          const e = evs.current[idx.current];
          await play(e);
          idx.current += 1;
          setPlayed(idx.current);
        } else {
          setActive(null);
          await sleep(200);
        }
      }
    })();
    return () => { stop = true; };
  }, [session, enabled, ripple]);

  return { pos, dragging, bubble, clicks, active, played, behind: Math.max(0, events.length - played), skip };
}

/** The cursors themselves, drawn over the whole page. */
export default function LiveCursors({ agents, player, anchor, geo }: {
  agents: CursorAgent[]; player: ReturnType<typeof useCursorPlayer>; anchor: React.RefObject<HTMLDivElement | null>;
  geo: React.MutableRefObject<ToXY | null>;
}) {
  const [, tick] = useState(0);
  const motion = useRef<Record<string, Motion>>({});
  useEffect(() => {
    let id = 0;
    const loop = () => { tick((n) => (n + 1) % 1e6); id = requestAnimationFrame(loop); };
    id = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(id);
  }, []);
  const r = anchor.current?.getBoundingClientRect();
  const now = performance.now();
  const target = (id: string): { x: number; y: number } | null => {
    const p = player.pos.current[id];
    if (!p) return null;
    if (p.mode === "chart") {
      if (!r) return null;
      const c = geo.current?.(p.time, p.price);
      return c ? { x: r.left + Math.min(Math.max(c.x, 6), r.width - 6), y: r.top + Math.min(Math.max(c.y, 6), r.height - 6) } : null;
    }
    const b = el(p.target)?.getBoundingClientRect();
    return b && b.width ? { x: b.left + Math.min(b.width * 0.35, 60), y: b.top + Math.min(b.height / 2, 14) } : null;
  };

  return createPortal(
    <div className="pointer-events-none fixed inset-0 z-[60] overflow-hidden">
      <style>{`.agent-click{animation:agentclick .65s ease-out forwards}@keyframes agentclick{from{transform:scale(.3);opacity:1}to{transform:scale(1.7);opacity:0}}
.think-dots span{animation:thinkdot 1.2s infinite}.think-dots span:nth-child(2){animation-delay:.2s}.think-dots span:nth-child(3){animation-delay:.4s}
@keyframes thinkdot{0%,60%,100%{opacity:.2}30%{opacity:1}}`}</style>
      {agents.map((a) => {
        const T = target(a.id);
        let m = motion.current[a.id];
        if (!T && !m) return null;
        if (T) {
          if (!m) {
            const sx = T.x + 60, sy = T.y + 50; // picks up the mouse a little away from the first target
            m = motion.current[a.id] = { x: sx, y: sy, sx, sy, cx: sx, cy: sy, tx: sx, ty: sy, t0: now, dur: 1, seed: Math.random() * 6.28, jx: 0, jy: 0 };
          }
          if (player.dragging.current.has(a.id)) {
            m.x += (T.x - m.x) * 0.5; m.y += (T.y - m.y) * 0.5;
            m.tx = T.x; m.ty = T.y; m.sx = m.x; m.sy = m.y; m.t0 = now; m.dur = 1;
          } else {
            if (Math.hypot(T.x - m.tx, T.y - m.ty) > 6) {
              const d = Math.hypot(T.x - m.x, T.y - m.y);
              const bend = (Math.random() * 0.5 - 0.25) * d;
              const nx = -(T.y - m.y) / (d || 1), ny = (T.x - m.x) / (d || 1);
              m.sx = m.x; m.sy = m.y;
              m.cx = (m.x + T.x) / 2 + nx * bend; m.cy = (m.y + T.y) / 2 + ny * bend;
              m.jx = jitter(5); m.jy = jitter(4); // a hand never lands on the exact pixel
              m.t0 = now;
              m.dur = 260 + 150 * Math.log2(d / 12 + 1);
            }
            m.tx = T.x; m.ty = T.y;
            const ax = T.x + m.jx, ay = T.y + m.jy;
            const k = Math.min(1, (now - m.t0) / m.dur);
            const e = ease(k);
            const over = Math.sin(Math.PI * Math.min(1, k * 1.15)) * 0.045 * (1 - k);
            const ex = ax + (ax - m.sx) * over, ey = ay + (ay - m.sy) * over;
            const u = 1 - e;
            m.x = u * u * m.sx + 2 * u * e * m.cx + e * e * ex;
            m.y = u * u * m.sy + 2 * u * e * m.cy + e * e * ey;
            if (k >= 1) { m.x = ax + Math.sin(now / 900 + m.seed) * 1.3; m.y = ay + Math.cos(now / 1100 + m.seed) * 1.3; }
          }
        }
        const on = player.active === a.id;
        const b = player.bubble[a.id];
        return (
          <div key={a.id} className="absolute left-0 top-0 transition-opacity duration-500"
            style={{ transform: `translate(${m.x}px, ${m.y}px)`, zIndex: on ? 3 : 1, opacity: on ? 1 : 0.45 }}>
            <svg width="20" height="24" viewBox="0 0 18 22" style={{ filter: on ? `drop-shadow(0 0 6px ${a.color})` : "drop-shadow(0 1px 1px #000)" }}>
              <path d="M1 1 L1 17 L5.5 13 L8.5 20.5 L11.5 19.2 L8.6 12 L15 12 Z" fill={a.color} stroke="#ffffff" strokeWidth="1.3" />
            </svg>
            <div className="-mt-1 ml-4 inline-block whitespace-nowrap rounded px-1.5 py-0.5 text-[10px] font-bold text-slate-900 shadow" style={{ background: a.color }}>{a.name}</div>
            {on && b?.text && (
              <div className={`ml-4 mt-1 w-max max-w-[300px] rounded-lg border border-line bg-slate-950/95 px-2.5 py-1.5 text-[11px] leading-snug shadow-2xl ${b.kind === "say" ? "text-slate-100" : "italic text-slate-300"}`}
                style={{ borderColor: a.color + "88" }}>
                {b.text.replaceAll("**", "").slice(0, 200)}{b.text.length > 200 ? "…" : ""}
                {b.kind === "think" && <span className="think-dots ml-1"><span>●</span><span>●</span><span>●</span></span>}
              </div>
            )}
          </div>
        );
      })}
      {player.clicks.map((c) => {
        const a = agents.find((x) => x.id === c.agent);
        return <span key={c.id} className="agent-click absolute h-7 w-7 rounded-full border-2" style={{ left: c.x - 14, top: c.y - 14, borderColor: a?.color }} />;
      })}
    </div>,
    document.body,
  );
}
