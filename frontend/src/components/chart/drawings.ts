import type {
  IChartApi, IPrimitivePaneRenderer, IPrimitivePaneView, ISeriesApi, ISeriesPrimitive, Logical, SeriesAttachedParameter,
  SeriesType, Time,
} from "lightweight-charts";
import type { CanvasRenderingTarget2D } from "fancy-canvas";
import type { ChartObject } from "@/lib/types";

export type Hit = { id: string; handle: "p1" | "p2" | "body" | "entry" | "stop" | "target" } | null;
export type VolumeProfile = { bins: { price_low: number; price_high: number; volume: number; buy_volume: number; in_value_area: boolean }[]; poc: number; vah: number; val: number } | null;

const FONT = "11px ui-sans-serif, system-ui, -apple-system";

function withAlpha(color: string, a: number): string {
  if (color.startsWith("#") && (color.length === 7 || color.length === 9)) {
    const hex = color.slice(0, 7);
    return hex + Math.round(a * 255).toString(16).padStart(2, "0");
  }
  return color;
}

/** Maps times <-> x and prices <-> y, extrapolating beyond the loaded bars (so zones can extend right). */
export class Geometry {
  chart: IChartApi | null = null;
  series: ISeriesApi<SeriesType> | null = null;
  times: number[] = [];
  step = 3600;

  setTimes(t: number[]) {
    this.times = t;
    if (t.length > 1) this.step = t[t.length - 1] - t[t.length - 2];
  }

  logicalOf(t: number): number {
    const a = this.times;
    if (!a.length) return 0;
    if (t <= a[0]) return (t - a[0]) / this.step;
    if (t >= a[a.length - 1]) return a.length - 1 + (t - a[a.length - 1]) / this.step;
    let lo = 0, hi = a.length - 1;
    while (hi - lo > 1) {
      const m = (lo + hi) >> 1;
      if (a[m] <= t) lo = m;
      else hi = m;
    }
    return lo + (t - a[lo]) / Math.max(a[hi] - a[lo], 1);
  }

  x(t: number | null | undefined): number | null {
    if (t === null || t === undefined || !this.chart) return null;
    return this.chart.timeScale().logicalToCoordinate(this.logicalOf(t) as Logical);
  }

  timeAt(x: number): number | null {
    if (!this.chart || !this.times.length) return null;
    const l = this.chart.timeScale().coordinateToLogical(x);
    if (l === null) return null;
    const i = Math.round(l);
    const a = this.times;
    if (i < 0) return a[0] + i * this.step;
    if (i >= a.length) return a[a.length - 1] + (i - a.length + 1) * this.step;
    return a[i];
  }

  y(p: number | null | undefined): number | null {
    if (p === null || p === undefined || !this.series) return null;
    return this.series.priceToCoordinate(p);
  }

  priceAt(y: number): number | null {
    return this.series ? (this.series.coordinateToPrice(y) as number | null) : null;
  }
}

class Renderer implements IPrimitivePaneRenderer {
  constructor(private layer: DrawingLayer) {}

  draw(target: CanvasRenderingTarget2D) {
    target.useMediaCoordinateSpace(({ context: ctx, mediaSize }) => {
      const L = this.layer;
      const W = mediaSize.width;
      ctx.font = FONT;
      if (L.profile) this.drawProfile(ctx, W);
      for (const o of L.objects) {
        if (o.hidden) continue;
        const sel = o.id === L.selectedId;
        const hl = o.id === L.highlightId;
        ctx.save();
        if (hl) {
          ctx.shadowColor = "#facc15";
          ctx.shadowBlur = 14;
        }
        try {
          this.drawObject(ctx, o, W, sel || hl);
        } catch {}
        ctx.restore();
        if (sel && o.origin !== "ai_chat") this.drawHandles(ctx, o);
      }
      if (L.preview) {
        ctx.save();
        ctx.globalAlpha = 0.7;
        this.drawObject(ctx, L.preview, W, true);
        ctx.restore();
      }
    });
  }

  dash(ctx: CanvasRenderingContext2D, style?: string) {
    ctx.setLineDash(style === "dashed" ? [6, 4] : style === "dotted" ? [2, 3] : []);
  }

