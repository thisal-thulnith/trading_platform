"use client";
import { Bell, ChevronDown, Circle } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { ago } from "@/lib/format";
import { useStore } from "@/lib/store";

const LINKS = [["/", "Desk"], ["/system", "$100 System"], ["/portfolio", "Portfolio"], ["/learn", "Learn"]] as const;
// expert tools, one click away but out of the way
const ADVANCED = [
  ["/terminal", "Terminal (manual charting)"], ["/board", "Analysis Board"], ["/radar", "Radar"], ["/signals", "Signal history"],
  ["/follow", "Every-trade $100"], ["/backtest", "Backtest"], ["/research", "Research Lab"], ["/news", "News"],
  ["/health", "System"], ["/methodology", "Methodology"],
] as const;

export default function Nav() {
  const path = usePathname();
  const { wsConnected, notifications, unread, markRead, user, setAuth } = useStore();
  const [open, setOpen] = useState(false);
  const [adv, setAdv] = useState(false);
  return (
    <header className="flex h-10 shrink-0 items-center gap-1 border-b border-line bg-panel px-3">
      <Link href="/" className="mr-3 font-semibold tracking-wide text-slate-100">
        <span className="text-sky-400">◆</span> Confluence<span className="text-slate-500">AI</span>
      </Link>
      {LINKS.map(([href, label]) => (
        <Link key={href} href={href}
          className={`rounded px-2 py-1 ${path === href || (href !== "/" && path.startsWith(href)) ? "bg-white/5 text-slate-100" : "text-slate-400 hover:text-slate-200"}`}>
          {label}
        </Link>
      ))}
      <div className="relative">
        <button onClick={() => setAdv(!adv)} onBlur={() => setTimeout(() => setAdv(false), 150)}
          className={`flex items-center gap-1 rounded px-2 py-1 ${ADVANCED.some(([h]) => path.startsWith(h)) ? "bg-white/5 text-slate-100" : "text-slate-400 hover:text-slate-200"}`}>
          Advanced <ChevronDown size={12} />
        </button>
        {adv && (
          <div className="absolute left-0 top-8 z-50 w-56 rounded-md border border-line bg-panel2 py-1 shadow-2xl">
            {ADVANCED.map(([href, label]) => (
              <Link key={href} href={href} onClick={() => setAdv(false)} className={`block px-3 py-1.5 text-[12px] hover:bg-white/5 ${path.startsWith(href) ? "text-slate-100" : "text-slate-400"}`}>{label}</Link>
            ))}
          </div>
        )}
      </div>
      <div className="ml-auto flex items-center gap-3">
        <span className="flex items-center gap-1 text-[11px] text-slate-500" title="Live WebSocket connection to the engine">
          <Circle size={8} className={wsConnected ? "fill-up text-up" : "fill-down text-down"} /> {wsConnected ? "live" : "offline"}
        </span>
        <div className="relative">
          <button onClick={() => { setOpen(!open); markRead(); }} className="relative rounded p-1 text-slate-400 hover:text-slate-200">
            <Bell size={16} />
            {unread > 0 && <span className="absolute -right-1 -top-1 rounded-full bg-red-500 px-1 text-[9px] text-white">{unread}</span>}
          </button>
          {open && (
            <div className="absolute right-0 top-8 z-50 max-h-[70vh] w-[420px] overflow-auto rounded-md border border-line bg-panel2 shadow-2xl">
              {typeof Notification !== "undefined" && Notification.permission === "default" && (
                <button className="w-full border-b border-line p-2 text-left text-sky-300" onClick={() => Notification.requestPermission()}>
                  Enable browser notifications
                </button>
              )}
              {notifications.length === 0 && <div className="p-3 text-slate-500">No alerts yet.</div>}
              {notifications.map((n) => (
                <Link key={n.id} href={n.signal_id ? `/signals/${n.signal_id}` : n.snapshot_id ? `/snapshots/${n.snapshot_id}` : "/news"}
                  onClick={() => setOpen(false)} className="block border-b border-line/60 p-2 hover:bg-white/5">
                  <div className="flex items-center gap-2">
                    <span className={`chip ${n.severity === "high" ? "bg-red-500/20 text-red-300" : n.severity === "medium" ? "bg-amber-500/15 text-amber-300" : "bg-slate-500/20 text-slate-300"}`}>
                      {n.category.replaceAll("_", " ")}
                    </span>
                    <span className="ml-auto text-[10px] text-slate-500">{ago(n.ts)}</span>
                  </div>
                  <div className="mt-1 text-slate-200">{n.title}</div>
                  <div className="text-[11px] text-slate-500">{n.body}</div>
                </Link>
              ))}
            </div>
          )}
        </div>
        {user ? (
          <span className="flex items-center gap-2 text-[11px] text-slate-400">
            <Link href="/settings" className="hover:text-slate-200">{user.email}</Link>
            <button onClick={() => setAuth(null, null)} className="text-slate-500 hover:text-slate-300">logout</button>
          </span>
        ) : (
          <Link href="/login" className="btn py-0.5 text-[12px]">Log in</Link>
        )}
      </div>
    </header>
  );
}
