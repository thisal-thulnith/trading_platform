"use client";
import { Camera, Crosshair, Eraser, LayoutTemplate, Minus, MoveVertical, Redo2, Ruler, Save, Square, Tag, TrendingUp, Undo2, Waves } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { DEFAULT_LAYERS, useStore } from "@/lib/store";
import type { IndicatorCfg } from "./TradingChart";

export type Tool = "cursor" | "trendline" | "ray" | "hline" | "vline" | "rect" | "fib" | "label" | "position";

const TOOLS: { t: Tool; icon: any; title: string }[] = [
  { t: "cursor", icon: Crosshair, title: "Select / move (Esc)" },
  { t: "trendline", icon: TrendingUp, title: "Trendline" },
  { t: "ray", icon: TrendingUp, title: "Ray (extends right)" },
  { t: "hline", icon: Minus, title: "Horizontal line" },
  { t: "vline", icon: MoveVertical, title: "Vertical line" },
  { t: "rect", icon: Square, title: "Rectangle / zone" },
  { t: "fib", icon: Ruler, title: "Fibonacci retracement" },
  { t: "position", icon: LayoutTemplate, title: "Long position risk/reward box" },
  { t: "label", icon: Tag, title: "Text label" },
];

export function ChartToolbar(p: {
  tool: Tool; setTool: (t: Tool) => void; indicators: IndicatorCfg[]; setIndicators: (i: IndicatorCfg[]) => void;
  onUndo: () => void; onRedo: () => void; onScreenshot: () => void; showProfile: boolean; setShowProfile: (b: boolean) => void;
  onSaveLayout: () => void; onDelete: () => void; hasSelection: boolean;
  menu?: "ind" | "layers" | null; setMenu?: (m: "ind" | "layers" | null) => void;
}) {
  const { layers, setLayer } = useStore();
  const [catalog, setCatalog] = useState<any[]>([]);
  const [ownOpen, setOwnOpen] = useState<"ind" | "layers" | null>(null);
  const open = p.setMenu ? p.menu ?? null : ownOpen;
  const setOpen = p.setMenu || setOwnOpen;
  const [q, setQ] = useState("");
  useEffect(() => {
    api<any[]>("/indicators").then(setCatalog).catch(() => {});
  }, []);
  const add = (meta: any) => {
    const params: Record<string, any> = {};
    meta.params.forEach((x: any) => (params[x.name] = x.default));
    const key = `${meta.id}:${Object.values(params).join(",")}:${Date.now() % 10000}`;
    p.setIndicators([...p.indicators, { id: meta.id, params, key }]);
  };
  const edit = (key: string, name: string, val: string) =>
    p.setIndicators(p.indicators.map((i) => (i.key === key ? { ...i, params: { ...i.params, [name]: isNaN(+val) ? val : +val } } : i)));
  return (
    <div className="relative z-20 flex items-center gap-0.5 border-b border-line bg-panel px-1 py-1 text-xs">
      {TOOLS.map(({ t, icon: I, title }) => (
        <button key={t} title={title} onClick={() => p.setTool(t)} data-agent-target={`tool:${t}`}
          className={`rounded p-1.5 ${p.tool === t ? "bg-sky-500/20 text-sky-300" : "text-slate-400 hover:bg-white/5"}`}>
          <I size={14} className={t === "ray" ? "rotate-12" : ""} />
        </button>
      ))}
      <span className="mx-1 h-4 w-px bg-line" />
      <button title="Undo (Ctrl+Z)" onClick={p.onUndo} className="rounded p-1.5 text-slate-400 hover:bg-white/5"><Undo2 size={14} /></button>
      <button title="Redo (Ctrl+Shift+Z)" onClick={p.onRedo} className="rounded p-1.5 text-slate-400 hover:bg-white/5"><Redo2 size={14} /></button>
      <button title="Delete selected / hide AI object (Del)" disabled={!p.hasSelection} onClick={p.onDelete}
        className="rounded p-1.5 text-slate-400 hover:bg-white/5 disabled:opacity-30"><Eraser size={14} /></button>
      <span className="mx-1 h-4 w-px bg-line" />
      <button data-agent-target="indicators" onClick={() => setOpen(open === "ind" ? null : "ind")} className="rounded px-2 py-1 text-slate-300 hover:bg-white/5">Indicators ({p.indicators.length})</button>
      <button onClick={() => setOpen(open === "layers" ? null : "layers")} className="rounded px-2 py-1 text-slate-300 hover:bg-white/5">AI layers</button>
      <button data-agent-target="vp" onClick={() => p.setShowProfile(!p.showProfile)} title="Volume profile"
        className={`rounded px-2 py-1 ${p.showProfile ? "bg-indigo-500/20 text-indigo-300" : "text-slate-300 hover:bg-white/5"}`}>
        <Waves size={13} className="mr-1 inline" />VP
      </button>
      <div className="ml-auto flex">
        <button title="Save layout" onClick={p.onSaveLayout} className="rounded p-1.5 text-slate-400 hover:bg-white/5"><Save size={14} /></button>
        <button title="Screenshot" onClick={p.onScreenshot} className="rounded p-1.5 text-slate-400 hover:bg-white/5"><Camera size={14} /></button>
      </div>
      {open === "ind" && (
        <div className="absolute left-40 top-8 w-[460px] rounded-md border border-line bg-panel2 p-2 shadow-2xl">
          <div className="mb-2 font-medium text-slate-200">Active</div>
          <div className="mb-3 max-h-40 space-y-1 overflow-auto">
            {p.indicators.map((i) => (
              <div key={i.key} className="flex items-center gap-2">
                <span className="w-24 uppercase text-slate-300">{i.id}</span>
                {Object.entries(i.params).map(([k, v]) => (
                  <label key={k} className="flex items-center gap-1 text-slate-500">{k}
                    <input defaultValue={String(v)} onBlur={(e) => edit(i.key, k, e.target.value)}
                      className="w-14 rounded border border-line bg-bg px-1 text-slate-200" />
                  </label>
                ))}
                <button onClick={() => p.setIndicators(p.indicators.filter((x) => x.key !== i.key))} className="ml-auto text-down">remove</button>
              </div>
            ))}
          </div>
          <input placeholder="Search 41 indicators…" value={q} onChange={(e) => setQ(e.target.value)}
            className="mb-2 w-full rounded border border-line bg-bg px-2 py-1 text-slate-200" />
          <div className="grid max-h-60 grid-cols-2 gap-1 overflow-auto">
            {catalog.filter((c) => (c.name + c.id).toLowerCase().includes(q.toLowerCase())).map((c) => (
              <button key={c.id} data-agent-target={`ind:${c.id}`} onClick={() => add(c)} className="rounded px-2 py-1 text-left hover:bg-white/5">
                <span className="text-slate-200">{c.name}</span> <span className="text-[10px] text-slate-500">{c.category}</span>
              </button>
            ))}
          </div>
        </div>
      )}
      {open === "layers" && (
        <div className="absolute left-64 top-8 grid w-[420px] grid-cols-2 gap-1 rounded-md border border-line bg-panel2 p-2 shadow-2xl">
          {Object.keys(DEFAULT_LAYERS).map((k) => (
            <label key={k} className="flex items-center gap-2 text-slate-300">
              <input type="checkbox" checked={layers[k] !== false} onChange={(e) => setLayer(k, e.target.checked)} />
              {k.replaceAll("_", " ")}
            </label>
          ))}
        </div>
      )}
    </div>
  );
}
