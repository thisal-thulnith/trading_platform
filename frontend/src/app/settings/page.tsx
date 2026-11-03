"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { useStore } from "@/lib/store";

export default function Settings() {
  const { user } = useStore();
  const [perm, setPerm] = useState<string>("unsupported");
  const [browserOn, setBrowserOn] = useState(true);
  useEffect(() => {
    if (typeof Notification !== "undefined") setPerm(Notification.permission);
    try { setBrowserOn(localStorage.getItem("ait_browser_notif") !== "off"); } catch {}
  }, []);
  return (
    <div className="h-full max-w-3xl space-y-3 overflow-auto p-4">
      <h1 className="text-lg font-semibold text-slate-100">Settings</h1>
      <div className="card p-3 text-[12px]"><div className="mb-1 font-medium">Account</div>{user ? <div>{user.email} · role {user.role}</div> : <Link href="/login" className="text-sky-400">Log in</Link>}
        <div className="mt-1 text-slate-500">Risk profile, capital and limits: <Link href="/portfolio" className="text-sky-400">Portfolio → Risk settings</Link></div></div>
      <div className="card space-y-2 p-3 text-[12px]"><div className="font-medium">Notifications</div>
        <div>Browser permission: <b>{perm}</b> {perm === "default" && <button className="btn ml-2" onClick={() => Notification.requestPermission().then(setPerm)}>Enable</button>}</div>
        <label className="flex items-center gap-2"><input type="checkbox" checked={browserOn} onChange={(e) => { setBrowserOn(e.target.checked); localStorage.setItem("ait_browser_notif", e.target.checked ? "on" : "off"); }} />Browser pop-ups for medium/high severity alerts</label>
        <div className="text-slate-500">Telegram and e-mail delivery are configured server-side (AIT_TELEGRAM_BOT_TOKEN / AIT_TELEGRAM_CHAT_ID, AIT_SMTP_URL) — see DEPLOYMENT.md. Alerts are de-duplicated and each links to the exact signal / snapshot that triggered it.</div></div>
      <div className="card space-y-1 p-3 text-[12px]"><div className="font-medium">API &amp; providers</div>
        <div className="text-slate-400">Market data: Binance public endpoints (no keys). Market caps: CoinGecko (optional demo key). LLM analyst: Claude (AIT_ANTHROPIC_API_KEY) or OpenAI (AIT_OPENAI_API_KEY), both optional — the platform runs fully without it. Keys live only on the server; the browser never sees them.</div>
        <div className="text-slate-500">Exchange trading keys are never requested. Live order execution is not implemented.</div></div>
    </div>
  );
}
