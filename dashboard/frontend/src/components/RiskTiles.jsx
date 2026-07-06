import React from "react";
import { useDashboard } from "../ws.jsx";
import { rupees } from "../format.js";

export default function RiskTiles() {
  const { risk } = useDashboard();
  const pnl = risk.daily_pnl_paise ?? 0;
  const limit = risk.daily_loss_limit_paise ?? -1; // negative number
  // presentation math only: how far along the loss budget we are
  const lossFrac = limit < 0 ? Math.min(1, Math.max(0, pnl / limit)) : 0;

  return (
    <section>
      <h2 className="mb-2 text-sm font-semibold text-zinc-400">Risk</h2>
      {risk.halted && (
        <div className="mb-3 rounded border border-red-800 bg-red-950 px-3 py-2 text-sm font-semibold text-red-300">
          Daily loss limit hit — trading halted until next session
        </div>
      )}
      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <div className="rounded-lg border border-zinc-800 bg-zinc-900 p-3">
          <div className="text-xs uppercase tracking-wide text-zinc-500">Day P&L</div>
          <div
            className={
              "mt-1 text-2xl font-bold " + (pnl >= 0 ? "text-emerald-400" : "text-red-400")
            }
          >
            {rupees(pnl, { sign: true })}
          </div>
          <div className="mt-2 h-2 w-full rounded bg-zinc-800">
            <div
              className={
                "h-2 rounded " + (lossFrac > 0.75 ? "bg-red-500" : lossFrac > 0.4 ? "bg-amber-400" : "bg-emerald-500")
              }
              style={{ width: `${lossFrac * 100}%` }}
            />
          </div>
          <div className="mt-1 text-xs text-zinc-500">
            {Math.round(lossFrac * 100)}% of loss limit ({rupees(limit)})
          </div>
        </div>
        <div className="rounded-lg border border-zinc-800 bg-zinc-900 p-3">
          <div className="text-xs uppercase tracking-wide text-zinc-500">Open positions</div>
          <div className="mt-1 text-2xl font-bold text-zinc-100">
            {risk.open_positions ?? 0}
            <span className="text-base font-normal text-zinc-500"> / {risk.max_positions ?? "-"}</span>
          </div>
        </div>
        <div className="rounded-lg border border-zinc-800 bg-zinc-900 p-3">
          <div className="text-xs uppercase tracking-wide text-zinc-500">Trades today</div>
          <div className="mt-1 text-2xl font-bold text-zinc-100">
            {risk.trades_today ?? 0}
            <span className="text-base font-normal text-zinc-500">
              {" "}/ {risk.max_trades_per_day ?? "∞"}
            </span>
          </div>
        </div>
      </div>
    </section>
  );
}
