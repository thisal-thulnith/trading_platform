"use client";
import dynamic from "next/dynamic";
import { GraduationCap } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import AnalysisPanel from "@/components/terminal/AnalysisPanel";
import BottomPanel from "@/components/terminal/BottomPanel";
import ChatPanel from "@/components/terminal/ChatPanel";
import MarketList from "@/components/terminal/MarketList";
import Walkthrough from "@/components/terminal/Walkthrough";
import SignalTab from "@/components/terminal/SignalTab";
import { api } from "@/lib/api";
import { fmtPrice, pair } from "@/lib/format";
import { useStore } from "@/lib/store";
import { TIMEFRAMES, type SignalRow, type Snapshot } from "@/lib/types";
import { live } from "@/lib/ws";

const TradingChart = dynamic(() => import("@/components/chart/TradingChart"), { ssr: false });

export default function Terminal() {
  const { symbol, timeframe, setTimeframe, replayAt, walkRequest, setSymbol } = useStore();
  useEffect(() => {   // a link to one coin: /terminal?symbol=SOLUSDT (or SOL)
    const q = new URLSearchParams(window.location.search).get("symbol");
    if (q) { const u = q.toUpperCase().replace("/", ""); setSymbol(u.endsWith("USDT") ? u : `${u}USDT`); }
  }, [setSymbol]);
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [signals, setSignals] = useState<SignalRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [right, setRight] = useState<"signal" | "walk" | "analysis" | "chat">("signal");
  const [showScanner, setShowScanner] = useState(false);   // the old chart scanner's arrows: off unless asked
  useEffect(() => {
    if (walkRequest) setRight("walk");
  }, [walkRequest]);
  const [err, setErr] = useState("");

  const load = useCallback(async (fresh = false) => {
    setLoading(true);
    setErr("");
    try {
      const url = replayAt ? `/replay/${symbol}?tf=${timeframe}&at=${replayAt}` : `/analysis/${symbol}?tf=${timeframe}${fresh ? "&fresh=true" : ""}`;
      setSnap(await api<Snapshot>(url));
    } catch (e: any) {
      setErr(e.message);
      setSnap(null);
    } finally {
      setLoading(false);
    }
  }, [symbol, timeframe, replayAt]);

  useEffect(() => {
    load();
  }, [load]);
  useEffect(() => {
    const ls = () => api<SignalRow[]>("/signals?active=true&limit=200").then(setSignals).catch(() => {});
    ls();
    const off = live.on((topic, d) => {
      if (topic === "signal") ls();
      if (topic === "scan" && d.symbol === symbol && d.tf === timeframe && !replayAt) load();
    });
    return () => { off(); };
  }, [symbol, timeframe, load, replayAt]);

  return (
    <div className="grid h-full grid-cols-[210px_1fr_380px] grid-rows-[1fr_220px]">
      <aside className="row-span-2 border-r border-line bg-panel"><MarketList /></aside>
      <section className="flex min-w-0 flex-col border-b border-line">
        <div className="flex items-center gap-3 border-b border-line bg-panel px-3 py-1.5">
          <span className="text-base font-semibold text-slate-100">{pair(symbol)}</span>
          <span className="num text-slate-300">{snap ? fmtPrice(snap.price) : ""}</span>
          <div className="flex gap-0.5">
            {TIMEFRAMES.map((t) => (
              <button key={t} onClick={() => setTimeframe(t)} className={`rounded px-1.5 py-0.5 text-[11px] ${t === timeframe ? "bg-sky-500/20 text-sky-300" : "text-slate-500 hover:text-slate-200"}`}>{t}</button>
            ))}
          </div>
          <button onClick={() => setRight("signal")} className="btn btn-primary shrink-0 whitespace-nowrap py-1 text-[12px]" title="Show the main signal for this coin">
            <GraduationCap size={14} /> Signal
          </button>
          {err && <span className="text-[11px] text-amber-300">{err}</span>}
          <label className="ml-auto flex items-center gap-1.5 text-[11px] text-slate-500" title="Arrows from the older chart scanner (a Lab system). The main signal is the Signal tab, drawn as entry, stop loss and take profit.">
            <input type="checkbox" checked={showScanner} onChange={(e) => setShowScanner(e.target.checked)} /> show chart-scanner arrows (Lab)</label>
        </div>
        <div className="min-h-0 flex-1"><TradingChart snapshot={snap} signals={showScanner ? signals : []} /></div>
      </section>
      <aside className="row-span-2 flex min-h-0 flex-col border-l border-line bg-panel">
        <div className="flex border-b border-line text-xs">
          <button className={`tab ${right === "signal" ? "active" : ""}`} onClick={() => setRight("signal")}>Signal</button>
          <button className={`tab ${right === "walk" ? "active" : ""}`} onClick={() => setRight("walk")} title="The older chart scanner (Lab): a code reading of this chart, not the main signal">Chart reading (Lab)</button>
          <button className={`tab ${right === "analysis" ? "active" : ""}`} onClick={() => setRight("analysis")} title="The older chart scanner (Lab)">Detail (Lab)</button>
          <button className={`tab ${right === "chat" ? "active" : ""}`} onClick={() => setRight("chat")}>Analyst chat</button>
        </div>
        <div className="min-h-0 flex-1">{right === "signal" ? <SignalTab /> : right === "walk" ? <Walkthrough onClose={() => setRight("analysis")} /> : right === "analysis" ? <AnalysisPanel snap={snap} reload={() => load(true)} loading={loading} /> : <ChatPanel />}</div>
      </aside>
      <section className="min-w-0 bg-panel"><BottomPanel signals={signals} snap={snap} /></section>
    </div>
  );
}
