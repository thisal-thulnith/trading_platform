export const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8100";
export const WS_URL = API.replace(/^http/, "ws") + "/ws";

export function getToken(): string | null {
  try {
    return localStorage.getItem("ait_token");
  } catch {
    return null;
  }
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T = any>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = { ...(init.headers as Record<string, string>) };
  const tok = getToken();
  if (tok) headers.authorization = `Bearer ${tok}`;
  let body = init.body;
  if (init.json !== undefined) {
    headers["content-type"] = "application/json";
    body = JSON.stringify(init.json);
  }
  const r = await fetch(`${API}/api${path}`, { ...init, headers, body, cache: "no-store" });
  if (!r.ok) {
    let msg = r.statusText;
    try {
      const j = await r.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j);
    } catch {}
    throw new ApiError(r.status, msg);
  }
  return r.json();
}
