// Server is the source of truth: cold loads fetch /api/state, then /ws events
// layer live updates on top. On (re)connect the snapshot is refetched so a
// refresh mid-session always rebuilds correctly (PHASE-UI definition of done).

import React, {
  createContext,
  useContext,
  useEffect,
  useReducer,
  useRef,
} from "react";

const MAX_LOGS = 300;

const initial = {
  wsStatus: "connecting", // connecting | open | closed
  snapshot: null,
  symbols: {},
  logs: [],
  risk: {},
  health: {},
};

function reducer(state, action) {
  switch (action.type) {
    case "ws-status":
      return { ...state, wsStatus: action.status };
    case "snapshot": {
      const s = action.snapshot;
      return {
        ...state,
        snapshot: s,
        symbols: s.symbols ?? {},
        logs: s.logs ?? [],
        risk: s.risk ?? {},
        health: s.health ?? {},
      };
    }
    case "event":
      return applyEvent(state, action.event);
    default:
      return state;
  }
}

function applyEvent(state, ev) {
  const p = ev.payload;
  switch (ev.type) {
    case "tick":
      return {
        ...state,
        symbols: {
          ...state.symbols,
          [p.symbol]: {
            ...state.symbols[p.symbol],
            ltp: p.ltp,
            last_tick_ts: p.ltt,
            receivedAtMs: Date.now(),
          },
        },
      };
    case "log":
      return { ...state, logs: [...state.logs.slice(-MAX_LOGS + 1), { ts: ev.ts, ...p }] };
    case "risk":
      return p.kind === "day_pnl"
        ? {
            ...state,
            risk: {
              ...state.risk,
              daily_pnl_paise: p.daily_pnl_paise,
              halted: p.halted,
              open_positions: p.open_positions,
              trades_today: p.trades_today,
            },
          }
        : state;
    case "position":
      return {
        ...state,
        risk: { ...state.risk, open_positions: p.open_positions },
      };
    case "health":
      return p.component === "feed"
        ? { ...state, health: { ...state.health, feed: p.status } }
        : state;
    default:
      return state; // candle/signal/order feed later screens
  }
}

const Ctx = createContext(null);
export const useDashboard = () => useContext(Ctx);

export function DashboardProvider({ children }) {
  const [state, dispatch] = useReducer(reducer, initial);
  const retryRef = useRef(1000);

  useEffect(() => {
    let ws = null;
    let closed = false;
    let timer = null;

    const fetchSnapshot = async () => {
      try {
        const res = await fetch("/api/state");
        if (res.ok) dispatch({ type: "snapshot", snapshot: await res.json() });
      } catch {
        /* server briefly away — the reconnect loop will retry */
      }
    };

    const connect = () => {
      if (closed) return;
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/ws`);
      dispatch({ type: "ws-status", status: "connecting" });
      ws.onopen = () => {
        retryRef.current = 1000;
        dispatch({ type: "ws-status", status: "open" });
        fetchSnapshot(); // rebuild state on every (re)connect
      };
      ws.onmessage = (msg) => {
        try {
          dispatch({ type: "event", event: JSON.parse(msg.data) });
        } catch {
          /* malformed frame — ignore */
        }
      };
      ws.onclose = () => {
        dispatch({ type: "ws-status", status: "closed" });
        if (!closed) {
          timer = setTimeout(connect, retryRef.current);
          retryRef.current = Math.min(retryRef.current * 2, 15000);
        }
      };
    };

    fetchSnapshot();
    connect();
    return () => {
      closed = true;
      clearTimeout(timer);
      ws?.close();
    };
  }, []);

  return <Ctx.Provider value={state}>{children}</Ctx.Provider>;
}
