import { fmtPrice } from "@/lib/format";

export type Signal = { action: "BUY" | "SELL" | "WAIT"; text: string; strategy?: string; direction?: string; entry?: number;
  stop?: number; target?: number; rr?: number; stop_pct?: number; target_pct?: number; quality?: number; stage?: string };
export type ScanRow = { symbol: string; price: number; score: number; direction: string; regime: string; reasons: string[];
  agree?: boolean; signal: Signal; council: { direction: string; bull: string[]; bear: string[] } };
export type Scan = { status: string; done: number; total: number; rows: ScanRow[]; note?: string; all_coins?: number;
  screened?: number; all?: number };

export function SignalBadge({ action, big }: { action: string; big?: boolean }) {
  const c = action === "BUY" ? "bg-emerald-500/15 text-emerald-300" : action === "SELL" ? "bg-red-500/15 text-red-300" : "bg-fill text-slate-400";
  return <span className={`inline-block rounded-full font-bold ${c} ${big ? "px-4 py-1 text-lg" : "px-2.5 py-0.5 text-[11px]"}`}>{action}</span>;
}

/** Entry / stop / target in plain words, with what it means for a $100 account risking 1%. */
export function SignalLevels({ s }: { s: Signal }) {
  if (s.entry === undefined) return null;
  return (
    <div className="mt-2 grid grid-cols-3 gap-1.5 text-center text-[11px]">
      <div className="rounded bg-white/5 p-1.5"><div className="text-slate-500">Entry</div><b className="num text-slate-100">{fmtPrice(s.entry)}</b></div>
      <div className="rounded bg-red-500/10 p-1.5"><div className="text-slate-500">Stop (exit if wrong)</div><b className="num text-down">{fmtPrice(s.stop!)}</b><div className="text-slate-500">−{s.stop_pct?.toFixed(1)}%</div></div>
      <div className="rounded bg-emerald-500/10 p-1.5"><div className="text-slate-500">Target</div><b className="num text-up">{fmtPrice(s.target!)}</b><div className="text-slate-500">+{s.target_pct?.toFixed(1)}%</div></div>
      <div className="col-span-3 text-[10px] text-slate-500">$100 account, 1% risk: a loss costs ≈ $1.00 · a win pays ≈ ${(s.rr || 0).toFixed(2)}</div>
    </div>
  );
}
