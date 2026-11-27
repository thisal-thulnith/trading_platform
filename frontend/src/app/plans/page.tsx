"use client";
import { Check } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";
import { Note, Page, PageHead } from "@/components/ui/Page";

type Plan = { key: string; name: string; price_usd_month: number; tagline: string; ai_analyses_per_day: number;
  signals_visible: number | null; timeframes: string[]; delay_min: number; features: string[] };
type Status = { plan: Plan; usage: { ai_desk_today: number; ai_desk_left: number }; enforced: boolean; plans: Plan[] };

/** The three packages, the current plan and today's AI usage. */
export default function PlansPage() {
  const { user } = useStore();
  const [st, setSt] = useState<Status | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  useEffect(() => { api<Status>("/my/plan").then(setSt).catch((e) => setErr(e.message)); }, [user]);
  const choose = (key: string) => {
    setBusy(key);
    api<Status>("/my/plan", { method: "POST", json: { plan: key } }).then(setSt).catch((e) => setErr(e.message)).finally(() => setBusy(null));
  };
  return (
    <Page>
        <PageHead title="Plans"
          sub={st ? <span>You are on <b className="font-semibold text-slate-200">{st.plan.name}</b> · {st.usage.ai_desk_today} of {st.plan.ai_analyses_per_day} AI analyses used today</span> : "Loading…"} />
        {err && <Note tone="bad">{err}</Note>}
        <div className="grid gap-5 md:grid-cols-3">
          {(st?.plans ?? []).map((p) => {
            const current = st?.plan.key === p.key;
            const featured = p.key === "pro";
            return (
              <section key={p.key} className={`flex flex-col rounded-2xl border p-6 ${featured ? "border-accent" : "border-line"}`}>
                <div className="flex items-center gap-2">
                  <h2 className="text-[17px] font-semibold">{p.name}</h2>
                  {featured && <span className="chip bg-accent-soft text-accent">Most popular</span>}
                </div>
                <p className="mt-1 text-[13px] text-slate-500">{p.tagline}</p>
                <div className="mt-5 flex items-baseline gap-1">
                  <span className="text-[34px] font-semibold tracking-tight">${p.price_usd_month}</span>
                  <span className="text-[13px] text-slate-500">{p.price_usd_month ? "/ month" : "forever"}</span>
                </div>
                <ul className="mt-5 flex-1 space-y-2.5 text-[13.5px]">
                  {p.features.map((f) => <li key={f} className="flex gap-2.5 text-slate-300"><Check size={16} className="mt-0.5 shrink-0 text-accent" />{f}</li>)}
                </ul>
                <button className={`btn mt-6 w-full ${featured && !current ? "btn-primary" : ""}`} disabled={current || !!busy || !user}
                  onClick={() => choose(p.key)} title={!user ? "Log in to choose a plan" : undefined}>
                  {current ? "Current plan" : busy === p.key ? "Switching…" : !user ? "Log in to choose" : `Choose ${p.name}`}
                </button>
              </section>
            );
          })}
        </div>
        <p className="text-[12.5px] text-slate-500">Billing is not connected yet, so switching is free. Not financial advice.</p>
    </Page>
  );
}
