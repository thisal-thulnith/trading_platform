"use client";
import { RefreshCw } from "lucide-react";
import { useState } from "react";
import { api } from "@/lib/api";
import { ago, dirColor, fmtNum, fmtPct, fmtPrice } from "@/lib/format";
import { useStore } from "@/lib/store";
import type { Candidate, Snapshot } from "@/lib/types";
import ConfluenceMap from "./ConfluenceMap";
import Inspector from "./Inspector";

function Section({ title, children, right }: { title: string; children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <div className="border-b border-line px-3 py-2">
      <div className="mb-1.5 flex items-center text-[12px] font-semibold text-slate-500">{title}<span className="ml-auto normal-case">{right}</span></div>
      {children}
    </div>
  );
}

function SetupCard({ c, onShow }: { c: Candidate; onShow: () => void }) {
  const v = c.validation;
  return (
    <div className="mb-2 rounded border border-line bg-panel2 p-2 text-[12px]">
      <div className="flex items-center gap-2">
        <span className={`font-semibold ${dirColor(c.direction)}`}>{c.direction}</span>
        <span className="text-slate-300">{c.strategy.replaceAll("_", " ")}</span>
        <span className={`chip ml-auto ${c.stage === "confirmed" ? (v.passed ? "bg-violet-500/20 text-violet-300" : "bg-red-500/15 text-red-300") : c.stage === "pre_signal" ? "bg-amber-500/15 text-amber-300" : "bg-sky-500/15 text-sky-300"}`}>
          {c.stage === "confirmed" && !v.passed ? "blocked" : c.stage.replace("_", "-")}
        </span>
      </div>
      <div className="num mt-1 grid grid-cols-4 gap-1 text-[11px] text-slate-400">
        <span>Entry <b className="text-slate-200">{fmtPrice(c.entry_low)}–{fmtPrice(c.entry_high)}</b></span>
        <span>SL <b className="text-down">{fmtPrice(c.stop)}</b></span>
        <span>TP1 <b className="text-up">{fmtPrice(c.targets[0])}</b></span>
        <span>R:R <b className="text-slate-200">{fmtNum(v.rr_tp1)}</b></span>
      </div>
      <ul className="mt-1 space-y-0.5 text-[11px]">
        {c.conditions.map((x) => (
          <li key={x.name} className={x.met ? "text-slate-300" : "text-slate-500"}>{x.met ? "✓" : "○"} {x.name} <span className="text-slate-600">{x.detail}</span></li>
        ))}
      </ul>
      {!v.passed && c.stage === "confirmed" && (
        <div className="mt-1 text-[11px] text-red-300">Blocked: {v.checks.filter((k) => !k.passed).map((k) => `${k.name} (${k.detail})`).join("; ")}</div>
      )}
      {v.counter_arguments.length > 0 && (
        <details className="mt-1 text-[11px] text-slate-400"><summary className="cursor-pointer text-amber-300/80">Counter-arguments ({v.counter_arguments.length})</summary>
          <ul className="ml-3 list-disc">{v.counter_arguments.map((a) => <li key={a}>{a}</li>)}</ul></details>
      )}
      <button onClick={onShow} className="mt-1 text-[11px] text-sky-400">Show on chart →</button>
    </div>
  );
}

