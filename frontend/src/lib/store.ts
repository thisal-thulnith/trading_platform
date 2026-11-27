"use client";
import { create } from "zustand";
import type { ChartObject } from "./types";

export type Notif = { id: number; ts: string; category: string; severity: string; title: string; body: string; symbol?: string; timeframe?: string; signal_id?: string; snapshot_id?: string };
export type ChatAction = { type: string; annotation_id?: string; note?: string; object?: Partial<ChartObject>; timeframe?: string };

type State = {
  symbol: string;
  timeframe: string;
  user: { id: string; email: string; role: string } | null;
  token: string | null;
  wsConnected: boolean;
  notifications: Notif[];
  unread: number;
  selectedId: string | null;
  highlightId: string | null;
  hiddenAi: Record<string, boolean>;
  chatObjects: ChartObject[];
  layers: Record<string, boolean>;
  replayAt: number | null;
  focus: { ids: string[]; objects: ChartObject[] } | null;
  prices: Record<string, number>;
  setPrice: (symbol: string, p: number) => void;
  walkRequest: number;
  setFocus: (f: { ids: string[]; objects: ChartObject[] } | null) => void;
  requestWalkthrough: () => void;
  setSymbol: (s: string) => void;
  setTimeframe: (t: string) => void;
  setAuth: (token: string | null, user: State["user"]) => void;
  setWs: (b: boolean) => void;
  pushNotif: (n: Notif) => void;
  setNotifs: (n: Notif[]) => void;
  markRead: () => void;
  select: (id: string | null) => void;
  highlight: (id: string | null) => void;
  toggleHidden: (id: string, v?: boolean) => void;
  addChatObject: (o: ChartObject) => void;
  clearChatObjects: () => void;
  setLayer: (k: string, v: boolean) => void;
  sidebar: boolean;
  toggleSidebar: () => void;
  setReplay: (t: number | null) => void;
};

const persisted = (k: string, d: any) => {
  if (typeof window === "undefined") return d;
  try {
    const v = localStorage.getItem(k);
    return v ? JSON.parse(v) : d;
  } catch {
    return d;
  }
};
const save = (k: string, v: any) => {
  try {
    localStorage.setItem(k, JSON.stringify(v));
  } catch {}
};

// a clean chart by default: every analysis overlay starts hidden and is one click away in "Overlays"
export const DEFAULT_LAYERS = {
  order_block: false, breaker: false, mitigation_block: false, fvg: false, ifvg: false, liquidity_void: false,
  structure_break: false, liquidity_sweep: false, buy_side_liquidity: false, sell_side_liquidity: false,
  equal_highs: false, equal_lows: false, premium_zone: false, discount_zone: false, ote: false, inducement: false,
  support_resistance: false, rising_support: false, falling_resistance: false, fibonacci: false, elliott: false, signals: false,
};

export const useStore = create<State>((set, get) => ({
  symbol: "BTCUSDT",
  timeframe: "1h",
  user: null,
  token: null,
  wsConnected: false,
  notifications: [],
  unread: 0,
  selectedId: null,
  highlightId: null,
  hiddenAi: {},
  chatObjects: [],
  layers: DEFAULT_LAYERS,
  replayAt: null,
  focus: null,
  prices: {},
  setPrice: (symbol, p) => set((s) => (s.prices[symbol] === p ? s : { prices: { ...s.prices, [symbol]: p } })),
  walkRequest: 0,
  setFocus: (focus) => set({ focus }),
  requestWalkthrough: () => set((s) => ({ walkRequest: s.walkRequest + 1 })),
  setSymbol: (symbol) => {
    save("ait_symbol", symbol);
    set({ symbol, selectedId: null, highlightId: null, chatObjects: [], replayAt: null, focus: null });
  },
  setTimeframe: (timeframe) => {
    save("ait_tf", timeframe);
    set({ timeframe, selectedId: null, highlightId: null, chatObjects: [], focus: null });
  },
  setAuth: (token, user) => {
    try {
      if (token) localStorage.setItem("ait_token", token);
      else localStorage.removeItem("ait_token");
      save("ait_user", user);
    } catch {}
    set({ token, user });
  },
  setWs: (wsConnected) => set({ wsConnected }),
  pushNotif: (n) => set((s) => ({ notifications: [n, ...s.notifications].slice(0, 300), unread: s.unread + 1 })),
  setNotifs: (notifications) => set({ notifications }),
  markRead: () => set({ unread: 0 }),
  select: (selectedId) => set({ selectedId }),
  highlight: (highlightId) => set({ highlightId }),
  toggleHidden: (id, v) => set((s) => ({ hiddenAi: { ...s.hiddenAi, [id]: v ?? !s.hiddenAi[id] } })),
  addChatObject: (o) => set((s) => ({ chatObjects: [...s.chatObjects, o] })),
  clearChatObjects: () => set({ chatObjects: [] }),
  setLayer: (k, v) => {
    const layers = { ...get().layers, [k]: v };
    save("ait_layers_v2", layers);
    set({ layers });
  },
  setReplay: (replayAt) => set({ replayAt }),
  sidebar: true,
  toggleSidebar: () => {
    const sidebar = !get().sidebar;
    save("ait_sidebar", sidebar);
    set({ sidebar });
  },
}));

export function hydrateStore() {
  useStore.setState({
    symbol: persisted("ait_symbol", "BTCUSDT"),
    timeframe: persisted("ait_tf", "1h"),
    layers: { ...DEFAULT_LAYERS, ...persisted("ait_layers_v2", {}) },
    sidebar: typeof window !== "undefined" && window.innerWidth < 1024 ? false : persisted("ait_sidebar", true),   // phones start with the page
    token: (() => {
      try {
        return localStorage.getItem("ait_token");
      } catch {
        return null;
      }
    })(),
    user: persisted("ait_user", null),
  });
}
