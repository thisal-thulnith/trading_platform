"use client";
import { useEffect, useState } from "react";
import { AreaSpark } from "@/components/dashboard/charts";
import { api } from "@/lib/api";
import { pair } from "@/lib/format";

type Block = { return_pct: number; cagr_pct: number; sharpe: number | null; max_dd_pct: number; by_year?: Record<string, number | null>;
  days_won_pct: number | null; weeks_won_pct: number | null; months_won_pct: number | null; worst_week_pct: number; worst_month_pct: number };
type Book = { name: string; full: Block; oos: Block; oos_from: string; pass: boolean; pass_rule: string;
  totals: { funding: number; basis: number; costs: number; liquidations: number }; trades: number; trade_win_pct: number | null;
  open_now: string[]; curve: { t: number; v: number }[] };
type Fwd = { start: string; data_through: string; books: Record<string, { status: string; days?: number; return_pct?: number; open?: string[] }> };

const ABOUT: Record<string, [string, string, boolean]> = {
  majors_unified: ["BTC + ETH, unified margin", "Found after the test (the spot leg as collateral) — counts only if it passes forward.", false],
  majors: ["BTC + ETH, separate margin", "Pre-registered. The short leg alone can be liquidated in a rally.", true],
  alts_unified: ["10 liquid alts, unified margin", "Found after the test — counts only if it passes forward.", false],
  alts: ["10 liquid alts, separate margin", "Pre-registered.", true],
};
const pct = (v?: number | null, d = 1) => v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(d)}%`;
const usd = (v: number) => `${v < 0 ? "−" : ""}$${Math.abs(Math.round(v)).toLocaleString()}`;

function Stat({ k, v, ok }: { k: string; v: string; ok?: boolean | null }) {
  return (
    <div><div className="text-[12px] text-slate-500">{k}</div>
      <div className={`num text-[17px] font-semibold ${ok == null ? "text-slate-100" : ok ? "text-up" : "text-down"}`}>{v}</div></div>
  );
}

/** The "steady" book: funding carry (long spot + short perp), where a high win rate is honest — with its real test. */
export default function CarryBooks() {
  const [d, setD] = useState<{ backtest: { books: Record<string, Book>; generated: string } | null; forward: Fwd | null } | null>(null);
  useEffect(() => { api("/sim/carry").then(setD).catch(() => setD({ backtest: null, forward: null })); }, []);
  if (!d) return <div className="card p-8 text-center text-[13.5px] text-slate-500">Loading…</div>;
  if (!d.backtest) return <div className="card p-8 text-center text-[13.5px] text-slate-500">The carry backtest has not run yet.</div>;
  const order = ["majors_unified", "majors", "alts_unified", "alts"].filter((k) => d.backtest!.books[k]);
  return (
    <div className="space-y-4">
      <section className="card p-5">
        <h2 className="text-[17px] font-semibold">Steady book: funding carry</h2>
        <p className="mt-1 text-[13px] text-slate-500">
          Buy the coin, short the same amount of its perpetual: price moves cancel, and the book collects the funding longs pay shorts.
          No prediction — this is where a high win rate is honest. Measured with real perp prices (the basis), fees on both legs and
          funding since 2021; judged out of sample from 2024 against the bar set before the test: ≥ 85% of weeks won, max drawdown
          under 3%, more than 4% a year.
        </p>
      </section>
      {order.map((k) => {
        const b = d.backtest!.books[k];
        const [title, about, prereg] = ABOUT[k] ?? [k, "", false];
        const f = d.forward?.books?.[k];
        return (
          <section key={k} className="card p-5">
            <div className="mb-3 flex flex-wrap items-baseline gap-2">
              <h3 className="text-[15px] font-semibold">{title}</h3>
              <span className={`chip ${b.pass ? "bg-up/10 text-up" : "bg-down/10 text-down"}`}>{b.pass ? "meets the bar" : "misses the bar"}{prereg ? "" : " · post-hoc"}</span>
              <span className="text-[12.5px] text-slate-500">{about}</span>
            </div>
            <div className="grid grid-cols-3 gap-4 md:grid-cols-7">
              <Stat k="Weeks won" v={pct(b.oos.weeks_won_pct).replace("+", "")} ok={(b.oos.weeks_won_pct ?? 0) >= 85} />
              <Stat k="Months won" v={pct(b.oos.months_won_pct).replace("+", "")} />
              <Stat k="Days won" v={pct(b.oos.days_won_pct).replace("+", "")} />
              <Stat k="Per year" v={pct(b.oos.cagr_pct)} ok={b.oos.cagr_pct > 4} />
              <Stat k="Max drawdown" v={`${b.oos.max_dd_pct}%`} ok={b.oos.max_dd_pct < 3} />
              <Stat k="Worst week" v={pct(b.oos.worst_week_pct, 2)} />
              <Stat k="Sharpe" v={`${b.oos.sharpe ?? "—"}`} />
            </div>
            <div className="mt-1 text-[12px] text-slate-500">Out of sample from {b.oos_from}. Since 2021: {pct(b.full.return_pct)} ({pct(b.full.cagr_pct)} a year), max drawdown {b.full.max_dd_pct}%.</div>
            <div className="mt-3"><AreaSpark data={b.curve} height={110} fmt={(x) => `$${Math.round(x).toLocaleString()}`} /></div>
            <div className="mt-3 flex flex-wrap gap-x-6 gap-y-1 text-[12.5px] text-slate-400">
              <span>Years: {Object.entries(b.full.by_year ?? {}).map(([y, v]) => `${y} ${pct(v)}`).join(" · ")}</span>
              <span>Funding {usd(b.totals.funding)} · basis {usd(b.totals.basis)} · fees {usd(b.totals.costs)}{b.totals.liquidations ? ` · ${b.totals.liquidations} liquidations` : ""}</span>
              <span>{b.trades} positions, {b.trade_win_pct}% closed positive</span>
              <span>Holding now: {b.open_now.length ? b.open_now.map(pair).join(", ") : "nothing"}</span>
              <span>Forward from {d.forward?.start ?? "2026-11-13"}: {f?.status === "running" ? `${f.days} days, ${pct(f.return_pct, 2)}` : "starts with the next day's data"}</span>
            </div>
          </section>
        );
      })}
    </div>
  );
}
