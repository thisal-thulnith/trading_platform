"use client";
import { fmtPrice, pair } from "@/lib/format";

const usd = (v: number | null | undefined) => (v === null || v === undefined || !isFinite(v) ? "—" : `$${Math.abs(v).toFixed(2)}`);

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return <div className="flex justify-between gap-2"><span className="text-slate-500">{k}</span><span className="num text-right text-slate-200">{v}</span></div>;
}

const STATE: Record<string, [string, string, string]> = {
  live: ["bg-emerald-500/10 text-emerald-300", "LIVE SETUP", "border-l-emerald-500/60"],
  skipped: ["bg-amber-500/10 text-amber-300", "LIVE · SKIPPED", "border-l-amber-500/60"],
  near: ["bg-sky-500/10 text-sky-300", "NEAR BREAKOUT", "border-l-sky-500/60"],
  blocked: ["bg-white/5 text-slate-400", "BLOCKED", "border-l-slate-600"],
};

/** One candidate trade of the system as a Binance-style card. Live cards are selectable: the user confirms the fill. */
export default function SystemSignalCard({ s, onSelect }: { s: any; onSelect: () => void }) {
  const [chip, label, border] = STATE[s.state] ?? STATE.blocked;
  const a = s.analysis ?? {};
  const rec = a.breakout_record;
  const planned = s.state === "live" || s.state === "skipped";
  return (
    <div className={`card border-l-2 p-3 text-[12px] ${border}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold text-slate-100">{pair(s.symbol)}</span>
        <span className="chip bg-white/5 text-slate-300">{s.venue === "futures" ? `Perp · Isolated ${s.leverage ?? a.safe_leverage ?? 1}×` : "Spot"}</span>
        <span className="chip bg-emerald-500/10 text-emerald-300">LONG</span>
        <span className={`chip ${chip}`}>{label}</span>
        {a.quant && <span className="ml-auto text-slate-500">trend rank {a.quant.rank} of {a.quant.of}</span>}
      </div>
      {planned ? (
        <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1">
          <Row k="Entry (next daily open ≈)" v={fmtPrice(s.entry_ref)} />
          <Row k="Stop" v={s.stop ? `${fmtPrice(s.stop)} (-${s.stop_pct}%)` : "—"} />
          <Row k="Take profit 2R (a third)" v={s.target ? fmtPrice(s.target) : "—"} />
          <Row k="Size" v={s.qty !== undefined ? `${s.qty} (${usd(s.size_usd)})` : "—"} />
          <Row k={s.venue === "futures" ? "Margin to put" : "Amount"} v={usd(s.margin_usd)} />
          {s.venue === "futures" && <Row k="Liquidation" v={s.liquidation ? `${fmtPrice(s.liquidation)} (-${s.liquidation_pct_below}%)` : "—"} />}
          <Row k="Risk at the stop" v={usd(s.risk_usd)} />
          <Row k="Safe leverage" v={s.safe_leverage ? `${s.safe_leverage}×` : "—"} />
        </div>
      ) : (
        <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1">
          <Row k="Price" v={fmtPrice(s.entry_ref)} />
          <Row k="Breakout level" v={s.breakout_level ? fmtPrice(s.breakout_level) : "—"} />
          <Row k="Distance" v={s.distance_pct !== undefined && s.distance_pct !== null ? `${s.distance_pct}%` : "—"} />
          <Row k="If it breaks out" v={a.stop_pct ? `stop ${a.stop_pct}% · max ${a.safe_leverage}×` : "—"} />
        </div>
      )}
      {rec && (
        <div className="mt-2 text-slate-400">
          Odds from this coin&apos;s own record: {rec.trades} breakouts since {rec.since}, {(rec.win_rate * 100).toFixed(0)}% won, {rec.avg_r > 0 ? "+" : ""}{rec.avg_r}R per trade.
        </div>
      )}
      {s.reason && <div className="mt-1 text-slate-500">{s.reason}</div>}
      {s.warnings?.length > 0 && <div className="mt-1 text-amber-200">{s.warnings.join(" ")}</div>}
      {s.why && <div className={`mt-1 ${s.state === "skipped" ? "text-amber-200" : "text-slate-400"}`}>{s.why}</div>}
      <div className="mt-2 flex items-center gap-2">
        {s.selectable ? (
          <button className="btn btn-primary" onClick={onSelect}>Select this trade — I&apos;m taking it</button>
        ) : (
          <span className="text-slate-500">
            {s.state === "near" ? "Not selectable yet: the rule waits for the daily close above the level." :
              s.state === "blocked" ? "Not selectable: BTC is below its 200-day average." : "Not selectable: the risk engine skipped it."}
          </span>
        )}
      </div>
    </div>
  );
}
