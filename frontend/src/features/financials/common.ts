import type { Statement } from "../../api/endpoints";
import type { PeriodOut, StatementRowOut } from "../../api/schema.gen";

export const STATEMENTS: { key: Statement; label: string }[] = [
  { key: "IS", label: "Income statement" },
  { key: "BS", label: "Balance sheet" },
  { key: "CF", label: "Cash flow statement" },
];
export const PERIOD_TYPES = ["FY", "Q1", "Q2", "Q3", "Q4", "H1", "9M"] as const;

export const PROVENANCE: Record<string, { short: string; label: string }> = {
  reported: { short: "R", label: "Reported — as filed (SEC fact)" },
  derived: { short: "D", label: "Derived — calculated from reported values by a documented rule" },
  manual_import: { short: "I", label: "Imported — from an imported statement file" },
  analyst: { short: "A", label: "Analyst — value set by a recorded conflict decision" },
};

export function periodLabel(p: PeriodOut): string {
  return p.period_type === "FY" ? `FY${p.fiscal_year}` : `${p.period_type} FY${p.fiscal_year}`;
}

export function periodKey(lineItem: string, p: PeriodOut): string {
  return `${lineItem}|${p.period_type}|${p.start_date}|${p.end_date}`;
}

/** Monetary and share-count rows are scaled for display; per-share and ratio rows never are. */
export function isScalable(unit: string | null | undefined, currency: string): boolean {
  return unit === currency || unit === "shares";
}

export interface SelectedCell {
  row: StatementRowOut;
  period: PeriodOut;
}