  label(ctx: CanvasRenderingContext2D, text: string | undefined, x: number, y: number, color: string, bg = true) {
    if (!text) return;
    const w = ctx.measureText(text).width + 8;
    if (bg) {
      ctx.fillStyle = "rgba(9,12,20,0.78)";
      ctx.fillRect(x, y - 13, w, 15);
    }
    ctx.fillStyle = color;
    ctx.fillText(text, x + 4, y - 2);
  }

  drawObject(ctx: CanvasRenderingContext2D, o: ChartObject, W: number, bold: boolean) {
    const g = this.layer.geo;
    const color = o.color || "#38bdf8";
    ctx.lineWidth = bold ? 2 : 1;
    ctx.strokeStyle = color;
    switch (o.kind) {
      case "zone":
      case "rect": {
        const x1 = g.x(o.time1) ?? 0;
        const x2 = o.time2 ? g.x(o.time2) ?? W : W;
        const y1 = g.y(o.price1), y2 = g.y(o.price2);
        if (y1 === null || y2 === null) return;
        const base = color.length === 9 ? color.slice(0, 7) : color;
        const alpha = color.length === 9 ? parseInt(color.slice(7), 16) / 255 : 0.16;
        ctx.fillStyle = withAlpha(base, bold ? Math.min(alpha + 0.12, 0.5) : alpha);
        ctx.fillRect(Math.min(x1, x2), Math.min(y1, y2), Math.abs(x2 - x1), Math.abs(y2 - y1));
        ctx.strokeStyle = withAlpha(base, bold ? 0.95 : 0.5);
        this.dash(ctx, o.status === "mitigated" ? "dashed" : o.style);
        ctx.strokeRect(Math.min(x1, x2), Math.min(y1, y2), Math.abs(x2 - x1), Math.abs(y2 - y1));
        ctx.setLineDash([]);
        if (o.label) this.label(ctx, o.label, Math.min(x1, x2) + 2, Math.min(y1, y2) + 14, withAlpha(base, 1), false);
        return;
      }
      case "hline": {
        const y = g.y(o.price1);
        if (y === null) return;
        const x1 = o.time1 ? Math.max(g.x(o.time1) ?? 0, 0) : 0;
        this.dash(ctx, o.style);
        ctx.beginPath();
        ctx.moveTo(x1, y);
        ctx.lineTo(W, y);
        ctx.stroke();
        ctx.setLineDash([]);
        this.label(ctx, o.label, Math.max(W - ctx.measureText(o.label || "").width - 70, x1), y, color);
        return;
      }
      case "vline": {
        const x = g.x(o.time1);
        if (x === null) return;
        this.dash(ctx, o.style);
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, 5000);
        ctx.stroke();
        ctx.setLineDash([]);
        this.label(ctx, o.label, x + 3, 16, color);
        return;
      }
      case "trendline":
      case "ray": {
        const x1 = g.x(o.time1), x2 = g.x(o.time2), y1 = g.y(o.price1), y2 = g.y(o.price2);
        if ([x1, x2, y1, y2].some((v) => v === null)) return;
        let ex = x2!, ey = y2!;
        if ((o.extend || o.kind === "ray") && x2! !== x1!) {
          const slope = (y2! - y1!) / (x2! - x1!);
          ex = W;
          ey = y1! + slope * (W - x1!);
        }
        this.dash(ctx, o.style);
        ctx.beginPath();
        ctx.moveTo(x1!, y1!);
        ctx.lineTo(ex, ey);
        ctx.stroke();
        ctx.setLineDash([]);
        if (o.label) this.label(ctx, o.label, (x1! + x2!) / 2, Math.min(y1!, y2!) - 2, color);
        return;
      }
      case "fib": {
        const x1 = g.x(o.time1) ?? 0;
        const p1 = o.price1!, p2 = o.price2!;
        const levels = o.levels?.length ? o.levels : [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];
        for (const lv of levels) {
          const p = p2 - lv * (p2 - p1);
          const y = g.y(p);
          if (y === null) continue;
          ctx.strokeStyle = lv === 0.618 || lv === 0.5 ? "#f59e0b" : withAlpha(color.slice(0, 7), 0.8);
          ctx.beginPath();
          ctx.moveTo(x1, y);
          ctx.lineTo(W, y);
          ctx.stroke();
          this.label(ctx, `${lv} (${p.toPrecision(6)})`, x1 + 2, y - 1, ctx.strokeStyle as string, false);
        }
        return;
      }
      case "wave": {
        const pts = (o.points || []).map((p) => ({ x: g.x(p.time), y: g.y(p.price), l: p.label }));
        if (pts.some((p) => p.x === null || p.y === null)) return;
        this.dash(ctx, o.role === "alternate" ? "dashed" : "solid");
        ctx.beginPath();
        pts.forEach((p, i) => (i ? ctx.lineTo(p.x!, p.y!) : ctx.moveTo(p.x!, p.y!)));
        ctx.stroke();
        ctx.setLineDash([]);
        pts.forEach((p, i) => {
          if (i === 0) return;
          ctx.fillStyle = color;
          ctx.beginPath();
          ctx.arc(p.x!, p.y!, 9, 0, Math.PI * 2);
          ctx.fill();
          ctx.fillStyle = "#0b0f19";
          ctx.font = "bold 10px ui-sans-serif";
          ctx.fillText(p.l, p.x! - ctx.measureText(p.l).width / 2, p.y! + 3.5);
          ctx.font = FONT;
        });
        if (o.invalidation) {
          const y = g.y(o.invalidation);
          if (y !== null) {
            ctx.strokeStyle = withAlpha(color.slice(0, 7), 0.6);
            this.dash(ctx, "dotted");
            ctx.beginPath();
            ctx.moveTo(pts[pts.length - 1].x!, y);
            ctx.lineTo(W, y);
            ctx.stroke();
            ctx.setLineDash([]);
            this.label(ctx, "EW invalidation", W - 170, y, color);
          }
        }
        return;
      }
      case "marker": {
        const x = g.x(o.time1), y = g.y(o.price1);
        if (x === null || y === null) return;
        const up = o.shape === "arrowUp";
        const yy = o.position === "above" ? y - 8 : y + 8;
        ctx.fillStyle = color;
        ctx.beginPath();
        if (up) {
          ctx.moveTo(x, yy - 6);
          ctx.lineTo(x - 6, yy + 5);
          ctx.lineTo(x + 6, yy + 5);
        } else {
          ctx.moveTo(x, yy + 6);
          ctx.lineTo(x - 6, yy - 5);
          ctx.lineTo(x + 6, yy - 5);
        }
        ctx.closePath();
        ctx.fill();
        if (bold) this.label(ctx, o.label, x + 8, yy, color);
        return;
      }
      case "label": {
        const x = g.x(o.time1), y = g.y(o.price1);
        if (x === null || y === null) return;
        this.label(ctx, o.label || "note", x, y, color);
        return;
      }
      case "position": {
        const x1 = o.time1 ? g.x(o.time1) ?? W - 160 : W - 160;
        const x2 = o.time2 ? g.x(o.time2) ?? W : Math.min(x1 + 160, W);
        const ye = g.y(o.entry), ys = g.y(o.stop), yt = g.y(o.target);
        if (ye === null || ys === null || yt === null) return;
        ctx.fillStyle = "rgba(34,197,94,0.16)";
        ctx.fillRect(x1, Math.min(ye, yt), x2 - x1, Math.abs(yt - ye));
        ctx.fillStyle = "rgba(239,68,68,0.16)";
        ctx.fillRect(x1, Math.min(ye, ys), x2 - x1, Math.abs(ys - ye));
        ctx.strokeStyle = "#e2e8f0";
        ctx.beginPath();
        ctx.moveTo(x1, ye);
        ctx.lineTo(x2, ye);
        ctx.stroke();
        const rr = Math.abs(o.target! - o.entry!) / Math.abs(o.entry! - o.stop!);
        const lp = this.layer.lastPrice;
        let live = "";
        if (lp && (o.concept === "signal" || o.origin === "user")) {
          const pct = ((o.direction === "SHORT" ? -1 : 1) * (lp - o.entry!)) / o.entry! * 100;
          live = `  ·  now ${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%`;
        }
        this.label(ctx, `${o.label || o.direction} R:R ${rr.toFixed(2)}${live}`, x1 + 2, Math.min(ye, yt, ys) - 2, "#e2e8f0");
        return;
      }
    }
  }

  drawHandles(ctx: CanvasRenderingContext2D, o: ChartObject) {
    for (const [, x, y] of this.layer.handles(o)) {
      ctx.fillStyle = "#0b0f19";
      ctx.strokeStyle = "#facc15";
      ctx.lineWidth = 1.5;
      ctx.fillRect(x - 4, y - 4, 8, 8);
      ctx.strokeRect(x - 4, y - 4, 8, 8);
    }
  }

  drawProfile(ctx: CanvasRenderingContext2D, W: number) {
    const vp = this.layer.profile!;
    const g = this.layer.geo;
    const max = Math.max(...vp.bins.map((b) => b.volume), 1);
    const width = Math.min(160, W * 0.22);
    for (const b of vp.bins) {
      const y1 = g.y(b.price_high), y2 = g.y(b.price_low);
      if (y1 === null || y2 === null) continue;
      const w = (b.volume / max) * width;
      const wb = (b.buy_volume / max) * width;
      const h = Math.max(1, Math.abs(y2 - y1) - 1);
      ctx.fillStyle = b.in_value_area ? "rgba(99,102,241,0.30)" : "rgba(148,163,184,0.16)";
      ctx.fillRect(W - w - 60, Math.min(y1, y2), w, h);
      ctx.fillStyle = "rgba(34,197,94,0.25)";
      ctx.fillRect(W - wb - 60, Math.min(y1, y2), wb, h);
    }
    for (const [p, c, l] of [[vp.poc, "#f59e0b", "POC"], [vp.vah, "#818cf8", "VAH"], [vp.val, "#818cf8", "VAL"]] as const) {
      const y = g.y(p);
      if (y === null) continue;
      ctx.strokeStyle = c;
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(W - width - 60, y);
      ctx.lineTo(W - 60, y);
      ctx.stroke();
      ctx.setLineDash([]);
      this.label(ctx, l, W - width - 60, y, c, false);
    }
  }
}

