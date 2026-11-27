"use client";
/** Minimal inline SVG line chart (equity curves, cumulative R). */
export default function Spark({ values, height = 120, zero = false }: { values: number[]; height?: number; zero?: boolean }) {
  if (values.length < 2) return <div className="text-[11px] text-slate-600" style={{ height }}>not enough data</div>;
  const min = Math.min(...values, zero ? 0 : Infinity), max = Math.max(...values, zero ? 0 : -Infinity);
  const W = 600, H = height, pad = 4;
  const x = (i: number) => pad + (i / (values.length - 1)) * (W - 2 * pad);
  const y = (v: number) => H - pad - ((v - min) / (max - min || 1)) * (H - 2 * pad);
  const d = values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const up = values[values.length - 1] >= values[0];
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height }} preserveAspectRatio="none">
      {zero && <line x1={0} x2={W} y1={y(0)} y2={y(0)} stroke="#d2d2d7" strokeDasharray="4 4" />}
      <path d={d} fill="none" stroke={up ? "#1a9b48" : "#e0342b"} strokeWidth={1.6} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
