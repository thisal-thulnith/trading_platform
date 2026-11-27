"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import AiCredits from "@/components/dashboard/AiCredits";
import { Card, More, Page, PageHead } from "@/components/ui/Page";

export default function Settings() {
  const [perm, setPerm] = useState<string>("unsupported");
  const [browserOn, setBrowserOn] = useState(true);
  useEffect(() => {
    if (typeof Notification !== "undefined") setPerm(Notification.permission);
    try { setBrowserOn(localStorage.getItem("ait_browser_notif") !== "off"); } catch {}
  }, []);
  return (
    <Page>
      <PageHead title="Settings" sub="Your account, alerts, AI usage and the guide" />

      <Card title="Platform guide" sub="Every page, number and chart explained in simple English, plus a crypto glossary and FAQ."
        right={<div className="flex gap-2"><Link href="/guide" className="btn btn-primary">Open the guide</Link>
          <a href="/guide/Platform-Guide.pdf" download className="btn btn-ghost">Download PDF</a></div>}>
        <div className="flex flex-wrap gap-2 text-[13px]">
          {[["Start here", "1-start-here"], ["Reading Today's signal", "4-reading-today-s-signal-step-by-step"], ["Leverage and liquidation", "9-risk-and-leverage-rules-for-a-20x-trader"], ["Place and manage a trade", "10-how-to-place-a-trade-and-manage-it"],
            ["Glossary", "11-crypto-a-z-glossary"], ["FAQ", "12-faq"]].map(([l, id]) => (
            <Link key={id} href={`/guide#${id}`} className="rounded-full bg-fill px-3 py-1.5 text-slate-300 hover:text-slate-100">{l}</Link>))}
        </div>
      </Card>

      <section className="space-y-3">
        <h2 className="text-[13px] font-semibold uppercase tracking-wider text-slate-500">AI usage</h2>
        <AiCredits />
      </section>

      <div className="grid gap-6 md:grid-cols-2">
        <Card title="Your account">
          <div className="space-y-2 text-[13.5px] text-slate-400">
            <div>Your balance, today&apos;s plan and your trades live on <Link href="/portfolio" className="text-accent hover:underline">Portfolio</Link>.</div>
            <div className="text-slate-500">No login needed: everything is stored on this computer.</div>
          </div>
        </Card>
        <Card title="Notifications">
          <div className="space-y-3 text-[13.5px]">
            <div className="text-slate-300">Browser permission: <b>{perm}</b> {perm === "default" && <button className="btn ml-2" onClick={() => Notification.requestPermission().then(setPerm)}>Enable</button>}</div>
            <label className="flex items-center gap-2 text-slate-300"><input type="checkbox" checked={browserOn} onChange={(e) => { setBrowserOn(e.target.checked); localStorage.setItem("ait_browser_notif", e.target.checked ? "on" : "off"); }} />Browser pop-ups for medium and high alerts</label>
          </div>
        </Card>
      </div>

      <More>
        <Card title="Telegram and e-mail alerts">
          <p className="text-[13px] text-slate-400">Set up on the server (AIT_TELEGRAM_BOT_TOKEN / AIT_TELEGRAM_CHAT_ID, AIT_SMTP_URL) — see DEPLOYMENT.md. Alerts are de-duplicated and each links to the exact signal or snapshot that triggered it.</p>
        </Card>
        <Card title="Data and AI providers">
          <div className="space-y-2 text-[13px]">
            <p className="text-slate-400">Market data: Binance public endpoints (no keys). Market caps: CoinGecko (optional demo key). AI: Claude (AIT_ANTHROPIC_API_KEY) or OpenAI (AIT_OPENAI_API_KEY), both optional — the platform runs fully without it. Keys live only on the server; the browser never sees them.</p>
            <p className="text-slate-500">Exchange trading keys are never requested. Live order execution is not implemented.</p>
          </div>
        </Card>
      </More>
    </Page>
  );
}
