// Shared controls for stored time series (rate observations, FX rates): date range, revision
// mode and order, kept in the URL so every view is bookmarkable.

import { navigate, type Route, withQuery } from "../../lib/router";

/** Rate/FX providers (D58). Labels only; unknown codes are shown as stored. */
export const PROVIDERS: Record<string, string> = {
  us_treasury: "US Treasury",
  bank_of_canada: "Bank of Canada",
};

export function providerLabel(code: string): string {
  return PROVIDERS[code] ?? code;
}

export interface HistoryQuery {
  start: string;
  end: string;
  revisions: "latest" | "all";
  sort: "date" | "-date";
  offset: number;
}

export function readHistoryFilters(route: Route): HistoryQuery {
  const q = route.query;
  return {
    start: q.get("start") ?? "",
    end: q.get("end") ?? "",
    revisions: q.get("revisions") === "all" ? "all" : "latest",
    // Newest first by default: the recent history is what a researcher checks first.
    sort: q.get("sort") === "date" ? "date" : "-date",
    offset: Number(q.get("offset") ?? 0) || 0,
  };
}

/** False when both dates are set and the range is reversed (no request is sent then). */
export function validRange(f: HistoryQuery): boolean {
  return !(f.start && f.end && f.start > f.end);
}

export function HistoryFilters({ route }: { route: Route }) {
  const f = readHistoryFilters(route);
  const set = (changes: Record<string, string | null>) => navigate(withQuery(route, { ...changes, offset: null }));
  const rangeError = validRange(f) ? null : "The start date is after the end date.";
  return (
    <>
      <div className="control-row">
        <label>
          From
          <input type="date" value={f.start} onChange={(e) => set({ start: e.target.value })} />
        </label>
        <label>
          To
          <input type="date" value={f.end} onChange={(e) => set({ end: e.target.value })} />
        </label>
        <label>
          Revisions
          <select value={f.revisions} onChange={(e) => set({ revisions: e.target.value === "all" ? "all" : null })}>
            <option value="latest">latest per date</option>
            <option value="all">every stored revision</option>
          </select>
        </label>
        <label>
          Order
          <select value={f.sort} onChange={(e) => set({ sort: e.target.value === "date" ? "date" : null })}>
            <option value="-date">newest first</option>
            <option value="date">oldest first</option>
          </select>
        </label>
        {(f.start || f.end) && (
          <button type="button" className="secondary" onClick={() => set({ start: null, end: null })}>
            Clear dates
          </button>
        )}
      </div>
      {rangeError && <p className="small error">{rangeError}</p>}
    </>
  );
}

/**
 * For "every revision" pages: when all stored observations of a date are on the page, mark
 * the latest one (highest id, the value the API returns in "latest" mode) as in effect and
 * the others as superseded. Dates whose revisions are split across pages are left unmarked
 * rather than guessed.
 */
export function revisionMarks(
  rows: { id: number; date: string; revisions: number }[],
): Record<number, "superseded" | "in effect"> {
  const byDate = new Map<string, { id: number; revisions: number }[]>();
  for (const r of rows) byDate.set(r.date, [...(byDate.get(r.date) ?? []), r]);
  const marks: Record<number, "superseded" | "in effect"> = {};
  for (const group of byDate.values()) {
    const revisions = group[0]?.revisions ?? 1;
    if (revisions < 2 || group.length !== revisions) continue;
    const latest = Math.max(...group.map((g) => g.id));
    for (const g of group) marks[g.id] = g.id === latest ? "in effect" : "superseded";
  }
  return marks;
}
