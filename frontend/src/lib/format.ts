/** "passed 400 · failed 12" from a {status: count} map, in the API's own vocabulary. */
export function formatCounts(obj: Record<string, unknown>): string {
  const parts = Object.entries(obj)
    .filter(([, v]) => typeof v === "number")
    .map(([k, v]) => `${k.replaceAll("_", " ")} ${String(v)}`);
  return parts.length ? parts.join(" · ") : "none";
}

/**
 * Unit note for a stored value. "percent" values are percentage points as published
 * (4.84 means 4.84 %, not 0.0484): label them so, and never convert them for display.
 */
export function unitLabel(unit: string): string | null {
  if (unit === "percent") return "percentage points";
  if (unit === "text" || unit === "date" || !unit) return null;
  return unit;
}