class View implements IPrimitivePaneView {
  constructor(private layer: DrawingLayer) {}
  renderer() {
    return new Renderer(this.layer);
  }
  zOrder() {
    return "top" as const;
  }
}

export class DrawingLayer implements ISeriesPrimitive<Time> {
  geo = new Geometry();
  objects: ChartObject[] = [];
  preview: ChartObject | null = null;
  selectedId: string | null = null;
  highlightId: string | null = null;
  profile: VolumeProfile = null;
  lastPrice: number | null = null;
  private request: () => void = () => {};
  private views = [new View(this)];

  attached(p: SeriesAttachedParameter<Time>) {
    this.geo.chart = p.chart as IChartApi;
    this.geo.series = p.series as ISeriesApi<SeriesType>;
    this.request = p.requestUpdate;
  }

  detached() {
    this.geo.chart = null;
    this.geo.series = null;
  }

  paneViews() {
    return this.views;
  }

  update() {
    this.request();
  }

  set(objects: ChartObject[]) {
    this.objects = objects;
    this.request();
  }

  handles(o: ChartObject): [Exclude<Hit, null>["handle"], number, number][] {
    const g = this.geo;
    const out: [Exclude<Hit, null>["handle"], number, number][] = [];
    const push = (h: Exclude<Hit, null>["handle"], t: number | null | undefined, p: number | null | undefined) => {
      const x = g.x(t), y = g.y(p);
      if (x !== null && y !== null) out.push([h, x, y]);
    };
    if (o.kind === "position") {
      const t = o.time1 ?? g.timeAt((g.chart?.timeScale().width() ?? 400) - 160);
      push("entry", t, o.entry);
      push("stop", t, o.stop);
      push("target", t, o.target);
      return out;
    }
    push("p1", o.time1 ?? g.times[0], o.price1);
    if (["zone", "rect", "trendline", "ray", "fib"].includes(o.kind)) push("p2", o.time2 ?? g.times[g.times.length - 1], o.price2);
    return out;
  }

