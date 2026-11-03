"use client";
import { WS_URL } from "./api";

type Handler = (topic: string, data: any) => void;

class LiveSocket {
  ws: WebSocket | null = null;
  handlers = new Set<Handler>();
  subs: string[] = [];
  base: string[] = []; // always-on subscriptions (live prices for the whole universe)
  retry = 0;
  onStatus: (b: boolean) => void = () => {};
  started = false;

  start() {
    if (this.started || typeof window === "undefined") return;
    this.started = true;
    this.connect();
  }

  connect() {
    const ws = new WebSocket(WS_URL);
    this.ws = ws;
    ws.onopen = () => {
      this.retry = 0;
      this.onStatus(true);
      this.send();
    };
    ws.onmessage = (ev) => {
      try {
        const m = JSON.parse(ev.data);
        this.handlers.forEach((h) => h(m.topic, m.data));
      } catch {}
    };
    ws.onclose = () => {
      this.onStatus(false);
      const delay = Math.min(30000, 1000 * 2 ** this.retry++) + Math.random() * 500;
      setTimeout(() => this.connect(), delay);
    };
    ws.onerror = () => ws.close();
  }

  private send() {
    const all = Array.from(new Set([...this.base, ...this.subs])).slice(0, 50);
    if (all.length && this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify({ subscribe: all }));
  }

  subscribe(keys: string[]) {
    this.subs = keys;
    this.send();
  }

  setBase(keys: string[]) {
    this.base = keys;
    this.send();
  }

  on(h: Handler) {
    this.handlers.add(h);
    return () => {
      this.handlers.delete(h);
    };
  }
}

export const live = new LiveSocket();
