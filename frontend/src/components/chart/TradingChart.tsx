"use client";
import {
  CandlestickSeries, ColorType, CrosshairMode, HistogramSeries, LineSeries, createChart,
  type IChartApi, type ISeriesApi, type MouseEventParams, type Time, type UTCTimestamp,
} from "lightweight-charts";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import { fmtPrice } from "@/lib/format";
import { useStore } from "@/lib/store";
import type { Candle, ChartObject, SignalRow, Snapshot } from "@/lib/types";
import { live } from "@/lib/ws";
import { DrawingLayer, dragObject, type Hit } from "./drawings";
import { ChartToolbar, type Tool } from "./Toolbar";

export type IndicatorCfg = { id: string; params: Record<string, string | number>; key: string };

const PALETTE = ["#d97706", "#0071e3", "#7c3aed", "#db2777", "#059669", "#e11d48", "#3a3a3c", "#ca8a04"];
const CONCEPT_LAYER: Record<string, string> = { elliott_impulse: "elliott", elliott_leading_diagonal: "elliott",
  elliott_zigzag: "elliott", elliott_flat: "elliott", elliott_expanded_flat: "elliott", elliott_contracting_triangle: "elliott" };

function storageKey(sym: string, tf: string) {
  return `ait_draw_${sym}_${tf}`;
}

function signalObjects(signals: SignalRow[]): ChartObject[] {
  const out: ChartObject[] = [];
  for (const s of signals) {
    const t = Math.floor(Date.parse(s.created_at + (s.created_at.includes("+") || s.created_at.endsWith("Z") ? "" : "Z")) / 1000);
    const entry = (s.entry_low + s.entry_high) / 2;
    out.push({ id: `sig_${s.id}`, kind: "position", origin: "ai", concept: "signal", time1: t, direction: s.direction,
      entry, stop: s.stop, target: s.tp1, label: `${s.state} ${s.strategy}`, explain: { signal_id: s.id } });
    out.push({ id: `sigz_${s.id}`, kind: "zone", origin: "ai", concept: "signal", time1: t, time2: null, price1: s.entry_high,
      price2: s.entry_low, color: "#a78bfa33", label: "entry zone", explain: { signal_id: s.id } });
    for (const [k, v, c] of [["SL", s.stop, "#ef4444"], ["TP2", s.tp2, "#22c55e"], ["TP3", s.tp3, "#22c55e"]] as const)
      out.push({ id: `sig${k}_${s.id}`, kind: "hline", origin: "ai", concept: "signal", time1: t, price1: v, color: c,
        label: `${k} ${fmtPrice(v)}`, style: "dashed", explain: { signal_id: s.id } });
  }
  return out;
}

export type ToXY = (time: number | null | undefined, price: number) => { x: number; y: number } | null;
/** Lets an automated operator (the War Room agents) use the chart like a person: pick tools, open menus, add indicators. */
export type ChartControl = {
  setTool: (t: Tool) => void; openMenu: (m: "ind" | "layers" | null) => void;
  addIndicator: (id: string, params: Record<string, string | number>) => void; setProfile: (on: boolean) => void;
  reset: () => void;
};

