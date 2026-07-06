import React, { useEffect, useState } from "react";
import { useDashboard } from "../ws.jsx";
import { age } from "../format.js";

function Tile({ label, value, ok, detail }) {
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900 p-3">
      <div className="text-xs uppercase tracking-wide text-zinc-500">{label}</div>
      <div
        className={
          "mt-1 text-lg font-semibold " +
          (ok === undefined ? "text-zinc-200" : ok ? "text-emerald-400" : "text-red-400")
        }
      >
        {value}
      </div>
      {detail && <div className="mt-0.5 text-xs text-zinc-500">{detail}</div>}
    </div>
  );
}

export default function HealthTiles() {
  const { snapshot, symbols, health } = useDashboard();
  const [, bump] = useState(0);
  useEffect(() => {
    const id = setInterval(() => bump((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, []);

  const marketOpen = snapshot?.market_phase === "open";
  const tokenSec = health.token_expires_in_sec;

  return (
    <section>
      <h2 className="mb-2 text-sm font-semibold text-zinc-400">Health</h2>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <Tile
          label="Feed"
          value={health.feed ?? "…"}
          ok={health.feed === "open"}
          detail={`events dropped: ${health.events_dropped ?? 0}`}
        />
        <Tile
          label="Token"
          value={tokenSec === null || tokenSec === undefined ? "none" : age(tokenSec)}
          ok={tokenSec > 3600}
          detail="until ~03:30 IST expiry"
        />
        <Tile
          label="Telegram"
          value={health.telegram_enabled ? "on" : "off"}
          ok={health.telegram_enabled}
        />
        <Tile label="DB" value={health.db_writable ? "writable" : "error"} ok={health.db_writable} />
        <Tile
          label="Mode"
          value={snapshot?.market_phase ?? "…"}
          detail={snapshot?.now?.slice(0, 10)}
        />
      </div>
      <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-5">
        {Object.entries(symbols).map(([sym, s]) => {
          const ageSec = s.receivedAtMs
            ? (Date.now() - s.receivedAtMs) / 1000
            : s.last_tick_age_sec;
          const fresh = ageSec !== null && ageSec !== undefined && ageSec < 30;
          return (
            <Tile
              key={sym}
              label={sym}
              value={s.ltp ? s.ltp.toFixed(2) : "-"}
              ok={marketOpen ? fresh : undefined}
              detail={`tick ${age(ageSec)} ago`}
            />
          );
        })}
      </div>
    </section>
  );
}
