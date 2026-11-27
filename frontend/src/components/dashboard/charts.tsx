"use client";
import { Area, AreaChart, Bar, BarChart, Cell, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

export type Point = { t: number; v: number };

const C = { accent: "#0064e0", up: "#1e9e5a", down: "#e0413b", grid: "#e3e8ec", muted: "#7a8a99" };
const day = (t: number) => new Date(t).toLocaleDateString([], { month: "short", day: "numeric" });

function Tip({ active, payload, fmt }: { active?: boolean; payload?: { payload: Point }[]; fmt: (v: number) => string }) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  return (
    <div className="rounded-lg border border-line bg-panel px-2.5 py-1.5 text-[12px] shadow-pop">
      <div className="text-slate-500">{day(p.t)}</div>
      <div className="num font-semibold text-slate-100">{fmt(p.v)}</div>
    </div>
  );
}

/** A quiet area chart for a metric card: one line, a faint solid fill, a hover tooltip, no axes. */
export function AreaSpark({ data, fmt, color = C.accent, height = 64 }: { data: Point[]; fmt: (v: number) => string; color?: string; height?: number }) {
  if (data.length < 2) return <div style={{ height }} />;
  const lo = Math.min(...data.map((d) => d.v)), hi = Math.max(...data.map((d) => d.v));
  const pad = (hi - lo) * 0.08 || 1;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 4, right: 0, bottom: 0, left: 0 }}>
        <YAxis hide domain={[lo - pad, hi + pad]} />
        <XAxis hide dataKey="t" />
        <Tooltip content={<Tip fmt={fmt} />} cursor={{ stroke: C.grid }} />
        <Area type="monotone" dataKey="v" stroke={color} strokeWidth={1.75} fill={color} fillOpacity={0.07} isAnimationActive={false} />
      </AreaChart>
    </ResponsiveContainer>
  );
}

/** Fear & greed over time with the neutral 50 line. */
export function GaugeLine({ data, height = 64 }: { data: Point[]; height?: number }) {
  if (data.length < 2) return <div style={{ height }} />;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 4, right: 0, bottom: 0, left: 0 }}>
        <YAxis hide domain={[0, 100]} />
        <XAxis hide dataKey="t" />
        <ReferenceLine y={50} stroke={C.grid} strokeDasharray="3 3" />
        <Tooltip content={<Tip fmt={(v) => `${v}`} />} cursor={{ stroke: C.grid }} />
        <Line type="monotone" dataKey="v" stroke={C.accent} strokeWidth={1.75} dot={false} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}

/** A tiny price line for a table row, green when the window ends higher. */
export function RowSpark({ values }: { values: number[] }) {
  if (values.length < 2) return <div className="h-8 w-20" />;
  const up = values[values.length - 1] >= values[0];
  const data = values.map((v, i) => ({ i, v }));
  return (
    <div className="h-8 w-20">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 2, right: 0, bottom: 2, left: 0 }}>
          <YAxis hide domain={["dataMin", "dataMax"]} />
          <Line type="monotone" dataKey="v" stroke={up ? C.up : C.down} strokeWidth={1.5} dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

/** R per graded call: green above zero, red below. */
export function RBars({ values, height = 72 }: { values: { label: string; r: number }[]; height?: number }) {
  if (!values.length) return <div style={{ height }} />;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={values} margin={{ top: 4, right: 0, bottom: 0, left: 0 }}>
        <YAxis hide domain={[(min: number) => Math.min(min, -1), (max: number) => Math.max(max, 1)]} />
        <XAxis hide dataKey="label" />
        <ReferenceLine y={0} stroke={C.grid} />
        <Tooltip cursor={{ fill: "#f1f4f7" }} content={({ active, payload }) => active && payload?.length ? (
          <div className="rounded-lg border border-line bg-panel px-2.5 py-1.5 text-[12px] shadow-pop">
            <div className="text-slate-500">{(payload[0].payload as { label: string }).label}</div>
            <div className="num font-semibold text-slate-100">{(payload[0].value as number) > 0 ? "+" : ""}{payload[0].value as number}R</div>
          </div>) : null} />
        <Bar dataKey="r" radius={[3, 3, 3, 3]} isAnimationActive={false}>
          {values.map((v, i) => <Cell key={i} fill={v.r >= 0 ? C.up : C.down} />)}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
