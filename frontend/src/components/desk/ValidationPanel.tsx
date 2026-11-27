"use client";
import { AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, ShieldCheck, ShieldX, Wrench, XCircle } from "lucide-react";
import { useState } from "react";

export type Check = { group: string; name: string; status: "pass" | "warn" | "fail"; detail: string; critical: boolean };
export type FactCheck = { checked: number; traced: number; unverified: string[] };
export type Validation = {
  score: number; grade: string; evidence_score: number; evidence_grade: string; summary: string; gate: "open" | "blocked";
  critical: string[]; filled: { name: string; args: Record<string, unknown>; ok: boolean }[];
  groups: { group: string; kind: "research" | "evidence"; passed: number; total: number; checks: Check[] }[];
  fact_check: Record<string, FactCheck>;
};

const GRADE: Record<string, string> = { A: "text-emerald-300 border-emerald-500/40", B: "text-sky-300 border-sky-500/40",
  C: "text-amber-300 border-amber-500/40", D: "text-red-300 border-red-500/40", "—": "text-slate-400 border-line" };

function Icon({ s }: { s: Check["status"] }) {
  return s === "pass" ? <CheckCircle2 size={12} className="mt-0.5 shrink-0 text-emerald-400" />
    : s === "warn" ? <AlertTriangle size={12} className="mt-0.5 shrink-0 text-amber-400" />
      : <XCircle size={12} className="mt-0.5 shrink-0 text-red-400" />;
}

function Score({ label, value, grade, hint }: { label: string; value: number; grade: string; hint: string }) {
  return (
    <div className={`flex-1 rounded border px-2 py-1.5 ${GRADE[grade] ?? GRADE["—"]}`} title={hint}>
      <div className="text-[11px] font-medium text-slate-500">{label}</div>
      <div className="num text-[15px] font-semibold">{value}<span className="text-[11px] text-slate-500">/100</span> <span className="text-[13px]">{grade}</span></div>
    </div>
  );
}

/** The validators' report: two scores, the gate, every check grouped, the gaps the validator filled, and the fact-check. */
export default function ValidationPanel({ v, compact }: { v: Validation; compact?: boolean }) {
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const blocked = v.gate === "blocked";
  return (
    <div className="rounded border border-line bg-panel2 p-2.5 text-[12px]">
      <div className="mb-2 flex items-center gap-1.5">
        {blocked ? <ShieldX size={14} className="text-red-400" /> : <ShieldCheck size={14} className="text-emerald-400" />}
        <b className="text-slate-200">Validators</b>
        <span className={`chip ml-auto ${blocked ? "bg-red-500/15 text-red-300" : "bg-emerald-500/15 text-emerald-300"}`}>
          {blocked ? "trade blocked" : "gate open"}
        </span>
      </div>
      <div className="flex gap-1.5">
        <Score label="Research quality" value={v.score} grade={v.grade} hint="data quality, research coverage, every number traced, consistency" />
        <Score label="Trade evidence" value={v.evidence_score} grade={v.evidence_grade} hint="trade checks and base rates for this side" />
      </div>
      {v.critical.length > 0 && <div className="mt-2 rounded bg-red-500/10 px-2 py-1 text-[11px] text-red-200">Critical: {v.critical.join(" · ")}</div>}
      {v.filled.length > 0 && !compact && (
        <div className="mt-2 flex flex-wrap items-center gap-1 text-[10px] text-slate-500">
          <Wrench size={10} /> gaps the validator researched itself:
          {v.filled.map((f, i) => (
            <span key={i} className={`chip ${f.ok ? "bg-white/5 text-slate-400" : "bg-red-500/10 text-red-300"}`}>
              {f.name.replaceAll("_", " ")}{f.args?.timeframe ? ` ${f.args.timeframe}` : ""}
            </span>
          ))}
        </div>
      )}
      <div className="mt-2 space-y-1">
        {v.groups.map((g) => {
          const bad = g.checks.filter((c) => c.status !== "pass");
          const isOpen = open[g.group] ?? bad.some((c) => c.status === "fail");
          return (
            <div key={g.group}>
              <button className="flex w-full items-center gap-1 text-left text-[11px] text-slate-300 hover:text-slate-100" onClick={() => setOpen({ ...open, [g.group]: !isOpen })}>
                {isOpen ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
                <span>{g.group}</span>
                <span className="text-[10px] text-slate-600">{g.kind === "research" ? "research" : "evidence"}</span>
                <span className={`num ml-auto ${g.passed === g.total ? "text-emerald-300" : bad.some((c) => c.status === "fail") ? "text-red-300" : "text-amber-300"}`}>{g.passed}/{g.total}</span>
              </button>
              {isOpen && (
                <div className="ml-3 mt-0.5 space-y-0.5">
                  {g.checks.map((c) => (
                    <div key={c.name} className="flex gap-1.5 text-[11px]">
                      <Icon s={c.status} />
                      <div><span className={c.critical ? "font-semibold text-red-200" : "text-slate-200"}>{c.name}</span>
                        <span className="text-slate-500"> — {c.detail}</span></div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** "12/12 numbers traced" next to an agent; unverified numbers are listed. */
export function FactBadge({ f }: { f?: FactCheck }) {
  if (!f || !f.checked) return null;
  const ok = f.traced === f.checked;
  return (
    <span className={`chip text-[10px] ${ok ? "bg-emerald-500/10 text-emerald-300" : "bg-amber-500/10 text-amber-300"}`}
      title={ok ? "every number traced to platform data" : `unverified: ${f.unverified.join(", ")}`}>
      {f.traced}/{f.checked} numbers traced{ok ? "" : ` · unverified ${f.unverified.slice(0, 3).join(", ")}`}
    </span>
  );
}
