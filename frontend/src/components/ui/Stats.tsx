import { fmtNum } from "@/lib/format";

export function Stat({ label, value, tone }: { label: string; value: React.ReactNode; tone?: "up" | "down" }) {
  return (
    <div className="card px-3 py-2">
      <div className="text-[11px] font-medium text-slate-500">{label}</div>
      <div className={`num text-base ${tone === "up" ? "text-up" : tone === "down" ? "text-down" : "text-slate-100"}`}>{value}</div>
    </div>
  );
}

export function TradeStatsTable({ rows }: { rows: Record<string, any> }) {
  const entries = Object.entries(rows || {});
  if (!entries.length) return <div className="text-[12px] text-slate-500">No resolved trades yet.</div>;
  return (
    <table className="grid-t text-[12px]">
      <thead><tr><th>Group</th><th>n</th><th>Win %</th><th>Expectancy R</th><th>95% CI</th><th>PF</th><th>Total R</th><th>Max loss streak</th></tr></thead>
      <tbody>
        {entries.sort((a, b) => (b[1].n || 0) - (a[1].n || 0)).map(([k, s]) => (
          <tr key={k}>
            <td className="text-slate-200">{k}{s.small_sample && <span className="chip ml-1 bg-amber-500/10 text-amber-300">n&lt;30</span>}</td>
            <td className="num">{s.n}</td><td className="num">{s.n ? fmtNum(s.win_rate * 100, 1) : "—"}</td>
            <td className={`num ${s.expectancy_r > 0 ? "text-up" : "text-down"}`}>{fmtNum(s.expectancy_r, 3)}</td>
            <td className="num text-slate-500">{s.expectancy_ci95 ? `[${fmtNum(s.expectancy_ci95[0], 2)}, ${fmtNum(s.expectancy_ci95[1], 2)}]` : "—"}</td>
            <td className="num">{fmtNum(s.profit_factor, 2)}</td><td className="num">{fmtNum(s.total_r, 1)}</td><td className="num">{s.max_consecutive_losses ?? "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
