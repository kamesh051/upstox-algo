// Always visible on every screen (PHASE-UI hard rule): mode badge, engine
// state, market phase, IST clock, WS connection dot.

import React, { useEffect, useState } from "react";
import { useDashboard } from "../ws.jsx";
import { istClock } from "../format.js";

const WS_DOT = { open: "bg-emerald-400", connecting: "bg-amber-400", closed: "bg-red-500" };

export default function StatusBar() {
  const { snapshot, wsStatus, risk } = useDashboard();
  const [clock, setClock] = useState(istClock());
  useEffect(() => {
    const id = setInterval(() => setClock(istClock()), 1000);
    return () => clearInterval(id);
  }, []);

  const mode = snapshot?.mode ?? "paper";
  const halted = risk.halted ?? snapshot?.engine_state === "HALTED";

  return (
    <header className="flex items-center gap-4 border-b border-zinc-800 bg-zinc-900 px-4 py-2 sticky top-0 z-10">
      <span className="font-semibold tracking-wide text-zinc-100">upstox-algo</span>
      <span
        className={
          "rounded px-2 py-0.5 text-xs font-bold uppercase " +
          (mode === "live" ? "bg-red-600 text-white" : "bg-amber-400 text-black")
        }
      >
        {mode}
      </span>
      <span
        className={
          "rounded px-2 py-0.5 text-xs font-semibold " +
          (halted ? "bg-red-900 text-red-300" : "bg-emerald-900 text-emerald-300")
        }
      >
        {halted ? "HALTED" : "RUNNING"}
      </span>
      <span className="text-xs text-zinc-400">
        market: <span className="text-zinc-200">{snapshot?.market_phase ?? "…"}</span>
      </span>
      {snapshot?.strategy && (
        <span className="text-xs text-zinc-400">
          {snapshot.strategy} · {snapshot.interval}
        </span>
      )}
      <div className="ml-auto flex items-center gap-3">
        <span className="font-mono text-sm text-zinc-300">{clock} IST</span>
        <span className="flex items-center gap-1 text-xs text-zinc-400">
          <span className={`inline-block h-2 w-2 rounded-full ${WS_DOT[wsStatus]}`} />
          ws
        </span>
      </div>
    </header>
  );
}
