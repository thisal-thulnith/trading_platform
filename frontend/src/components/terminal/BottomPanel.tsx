"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { ago, dirColor, fmtCompact, fmtNum, fmtPrice, pair, stateColor } from "@/lib/format";
import { useStore } from "@/lib/store";
import type { SignalRow, Snapshot } from "@/lib/types";
import { live } from "@/lib/ws";
import { Pct, SignalPnl } from "@/components/ui/Pnl";
import TakeSignal from "@/components/portfolio/TakeSignal";

export default function BottomPanel({ signals, snap }: { signals: SignalRow[]; snap: Snapshot | null }) {
  const [tab, setTab] = useState("signals");
  const { notifications, symbol, timeframe, setSymbol, setTimeframe, token, setReplay, replayAt } = useStore();
  const [liqs, setLiqs] = useState<any[]>([]);
  const [pf, setPf] = useState<any>(null);
  const [hist, setHist] = useState<any[]>([]);
  const [book, setBook] = useState<any>(null);

  useEffect(() => live.on((t, d) => { if (t === "liquidation") setLiqs((l) => [d, ...l].slice(0, 100)); }), []);
  useEffect(() => {
    if (tab === "positions" && token) {
      const load = () => api("/portfolio").then(setPf).catch(() => setPf(null));
      load();
      const t = setInterval(load, 3000);
      return () => clearInterval(t);
    }
    if (tab === "history") api<any[]>(`/analysis/${symbol}/history?tf=${timeframe}`).then(setHist).catch(() => {});
    if (tab === "flow") api(`/orderbook/${symbol}`).then(setBook).catch(() => setBook(null));
  }, [tab, token, symbol, timeframe]);

  const TABS = [["signals", `Chart-scanner signals, Lab (${signals.filter((s) => ["FORMING", "PRE_SIGNAL", "CONFIRMED", "ACTIVE"].includes(s.state)).length})`],
    ["alerts", "Alerts"], ["flow", "Order flow"], ["positions", "Positions"], ["history", "Analysis history"], ["replay", "Replay"]];
  return (
    <div className="flex h-full flex-col">
      <div className="flex border-b border-line text-xs">
        {TABS.map(([k, l]) => <button key={k} className={`tab ${tab === k ? "active" : ""}`} onClick={() => setTab(k)}>{l}</button>)}
      </div>
      <div className="flex-1 overflow-auto text-[12px]">
        {tab === "signals" && (
          <table className="grid-t">
            <thead><tr><th>Market</th><th>TF</th><th>Strategy</th><th>Dir</th><th>State</th><th>Entry</th><th>SL</th><th>TP1</th><th>Last</th><th>Live P&amp;L</th><th>Missing / outcome</th><th>Age</th><th /></tr></thead>
            <tbody>
              {signals.filter((s) => ["FORMING", "PRE_SIGNAL", "CONFIRMED", "ACTIVE"].includes(s.state))
                .filter((s, i, all) => all.findIndex((x) => x.symbol === s.symbol && x.timeframe === s.timeframe && x.strategy === s.strategy && x.direction === s.direction) === i)   // one row per setup
                .sort((a, b) => (RANK[a.state] - RANK[b.state]) || b.created_at.localeCompare(a.created_at)).map((s) => (
                <tr key={s.id} className="cursor-pointer" onClick={() => { setSymbol(s.symbol); setTimeframe(s.timeframe); }}>
                  <td className="text-slate-200">{pair(s.symbol)}</td><td>{s.timeframe}</td><td>{s.strategy}</td>
                  <td className={dirColor(s.direction)}>{s.direction}</td>
                  <td><span className={`chip ${stateColor(s.state)}`}>{s.state}</span></td>
                  <td className="num">{fmtPrice(s.entry_low)}–{fmtPrice(s.entry_high)}</td>
                  <td className="num text-down">{fmtPrice(s.stop)}</td><td className="num text-up">{fmtPrice(s.tp1)}</td>
                  <td className="num">{fmtPrice(s.last_price)}</td>
                  <td><SignalPnl s={s} /></td>
                  <td className="max-w-[260px] truncate text-slate-500">{s.summary?.blocked_reasons?.length ? "blocked: " + s.summary.blocked_reasons.join("; ") : (s.summary?.missing || []).join(", ")}</td>
                  <td className="text-slate-500"><Link href={`/signals/${s.id}`} onClick={(e) => e.stopPropagation()} className="text-sky-400">{ago(s.created_at)}</Link></td>
                  <td onClick={(e) => e.stopPropagation()}>{s.stop && s.tp1 && (s.direction === "LONG" || s.direction === "SHORT") &&
                    <TakeSignal symbol={s.symbol} side={s.direction} origin="lab" compact label="I took it"
                      levels={{ entry: (s.entry_low + s.entry_high) / 2, stop: s.stop, target: s.tp1 }} />}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {tab === "alerts" && notifications.map((n) => (
          <div key={n.id} className="flex gap-2 border-b border-line/50 px-2 py-1">
            <span className="w-16 text-slate-500">{ago(n.ts)}</span>
            <span className={`chip ${n.severity === "high" ? "bg-red-500/20 text-red-300" : "bg-slate-600/30 text-slate-300"}`}>{n.category.replaceAll("_", " ")}</span>
            <span className="text-slate-200">{n.title}</span><span className="truncate text-slate-500">{n.body}</span>
            {n.signal_id && <Link href={`/signals/${n.signal_id}`} className="ml-auto text-sky-400">open</Link>}
          </div>
        ))}
        {tab === "flow" && (
          <div className="grid grid-cols-2 gap-3 p-2">
            <div>
              <div className="mb-1 text-[11px] text-slate-500">Liquidations (Binance USDⓈ-M, ≤1/s per symbol — lower bound)</div>
              <table className="grid-t"><thead><tr><th>Time</th><th>Symbol</th><th>Side</th><th>Price</th><th>Notional</th></tr></thead>
                <tbody>{[...liqs, ...(snap?.liquidations || []).slice().reverse()].slice(0, 60).map((l, i) => (
                  <tr key={i}><td>{new Date(l.time).toISOString().slice(11, 19)}</td><td>{l.symbol}</td>
                    <td className={l.side === "SELL" ? "text-down" : "text-up"}>{l.side === "SELL" ? "long liq" : "short liq"}</td>
                    <td className="num">{fmtPrice(l.price)}</td><td className="num">{fmtCompact(l.notional)}</td></tr>))}</tbody></table>
            </div>
            <div>
              <div className="mb-1 text-[11px] text-slate-500">Order book snapshot {symbol} (top 50 levels, observed)</div>
              {book?.raw ? (
                <div className="num grid grid-cols-2 gap-2 text-[11px]">
                  <div>{book.raw.bids.slice(0, 15).map((b: number[], i: number) => <div key={i} className="flex justify-between text-up"><span>{fmtPrice(b[0])}</span><span>{fmtNum(b[1], 4)}</span></div>)}</div>
                  <div>{book.raw.asks.slice(0, 15).map((a: number[], i: number) => <div key={i} className="flex justify-between text-down"><span>{fmtPrice(a[0])}</span><span>{fmtNum(a[1], 4)}</span></div>)}</div>
                  <div className="col-span-2 text-slate-500">walls: {(book.walls || []).map((w: any) => `${w.side} ${fmtPrice(w.price)} (${fmtCompact(w.notional)})`).join(", ") || "none"}</div>
                </div>
              ) : <div className="text-slate-500">No snapshot yet.</div>}
            </div>
          </div>
        )}
        {tab === "positions" && (!token ? <div className="p-2 text-slate-500"><Link href="/login" className="text-sky-400">Log in</Link> to paper trade.</div> : pf ? (
          <table className="grid-t"><thead><tr><th>Symbol</th><th>Side</th><th>Qty</th><th>Entry</th><th>Mark</th><th>Stop</th><th>TP</th><th>uPnL</th><th>P&amp;L %</th><th>ROE %</th></tr></thead>
            <tbody>{pf.open_positions.map((p: any) => (
              <tr key={p.id}><td>{pair(p.symbol)}</td><td className={dirColor(p.side)}>{p.side}</td><td className="num">{fmtNum(p.qty, 5)}</td>
                <td className="num">{fmtPrice(p.entry_price)}</td><td className="num">{fmtPrice(p.mark)}</td><td className="num">{fmtPrice(p.stop)}</td>
                <td className="num">{fmtPrice(p.take_profit)}</td><td className={`num ${p.unrealized_pnl >= 0 ? "text-up" : "text-down"}`}>{fmtNum(p.unrealized_pnl)}</td>
                <td><Pct v={p.unrealized_pnl_pct} /></td><td><Pct v={p.roe_pct} /></td></tr>))}</tbody></table>
        ) : <div className="p-2 text-slate-500">Complete onboarding on the <Link href="/portfolio" className="text-sky-400">Portfolio</Link> page.</div>)}
        {tab === "history" && (
          <table className="grid-t"><thead><tr><th>Saved</th><th>As of</th><th>Decision</th><th>Bias</th><th>Regime</th><th></th></tr></thead>
            <tbody>{hist.map((h, i) => (
              <tr key={h.id}><td>{ago(h.created_at)}</td><td>{new Date(h.as_of).toISOString().slice(0, 16)}</td><td>{h.decision}</td>
                <td className={dirColor(h.bias)}>{h.bias.toFixed(2)}</td><td>{h.regime}</td>
                <td><Link className="text-sky-400" href={`/snapshots/${h.id}`}>open</Link>{hist[i + 1] && <> · <Link className="text-sky-400" href={`/snapshots/${h.id}?compare=${hist[i + 1].id}`}>diff prev</Link></>}</td></tr>))}</tbody></table>
        )}
        {tab === "replay" && (
          <div className="space-y-2 p-3">
            <div className="text-slate-400">Replay reconstructs the analysis using only candles that had closed at the chosen time (no future data). Derivatives, order book and news are excluded because their history is incomplete.</div>
            <div className="flex items-center gap-2">
              <input type="datetime-local" className="input" onChange={(e) => e.target.value && setReplay(Math.floor(Date.parse(e.target.value + "Z") / 1000))} />
              {replayAt && (<>
                <button className="btn" onClick={() => setReplay(replayAt - tfSec(timeframe))}>◀ bar</button>
                <button className="btn" onClick={() => setReplay(replayAt + tfSec(timeframe))}>bar ▶</button>
                <button className="btn" onClick={() => setReplay(null)}>Exit replay</button>
              </>)}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

const RANK: Record<string, number> = { ACTIVE: 0, CONFIRMED: 1, PRE_SIGNAL: 2, FORMING: 3 };

export function tfSec(tf: string): number {
  const n = parseInt(tf);
  return n * (tf.endsWith("m") ? 60 : tf.endsWith("h") ? 3600 : tf.endsWith("d") ? 86400 : 604800);
}
