/** The app's page layout, so every page looks the same as "Home" and "Live results": a centred column with room to
 *  breathe, a large title with one grey line under it, white cards with soft corners and grey stat tiles. */

export function Page({ children }: { children: React.ReactNode }) {
  return <div className="mx-auto w-full min-w-0 max-w-[1320px] space-y-6 px-4 py-5 sm:px-8 sm:py-7">{children}</div>;
}

/** Everything secondary on a page: collapsed by default under one "More details" bar. */
export function More({ title = "More details", children, open }: { title?: React.ReactNode; children: React.ReactNode; open?: boolean }) {
  return (
    <details open={open} className="group">
      <summary className="flex cursor-pointer list-none items-center gap-2 rounded-2xl border border-line bg-panel px-6 py-4 text-[14px] font-semibold text-slate-200 hover:bg-fill">
        <span className="inline-block text-slate-500 transition-transform group-open:rotate-90">›</span>{title}
      </summary>
      <div className="mt-6 space-y-6">{children}</div>
    </details>
  );
}

export function PageHead({ title, sub, children }: { title: React.ReactNode; sub?: React.ReactNode; children?: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <h1 className="text-[26px] font-semibold text-slate-100">{title}</h1>
        {sub && <div className="mt-1 flex flex-wrap items-center gap-2 text-[13.5px] text-slate-500">{sub}</div>}
      </div>
      {children && <div className="flex flex-wrap items-center gap-2">{children}</div>}
    </div>
  );
}

export function Card({ title, sub, right, children, className = "" }: {
  title?: React.ReactNode; sub?: React.ReactNode; right?: React.ReactNode; children?: React.ReactNode; className?: string;
}) {
  return (
    <section className={`min-w-0 rounded-2xl border border-line bg-panel p-4 sm:p-6 ${className}`}>
      {(title || right) && (
        <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            {title && <h2 className="flex items-center gap-2 text-[16px] font-semibold text-slate-100">{title}</h2>}
            {sub && <p className="mt-0.5 text-[12.5px] text-slate-500">{sub}</p>}
          </div>
          {right}
        </div>
      )}
      {children}
    </section>
  );
}

export function Tile({ label, value, sub, tone = "text-slate-100" }: { label: React.ReactNode; value: React.ReactNode; sub?: React.ReactNode; tone?: string }) {
  return (
    <div className="rounded-2xl bg-fill px-5 py-4">
      <div className="text-[12.5px] text-slate-500">{label}</div>
      <div className={`num mt-0.5 text-[22px] font-semibold ${tone}`}>{value}</div>
      {sub && <div className="mt-0.5 text-[12px] text-slate-500">{sub}</div>}
    </div>
  );
}

/** The banner on every lab page: a separate system kept for research, not the main signal. */
export function LabBanner({ what }: { what: string }) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl border border-violet-500/25 bg-violet-500/10 px-4 py-2.5 text-[13px] text-violet-200">
      <span className="chip bg-violet-500/20 text-[11px] font-bold tracking-wide text-violet-200">LAB</span>
      <span>{what} <b>Not</b> the main signal; kept for research.</span>
      <a href="/signal" className="ml-auto font-medium text-violet-100 underline-offset-2 hover:underline">Main signal →</a>
    </div>
  );
}

/** A short coloured message: info, good, warn or bad. */
export function Note({ tone = "info", children, className = "" }: { tone?: "info" | "good" | "warn" | "bad"; children: React.ReactNode; className?: string }) {
  const c = { info: "bg-sky-500/10 text-sky-200", good: "bg-emerald-500/10 text-emerald-200", warn: "bg-amber-500/10 text-amber-200",
    bad: "bg-red-500/10 text-red-200" }[tone];
  return <div className={`rounded-xl px-4 py-3 text-[13.5px] ${c} ${className}`}>{children}</div>;
}
