"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";

export default function Login() {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [pw, setPw] = useState("");
  const [err, setErr] = useState("");
  const setAuth = useStore((s) => s.setAuth);
  const router = useRouter();
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr("");
    try {
      const r = await api<any>(`/auth/${mode}`, { method: "POST", json: { email, password: pw } });
      setAuth(r.token, r.user);
      router.push(mode === "register" ? "/portfolio" : "/");
    } catch (e: any) { setErr(e.message); }
  };
  return (
    <div className="flex h-full items-center justify-center">
      <form onSubmit={submit} className="card w-[360px] space-y-3 p-5">
        <h1 className="text-lg font-semibold text-slate-100">{mode === "login" ? "Log in" : "Create account"}</h1>
        <input className="input w-full" type="email" placeholder="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        <input className="input w-full" type="password" placeholder="password (min 10 chars)" value={pw} onChange={(e) => setPw(e.target.value)} required minLength={mode === "register" ? 10 : 1} />
        {err && <div className="text-[12px] text-red-300">{err}</div>}
        <button className="btn btn-primary w-full">{mode === "login" ? "Log in" : "Register"}</button>
        <button type="button" className="w-full text-[12px] text-slate-400" onClick={() => setMode(mode === "login" ? "register" : "login")}>
          {mode === "login" ? "No account? Register" : "Have an account? Log in"}
        </button>
        <p className="text-[11px] text-slate-600">Accounts store watchlists, chart drawings, layouts and paper portfolios. No exchange keys are ever requested.</p>
      </form>
    </div>
  );
}
