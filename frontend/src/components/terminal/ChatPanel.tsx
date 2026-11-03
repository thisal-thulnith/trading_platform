"use client";
import { Send } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";

type Msg = { role: "user" | "assistant"; content: string; mode?: string; walk?: boolean };
const SUGGEST = ["Analyse this market step by step", "Why is the decision what it is?", "Show me the liquidity sweep", "Which indicators disagree?",
  "Show the Elliott Wave interpretation", "Find the best setups right now", "Why did the AI choose this entry?"];

export default function ChatPanel() {
  const { symbol, timeframe, highlight, select, toggleHidden, addChatObject, setTimeframe, requestWalkthrough } = useStore();
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => end.current?.scrollIntoView({ behavior: "smooth" }), [msgs]);

  const send = async (text: string) => {
    if (!text.trim() || busy) return;
    const history = msgs.slice(-10).map(({ role, content }) => ({ role, content }));
    setMsgs((m) => [...m, { role: "user", content: text }]);
    setInput("");
    setBusy(true);
    try {
      const r = await api<any>("/chat", { method: "POST", json: { message: text, symbol, timeframe, history } });
      for (const a of r.actions || []) {
        if (a.type === "highlight") { highlight(a.annotation_id); select(a.annotation_id); }
        else if (a.type === "hide") toggleHidden(a.annotation_id, true);
        else if (a.type === "timeframe") setTimeframe(a.timeframe);
        else if (a.type === "draw") addChatObject({ ...a.object, id: `chat_${Date.now()}_${Math.random()}`, origin: "ai_chat" });
      }
      const walk = (r.actions || []).some((a: any) => a.type === "walkthrough");
      setMsgs((m) => [...m, { role: "assistant", content: r.text, mode: r.mode, walk }]);
    } catch (e: any) {
      setMsgs((m) => [...m, { role: "assistant", content: `Error: ${e.message}` }]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 space-y-2 overflow-auto p-2">
        {msgs.length === 0 && (
          <div className="text-[12px] text-slate-500">
            Ask the analyst about {symbol}. Answers use only verified, computed analysis and can draw on the chart.
            <div className="mt-2 flex flex-wrap gap-1">{SUGGEST.map((s) => <button key={s} onClick={() => send(s)} className="chip border border-line text-slate-300 hover:bg-white/5">{s}</button>)}</div>
          </div>
        )}
        {msgs.map((m, i) => (
          <div key={i} className={m.role === "user" ? "ml-8 rounded bg-sky-900/30 p-2 text-slate-200" : "prose-sm mr-4 rounded bg-panel2 p-2 text-slate-300"}>
            {m.role === "assistant" ? <ReactMarkdown>{m.content}</ReactMarkdown> : m.content}
            {m.walk && <button onClick={requestWalkthrough} className="btn btn-primary mt-2 py-0.5 text-[12px]">▶ Open step-by-step walkthrough on the chart</button>}
            {m.mode === "rules" && <div className="mt-1 text-[10px] text-slate-600">deterministic mode (no LLM provider configured)</div>}
          </div>
        ))}
        {busy && <div className="text-[12px] text-slate-500">analysing…</div>}
        <div ref={end} />
      </div>
      <form onSubmit={(e) => { e.preventDefault(); send(input); }} className="flex gap-1 border-t border-line p-1.5">
        <input value={input} onChange={(e) => setInput(e.target.value)} placeholder={`Ask about ${symbol}…`} className="input flex-1 text-[12px]" />
        <button className="btn btn-primary px-2" disabled={busy}><Send size={13} /></button>
      </form>
    </div>
  );
}
