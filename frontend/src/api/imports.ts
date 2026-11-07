// Import preview report shapes. The backend returns these as untyped JSON (``report.as_dict()``
// in statements/file_import.py, market/file_import.py and reference/damodaran.py), so OpenAPI
// has no schema for them; these interfaces mirror those functions and are tolerant of extra
// keys. Ingestion results (StatementIngestOut etc.) arrive inside ActionOut.result.

export type ImportKind = "statements" | "market" | "reference";

export const MARKET_TEMPLATES = ["market_prices.v1", "corporate_actions.v1"] as const;
export const REFERENCE_TEMPLATES = [
  "damodaran_implied_erp.v1",
  "damodaran_country_risk.v1",
  "damodaran_industry_betas.v1",
  "damodaran_rating_spreads.v1",
] as const;
export const REFERENCE_REGIONS = [
  "us",
  "europe",
  "japan",
  "aus_nz_canada",
  "emerging",
  "china",
  "india",
  "global",
] as const;
export const REFERENCE_VARIANTS = ["large_cap", "small_cap"] as const;

/** Every record status any of the three validators can emit. */
export const RECORD_STATUSES = [
  "accepted",
  "duplicate",
  "conflict",
  "conflicting",
  "unmapped",
  "ambiguous",
  "rejected",
] as const;

export interface PreviewIssue {
  level: "error" | "warning" | string;
  code: string;
  message: string;
  column?: string | null;
}

export interface PreviewRecord {
  row: number;
  locator: string;
  status: string;
  issues: PreviewIssue[];
  // statements
  key?: string;
  line_item?: string | null;
  period?: string;
  value?: string;
  normalized_value?: string | null;
  unit?: string | null;
  // market
  listing?: string | null;
  date?: string | null;
  // reference
  record_key?: string | null;
  storable?: boolean;
}

export interface PreviewReport {
  template: string;
  filename: string;
  file_sha256: string;
  bytes: number;
  file_errors: string[];
  file_warnings?: string[];
  summary: Record<string, number>;
  records: PreviewRecord[];
  // statements
  issuer?: { company_id: string; name: string | null } | null;
  metadata?: Record<string, unknown> | null;
  mapping_version?: string | null;
  periods?: {
    label: string;
    period_type: string;
    fiscal_year: number;
    start: string;
    end: string;
    source_document: string;
  }[];
  sec_compared?: boolean;
  // reference
  lineage?: { kind: string; edition_date: string; region: string | null; variant: string | null };
  outcome?: string;
  current_version?: Record<string, unknown> | null;
  removed_keys?: string[];
  content_sha256?: string | null;
}

/** Query parameters of a preview, by kind (the file itself is the raw body). */
export interface PreviewParams {
  kind: ImportKind;
  filename: string;
  template?: string;
  edition_date?: string;
  region?: string;
  variant?: string;
}

/** Ingestion arguments beyond the preview's (pending P15 for every role). */
export interface IngestParams extends PreviewParams {
  expect_sha256: string;
  source: string;
  source_reference?: string;
  allow_partial?: boolean;
  allow_conflicts?: boolean;
  supersede?: boolean;
  reason?: string;
}
