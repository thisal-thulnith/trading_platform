"use client";
import type { Snapshot } from "@/lib/types";

/** Independent evidence families; correlated indicators are collapsed into one family score. */
export default function ConfluenceMap({ snap }: { snap: Snapshot }) {
  const fams = Object.entries(snap.confluence.families).filter(([k]) => k !== "context");
  return (
    <div className="space-y-1">
      {fams.sort((a, b) => Math.abs(b[1].score) - Math.abs(a[1].score)).map(([k, v]) => {
        const w = Math.min(Math.abs(v.score), 1) * 50;
        return (
          <div key={k} className="flex items-center gap-2 text-[11px]">
            <span className="w-24 truncate text-slate-400" title={`weight ${snap.confluence.weights[k] ?? 0.3}`}>{k.replace("_", " ")}</span>
            <div className="relative h-2.5 flex-1 rounded bg-slate-800">
              <div className="absolute left-1/2 top-0 h-full w-px bg-slate-600" />
              <div className={`absolute top-0 h-full rounded ${v.score >= 0 ? "bg-up/70" : "bg-down/70"}`}
                style={{ left: v.score >= 0 ? "50%" : `${50 - w}%`, width: `${w}%` }} />
            </div>
            <span className={`num w-10 text-right ${v.score > 0.15 ? "text-up" : v.score < -0.15 ? "text-down" : "text-slate-500"}`}>{v.score.toFixed(2)}</span>
            <span className="w-10 text-right text-[10px] text-slate-600">{v.bull}↑{v.bear}↓</span>
          </div>
        );
      })}
    </div>
  );
}
