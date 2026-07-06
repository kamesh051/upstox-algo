// Presentation-only helpers. Money arrives in paise (ints) from the server;
// converting to rupee strings here is the frontend's ONLY money math.

export function rupees(paise, { sign = false } = {}) {
  if (paise === null || paise === undefined) return "-";
  const value = paise / 100;
  const s = value.toLocaleString("en-IN", {
    maximumFractionDigits: 2,
    minimumFractionDigits: 2,
  });
  return (sign && value > 0 ? "+" : "") + "₹" + s;
}

export function age(seconds) {
  if (seconds === null || seconds === undefined) return "-";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}

export function istClock(date = new Date()) {
  return date.toLocaleTimeString("en-IN", {
    timeZone: "Asia/Kolkata",
    hour12: false,
  });
}