export default function TradingChart({ snapshot, signals, geoRef, overlay, control, indicatorPreset }: {
  snapshot: Snapshot | null; signals: SignalRow[];
  /** receives a (time, price) → pixel converter, for overlays such as the War Room cursors */
  geoRef?: React.MutableRefObject<ToXY | null>;
  overlay?: React.ReactNode;
  control?: React.MutableRefObject<ChartControl | null>;
  /** start with these indicators and don't save changes (the user's own layout stays untouched) */
  indicatorPreset?: IndicatorCfg[];
}) {
  const { symbol, timeframe, layers, hiddenAi, chatObjects, selectedId, highlightId, select, toggleHidden, token, replayAt, focus } = useStore();
  const box = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const layerRef = useRef<DrawingLayer | null>(null);
  const indSeries = useRef<Record<string, ISeriesApi<any>[]>>({});
  const candlesRef = useRef<Candle[]>([]);
  /** prices the price axis must include (the desk's plan: entry, stop, target) */
  const keepInView = useRef<number[]>([]);
  const loadReq = useRef(0);
  const [legend, setLegend] = useState<Candle | null>(null);
  const [tool, setTool] = useState<Tool>("cursor");
  const [userObjs, setUserObjs] = useState<ChartObject[]>([]);
  const [indicators, setIndicators] = useState<IndicatorCfg[]>(() => {
    if (indicatorPreset) return indicatorPreset;
    try {
      return JSON.parse(localStorage.getItem("ait_indicators") || "null") || [
        { id: "ema", params: { length: 21 }, key: "ema:21" }, { id: "ema", params: { length: 50 }, key: "ema:50" },
        { id: "rsi", params: { length: 14 }, key: "rsi:14" }];
    } catch {
      return [];
    }
  });
  const [showProfile, setShowProfile] = useState(false);
  const [menu, setMenu] = useState<"ind" | "layers" | null>(null);
  if (control) {
    control.current = {
      setTool, openMenu: setMenu, setProfile: setShowProfile,
      addIndicator: (id, params) => setIndicators((cur) => cur.some((i) => i.id === id && JSON.stringify(i.params) === JSON.stringify(params))
        ? cur : [...cur, { id, params, key: `${id}:${Object.values(params).join(",")}` }]),
      reset: () => { setTool("cursor"); setMenu(null); setShowProfile(false); setIndicators(indicatorPreset || []); },
    };
  }
  const [status, setStatus] = useState<string>("");
  const history = useRef<{ past: ChartObject[][]; future: ChartObject[][] }>({ past: [], future: [] });
  const drag = useRef<{ hit: Exclude<Hit, null>; startT: number; startP: number; orig: ChartObject } | null>(null);
  const pending = useRef<ChartObject | null>(null);

  // ---------------------------------------------------------------- chart lifecycle
  useEffect(() => {
    if (!box.current) return;
    const chart = createChart(box.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "#ffffff" }, textColor: "#6e6e73", fontSize: 11,
        panes: { separatorColor: "#e5e5ea", enableResize: true }, attributionLogo: true },
      grid: { vertLines: { color: "#f2f2f5" }, horzLines: { color: "#f2f2f5" } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: "#e5e5ea" },
      timeScale: { borderColor: "#e5e5ea", timeVisible: true, secondsVisible: false, rightOffset: 12 },
    });
    const candles = chart.addSeries(CandlestickSeries, { upColor: "#1a9b48", downColor: "#e0342b", borderVisible: false,
      wickUpColor: "#1a9b48", wickDownColor: "#e0342b",
      autoscaleInfoProvider: (original: () => { priceRange: { minValue: number; maxValue: number } } | null) => {
        const r = original();
        const keep = keepInView.current;
        if (!r || !keep.length) return r;
        return { ...r, priceRange: { minValue: Math.min(r.priceRange.minValue, ...keep), maxValue: Math.max(r.priceRange.maxValue, ...keep) } };
      } });
    const vol = chart.addSeries(HistogramSeries, { priceScaleId: "vol", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    const layer = new DrawingLayer();
    candles.attachPrimitive(layer);
    chart.subscribeCrosshairMove((p: MouseEventParams<Time>) => {
      const d = p.seriesData.get(candles) as any;
      setLegend(d ? { ...d, volume: (p.seriesData.get(vol) as any)?.value ?? 0 } : null);
    });
    chartRef.current = chart;
    if (geoRef) {
      geoRef.current = (t, p) => {
        const x = layer.geo.x(t ?? candlesRef.current[candlesRef.current.length - 1]?.time), y = layer.geo.y(p);
        return x === null || y === null ? null : { x, y };
      };
    }
    candleRef.current = candles;
    volRef.current = vol;
    layerRef.current = layer;
    return () => {
      chart.remove();
      chartRef.current = null;
      indSeries.current = {};
    };
  }, []);

  // ---------------------------------------------------------------- data
  const loadCandles = useCallback(async () => {
    const end = replayAt ? `&end=${replayAt * 1000}` : "";
    const req = ++loadReq.current;
    setStatus("loading…");
    try {
      const r = await api<{ candles: Candle[] }>(`/candles/${symbol}?tf=${timeframe}&limit=1500${end}`);
      if (req !== loadReq.current) return; // a newer coin or timeframe was requested meanwhile: drop this stale reply
      candlesRef.current = r.candles;
      candleRef.current?.setData(r.candles.map((c) => ({ ...c, time: c.time as UTCTimestamp })));
      volRef.current?.setData(r.candles.map((c) => ({ time: c.time as UTCTimestamp, value: c.volume,
        color: c.close >= c.open ? "#22c55e40" : "#ef444440" })));
      layerRef.current?.geo.setTimes(r.candles.map((c) => c.time));
      if (layerRef.current && r.candles.length) layerRef.current.lastPrice = r.candles[r.candles.length - 1].close;
      chartRef.current?.timeScale().scrollToRealTime();
      setStatus("");
    } catch (e: any) {
      if (req === loadReq.current) setStatus(`market data unavailable: ${e.message}`);
    }
  }, [symbol, timeframe, replayAt]);

  useEffect(() => {
    loadCandles();
  }, [loadCandles]);

  useEffect(() => {
    if (replayAt) return;
    live.subscribe([`${symbol}:${timeframe}`]);
    return live.on((topic, d) => {
      if (topic !== "candle" || d.symbol !== symbol || d.tf !== timeframe) return;
      const c = d.candle;
      const bar = { time: (c.open_time / 1000) as UTCTimestamp, open: c.open, high: c.high, low: c.low, close: c.close };
      const arr = candlesRef.current;
      const last = arr[arr.length - 1];
      if (last && bar.time < last.time) return;
      candleRef.current?.update(bar);
      if (layerRef.current) {
        layerRef.current.lastPrice = bar.close;
        layerRef.current.update();
      }
      volRef.current?.update({ time: bar.time, value: c.volume, color: c.close >= c.open ? "#22c55e40" : "#ef444440" });
      if (!last || bar.time > last.time) {
        arr.push({ ...bar, volume: c.volume });
        layerRef.current?.geo.setTimes(arr.map((x) => x.time));
      } else arr[arr.length - 1] = { ...bar, volume: c.volume };
    });
  }, [symbol, timeframe, replayAt]);

  // ---------------------------------------------------------------- indicators
  useEffect(() => {
    if (!indicatorPreset) {
      try {
        localStorage.setItem("ait_indicators", JSON.stringify(indicators));
      } catch {}
    }
    const chart = chartRef.current;
    if (!chart) return;
    let cancelled = false;
    (async () => {
      for (const arr of Object.values(indSeries.current)) arr.forEach((s) => chart.removeSeries(s));
      indSeries.current = {};
      let pane = 1;
      for (const [i, ind] of indicators.entries()) {
        const params = Object.entries(ind.params).map(([k, v]) => `${k}=${v}`).join(",");
        try {
          const r = await api<any>(`/indicators/${ind.id}/${symbol}?tf=${timeframe}&limit=1500&params=${encodeURIComponent(params)}`);
          if (cancelled) return;
          const outs = r.meta.outputs as { name: string; pane: string; style: string }[];
          const separate = outs.some((o) => o.pane === "separate");
          const paneIdx = separate ? pane++ : 0;
          const series: ISeriesApi<any>[] = [];
          outs.forEach((o, j) => {
            const data = (r.series[o.name] || []).map((p: any) => ({ time: p.time as UTCTimestamp, value: p.value }));
            const color = PALETTE[(i + j) % PALETTE.length];
            const s = o.style === "histogram"
              ? chart.addSeries(HistogramSeries, { color: color + "99", priceLineVisible: false, lastValueVisible: false }, paneIdx)
              : chart.addSeries(LineSeries, { color, lineWidth: o.style.startsWith("band") ? 1 : 2, lineStyle: o.style.startsWith("band") ? 2 : 0,
                  priceLineVisible: false, lastValueVisible: o.pane === "separate", crosshairMarkerVisible: false,
                  title: j === 0 ? `${ind.id.toUpperCase()}${Object.values(ind.params).length ? "(" + Object.values(ind.params).join(",") + ")" : ""}` : "" }, paneIdx);
            s.setData(data);
            series.push(s);
          });
          if (separate) r.meta.levels?.forEach((lv: number) => series[0]?.createPriceLine({ price: lv, color: "#aeaeb2", lineWidth: 1, lineStyle: 2, axisLabelVisible: false }));
          indSeries.current[ind.key] = series;
        } catch {}
      }
      const panes = chart.panes();
      panes.forEach((p, idx) => idx > 0 && p.setHeight(110));
    })();
    return () => {
      cancelled = true;
    };
  }, [indicators, symbol, timeframe]);

  // ---------------------------------------------------------------- user drawings persistence
  useEffect(() => {
    history.current = { past: [], future: [] };
    (async () => {
      if (token) {
        try {
          const rows = await api<any[]>(`/drawings?symbol=${symbol}&timeframe=${timeframe}`);
          setUserObjs(rows.map((r) => ({ ...r.payload, id: r.parent_id || r.id, dbId: r.id, parentId: r.parent_id, origin: "user", hidden: r.hidden })));
          return;
        } catch {}
      }
      try {
        setUserObjs(JSON.parse(localStorage.getItem(storageKey(symbol, timeframe)) || "[]"));
      } catch {
        setUserObjs([]);
      }
    })();
  }, [symbol, timeframe, token]);

  const persist = useCallback(async (next: ChartObject[], changed: ChartObject | null, removed?: ChartObject) => {
    history.current.past.push(userObjs);
    history.current.future = [];
    setUserObjs(next);
    if (!token) {
      try {
        localStorage.setItem(storageKey(symbol, timeframe), JSON.stringify(next));
      } catch {}
      return;
    }
    try {
      if (removed?.dbId) await api(`/drawings/${removed.dbId}`, { method: "DELETE" });
      if (changed) {
        const { dbId, ...payload } = changed;
        if (dbId) await api(`/drawings/${dbId}`, { method: "PATCH", json: { payload, hidden: !!changed.hidden } });
        else {
          const r = await api<any>("/drawings", { method: "POST", json: { symbol, timeframe, kind: changed.kind, payload,
            parent_id: changed.parentId || null, hidden: !!changed.hidden } });
          setUserObjs((cur) => cur.map((o) => (o.id === changed.id ? { ...o, dbId: r.id } : o)));
        }
      }
    } catch (e: any) {
      setStatus(`save failed: ${e.message}`);
    }
  }, [userObjs, token, symbol, timeframe]);

  const undo = useCallback(() => {
    const prev = history.current.past.pop();
    if (!prev) return;
    history.current.future.push(userObjs);
    setUserObjs(prev);
    if (!token) localStorage.setItem(storageKey(symbol, timeframe), JSON.stringify(prev));
  }, [userObjs, token, symbol, timeframe]);
  const redo = useCallback(() => {
    const nxt = history.current.future.pop();
    if (!nxt) return;
    history.current.past.push(userObjs);
    setUserObjs(nxt);
    if (!token) localStorage.setItem(storageKey(symbol, timeframe), JSON.stringify(nxt));
  }, [userObjs, token, symbol, timeframe]);

  // ---------------------------------------------------------------- compose rendered objects
  const aiObjs = useMemo(() => {
    if (!snapshot || snapshot.symbol !== symbol || snapshot.timeframe !== timeframe) return [];
    if (focus) {
      // walkthrough focus mode: only the objects the current step talks about
      const ids = new Set(focus.ids);
      return [...snapshot.annotations.filter((a) => ids.has(a.id)), ...focus.objects];
    }
    return snapshot.annotations.filter((a) => layers[CONCEPT_LAYER[a.concept || ""] || a.concept || ""] !== false);
  }, [snapshot, symbol, timeframe, layers, focus]);

  // keep the plan's entry, stop and target on the price axis
  useEffect(() => {
    keepInView.current = (focus?.objects ?? []).filter((o) => o.concept === "plan")
      .flatMap((o) => [o.entry, o.stop, o.target, o.price1]).filter((v): v is number => typeof v === "number" && isFinite(v));
    chartRef.current?.priceScale("right").applyOptions({ autoScale: true });
  }, [focus]);

  // scroll the chart so that the focused objects are visible
  useEffect(() => {
    const chart = chartRef.current;
    const bars = candlesRef.current;
    if (!focus || !chart || bars.length < 2) return;
    const times: number[] = [];
    for (const o of aiObjs) {
      if (o.time1) times.push(o.time1);
      o.points?.forEach((p) => times.push(p.time));
    }
    if (!times.length) return;
    const step = bars[bars.length - 1].time - bars[bars.length - 2].time;
    const from = Math.max(Math.min(...times) - 15 * step, bars[0].time);
    const range = chart.timeScale().getVisibleRange();
    if (!range || from < (range.from as number)) {
      chart.timeScale().setVisibleRange({ from: from as UTCTimestamp, to: (bars[bars.length - 1].time + 8 * step) as UTCTimestamp });
    }
  }, [focus, aiObjs]);

  const rendered = useMemo(() => {
    const overrides = new Map(userObjs.filter((u) => u.parentId).map((u) => [u.parentId!, u]));
    const ai = aiObjs.map((a) => {
      const ov = overrides.get(a.id);
      if (ov) return { ...a, ...ov, id: a.id, origin: "user" as const, parentId: a.id, explain: a.explain };
      return { ...a, hidden: !!hiddenAi[a.id] };
    });
    const own = userObjs.filter((u) => !u.parentId);
    const sigs = layers.signals && !focus ? signalObjects(signals.filter((s) => s.symbol === symbol && s.timeframe === timeframe && ["CONFIRMED", "ACTIVE"].includes(s.state))) : [];
    return [...ai, ...sigs, ...own, ...chatObjects];
  }, [aiObjs, userObjs, hiddenAi, chatObjects, signals, layers.signals, symbol, timeframe, focus]);

  useEffect(() => {
    const L = layerRef.current;
    if (!L) return;
    L.selectedId = selectedId;
    L.highlightId = highlightId;
    L.set(rendered);
  }, [rendered, selectedId, highlightId]);

  useEffect(() => {
    if (!showProfile) {
      if (layerRef.current) {
        layerRef.current.profile = null;
        layerRef.current.update();
      }
      return;
    }
    api(`/volume_profile/${symbol}?tf=${timeframe}&bars=300&bins=60`).then((vp) => {
      if (layerRef.current) {
        layerRef.current.profile = vp;
        layerRef.current.update();
      }
    });
  }, [showProfile, symbol, timeframe]);

  // ---------------------------------------------------------------- interaction
  const pos = (e: React.MouseEvent) => {
    const r = box.current!.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top, W: r.width - 70 };
  };

  const commitObject = useCallback((o: ChartObject) => {
    const existing = userObjs.find((u) => u.id === o.id);
    const next = existing ? userObjs.map((u) => (u.id === o.id ? { ...existing, ...o } : u)) : [...userObjs, o];
    persist(next, existing ? { ...existing, ...o } : o);
  }, [userObjs, persist]);

  const onDown = (e: React.MouseEvent) => {
    const L = layerRef.current;
    if (!L || e.button !== 0) return;
    const { x, y, W } = pos(e);
    const t = L.geo.timeAt(x), p = L.geo.priceAt(y);
    if (t === null || p === null) return;
    if (tool !== "cursor") {
      const id = `u_${Date.now().toString(36)}`;
      if (tool === "hline") commitObject({ id, kind: "hline", origin: "user", price1: p, color: "#38bdf8", label: fmtPrice(p) });
      else if (tool === "vline") commitObject({ id, kind: "vline", origin: "user", time1: t, color: "#38bdf8" });
      else if (tool === "label") {
        const txt = prompt("Label text");
        if (txt) commitObject({ id, kind: "label", origin: "user", time1: t, price1: p, label: txt.slice(0, 80), color: "#1d1d1f" });
      } else if (tool === "position") {
        const a = (snapshot?.indicators.atr14 as number) || p * 0.01;
        commitObject({ id, kind: "position", origin: "user", time1: t, direction: "LONG", entry: p, stop: p - a, target: p + 2 * a, label: "LONG" });
      } else if (!pending.current) {
        pending.current = { id, kind: tool === "rect" ? "zone" : tool, origin: "user", time1: t, price1: p, time2: t, price2: p,
          color: tool === "fib" ? "#94a3b8" : "#38bdf8", extend: tool === "ray" } as ChartObject;
        L.preview = pending.current;
        return;
      }
      if (!["trendline", "ray", "rect", "fib"].includes(tool)) setTool("cursor");
      return;
    }
    const hit = L.hit(x, y, W);
    if (!hit) {
      select(null);
      return;
    }
    select(hit.id);
    const obj = rendered.find((o) => o.id === hit.id);
    if (!obj || obj.concept === "signal" || obj.origin === "ai_chat") return;
    drag.current = { hit, startT: t, startP: p, orig: obj };
    chartRef.current?.applyOptions({ handleScroll: false, handleScale: false });
  };

  const onMove = (e: React.MouseEvent) => {
    const L = layerRef.current;
    if (!L) return;
    const { x, y } = pos(e);
    const t = L.geo.timeAt(x), p = L.geo.priceAt(y);
    if (pending.current && t !== null && p !== null) {
      pending.current = { ...pending.current, time2: t, price2: p };
      L.preview = pending.current;
      L.update();
      return;
    }
    const d = drag.current;
    if (!d || t === null || p === null) return;
    const moved = dragObject(d.orig, d.hit.handle, t - d.startT, p - d.startP, t, p);
    L.set(L.objects.map((o) => (o.id === moved.id ? moved : o)));
  };

  const onUp = (e: React.MouseEvent) => {
    const L = layerRef.current;
    if (!L) return;
    if (pending.current) {
      const { x, y } = pos(e);
      const t = L.geo.timeAt(x), p = L.geo.priceAt(y);
      if (t !== null && p !== null && (t !== pending.current.time1 || Math.abs(p - pending.current.price1!) > 0)) {
        const done = { ...pending.current, time2: t, price2: p };
        if (done.time2 !== done.time1) {
          pending.current = null;
          L.preview = null;
          commitObject(done);
          setTool("cursor");
        }
      }
      return;
    }
    const d = drag.current;
    drag.current = null;
    chartRef.current?.applyOptions({ handleScroll: true, handleScale: true });
    if (!d) return;
    const moved = L.objects.find((o) => o.id === d.orig.id);
    if (!moved || JSON.stringify(moved) === JSON.stringify(d.orig)) return;
    if (d.orig.origin === "ai") {
      // never mutate AI evidence: store a user copy that overrides it in this user's view
      const { explain, ...rest } = moved;
      commitObject({ ...rest, origin: "user", parentId: d.orig.id, id: d.orig.id });
    } else commitObject(moved);
  };

  const deleteSelected = useCallback(() => {
    if (!selectedId) return;
    const own = userObjs.find((u) => u.id === selectedId);
    if (own && !own.parentId) persist(userObjs.filter((u) => u.id !== selectedId), null, own);
    else if (own?.parentId) persist(userObjs.filter((u) => u.id !== selectedId), null, own); // revert user edit of AI object
    else toggleHidden(selectedId, true);
    select(null);
  }, [selectedId, userObjs, persist, toggleHidden, select]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;
      if ((e.key === "Delete" || e.key === "Backspace") && selectedId) deleteSelected();
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z") {
        e.preventDefault();
        e.shiftKey ? redo() : undo();
      }
      if (e.key === "Escape") {
        pending.current = null;
        if (layerRef.current) {
          layerRef.current.preview = null;
          layerRef.current.update();
        }
        setTool("cursor");
        select(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selectedId, deleteSelected, undo, redo, select]);

  const screenshot = () => {
    const c = chartRef.current?.takeScreenshot(true);
    if (!c) return;
    const a = document.createElement("a");
    a.href = c.toDataURL("image/png");
    a.download = `${symbol}_${timeframe}_${new Date().toISOString().slice(0, 16)}.png`;
    a.click();
  };

  const saveLayout = async () => {
    if (!token) return setStatus("log in to save layouts to your account");
    await api("/layouts", { method: "PUT", json: { name: "default", payload: { indicators, layers, showProfile } } });
    setStatus("layout saved");
    setTimeout(() => setStatus(""), 1500);
  };

  useEffect(() => {
    if (!token) return;
    api<any[]>("/layouts").then((ls) => {
      const d = ls.find((l) => l.name === "default");
      if (d?.payload?.indicators) setIndicators(d.payload.indicators);
      if (d?.payload?.showProfile !== undefined) setShowProfile(d.payload.showProfile);
    }).catch(() => {});
  }, [token]);

  const last = candlesRef.current[candlesRef.current.length - 1];
  const lg = legend || last;
  return (
    <div className="relative flex h-full flex-col">
      <ChartToolbar tool={tool} setTool={(t) => { pending.current = null; setTool(t); }} indicators={indicators}
        setIndicators={setIndicators} onUndo={undo} onRedo={redo} onScreenshot={screenshot} showProfile={showProfile}
        setShowProfile={setShowProfile} onSaveLayout={saveLayout} onDelete={deleteSelected} hasSelection={!!selectedId}
        menu={menu} setMenu={setMenu} />
      <div className="relative flex-1" onMouseDown={onDown} onMouseMove={onMove} onMouseUp={onUp}
        style={{ cursor: tool !== "cursor" ? "crosshair" : undefined }}>
        <div ref={box} className="absolute inset-0" />
        {overlay}
        <div className="pointer-events-none absolute left-2 top-1 z-10 flex gap-3 text-[11px] text-slate-400">
          {lg && (<>
            <span>O <b className="text-slate-200">{fmtPrice(lg.open)}</b></span>
            <span>H <b className="text-slate-200">{fmtPrice(lg.high)}</b></span>
            <span>L <b className="text-slate-200">{fmtPrice(lg.low)}</b></span>
            <span>C <b className={lg.close >= lg.open ? "text-up" : "text-down"}>{fmtPrice(lg.close)}</b></span>
          </>)}
          {replayAt && <span className="rounded bg-amber-500/20 px-1 text-amber-300">REPLAY {new Date(replayAt * 1000).toISOString().slice(0, 16)}Z</span>}
          {status && <span className="text-amber-300">{status}</span>}
        </div>
      </div>
    </div>
  );
}
