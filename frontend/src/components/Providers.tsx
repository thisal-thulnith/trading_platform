"use client";
import { useEffect } from "react";
import { api } from "@/lib/api";
import { hydrateStore, useStore, type Notif } from "@/lib/store";
import { live } from "@/lib/ws";

export default function Providers({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    hydrateStore();
    const { token } = useStore.getState();
    if (token) {
      api("/auth/me")
        .then((u) => useStore.getState().setAuth(token, u))
        .catch(() => useStore.getState().setAuth(null, null)); // expired/invalid token
    }
    live.onStatus = (b) => useStore.getState().setWs(b);
    live.start();
    api<Notif[]>("/notifications?limit=150").then((n) => useStore.getState().setNotifs(n)).catch(() => {});
    api<any>("/markets").then((r) => live.setBase(r.markets.map((m: any) => `${m.symbol}:5m`))).catch(() => {});
    const off = live.on((topic, d) => {
      if (topic === "candle") {
        useStore.getState().setPrice(d.symbol, d.candle.close);
        return;
      }
      if (topic !== "notification") return;
      useStore.getState().pushNotif(d);
      try {
        if (typeof Notification !== "undefined" && Notification.permission === "granted" &&
          ["high", "medium"].includes(d.severity) && localStorage.getItem("ait_browser_notif") !== "off") {
          const n = new Notification(d.title, { body: d.body, tag: String(d.id) });
          n.onclick = () => window.open(d.signal_id ? `/signals/${d.signal_id}` : d.snapshot_id ? `/snapshots/${d.snapshot_id}` : "/", "_self");
        }
      } catch {}
    });
    return () => {
      off();
    };
  }, []);
  return <>{children}</>;
}
