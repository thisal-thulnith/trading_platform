"use client";
import Link from "next/link";
import { useState } from "react";
import { api } from "@/lib/api";
import { pair } from "@/lib/format";

export type TradeDraft = {
  symbol: string; venue: "spot" | "futures"; leverage: number; entry: number; qty: number; stop: number; target: number;
  budget_usd: number; mode: string; source: "system" | "manual"; opened_at?: string; note?: string;
};

/** Next UTC midnight — the daily open where a system ticket is meant to be filled. */
export const nextDailyOpen = () => { const d = new Date(); return new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate() + 1)).toISOString(); };

const lev = (x: { leverage: number }) => Math.max(x.leverage || 1, 1);
const qtyFor = (amount: number, leverage: number, entry: number) => (entry > 0 ? +((amount * Math.max(leverage, 1)) / entry).toPrecision(6) : 0);

/** "I took this trade": the user confirms the real fill of a ticket and it joins their portfolio (POST /api/my/trades).
 *  Binance-style input: margin (or amount on spot) and leverage; the quantity follows. */
export default function TakeTrade({ draft, onClose, onAdded }: { draft: TradeDraft; onClose: () => void; onAdded?: () => void }) {
  const [f, setF] = useState<TradeDraft>(draft);
  const [amount, setAmount] = useState(() => +((draft.qty * draft.entry) / lev(draft)).toFixed(2));
  const [err, setErr] = useState("");
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);
  const fromAmount = (amt: number) => { setAmount(amt); setF({ ...f, qty: qtyFor(amt, f.leverage, f.entry) }); };
  const fromQty = (q: number) => { setF({ ...f, qty: q }); setAmount(+((q * f.entry) / lev(f)).toFixed(2)); };
  const fromLeverage = (l: number) => setF({ ...f, leverage: l, qty: qtyFor(amount, l, f.entry) });
  const fromEntry = (e: number) => setF({ ...f, entry: e, qty: qtyFor(amount, f.leverage, e) });
  const set = (k: keyof TradeDraft, v: any) => setF({ ...f, [k]: v });
  const submit = async () => {
    setBusy(true); setErr("");
    try { await api("/my/trades", { method: "POST", json: f }); setDone(true); onAdded?.(); } catch (e: any) { setErr(e.message); }
    setBusy(false);
  };
  const risk = (f.entry - f.stop) * f.qty;
  const field = (label: string, value: number, onChange: (v: number) => void, step = 0.0001) => (
    <label className="flex flex-col text-[11px] text-slate-400">{label}
      <input className="input" type="number" step={step} value={value} onChange={(e) => onChange(+e.target.value)} />
    </label>
  );
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div className="card w-full max-w-lg space-y-3 p-4 text-[12px]" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center">
          <h2 className="font-semibold text-slate-100">I took this trade — {pair(f.symbol)} {f.venue === "futures" ? `Perpetual · Isolated ${f.leverage}×` : "Spot"}</h2>
          <button className="btn ml-auto" onClick={onClose}>✕</button>
        </div>
        {done ? (
          <div className="space-y-2">
            <p className="text-emerald-200">Added. It is now a running trade: marked to the live price, with the next action every day.</p>
            <div className="flex gap-2"><button className="btn btn-primary" onClick={onClose}>Back to the page</button><Link href="/portfolio" className="btn">Open my portfolio</Link></div>
          </div>
        ) : (
          <>
            <p className="text-slate-500">Enter what Binance actually filled. {f.venue === "futures" ? "Type the margin you put and the leverage you set; the quantity follows." : "Type the amount you spent; the quantity follows."} The stop and target come from the plan; change them only if your orders differ.</p>
            <div className="grid grid-cols-2 gap-2 md:grid-cols-3">
              {field("Entry price (your fill)", f.entry, fromEntry)}
              {field(f.venue === "futures" ? "Margin you put (USDT)" : "Amount spent (USDT)", amount, fromAmount, 0.01)}
              {f.venue === "futures" && field("Leverage set on Binance", f.leverage, fromLeverage, 1)}
              {field("Quantity (coins)", f.qty, fromQty, 0.000001)}
              {field("Stop price", f.stop, (v) => set("stop", v))}
              {field("Take-profit price (2R, a third)", f.target, (v) => set("target", v))}
              <label className="flex flex-col text-[11px] text-slate-400">Note
                <input className="input" value={f.note ?? ""} onChange={(e) => set("note", e.target.value)} placeholder="optional" /></label>
            </div>
            <div className="num text-slate-400">
              Position ${(f.entry * f.qty).toFixed(2)}{f.venue === "futures" ? ` = $${amount.toFixed(2)} margin × ${f.leverage}×` : ""} · risk at the stop ${risk.toFixed(2)}
              {f.budget_usd ? ` (${(risk / f.budget_usd * 100).toFixed(1)}% of your $${f.budget_usd})` : ""}
            </div>
            {err && <div className="text-red-300">{err}</div>}
            <div className="flex gap-2">
              <button className="btn btn-primary" disabled={busy} onClick={submit}>{busy ? "Saving…" : "Add as a running trade"}</button>
              <button className="btn" onClick={onClose}>Cancel</button>
            </div>
            <p className="text-slate-600">Paper record only: nothing is sent to Binance. You placed the order; the platform tracks and manages it.</p>
          </>
        )}
      </div>
    </div>
  );
}