export default function AnalysisPanel({ snap, reload, loading }: { snap: Snapshot | null; reload: () => void; loading: boolean }) {
  const { select, highlight, addChatObject } = useStore();
  const [saving, setSaving] = useState(false);
  if (!snap) return <div className="p-3 text-slate-500">{loading ? "Running deterministic analysis…" : "No analysis available."}</div>;
  const d = snap.decision;
  const ind = snap.indicators;
  const der = snap.derivatives;
  const cands = snap.candidates.filter((c) => !c.error).sort((a, b) => ({ confirmed: 0, pre_signal: 1, forming: 2 }[a.stage] - { confirmed: 0, pre_signal: 1, forming: 2 }[b.stage]));
  const saveVersion = async () => {
    setSaving(true);
    try {
      await api(`/analysis/${snap.symbol}/run?tf=${snap.timeframe}`, { method: "POST" });
    } finally {
      setSaving(false);
    }
  };
  return (
    <div className="h-full overflow-auto">
      <Inspector snap={snap} />
      <Section title="Chart scanner call (Lab, not the main signal)" right={<span className="text-[10px] text-slate-500">as of close {new Date(snap.as_of_close_time).toISOString().slice(11, 16)}Z · {ago(snap.generated_at)}</span>}>
        <div className="flex items-center gap-2">
          <span className={`text-lg font-bold ${d.action === "LONG" ? "text-up" : d.action === "SHORT" ? "text-down" : "text-slate-300"}`}>{d.action.replace("_", " ")}</span>
          {d.quality && <span className="chip bg-violet-500/20 text-violet-300">quality {d.quality}</span>}
          <button onClick={reload} title="Re-run analysis" className="ml-auto text-slate-500 hover:text-slate-200"><RefreshCw size={13} className={loading ? "animate-spin" : ""} /></button>
          <button onClick={saveVersion} className="btn py-0 text-[11px]" disabled={saving}>{saving ? "saving…" : "Save version"}</button>
        </div>
        <ul className="mt-1 list-disc pl-4 text-[12px] text-slate-400">{d.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
      </Section>
      <Section title="Market regime">
        <div className="text-slate-200">{snap.regime.regime.replaceAll("_", " ")} {snap.regime.secondary?.length ? <span className="text-slate-500">+ {snap.regime.secondary.join(", ")}</span> : null}</div>
        <div className="text-[11px] text-slate-500">{snap.regime.reasons.join("; ")}</div>
        {snap.regime.suited_strategies && <div className="mt-0.5 text-[11px] text-slate-500">Suits: {snap.regime.suited_strategies.join(", ")}</div>}
      </Section>
      <Section title="AI confluence map" right={<span className={`num ${dirColor(snap.confluence.bias)}`}>bias {snap.confluence.bias.toFixed(2)}</span>}>
        <ConfluenceMap snap={snap} />
        <div className="mt-1 text-[10px] text-slate-600">Correlated indicators are capped per family to avoid double-counting.</div>
      </Section>
      <Section title={`Setups (${cands.length})`}>
        {cands.length === 0 && <div className="text-slate-500">No strategy conditions met — abstaining.</div>}
        {cands.map((c, i) => (
          <SetupCard key={i} c={c} onShow={() => {
            addChatObject({ id: `setup_${i}_${Date.now()}`, kind: "position", origin: "ai_chat", direction: c.direction,
              entry: (c.entry_low + c.entry_high) / 2, stop: c.stop, target: c.targets[0], label: c.strategy });
            const ann = snap.annotations.find((a) => a.id === c.anchor_id);
            if (ann) { select(ann.id); highlight(ann.id); }
          }} />
        ))}
      </Section>
      <Section title="Evidence">
        {(["bullish", "bearish"] as const).map((side) => (
          <div key={side} className="mb-1">
            <div className={`text-[11px] font-medium ${side === "bullish" ? "text-up" : "text-down"}`}>{side}</div>
            {snap.evidence.filter((e) => e.direction === (side === "bullish" ? 1 : -1)).map((e, i) => (
              <div key={i} className="flex cursor-pointer gap-2 text-[11px] hover:bg-white/5" onClick={() => e.ref && (select(e.ref), highlight(e.ref))}>
                <span className="w-20 shrink-0 text-slate-500">{e.family}</span>
                <span className="text-slate-300">{e.name}</span>
                <span className="truncate text-slate-500">{e.detail}</span>
                <span className="ml-auto shrink-0 text-[9px] uppercase text-slate-600">{e.basis}</span>
              </div>
            ))}
          </div>
        ))}
      </Section>
      <Section title="ML forecast">
        {snap.ml ? (
          <div className="text-[12px]">
            <div>P(up {snap.ml.horizon}) <b className="num text-slate-100">{(snap.ml.p_up * 100).toFixed(1)}%</b> · {snap.ml.model} v{snap.ml.version}</div>
            {snap.ml.drift_alert && <div className="text-amber-300">Feature drift alert (PSI {snap.ml.drift_psi_max.toFixed(2)})</div>}
            <div className="mt-1 text-[11px] text-slate-500">{snap.ml.contributions.map((c: any) => `${c.feature} ${c.shap > 0 ? "+" : ""}${c.shap.toFixed(3)}`).join(" · ")}</div>
          </div>
        ) : <div className="text-[12px] text-slate-500">No validated model in use. Candidate models are rejected unless they beat baselines out-of-sample after costs.</div>}
      </Section>
      <Section title="Derivatives & flow">
        {der?.available ? (<>
          <div className="num grid grid-cols-2 gap-x-3 gap-y-0.5 text-[11px]">
            <span className="text-slate-500">Funding</span><span>{fmtPct((der.funding_rate ?? 0) * 100, 4)} {der.funding_z !== undefined && `(z ${fmtNum(der.funding_z, 1)})`}</span>
            <span className="text-slate-500">OI Δ 1h / 4h</span><span>{fmtPct(der.oi_change_1h_pct)} / {fmtPct(der.oi_change_4h_pct)}</span>
            <span className="text-slate-500">Basis</span><span>{fmtNum(der.basis_bps, 1)} bps</span>
            <span className="text-slate-500">L/S accounts</span><span>{fmtNum(der.long_short_ratio)}</span>
            <span className="text-slate-500">Taker buy/sell</span><span>{fmtNum(der.taker_buy_sell_ratio)}</span>
            {der.oi_price_regime && (<><span className="text-slate-500">OI/price</span><span className="font-sans">{der.oi_price_regime.label} <i className="text-slate-600">(inferred)</i></span></>)}
            {der.squeeze_flags?.length > 0 && (<><span className="text-slate-500">Squeeze</span><span className="font-sans text-amber-300">{der.squeeze_flags.join(", ")}</span></>)}
          </div>
          {der.liquidation_bands_estimate && (
            <details className="mt-1 text-[11px] text-slate-400"><summary className="cursor-pointer">Estimated liquidation bands (estimate, not observed)</summary>
              <table className="num mt-1 w-full"><tbody>{der.liquidation_bands_estimate.map((b: any) => (
                <tr key={b.leverage}><td className="text-slate-500">{b.leverage}x</td><td className="text-down">longs ≈ {fmtPrice(b.long_liq)}</td><td className="text-up">shorts ≈ {fmtPrice(b.short_liq)}</td></tr>))}</tbody></table>
              <div className="text-[10px] text-slate-600">For positions opened near the current mark, isolated margin, flat MMR. Real liquidations depend on entries, brackets and margin mode.</div>
            </details>
          )}
        </>) : <div className="text-[11px] text-slate-500">No perpetual data.</div>}
        {snap.orderbook?.available && (
          <div className="num mt-1 grid grid-cols-2 gap-x-3 text-[11px]">
            <span className="text-slate-500">Spread</span><span>{fmtNum(snap.orderbook.spread_bps, 2)} bps</span>
            <span className="text-slate-500">Book imbalance ±0.5%</span><span className={dirColor(snap.orderbook["imbalance_0.5pct"])}>{fmtNum(snap.orderbook["imbalance_0.5pct"])}</span>
            <span className="text-slate-500">Slippage $100k buy</span><span>{snap.orderbook.slippage_buy_bps === null ? "> visible depth" : `${fmtNum(snap.orderbook.slippage_buy_bps, 2)} bps`}</span>
          </div>
        )}
      </Section>
      <Section title="Indicators">
        <div className="num grid grid-cols-3 gap-x-2 gap-y-0.5 text-[11px]">
          {Object.entries(ind).filter(([k]) => k !== "close").map(([k, v]) => (
            <span key={k}><span className="text-slate-500">{k}</span> {fmtNum(v as number, (v as number) > 100 ? 2 : 3)}</span>
          ))}
        </div>
        <div className="mt-1 text-[11px] text-slate-500">MTF: {Object.entries(snap.mtf).map(([k, v]) => `${k} ${v > 0 ? "▲" : v < 0 ? "▼" : "–"}`).join("  ")}</div>
      </Section>
      <Section title="News impact">
        {snap.fear_greed && <div className="text-[11px] text-slate-400">Fear & Greed: <b>{snap.fear_greed.value}</b> ({snap.fear_greed.label})</div>}
        {(snap.news || []).slice(0, 6).map((n: any) => (
          <a key={n.url} href={n.url} target="_blank" rel="noreferrer" className="block py-0.5 text-[11px] hover:bg-white/5">
            <span className={n.impact === "high" ? "text-red-300" : "text-slate-500"}>[{n.impact}]</span> <span className="text-slate-300">{n.title}</span> <span className="text-slate-600">{n.source}</span>
          </a>
        ))}
        <div className="text-[10px] text-slate-600">Headlines add context and abstention, never trades by themselves.</div>
      </Section>
      <div className="px-3 py-2 text-[10px] text-slate-600">engine {snap.engine_version} · data {snap.data_hash.slice(0, 12)} · decision support, not financial advice</div>
    </div>
  );
}
