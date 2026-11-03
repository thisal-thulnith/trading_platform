"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { fmtNum, pair } from "@/lib/format";

export default function Health() {
  const [h, setH] = useState<any>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    const load = () => api("/system/health").then((x) => { setH(x); setErr(""); }).catch((e) => setErr(e.message));
    load();
    const i = setInterval(load, 5000);
    return () => clearInterval(i);
  }, []);
  if (err) return <div className="p-4 text-red-300">Engine unreachable: {err}</div>;
  if (!h) return <div className="p-4 text-slate-500">Loading…</div>;
  return (
    <div className="h-full space-y-3 overflow-auto p-4">
      <h1 className="text-lg font-semibold text-slate-100">System health &amp; data quality</h1>
      <div className="text-[12px] text-slate-500">uptime {fmtNum(h.uptime_s / 3600, 2)}h · engine {h.engine_running ? "running" : "stopped"} · bus dropped {h.bus_dropped} · background tasks {h.tasks.total} (failed {h.tasks.failed.length})</div>
      <div className="grid grid-cols-2 gap-3">
        <div className="card p-3"><div className="mb-1 font-medium">REST providers</div>
          <table className="grid-t text-[12px]"><thead><tr><th>Provider</th><th>OK</th><th>Requests</th><th>Latency</th><th>Failures</th><th>Last error</th></tr></thead>
            <tbody>{h.providers.map((p: any) => <tr key={p.name}><td>{p.name}</td><td className={p.ok ? "text-up" : "text-down"}>{p.ok ? "ok" : "down"}</td><td className="num">{p.requests}</td><td className="num">{fmtNum(p.latency_ms, 0)} ms</td><td className="num">{p.consecutive_failures}</td><td className="max-w-[200px] truncate text-slate-500">{p.last_error}</td></tr>)}</tbody></table></div>
        <div className="card p-3"><div className="mb-1 font-medium">Streams</div>
          <table className="grid-t text-[12px]"><thead><tr><th>Stream</th><th>Connected</th><th>Messages</th><th>Last msg</th><th>Reconnects</th><th>Last error</th></tr></thead>
            <tbody>{h.streams.map((s: any) => <tr key={s.name}><td>{s.name}</td><td className={s.connected ? "text-up" : "text-down"}>{s.connected ? "yes" : "no"}</td><td className="num">{s.messages}</td><td className="num">{s.age_s !== null ? `${fmtNum(s.age_s, 1)}s` : "—"}</td><td className="num">{s.reconnects}</td><td className="max-w-[200px] truncate text-slate-500">{s.last_error}</td></tr>)}</tbody></table>
          <div className="mt-2 text-[12px] text-slate-400">Gaps repaired: {h.data_quality.gaps_repaired} · malformed candles rejected: {h.data_quality.candles_rejected}</div></div>
      </div>
      <div className="card p-3"><div className="mb-1 font-medium">Data freshness per market</div>
        <div className="grid grid-cols-5 gap-1 text-[12px]">{Object.entries(h.freshness).map(([s, f]: any) => (
          <div key={s} className={`rounded px-2 py-1 ${f.stale ? "bg-red-500/15 text-red-300" : "bg-emerald-500/10 text-emerald-300"}`}>{pair(s)} · {f.age_s !== null ? `${fmtNum(f.age_s, 1)}s` : "no data"}</div>))}</div></div>
      <div className="card p-3"><div className="mb-1 font-medium">Last scans</div>
        <div className="grid grid-cols-4 gap-1 text-[11px] text-slate-400">{Object.entries(h.last_scans).sort().map(([k, v]: any) => <span key={k}>{k} · {new Date(v).toISOString().slice(11, 19)}Z</span>)}</div></div>
      {h.tasks.failed.length > 0 && <div className="card p-3 text-red-300">{h.tasks.failed.map((f: string) => <div key={f}>{f}</div>)}</div>}
    </div>
  );
}
