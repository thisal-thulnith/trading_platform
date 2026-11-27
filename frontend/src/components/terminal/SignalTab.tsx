"use client";
import { ArrowRight } from "lucide-react";
import Link from "next/link";
import { useEffect } from "react";
import BestSignal from "@/components/desk/BestSignal";
import { api } from "@/lib/api";
import { pair } from "@/lib/format";
import { useStore } from "@/lib/store";
import type { ChartObject } from "@/lib/types";

type Plan = { covered?: boolean; engine?: string; clear?: boolean; side: "LONG" | "SHORT"; status?: string;
  plans?: { swing: { entry: number; stop: number; tp1: number } }; live_state?: { since?: number } | null };

/** The Chart page's first tab: the platform's ONE main signal for this coin (the same card as everywhere else), and
 *  its entry, stop and take profit drawn on the chart. The chart scanner's own call lives in the other tabs (Lab). */
export default function SignalTab() {
  const { symbol, setFocus } = useStore();
  useEffect(() => {
    let stop = false;
    const draw = () => api<Plan>(`/agent/signal/plan?symbol=${symbol}&margin=100`).then((p) => {
      if (stop) return;
      if (!p.covered || p.engine !== "unified" || p.clear === false || !p.plans) { setFocus({ ids: [], objects: [] }); return; }
      const sw = p.plans.swing;
      const t = p.live_state?.since ?? Math.floor(Date.now() / 1000) - 86400;
      const base = { origin: "ai" as const, concept: "plan", time1: t };
      const objs: ChartObject[] = [
        { ...base, id: "main_pos", kind: "position", direction: p.side, entry: sw.entry, stop: sw.stop, target: sw.tp1, label: `Main signal: ${p.side}${p.status === "trade" ? "" : " (watch only)"}` },
        { ...base, id: "main_entry", kind: "hline", price1: sw.entry, color: "#64748b", style: "solid", label: "ENTRY (main signal)" },
        { ...base, id: "main_sl", kind: "hline", price1: sw.stop, color: "#ef4444", style: "dashed", label: "STOP LOSS" },
        { ...base, id: "main_tp", kind: "hline", price1: sw.tp1, color: "#22c55e", style: "dashed", label: "TAKE PROFIT" },
      ] as ChartObject[];
      setFocus({ ids: [], objects: objs });
    }).catch(() => undefined);
    draw();
    const i = setInterval(draw, 30_000);
    return () => { stop = true; clearInterval(i); setFocus(null); };
  }, [symbol, setFocus]);

  return (
    <div className="h-full space-y-3 overflow-auto p-3">
      <BestSignal symbol={symbol} margin={100} />
      <p className="px-1 text-[11.5px] text-slate-500">This is the platform&apos;s main signal, the same on every page. Its entry, stop loss and take profit are drawn on the chart. The &quot;Chart reading&quot; and &quot;Detail&quot; tabs are the older chart scanner (Lab): a code reading of this one chart, not the main signal.</p>
      <div className="grid gap-2">
        <Link href={`/desk?coin=${symbol}`} className="flex items-center justify-between rounded-xl border border-line bg-panel px-3 py-2.5 text-[12.5px] text-accent hover:bg-fill">Ask the AI agents about {pair(symbol)} <ArrowRight size={14} /></Link>
        <Link href={`/signal?symbol=${symbol}`} className="flex items-center justify-between rounded-xl border border-line bg-panel px-3 py-2.5 text-[12.5px] text-accent hover:bg-fill">Full plan with leverage table <ArrowRight size={14} /></Link>
        <Link href="/signals" className="flex items-center justify-between rounded-xl border border-line bg-panel px-3 py-2.5 text-[12.5px] text-accent hover:bg-fill">All coins today <ArrowRight size={14} /></Link>
      </div>
    </div>
  );
}
