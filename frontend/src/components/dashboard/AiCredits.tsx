"use client";
import { Coins, Pencil } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Card } from "@/components/ui/Page";

type Sum = { calls: number; input: number; cached: number; output: number; usd: number; unpriced_calls: number };
type Credit = { amount: number; set_at: string; used: number; left: number; used_pct: number | null; desk_runs_left: number | null; low: boolean };
export type Costs = { model: string | null; provider: string | null; price_per_1m: { input: number; cached: number; output: number } | null;
  today: Sum; last_7_days: Sum; last_30_days: Sum; all_time: Sum; since: string | null;
  by_day: (Sum & { day: string })[]; by_feature: (Sum & { feature: string; label: string })[];
  desk_runs: (Sum & { run: string; at: number })[]; avg_desk_run_usd: number | null; credit: Credit | null; note: string };

export const usd = (v?: number | null) => (v === undefined || v === null ? "—" : `$${v < 0.01 && v > 0 ? v.toFixed(4) : v.toFixed(2)}`);
const tok = (n: number) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${Math.round(n / 1e3)}k` : String(n));

/** AI credits, like a provider's usage page: the balance left (from the balance you enter and the spend counted since),
 *  spend by day, by feature and per AI-agent run. Estimated from the token counts the provider returns. */
export default function AiCredits() {
  const [c, setC] = useState<Costs | null>(null);
  const [edit, setEdit] = useState(false);
  const [amt, setAmt] = useState("");
  const [err, setErr] = useState("");

  const load = useCallback(() => api<Costs>("/agent/costs").then(setC).catch((e) => setErr(String(e.message || e))), []);
  useEffect(() => { load(); const t = setInterval(load, 60_000); return () => clearInterval(t); }, [load]);

  const save = async (e: React.FormEvent) => {
    e.preventDefault(); setErr("");
    if (amt.trim() === "") {   // empty: clear the balance
      try { setC(await api<Costs>("/agent/costs/credit", { method: "POST", json: { amount: null } })); setEdit(false); } catch (e2) { setErr(String((e2 as Error).message || e2)); }
      return;
    }
    const n = Number(amt);
    if (!(n >= 0)) { setErr("Enter the balance in dollars, e.g. 10"); return; }
    try { setC(await api<Costs>("/agent/costs/credit", { method: "POST", json: { amount: n } })); setEdit(false); setAmt(""); }
    catch (e2) { setErr(String((e2 as Error).message || e2)); }
  };

  const cr = c?.credit;
  const usedPct = Math.max(0, Math.min(100, (cr?.used_pct ?? 0) * 100));
  const bar = cr?.low ? "bg-red-500" : usedPct > 70 ? "bg-amber-500" : "bg-accent";
  const days = c?.by_day ?? [];
  const mx = Math.max(...days.map((d) => d.usd), 0.0001);

  return (
    <Card title={<><Coins size={17} className="text-amber-300" /> AI credits</>}
      sub={<span title={c?.price_per_1m ? `$${c.price_per_1m.input} in · $${c.price_per_1m.cached} cached · $${c.price_per_1m.output} out per 1M tokens` : undefined}>{c?.model ?? "No AI key"}</span>}>
      <div className="grid gap-6 lg:grid-cols-[1.3fr_1fr]">
        {/* balance */}
        <div>
          {cr && !edit ? (
            <>
              <div className="flex items-end gap-3">
                <div className={`num text-[34px] font-semibold leading-none ${cr.low ? "text-red-300" : "text-slate-100"}`}>{usd(Math.max(0, cr.left))}</div>
                <div className="pb-1 text-[13px] text-slate-500">left of {usd(cr.amount)}</div>
                <button className="ml-auto text-slate-500 hover:text-slate-200" title="Update balance" onClick={() => { setEdit(true); setAmt(String(cr.amount)); }}><Pencil size={14} /></button>
              </div>
              <div className="mt-3 h-2.5 overflow-hidden rounded-full bg-fill"><div className={`h-full rounded-full ${bar}`} style={{ width: `${Math.max(1.5, usedPct)}%` }} /></div>
              <div className="mt-2 flex flex-wrap gap-x-4 text-[12.5px] text-slate-500">
                <span><b className="text-slate-200">{usd(cr.used)}</b> used ({usedPct.toFixed(1)}%)</span>
                <span>since {new Date(cr.set_at).toLocaleDateString([], { month: "short", day: "numeric" })}</span>
                {cr.desk_runs_left !== null && <span>≈ <b className="text-slate-200">{cr.desk_runs_left}</b> AI-agent runs left</span>}
              </div>
              {cr.low && <p className="mt-2 text-[12.5px] text-red-300">Low balance. When it runs out, the AI agents stop; the main signal keeps working.</p>}
            </>
          ) : (
            <form onSubmit={save}>
              <div className="text-[13px] text-slate-300">{cr ? "Update your balance" : "Enter your OpenAI credit balance"}</div>
              <p className="mt-0.5 text-[12px] text-slate-500">Find it on platform.openai.com → Settings → Billing.</p>
              <div className="mt-2 flex items-center gap-2">
                <span className="relative"><span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-500">$</span>
                  <input className="input num w-32 pl-6" type="number" min={0} step="0.01" placeholder="10.00" value={amt} onChange={(e) => setAmt(e.target.value)} /></span>
                <button className="btn btn-primary" type="submit">Save</button>
                {cr && <button className="btn btn-ghost" type="button" onClick={() => setEdit(false)}>Cancel</button>}
              </div>
            </form>
          )}
          {err && <p className="mt-2 text-[12px] text-red-300">{err}</p>}

          <div className="mt-5 grid grid-cols-3 gap-3">
            {([["Today", c?.today], ["7 days", c?.last_7_days], ["30 days", c?.last_30_days]] as const).map(([k, s]) => (
              <div key={k} className="rounded-xl bg-fill px-4 py-3">
                <div className="text-[12px] text-slate-500">{k}</div>
                <div className="num mt-0.5 text-[18px] font-semibold text-slate-100">{usd(s?.usd)}</div>
                <div className="text-[11.5px] text-slate-500">{s ? `${s.calls} calls · ${tok(s.input + s.output)} tokens` : "—"}</div>
              </div>
            ))}
          </div>
        </div>

        {/* usage */}
        <div>
          <div className="text-[12.5px] text-slate-500">Spend per day · last {days.length} days</div>
          <div className="mt-2 flex h-[84px] items-end gap-1">
            {days.map((d) => (
              <div key={d.day} className="group relative flex-1" title={`${d.day}: ${usd(d.usd)} · ${d.calls} calls`}>
                <div className={`w-full rounded-t ${d.usd > 0 ? "bg-accent" : "bg-fill"}`} style={{ height: `${d.usd > 0 ? Math.max(4, (d.usd / mx) * 84) : 3}px` }} />
              </div>
            ))}
          </div>
          <div className="mt-1 flex justify-between text-[11px] text-slate-500"><span>{days[0]?.day.slice(5)}</span><span>today</span></div>

          <div className="mt-4 text-[12.5px] text-slate-500">By feature · 30 days</div>
          <div className="mt-1.5 space-y-1 text-[13px]">
            {(c?.by_feature ?? []).length === 0 && <div className="text-slate-500">No AI calls counted yet.</div>}
            {(c?.by_feature ?? []).map((f) => (
              <div key={f.feature} className="flex items-center gap-3"><span className="flex-1 text-slate-300">{f.label}</span>
                <span className="text-[11.5px] text-slate-500">{f.calls} calls</span><span className="num w-16 text-right font-medium text-slate-100">{usd(f.usd)}</span></div>
            ))}
          </div>
          {(c?.desk_runs ?? []).length > 0 && (
            <>
              <div className="mt-4 text-[12.5px] text-slate-500">Last AI-agent runs{c?.avg_desk_run_usd ? ` · about ${usd(c.avg_desk_run_usd)} a run` : ""}</div>
              <div className="mt-1.5 space-y-1 text-[12.5px]">
                {c!.desk_runs.slice(0, 5).map((r) => (
                  <div key={r.run} className="flex items-center gap-3"><span className="flex-1 text-slate-400">{new Date(r.at * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</span>
                    <span className="text-slate-500">{r.calls} calls · {tok(r.input + r.output)}</span><span className="num w-16 text-right text-slate-100">{usd(r.usd)}</span></div>
                ))}
              </div>
            </>
          )}
        </div>
      </div>
      <details className="mt-4 text-[11.5px] text-slate-500">
        <summary className="cursor-pointer hover:text-slate-300">How this is counted</summary>
        <p className="mt-1">{c?.note ?? ""}{c?.since ? ` Counting since ${new Date(c.since).toLocaleDateString([], { month: "short", day: "numeric" })}.` : " Counting starts with the next AI call."}</p>
      </details>
    </Card>
  );
}