  /** Returns the top-most object under (x, y) in media coordinates. */
  hit(x: number, y: number, W: number): Hit {
    const g = this.geo;
    const sel = this.objects.find((o) => o.id === this.selectedId);
    if (sel) for (const [h, hx, hy] of this.handles(sel)) if (Math.abs(hx - x) < 7 && Math.abs(hy - y) < 7) return { id: sel.id, handle: h };
    for (let i = this.objects.length - 1; i >= 0; i--) {
      const o = this.objects[i];
      if (o.hidden) continue;
      if (o.kind === "zone" || o.kind === "rect") {
        const x1 = g.x(o.time1) ?? 0, x2 = o.time2 ? g.x(o.time2) ?? W : W;
        const y1 = g.y(o.price1), y2 = g.y(o.price2);
        if (y1 === null || y2 === null) continue;
        if (x >= Math.min(x1, x2) && x <= Math.max(x1, x2) && y >= Math.min(y1, y2) - 2 && y <= Math.max(y1, y2) + 2)
          return { id: o.id, handle: "body" };
      } else if (o.kind === "hline" || o.kind === "fib") {
        const yy = g.y(o.price1);
        if (yy !== null && Math.abs(yy - y) < 5 && x >= (o.time1 ? g.x(o.time1) ?? 0 : 0)) return { id: o.id, handle: "body" };
      } else if (o.kind === "vline") {
        const xx = g.x(o.time1);
        if (xx !== null && Math.abs(xx - x) < 5) return { id: o.id, handle: "body" };
      } else if (o.kind === "trendline" || o.kind === "ray") {
        const x1 = g.x(o.time1), x2 = g.x(o.time2), y1 = g.y(o.price1), y2 = g.y(o.price2);
        if ([x1, x2, y1, y2].some((v) => v === null)) continue;
        const ex = o.extend || o.kind === "ray" ? W : x2!;
        if (x < Math.min(x1!, ex) - 4 || x > Math.max(x1!, ex) + 4) continue;
        const slope = x2! === x1! ? 0 : (y2! - y1!) / (x2! - x1!);
        if (Math.abs(y1! + slope * (x - x1!) - y) < 5) return { id: o.id, handle: "body" };
      } else if (o.kind === "marker" || o.kind === "label") {
        const xx = g.x(o.time1), yy = g.y(o.price1);
        if (xx !== null && yy !== null && Math.abs(xx - x) < 9 && Math.abs(yy - y) < 14) return { id: o.id, handle: "body" };
      } else if (o.kind === "wave") {
        for (const p of o.points || []) {
          const xx = g.x(p.time), yy = g.y(p.price);
          if (xx !== null && yy !== null && Math.hypot(xx - x, yy - y) < 10) return { id: o.id, handle: "body" };
        }
      } else if (o.kind === "position") {
        const ys = [g.y(o.entry), g.y(o.stop), g.y(o.target)].filter((v) => v !== null) as number[];
        const x1 = o.time1 ? g.x(o.time1) ?? W - 160 : W - 160;
        if (x >= x1 && x <= x1 + 160 && y >= Math.min(...ys) && y <= Math.max(...ys)) return { id: o.id, handle: "body" };
      }
    }
    return null;
  }
}

/** Apply a drag delta to an object (returns a new object). */
export function dragObject(o: ChartObject, handle: Exclude<Hit, null>["handle"], dt: number, dp: number, t: number | null, p: number | null): ChartObject {
  const n: ChartObject = { ...o };
  if (handle === "body") {
    if (n.time1 != null) n.time1 = n.time1 + dt;
    if (n.time2 != null) n.time2 = n.time2 + dt;
    if (n.price1 != null) n.price1 = n.price1 + dp;
    if (n.price2 != null) n.price2 = n.price2 + dp;
    if (n.entry != null) {
      n.entry += dp;
      n.stop! += dp;
      n.target! += dp;
    }
    if (n.points) n.points = n.points.map((q) => ({ ...q, time: q.time + dt, price: q.price + dp }));
  } else if (handle === "p1") {
    if (t !== null) n.time1 = t;
    if (p !== null) n.price1 = p;
  } else if (handle === "p2") {
    if (t !== null) n.time2 = t;
    if (p !== null) n.price2 = p;
  } else if (p !== null) {
    (n as any)[handle] = p;
  }
  return n;
}
