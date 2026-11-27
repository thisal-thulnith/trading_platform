"use client";
import { ArrowRight, CornerDownLeft, Search } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";

export type Dest = { label: string; hint: string; href: string; group: string; keywords?: string };
type Hit = Dest & { run?: () => void };

// pages and actions you can jump to from anywhere (coins are added as you type)
const ACTIONS: Dest[] = [
  { group: "Do", label: "Add a trade I placed", hint: "Portfolio · coin, entry, margin, leverage, stop, take profit, time", href: "/portfolio?add=1", keywords: "record new position" },
  { group: "Do", label: "Set my account balance", hint: "Portfolio · the Risk manager sizes every trade for it", href: "/portfolio", keywords: "budget money" },
  { group: "Do", label: "Set my AI credit balance", hint: "Settings · AI usage", href: "/settings", keywords: "cost openai" },
  { group: "Do", label: "Download the guide (PDF)", hint: "65+ pages, every page explained", href: "/guide/Platform-Guide.pdf", keywords: "help manual pdf" },
  { group: "Do", label: "Look up a past day", hint: "Signals · History", href: "/history", keywords: "date calendar past" },
  { group: "Do", label: "Learn a word", hint: "Guide · glossary", href: "/guide#11-crypto-a-z-glossary", keywords: "glossary meaning atr r liquidation funding" },
];

/** Quick search, from every page (the search box in the top bar, or Ctrl/⌘ + K): any page, any action, any coin.
 *  Typing a coin offers its signal, chart, AI agents and "add a trade". */
export default function CommandPalette({ pages, open, onClose }: { pages: Dest[]; open: boolean; onClose: () => void }) {
  const router = useRouter();
  const { setSymbol } = useStore();
  const [q, setQ] = useState("");
  const [i, setI] = useState(0);
  const [coins, setCoins] = useState<string[]>([]);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    setQ(""); setI(0);
    setTimeout(() => input.current?.focus(), 10);
    if (!coins.length) api<{ coins: { symbol: string }[] }>("/coins").then((r) => setCoins(r.coins.map((c) => c.symbol))).catch(() => undefined);
  }, [open, coins.length]);

  const hits = useMemo<Hit[]>(() => {
    const t = q.trim().toLowerCase();
    const all: Dest[] = [...pages, ...ACTIONS];
    const match = (d: Dest) => !t || `${d.label} ${d.hint} ${d.group} ${d.keywords ?? ""}`.toLowerCase().includes(t);
    const out: Hit[] = all.filter(match).slice(0, t ? 8 : 20);
    const T = t.toUpperCase().replace("/", "").replace(/USDT$/, "");
    if (T.length >= 2) {
      const cs = coins.filter((c) => c.replace(/USDT$/, "").startsWith(T)).slice(0, 3);
      const coinHits: Hit[] = cs.flatMap((c) => {
        const b = c.replace(/USDT$/, "");
        return [
          { group: `Coin · ${b}`, label: `${b} — main signal and plan`, hint: "Signals · the plan, leverage, the three opinions", href: `/signal?symbol=${c}` },
          { group: `Coin · ${b}`, label: `${b} — chart`, hint: "Chart & AI · the main signal drawn on the chart", href: "/terminal", run: () => setSymbol(c) },
          { group: `Coin · ${b}`, label: `${b} — ask the AI agents`, hint: "Chart & AI · AI agents (runs only when you press Analyse)", href: `/desk?coin=${c}` },
          { group: `Coin · ${b}`, label: `${b} — add a trade I placed`, hint: "Portfolio · record it with your numbers", href: `/portfolio?add=${c}` },
        ];
      });
      return [...coinHits, ...out];
    }
    return out;
  }, [q, pages, coins, setSymbol]);

  const go = (h: Hit) => {
    onClose();
    h.run?.();
    if (h.href.endsWith(".pdf")) window.open(h.href, "_blank");
    else router.push(h.href);
  };
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setI((x) => Math.min(x + 1, hits.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setI((x) => Math.max(x - 1, 0)); }
    else if (e.key === "Enter" && hits[i]) { e.preventDefault(); go(hits[i]); }
    else if (e.key === "Escape") onClose();
  };
  if (!open) return null;
  let last = "";
  return (
    <div className="fixed inset-0 z-[60] flex items-start justify-center bg-black/30 p-4 pt-[12vh]" onClick={onClose}>
      <div className="w-full max-w-[620px] overflow-hidden rounded-2xl border border-line bg-panel shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-3 border-b border-line px-4">
          <Search size={17} className="text-slate-500" />
          <input ref={input} value={q} onChange={(e) => { setQ(e.target.value); setI(0); }} onKeyDown={onKey}
            placeholder="Search a coin (e.g. SOL), a page or an action…" className="h-14 flex-1 bg-transparent text-[15px] text-slate-100 outline-none placeholder:text-slate-500" />
          <kbd className="rounded-md border border-line px-1.5 py-0.5 text-[11px] text-slate-500">Esc</kbd>
        </div>
        <div className="max-h-[60vh] overflow-auto py-2">
          {hits.length === 0 && <div className="px-4 py-6 text-center text-[13px] text-slate-500">Nothing found. Try a coin like BTC, or a word like &quot;history&quot;.</div>}
          {hits.map((h, k) => {
            const head = h.group !== last ? <div key={`g-${k}`} className="px-4 pb-1 pt-2 text-[11px] font-semibold uppercase tracking-wider text-slate-500">{h.group}</div> : null;
            last = h.group;
            return (
              <div key={`${h.href}-${h.label}`}>
                {head}
                <button onMouseEnter={() => setI(k)} onClick={() => go(h)}
                  className={`flex w-full items-center gap-3 px-4 py-2 text-left ${k === i ? "bg-accent-soft" : ""}`}>
                  <div className="min-w-0 flex-1">
                    <div className={`text-[14px] ${k === i ? "font-semibold text-accent" : "text-slate-100"}`}>{h.label}</div>
                    <div className="truncate text-[12px] text-slate-500">{h.hint}</div>
                  </div>
                  {k === i ? <CornerDownLeft size={14} className="text-accent" /> : <ArrowRight size={14} className="text-slate-600" />}
                </button>
              </div>
            );
          })}
        </div>
        <div className="flex gap-4 border-t border-line px-4 py-2 text-[11.5px] text-slate-500"><span>↑ ↓ move</span><span>Enter open</span><span>Ctrl/⌘ K anywhere</span></div>
      </div>
    </div>
  );
}
