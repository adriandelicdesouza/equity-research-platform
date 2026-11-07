/** "passed 400 · failed 12" from a {status: count} map, in the API's own vocabulary. */
export function formatCounts(obj: Record<string, unknown>): string {
  const parts = Object.entries(obj)
    .filter(([, v]) => typeof v === "number")
    .map(([k, v]) => `${k.replaceAll("_", " ")} ${String(v)}`);
  return parts.length ? parts.join(" · ") : "none";
}
