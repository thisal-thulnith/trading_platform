"use client";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { ToXY } from "@/components/chart/TradingChart";

export type Agent = { id: string; name: string; role: string; color: string; concepts: string[] };
export type Pos = { mode: "chart"; time: number; price: number } | { mode: "el"; target: string } | { mode: "park" };
type Motion = { x: number; y: number; sx: number; sy: number; cx: number; cy: number; tx: number; ty: number; t0: number;
  dur: number; seed: number };

export const el = (target: string) => document.querySelector(`[data-agent-target="${target}"]`) as HTMLElement | null;
const ease = (k: number) => (k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2);

/**
 * Mouse cursors floating over the whole page. Movement imitates a hand on a mouse: curved paths (quadratic Bézier
 * with a random bend), duration from distance (Fitts's law), ease-in/ease-out, a small overshoot that settles, and
 * a faint idle drift. While an agent drags a drawing the cursor tracks the drag point directly.
 */
export default function Cursors({ agents, pos, anchor, geo, talking, thinking, bubble, clicks, dragging, speed }: {
  agents: Agent[]; pos: React.MutableRefObject<Record<string, Pos>>; anchor: React.RefObject<HTMLDivElement | null>;
  geo: React.MutableRefObject<ToXY | null>; talking: Record<string, boolean>; thinking: Record<string, string>;
  bubble: Record<string, string>; clicks: { id: number; agent: string; x: number; y: number }[];
  dragging: React.MutableRefObject<Set<string>>; speed: number;
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

  const target = (a: Agent, i: number): { x: number; y: number } | null => {
    const p = pos.current[a.id];
    if (!p || !r) return null;
    if (p.mode === "chart") {
      const c = geo.current?.(p.time, p.price);
      return c ? { x: r.left + Math.min(Math.max(c.x, 4), r.width - 4), y: r.top + Math.min(Math.max(c.y, 4), r.height - 4) } : null;
    }
    if (p.mode === "el") {
      const b = el(p.target)?.getBoundingClientRect();
      return b && b.width ? { x: b.left + b.width / 2, y: b.top + b.height / 2 } : null;
    }
    return { x: r.right - 140 - Math.floor(i / 11) * 85, y: r.top + 34 + (i % 11) * 36 };
  };

  return createPortal(
    <div className="pointer-events-none fixed inset-0 z-[60] overflow-hidden">
      <style>{`.agent-click{animation:agentclick .65s ease-out forwards}@keyframes agentclick{from{transform:scale(.3);opacity:1}to{transform:scale(1.7);opacity:0}}
.think-dots span{animation:thinkdot 1.2s infinite}.think-dots span:nth-child(2){animation-delay:.2s}.think-dots span:nth-child(3){animation-delay:.4s}
@keyframes thinkdot{0%,60%,100%{opacity:.2}30%{opacity:1}}`}</style>
      {agents.map((a, i) => {
        const T = target(a, i);
        let m = motion.current[a.id];
        if (!T && !m) return null;
        if (T) {
          if (!m) {
            // enter from the right edge of the chart, like picking up the mouse
            const sx = (r?.right ?? T.x) - 20, sy = T.y + 40;
            m = motion.current[a.id] = { x: sx, y: sy, sx, sy, cx: sx, cy: sy, tx: sx, ty: sy, t0: now, dur: 1, seed: Math.random() * 6.28 };
          }
          if (dragging.current.has(a.id)) {
            m.x += (T.x - m.x) * 0.45; m.y += (T.y - m.y) * 0.45;
            m.tx = T.x; m.ty = T.y; m.sx = m.x; m.sy = m.y; m.t0 = now; m.dur = 1;
          } else {
            const moved = Math.hypot(T.x - m.tx, T.y - m.ty);
            if (moved > 6) {
              // new aim: curved path from where the cursor is now
              const d = Math.hypot(T.x - m.x, T.y - m.y);
              const bend = (Math.random() * 0.5 - 0.25) * d;
              const nx = -(T.y - m.y) / (d || 1), ny = (T.x - m.x) / (d || 1);
              m.sx = m.x; m.sy = m.y;
              m.cx = (m.x + T.x) / 2 + nx * bend; m.cy = (m.y + T.y) / 2 + ny * bend;
              m.t0 = now;
              m.dur = (280 + 140 * Math.log2(d / 14 + 1)) / Math.sqrt(speed);
            }
            m.tx = T.x; m.ty = T.y;
            const k = Math.min(1, (now - m.t0) / m.dur);
            const e = ease(k);
            const over = Math.sin(Math.PI * Math.min(1, k * 1.15)) * 0.04 * (1 - k); // slight overshoot that settles
            const ex = T.x + (T.x - m.sx) * over, ey = T.y + (T.y - m.sy) * over;
            const u = 1 - e;
            m.x = u * u * m.sx + 2 * u * e * m.cx + e * e * ex;
            m.y = u * u * m.sy + 2 * u * e * m.cy + e * e * ey;
            if (k >= 1) { m.x = T.x + Math.sin(now / 900 + m.seed) * 1.2; m.y = T.y + Math.cos(now / 1100 + m.seed) * 1.2; }
          }
        }
        const live = talking[a.id] || !!thinking[a.id];
        return (
          <div key={a.id} className="absolute left-0 top-0" style={{ transform: `translate(${m.x}px, ${m.y}px)`, zIndex: live ? 2 : 1 }}>
            <svg width="20" height="24" viewBox="0 0 18 22" style={{ filter: live ? `drop-shadow(0 0 6px ${a.color})` : "drop-shadow(0 1px 1px #000)" }}>
              <path d="M1 1 L1 17 L5.5 13 L8.5 20.5 L11.5 19.2 L8.6 12 L15 12 Z" fill={a.color} stroke="#0b0f19" strokeWidth="1.3" />
            </svg>
            <div className="-mt-1 ml-4 inline-block whitespace-nowrap rounded px-1.5 py-0.5 text-[10px] font-bold text-slate-900 shadow" style={{ background: a.color }}>{a.name}</div>
            {thinking[a.id] && !talking[a.id] && (
              <div className="ml-4 mt-1 w-max max-w-[240px] rounded-lg border border-white/10 bg-slate-950/90 px-2 py-1 text-[11px] italic text-slate-300 shadow-xl">
                {thinking[a.id]} <span className="think-dots"><span>●</span><span>●</span><span>●</span></span>
              </div>
            )}
            {talking[a.id] && bubble[a.id] && (
              <div className="ml-4 mt-1 w-max max-w-[300px] rounded-lg border bg-slate-950/95 px-2.5 py-1.5 text-[11px] leading-snug text-slate-100 shadow-2xl" style={{ borderColor: a.color + "88" }}>
                {bubble[a.id].replaceAll("**", "").slice(0, 180)}{bubble[a.id].length > 180 ? "…" : ""}
              </div>
            )}
          </div>
        );
      })}
      {clicks.map((c) => {
        const a = agents.find((x) => x.id === c.agent);
        return <span key={c.id} className="agent-click absolute h-7 w-7 rounded-full border-2" style={{ left: c.x - 14, top: c.y - 14, borderColor: a?.color }} />;
      })}
    </div>,
    document.body,
  );
}
