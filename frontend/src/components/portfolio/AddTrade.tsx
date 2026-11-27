"use client";
import { X } from "lucide-react";
import { useMemo, useState } from "react";
import { api } from "@/lib/api";
import { fmtPrice, pair } from "@/lib/format";
import Modal from "@/components/ui/Modal";

export type AddDraft = { symbol: string; side: "LONG" | "SHORT"; entry: number; stop: number; target: number; margin: number; leverage: number;
  source: "planner" | "manual" | "agent" | "lab"; mode?: string; note?: string };

const FEE = 0.0005;     // taker fee per side
const MMR = 0.005;      // maintenance margin used for the liquidation estimate
const usd = (v: number) => `${v < 0 ? "−" : ""}$${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: Math.abs(v) < 100 ? 2 : 0 })}`;
const localNow = () => { const d = new Date(); d.setSeconds(0, 0); return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16); };

/** Record a trade you placed on Binance, your way: your entry fill, margin, leverage, stop, take profit and the time
 *  you opened it. Prefilled from Today's plan when you accept it with your own numbers. Nothing is sent to Binance. */
export default function AddTrade({ draft, equity, onClose, onAdded }: { draft: AddDraft; equity: number; onClose: () => void; onAdded: () => void }) {
  const [f, setF] = useState({ ...draft, symbol: draft.symbol.replace(/USDT$/, ""), opened: localNow() });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const set = (k: string, v: string | number) => setF((x) => ({ ...x, [k]: v }));

  const c = useMemo(() => {
    const d = f.side === "LONG" ? 1 : -1;
    const pos = f.margin * f.leverage;
    const qty = f.entry > 0 ? pos / f.entry : 0;
    const liq = f.entry * (1 - d * (1 / Math.max(f.leverage, 1) - MMR));
    const loss = qty * Math.abs(f.entry - f.stop) + pos * FEE + qty * f.stop * FEE;
    const gain = qty * Math.abs(f.target - f.entry) - pos * FEE - qty * f.target * FEE;
    const okStop = (f.entry - f.stop) * d > 0, okTarget = (f.target - f.entry) * d > 0;
    const liqFirst = f.leverage > 1 && (d === 1 ? liq >= f.stop : liq <= f.stop);
    const lossCapped = liqFirst ? f.margin : loss;   // liquidation comes first: you lose the whole margin
    return { pos, qty, liq, loss: lossCapped, gain, okStop, okTarget, liqFirst, pctAcct: equity > 0 ? (lossCapped / equity) * 100 : 0 };
  }, [f, equity]);

  const valid = f.symbol.trim() && f.entry > 0 && f.margin > 0 && f.leverage >= 1 && c.okStop && c.okTarget;
  const submit = async () => {
    setBusy(true); setErr("");
    try {
      await api("/my/trades", { method: "POST", json: {
        symbol: `${f.symbol.trim().toUpperCase().replace("/", "").replace(/USDT$/, "")}USDT`, side: f.side, venue: "futures",
        leverage: f.leverage, entry: f.entry, qty: Number(c.qty.toPrecision(8)), stop: f.stop, target: f.target,
        budget_usd: equity || 100, mode: f.mode ?? "conservative", source: f.source,
        opened_at: new Date(f.opened).toISOString(), note: f.note ?? (f.source === "planner" ? "Platform signal, my numbers" : f.source === "agent" ? "AI agents' plan" : "My own trade") } });
      onAdded(); onClose();
    } catch (e) { setErr(String((e as Error).message || e)); }
    setBusy(false);
  };
  const num = (label: string, k: "entry" | "stop" | "target" | "margin" | "leverage", hint?: string, step = "any") => (
    <label className="flex flex-col gap-1 text-[12px] text-slate-500">{label}
      <input className="input num" type="number" step={step} min={0} value={Number.isFinite(f[k]) ? f[k] : ""} onChange={(e) => set(k, +e.target.value)} />
      {hint && <span className="text-[11px] text-slate-500">{hint}</span>}
    </label>
  );

  return (
    <Modal onClose={onClose} width={640}>
        <div className="flex items-center gap-2">
          <h2 className="text-[17px] font-semibold text-slate-100">{f.source === "planner" ? "I took the platform's signal, with my numbers" : f.source === "agent" ? "I took the AI agents' plan" : "Add a trade I placed"}</h2>
          <button className="ml-auto text-slate-500 hover:text-slate-200" onClick={onClose}><X size={18} /></button>
        </div>
        <p className="mt-1 text-[12.5px] text-slate-500">Enter what you actually did on Binance. The platform tracks it from the time you opened it. Nothing is sent to Binance.</p>

        <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-3">
          <label className="flex flex-col gap-1 text-[12px] text-slate-500">Coin
            <input className="input uppercase" value={f.symbol} onChange={(e) => set("symbol", e.target.value.toUpperCase())} placeholder="e.g. SOL" /></label>
          <label className="flex flex-col gap-1 text-[12px] text-slate-500">Side
            <select className="input" value={f.side} onChange={(e) => set("side", e.target.value)}><option value="LONG">Long (buy)</option><option value="SHORT">Short (sell)</option></select></label>
          <label className="flex flex-col gap-1 text-[12px] text-slate-500">Opened at (your time)
            <input className="input" type="datetime-local" value={f.opened} onChange={(e) => set("opened", e.target.value)} /></label>
          {num("Entry price (your fill)", "entry")}
          {num("Margin you put ($)", "margin")}
          {num("Leverage", "leverage", "isolated", "1")}
          {num("Stop loss", "stop", c.okStop ? undefined : `must be ${f.side === "LONG" ? "below" : "above"} the entry`)}
          {num("Take profit", "target", c.okTarget ? undefined : `must be ${f.side === "LONG" ? "above" : "below"} the entry`)}
        </div>

        <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-4">
          {[["Position", usd(c.pos), `${c.qty ? Number(c.qty.toPrecision(6)) : 0} ${f.symbol || ""}`, "text-slate-100"],
            ["If the stop hits", c.okStop && f.entry > 0 ? usd(-c.loss) : "—", !c.okStop || !(f.entry > 0) ? "enter a valid stop" : c.liqFirst ? "liquidated first: the whole margin" : equity > 0 ? `${c.pctAcct.toFixed(1)}% of your account` : "set your balance in Portfolio", "text-red-300"],
            ["If take profit hits", c.okTarget && f.entry > 0 ? usd(c.gain) : "—", c.okTarget && f.entry > 0 ? "fees included" : "enter a valid take profit", "text-emerald-300"],
            ["Liquidation (approx.)", f.leverage > 1 ? fmtPrice(c.liq) : "none at 1x", "isolated", c.liqFirst ? "text-red-300" : "text-slate-100"]].map(([k, v, sub, tone]) => (
            <div key={k} className="rounded-xl bg-fill px-3 py-2.5"><div className="text-[11.5px] text-slate-500">{k}</div>
              <div className={`num text-[16px] font-semibold ${tone}`}>{v}</div><div className="text-[11px] text-slate-500">{sub}</div></div>))}
        </div>
        {c.liqFirst && <p className="mt-3 rounded-xl bg-red-500/10 px-3 py-2 text-[12.5px] text-red-200">At {f.leverage}x the liquidation price comes before your stop: you would lose the whole margin before the stop can work. Lower the leverage.</p>}
        {!c.liqFirst && c.pctAcct > 3 && <p className="mt-3 rounded-xl bg-amber-500/10 px-3 py-2 text-[12.5px] text-amber-200">This risks {c.pctAcct.toFixed(1)}% of your account at the stop, more than the Risk manager would (1–3%).</p>}
        {err && <p className="mt-3 text-[12.5px] text-red-300">{err}</p>}

        <div className="mt-5 flex items-center gap-2">
          <button className="btn btn-primary" disabled={!valid || busy} onClick={submit}>{busy ? "Saving…" : `Add ${f.symbol ? pair(`${f.symbol}USDT`) : "trade"} to my portfolio`}</button>
          <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
        </div>
    </Modal>
  );
}
