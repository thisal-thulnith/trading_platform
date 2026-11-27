"use client";
import { Camera, Crosshair, Eraser, Layers, LayoutTemplate, Minus, MoveVertical, PenLine, Redo2, Ruler, Save, Square, Tag, TrendingUp, Undo2, Waves } from "lucide-react";
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
  const [draw, setDraw] = useState(false);  // drawing tools stay folded until asked for
  const onCount = Object.keys(DEFAULT_LAYERS).filter((k) => layers[k]).length;
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
    <div className="relative z-20 flex h-10 items-center gap-1 border-b border-line bg-panel px-2 text-[12.5px]">
      <button title="Drawing tools" onClick={() => { setDraw(!draw); if (draw) p.setTool("cursor"); }} data-agent-target="tool:draw"
        className={`flex items-center gap-1.5 rounded-full px-3 py-1 font-medium ${draw ? "bg-fill text-slate-100" : "text-slate-400 hover:bg-fill"}`}>
        <PenLine size={14} />Draw</button>
      {(draw || p.tool !== "cursor") && (<>
        {TOOLS.map(({ t, icon: I, title }) => (
          <button key={t} title={title} onClick={() => p.setTool(t)} data-agent-target={`tool:${t}`}
            className={`rounded-lg p-1.5 ${p.tool === t ? "bg-accent-soft text-accent" : "text-slate-400 hover:bg-fill"}`}>
            <I size={15} className={t === "ray" ? "rotate-12" : ""} />
          </button>
        ))}
        <button title="Undo (Ctrl+Z)" onClick={p.onUndo} className="rounded-lg p-1.5 text-slate-400 hover:bg-fill"><Undo2 size={15} /></button>
        <button title="Redo (Ctrl+Shift+Z)" onClick={p.onRedo} className="rounded-lg p-1.5 text-slate-400 hover:bg-fill"><Redo2 size={15} /></button>
        <button title="Delete selected (Del)" disabled={!p.hasSelection} onClick={p.onDelete}
          className="rounded-lg p-1.5 text-slate-400 hover:bg-fill disabled:opacity-30"><Eraser size={15} /></button>
      </>)}
      <span className="mx-1 h-4 w-px bg-line" />
      <button data-agent-target="indicators" onClick={() => setOpen(open === "ind" ? null : "ind")}
        className={`rounded-full px-3 py-1 font-medium ${open === "ind" ? "bg-fill text-slate-100" : "text-slate-400 hover:bg-fill"}`}>
        Indicators{p.indicators.length ? <span className="num ml-1 text-slate-500">{p.indicators.length}</span> : null}</button>
      <button onClick={() => setOpen(open === "layers" ? null : "layers")}
        className={`flex items-center gap-1.5 rounded-full px-3 py-1 font-medium ${open === "layers" ? "bg-fill text-slate-100" : "text-slate-400 hover:bg-fill"}`}>
        <Layers size={14} />Overlays{onCount ? <span className="num text-slate-500">{onCount}</span> : null}</button>
      <button data-agent-target="vp" onClick={() => p.setShowProfile(!p.showProfile)} title="Volume profile"
        className={`flex items-center gap-1.5 rounded-full px-3 py-1 font-medium ${p.showProfile ? "bg-accent-soft text-accent" : "text-slate-400 hover:bg-fill"}`}>
        <Waves size={14} />Volume</button>
      <div className="ml-auto flex">
        <button title="Save layout" onClick={p.onSaveLayout} className="rounded-lg p-1.5 text-slate-400 hover:bg-fill"><Save size={15} /></button>
        <button title="Screenshot" onClick={p.onScreenshot} className="rounded-lg p-1.5 text-slate-400 hover:bg-fill"><Camera size={15} /></button>
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
        <div className="absolute left-40 top-10 w-[440px] rounded-2xl border border-line bg-panel p-3 shadow-pop">
          <div className="mb-2 flex items-center gap-2 text-[13px] font-semibold text-slate-100">Analysis overlays
            <button className="ml-auto rounded-full px-2.5 py-0.5 text-[12px] font-medium text-accent hover:bg-accent-soft"
              onClick={() => Object.keys(DEFAULT_LAYERS).forEach((k) => setLayer(k, true))}>Show all</button>
            <button className="rounded-full px-2.5 py-0.5 text-[12px] font-medium text-slate-400 hover:bg-fill"
              onClick={() => Object.keys(DEFAULT_LAYERS).forEach((k) => setLayer(k, false))}>Hide all</button>
          </div>
          <div className="grid grid-cols-2 gap-1">
          {Object.keys(DEFAULT_LAYERS).map((k) => (
            <label key={k} className="flex items-center gap-2 rounded-lg px-1.5 py-1 capitalize text-slate-300 hover:bg-fill">
              <input type="checkbox" checked={layers[k] !== false} onChange={(e) => setLayer(k, e.target.checked)} />
              {k.replaceAll("_", " ")}
            </label>
          ))}
          </div>
        </div>
      )}
    </div>
  );
}
