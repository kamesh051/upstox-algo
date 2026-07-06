import React, { useEffect, useRef, useState } from "react";
import { useDashboard } from "../ws.jsx";

const LEVELS = ["info", "warning", "error"];
const LEVEL_COLOR = {
  debug: "text-zinc-500",
  info: "text-zinc-300",
  warning: "text-amber-400",
  error: "text-red-400",
};

export default function LogTail() {
  const { logs } = useDashboard();
  const [levels, setLevels] = useState(new Set(LEVELS));
  const [autoScroll, setAutoScroll] = useState(true);
  const endRef = useRef(null);

  const visible = logs.filter((l) => levels.has(l.level ?? "info")).slice(-200);

  useEffect(() => {
    if (autoScroll) endRef.current?.scrollIntoView({ behavior: "instant", block: "end" });
  }, [visible.length, autoScroll]);

  const toggle = (lvl) =>
    setLevels((prev) => {
      const next = new Set(prev);
      next.has(lvl) ? next.delete(lvl) : next.add(lvl);
      return next;
    });

  return (
    <section>
      <div className="mb-2 flex items-center gap-3">
        <h2 className="text-sm font-semibold text-zinc-400">Log tail</h2>
        {LEVELS.map((lvl) => (
          <button
            key={lvl}
            onClick={() => toggle(lvl)}
            className={
              "rounded px-2 py-0.5 text-xs " +
              (levels.has(lvl)
                ? "bg-zinc-700 text-zinc-100"
                : "bg-zinc-900 text-zinc-600 line-through")
            }
          >
            {lvl}
          </button>
        ))}
        <label className="ml-auto flex items-center gap-1 text-xs text-zinc-400">
          <input
            type="checkbox"
            checked={autoScroll}
            onChange={(e) => setAutoScroll(e.target.checked)}
          />
          autoscroll
        </label>
      </div>
      <div className="h-72 overflow-y-auto rounded-lg border border-zinc-800 bg-black p-2 font-mono text-xs leading-5">
        {visible.map((l, i) => (
          <div key={i} className="whitespace-nowrap">
            <span className="text-zinc-600">{(l.ts ?? "").slice(11, 19)}</span>{" "}
            <span className={LEVEL_COLOR[l.level] ?? "text-zinc-300"}>
              {(l.level ?? "info").padEnd(7)}
            </span>{" "}
            <span className="text-zinc-100">{l.event}</span>{" "}
            <span className="text-zinc-500">
              {Object.entries(l)
                .filter(([k]) => !["ts", "level", "event"].includes(k))
                .map(([k, v]) => `${k}=${v}`)
                .join(" ")}
            </span>
          </div>
        ))}
        <div ref={endRef} />
      </div>
    </section>
  );
}
