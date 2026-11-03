"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { fmtCompact, fmtPct, fmtPrice } from "@/lib/format";
import { useStore } from "@/lib/store";

export default function MarketList() {
  const { symbol, setSymbol, token } = useStore();
  const [rows, setRows] = useState<any[]>([]);
  const prices = useStore((st) => st.prices);
  const [watch, setWatch] = useState<string[] | null>(null);
  const [tab, setTab] = useState<"universe" | "watchlist">("universe");
  const [q, setQ] = useState("");

  useEffect(() => {
    const load = () => api("/markets").then((r) => setRows(r.markets)).catch(() => {});
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, []);
  useEffect(() => {
    if (!token) return setWatch(null);
    api<any[]>("/watchlists").then((w) => setWatch(w[0]?.symbols ?? [])).catch(() => {});
  }, [token]);

  const toggleWatch = async (s: string) => {
    if (!watch) return;
    const next = watch.includes(s) ? watch.filter((x) => x !== s) : [...watch, s];
    setWatch(next);
    await api("/watchlists", { method: "PUT", json: { name: "Top universe", symbols: next } }).catch(() => {});
  };

  const list = rows.filter((r) => (tab === "universe" || watch?.includes(r.symbol)) && r.symbol.includes(q.toUpperCase()));
  const extra = q.length >= 2 && !rows.some((r) => r.symbol === q.toUpperCase() + "USDT") ? q.toUpperCase().replace("USDT", "") + "USDT" : null;
  return (
    <div className="flex h-full flex-col">
      <div className="flex border-b border-line text-xs">
        <button className={`tab ${tab === "universe" ? "active" : ""}`} onClick={() => setTab("universe")}>Universe</button>
        <button className={`tab ${tab === "watchlist" ? "active" : ""}`} onClick={() => setTab("watchlist")} disabled={!watch}>Watchlist</button>
      </div>
      <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search / any USDT pair" className="input m-1.5 text-xs" />
      {extra && <button onClick={() => setSymbol(extra)} className="mx-2 mb-1 text-left text-xs text-sky-400">Open {extra} →</button>}
      <div className="flex-1 overflow-auto">
        {list.map((r) => {
          const p = prices[r.symbol] ?? r.price;
          const best = r.radar?.["1h"]?.best || r.radar?.["4h"]?.best || r.radar?.["15m"]?.best;
          return (
            <div key={r.symbol} onClick={() => setSymbol(r.symbol)}
              className={`group cursor-pointer border-b border-line/50 px-2 py-1.5 ${r.symbol === symbol ? "bg-sky-500/10" : "hover:bg-white/5"}`}>
              <div className="flex items-center">
                <span className="font-medium text-slate-200">{r.base}</span>
                {watch && (
                  <button onClick={(e) => { e.stopPropagation(); toggleWatch(r.symbol); }}
                    className={`ml-1 text-[11px] ${watch.includes(r.symbol) ? "text-amber-400" : "text-slate-600 opacity-0 group-hover:opacity-100"}`}>★</button>
                )}
                {r.freshness?.stale && <span className="ml-1 chip bg-red-500/20 text-red-300">stale</span>}
                <span className="num ml-auto text-slate-200">{fmtPrice(p)}</span>
              </div>
              <div className="flex items-center text-[11px]">
                <span className="text-slate-500">{fmtCompact(r.quote_volume_24h)}</span>
                {best && (
                  <span className={`ml-2 chip ${best.stage === "confirmed" && best.passed ? "bg-violet-500/20 text-violet-300" : best.stage === "pre_signal" ? "bg-amber-500/10 text-amber-300" : "bg-sky-500/10 text-sky-300"}`}>
                    {best.direction[0]} {best.stage.replace("_", "-")}
                  </span>
                )}
                <span className={`num ml-auto ${(r.change_24h_pct ?? 0) >= 0 ? "text-up" : "text-down"}`}>{fmtPct(r.change_24h_pct)}</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
