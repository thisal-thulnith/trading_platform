"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export type AiStatus = { configured: boolean; provider: string | null; model: string | null; error: string | null; ready: boolean };
let cache: { t: number; v: AiStatus } | null = null;

/** Can the AI agents run right now? (key set and the provider not failing, e.g. out of credits). Cached for a minute,
 *  so every "Ask the AI agents" button can say why it is off instead of failing after a click. */
export function useAi(): AiStatus | null {
  const [s, setS] = useState<AiStatus | null>(cache && Date.now() - cache.t < 60_000 ? cache.v : null);
  useEffect(() => {
    if (cache && Date.now() - cache.t < 60_000) return;
    api<AiStatus>("/agent/status").then((v) => { cache = { t: Date.now(), v }; setS(v); }).catch(() => undefined);
  }, []);
  return s;
}

export const aiReason = (s: AiStatus | null) => !s ? "" : !s.configured ? "No AI key is set (backend/.env)."
  : s.error ? `The AI provider says: ${s.error.replace(/^Error code: \d+ - /, "").split(". ")[0]}.` : "";
