"use client";
import {
  Activity, Bell, BookOpen, BookOpenText, Bot, CalendarDays, CandlestickChart, CircleHelp, Crosshair, Dices, FlaskConical, LayoutDashboard, LayoutGrid, LineChart, Microscope, Newspaper, PanelLeft, Search, Settings, Table2, Wallet,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import CommandPalette from "@/components/CommandPalette";
import { ago } from "@/lib/format";
import { useStore } from "@/lib/store";

type Item = { href: string; label: string; icon: typeof Activity; hint: string };
type Group = { title: string; note?: string; items: Item[] };
// One menu, grouped by what you are doing, in the order of a trading day. Every page has exactly one place.
const GROUPS: Group[] = [
  { title: "Trade", items: [
    { href: "/", label: "Home", icon: LayoutDashboard, hint: "Your day at a glance: today's signal, your account, results, the market" },
    { href: "/signal", label: "Today's signal", icon: Crosshair, hint: "The main signal: entry, stop loss, take profit, leverage, three opinions" },
    { href: "/signals", label: "All coins", icon: LayoutGrid, hint: "Every coin scored by the machine, live, with confidence" },
    { href: "/portfolio", label: "Portfolio", icon: Wallet, hint: "Your account, today's plan sized for it, and your trades" },
  ] },
  { title: "Analyse", items: [
    { href: "/terminal", label: "Chart", icon: CandlestickChart, hint: "Full chart with the main signal drawn on it" },
    { href: "/desk", label: "AI agents", icon: Bot, hint: "Ask the 9 AI agents about any coin (uses AI credits)" },
    { href: "/markets", label: "Trend ranking", icon: Table2, hint: "118 coins by trend, momentum and funding (the factor ranking)" },
    { href: "/news", label: "News", icon: Newspaper, hint: "Crypto headlines in plain words" },
  ] },
  { title: "Results", items: [
    { href: "/signal/test", label: "Results", icon: FlaskConical, hint: "Every main signal graded on live prices" },
    { href: "/history", label: "History", icon: CalendarDays, hint: "Pick a day: what the platform said and how it turned out" },
  ] },
  { title: "Lab", note: "not the main signal", items: [
    { href: "/setups", label: "4h setups", icon: Microscope, hint: "Lab: a code screener's chart setups" },
    { href: "/record", label: "Setup record", icon: LineChart, hint: "Lab: how the 4h setups did" },
    { href: "/system", label: "System account", icon: Activity, hint: "Lab: the rule-based daily system" },
    { href: "/simulator", label: "Simulator", icon: Dices, hint: "Lab: replay the factor ranking on any coin" },
  ] },
  { title: "Help", items: [
    { href: "/guide", label: "Guide", icon: BookOpenText, hint: "How to use the platform: every page, number and chart (also a PDF)" },
    { href: "/learn", label: "Lessons", icon: BookOpen, hint: "Trading lessons and glossary" },
    { href: "/settings", label: "Settings", icon: Settings, hint: "AI usage, alerts and the guide" },
  ] },
];
const ITEMS = GROUPS.flatMap((g) => g.items.map((it) => ({ ...it, group: g.title })));
const matches = (path: string, href: string) => path === href || (href !== "/" && path.startsWith(`${href}/`));
/** The menu item for this path: the most specific match ("/signal/test" is Results, not Today's signal). */
const itemOf = (path: string) => ITEMS.filter((t) => matches(path, t.href)).sort((a, b) => b.href.length - a.href.length)[0]
  ?? (path.startsWith("/signals/") ? { ...ITEMS.find((x) => x.href === "/terminal")!, label: "Scanner signal" }
    : path.startsWith("/snapshots") ? { ...ITEMS.find((x) => x.href === "/markets")!, label: "Snapshot" } : ITEMS[0]);

function NavLink({ it, path }: { it: Item; path: string }) {
  const on = itemOf(path).href === it.href;
  const Icon = it.icon;
  return (
    <Link href={it.href} title={it.hint}
      className={`flex h-9 items-center gap-3 rounded-xl px-3 text-[14px] transition-colors ${
        on ? "bg-accent-soft font-semibold text-accent" : "font-medium text-slate-300 hover:bg-fill hover:text-slate-100"}`}>
      <Icon size={17} strokeWidth={on ? 2.2 : 1.8} className="shrink-0" />
      <span className="truncate">{it.label}</span>
    </Link>
  );
}

/** The app's left sidebar: brand, the main sections, learning and settings, the live status and the account. */
export default function Sidebar() {
  const path = usePathname();
  const { wsConnected, sidebar, toggleSidebar } = useStore();
  useEffect(() => {   // on a phone the menu is an overlay: close it after you pick a page
    if (sidebar && typeof window !== "undefined" && window.innerWidth < 1024) toggleSidebar();
  }, [path]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!sidebar) return null;  // closed from the top bar: the page gets the full width
  return (
    <>
    <button aria-label="Close the menu" className="fixed inset-0 z-[65] bg-black/30 lg:hidden" onClick={toggleSidebar} />
    <aside className="fixed inset-y-0 left-0 z-[70] flex w-[232px] shrink-0 flex-col border-r border-line bg-side shadow-xl lg:static lg:z-auto lg:shadow-none">
      <div className="flex h-16 items-center px-6">
        <span className="text-[17px] font-bold tracking-tight text-slate-100">Confluence<span className="text-accent">AI</span></span>
      </div>
      <nav className="flex flex-1 flex-col overflow-y-auto px-3 pb-2">
        {GROUPS.map((g) => (
          <div key={g.title} className="mt-3 first:mt-1">
            <div className="flex items-baseline gap-2 px-3 pb-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
              {g.title}{g.note && <span className="text-[10.5px] font-normal normal-case tracking-normal text-slate-600">{g.note}</span>}</div>
            <div className="space-y-0.5">{g.items.map((it) => <NavLink key={it.href} it={it} path={path} />)}</div>
          </div>
        ))}
      </nav>
      <div className="space-y-1 p-3">
        <div className="flex h-9 items-center gap-2 px-3 text-[12.5px] text-slate-400"
          title="Live connection to the market engine">
          <span className={`h-2 w-2 shrink-0 rounded-full ${wsConnected ? "bg-up" : "bg-down"}`} />
          {wsConnected ? "Live market data" : "Engine offline"}
        </div>
      </div>
    </aside>
    </>
  );
}

/** The bar above every page: where you are, and the alerts. */
export function TopBar() {
  const path = usePathname();
  const { notifications, unread, markRead, sidebar, toggleSidebar } = useStore();
  const [open, setOpen] = useState(false);
  const [find, setFind] = useState(false);
  const [showLab, setShowLab] = useState(false);
  const shown = notifications.filter((n) => showLab || !n.signal_id);   // scanner alerts point at Lab setups, not the main signal
  useEffect(() => {   // Ctrl/⌘ + K, or "/" outside a text field: quick search from anywhere
    const onKey = (e: KeyboardEvent) => {
      const typing = /input|textarea|select/i.test((e.target as HTMLElement)?.tagName ?? "");
      if ((e.key === "k" && (e.metaKey || e.ctrlKey)) || (e.key === "/" && !typing)) { e.preventDefault(); setFind(true); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  const it = itemOf(path);
  const Icon = it.icon;
  return (
    <header className="relative z-40 flex h-16 shrink-0 items-center gap-2 border-b border-line bg-panel px-3 sm:gap-3 sm:px-4">
      <button onClick={toggleSidebar} title={sidebar ? "Close the sidebar" : "Open the sidebar"}
        className="flex h-9 w-9 items-center justify-center rounded-full text-slate-400 hover:bg-fill hover:text-slate-100"><PanelLeft size={18} /></button>
      {!sidebar && <span className="mr-1 text-[15px] font-bold tracking-tight text-slate-100">Confluence<span className="text-accent">AI</span></span>}
      <Icon size={18} className="hidden shrink-0 text-slate-400 sm:block" />
      <span className="min-w-0 truncate text-[15px] font-semibold text-slate-100">{it.label}</span>
      <span className="hidden truncate text-[13px] text-slate-500 xl:inline">{it.hint}</span>
      <button onClick={() => setFind(true)} title="Search any coin, page or action (Ctrl/⌘ K)"
        className="ml-auto flex h-10 min-w-[44px] items-center gap-2 rounded-full bg-fill px-3.5 text-[13px] text-slate-400 hover:bg-slate-800 hover:text-slate-100 md:min-w-[240px]">
        <Search size={16} /> <span className="hidden md:inline">Search coin, page…</span>
        <kbd className="ml-auto hidden rounded border border-line px-1.5 text-[10.5px] text-slate-500 md:inline">⌘K</kbd>
      </button>
      <CommandPalette open={find} onClose={() => setFind(false)}
        pages={ITEMS.map((t) => ({ group: t.group, label: t.label, hint: t.hint, href: t.href }))} />
      <Link href="/guide" title="Guide: how to use the platform"
        className="flex h-10 shrink-0 items-center gap-1.5 rounded-full bg-fill px-3 text-[13px] sm:px-3.5 font-medium text-slate-300 hover:bg-slate-800 hover:text-slate-100">
        <CircleHelp size={17} /> <span className="hidden sm:inline">Guide</span>
      </Link>
      <div className="relative">
        <button onClick={() => { setOpen(!open); markRead(); }} title="Alerts"
          className="relative flex h-10 w-10 items-center justify-center rounded-full bg-fill text-slate-300 hover:bg-slate-800 hover:text-slate-100">
          <Bell size={18} />
          {unread > 0 && shown.length > 0 && <span className="absolute right-0.5 top-0.5 min-w-[16px] rounded-full bg-down px-1 text-center text-[10px] font-semibold leading-4 text-[#fff]">{Math.min(unread, shown.length)}</span>}
        </button>
        {open && (
          <div className="absolute right-0 top-12 z-50 max-h-[70vh] w-[400px] overflow-auto rounded-2xl border border-line bg-panel shadow-pop">
            <div className="flex items-center border-b border-line px-4 py-3 text-[13px] font-semibold text-slate-100">Alerts
              {typeof Notification !== "undefined" && Notification.permission === "default" && (
                <button className="ml-auto text-[12px] font-medium text-accent hover:underline" onClick={() => Notification.requestPermission()}>
                  Turn on browser alerts</button>)}
            </div>
            <label className="flex items-center gap-2 border-b border-line px-4 py-2 text-[12px] text-slate-500">
              <input type="checkbox" checked={showLab} onChange={(e) => setShowLab(e.target.checked)} /> Show chart-scanner alerts (Lab, {notifications.filter((n) => n.signal_id).length})</label>
            {shown.length === 0 && <div className="px-4 py-8 text-center text-[13px] text-slate-500">No alerts.</div>}
            {shown.map((n) => (
              <Link key={n.id} href={n.signal_id ? `/signals/${n.signal_id}` : n.snapshot_id ? `/snapshots/${n.snapshot_id}` : "/"}
                onClick={() => setOpen(false)} className="block border-b border-line/70 px-4 py-2.5 hover:bg-slate-950">
                <div className="flex items-center gap-2">
                  <span className={`chip ${n.severity === "high" ? "bg-red-500/15 text-red-300" : n.severity === "medium" ? "bg-amber-500/15 text-amber-300" : "bg-slate-800 text-slate-300"}`}>
                    {n.category.replaceAll("_", " ")}</span>
                  <span className="ml-auto text-[11px] text-slate-500">{ago(n.ts)}</span>
                </div>
                <div className="mt-1 text-[13px] text-slate-100">{n.title}</div>
                <div className="text-[12px] text-slate-500">{n.body}</div>
              </Link>
            ))}
          </div>
        )}
      </div>
    </header>
  );
}
