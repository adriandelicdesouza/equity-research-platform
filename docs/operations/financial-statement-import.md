# Financial-statement file import (CSV)

Decision D57; Blueprint §4.6 (Excel/CSV import), §4.2 (`csv_import`), §3.3 (`source.imports`, `fin.financial_values.import_id`), §10.1 (`manual_import` provenance), §10.3 (conflicts).

This imports **historical financial statements** for an issuer that already exists in the platform (SEC-resolved or registered with `listing-register`, D56). Each ingested file becomes an append-only source record and feeds the company's **`reported` dataset**, which follows the same draft → verify → lock lifecycle as SEC datasets. Prices and corporate actions use the separate market-data import (`market-data-import.md`).

## The template: `financial_statements.v1`

One CSV grid, UTF-8, comma-delimited. A blank template is in `templates/financial/financial_statements.v1.csv`.

```csv
item,FY2023,FY2024,source_page
template,financial_statements.v1
issuer_ticker,ACME
issuer_mic,XTSE
currency,CAD
scale,thousands
share_scale,thousands
accounting_standard,IFRS
source_document,Annual report 2024
period_type,FY,FY
fiscal_year,2023,2024
period_start,2023-01-01,2024-01-01
period_end,2023-12-31,2024-12-31
IS.REVENUE,<value>,<value>,45
BS.TOTAL_ASSETS,<value>,<value>,47
```

- **Header row:** the first cell is `item`. Every other column is a **period column** whose label is free text (used in messages), except an optional column named `source_page` (page of the source document, per line-item row).
- **Metadata rows** (`key,value`, nothing else on the row). All are required except `mapping_version`:

  | Key | Meaning |
  |---|---|
  | `template` | `financial_statements.v1` |
  | `issuer_ticker`, `issuer_mic` | an **existing** listing (exactly one). Imports never create issuers or listings. |
  | `currency` | ISO 4217 code. Must equal the company's declared **reporting currency**; nothing is converted or inferred. |
  | `scale` | `units`, `thousands`, `millions` or `billions`: applies to monetary amounts |
  | `share_scale` | the same choices: applies to share counts. Per-share amounts are never scaled. |
  | `accounting_standard` | `US_GAAP`, `IFRS` or `OTHER`. A mismatch with a *declared* company standard is an error; with an *inferred* one, a warning. |
  | `source_document` | the document the figures come from |
  | `mapping_version` | optional; default: the platform's current mapping (`v2`) |

- **Period rows**, one cell per period column: `period_type` (`FY`, `Q1`–`Q4`, `H1`, `9M`), `fiscal_year`, `period_start`, `period_end` (ISO dates); optional `period_source_document` overrides the document per column (e.g. FY2023 from the 2023 report).
- **Line-item rows:** the first cell is a **standard line-item code** (e.g. `IS.REVENUE`; the full list is in `mappings/v2/line_items.yaml` and the template) or the item's **exact standard label** (case-insensitive, e.g. `Cost of revenue`). A label match is reported as a warning so it can be reviewed. Empty cells mean "not reported".
- **Values:** plain decimals only, with an optional leading `-` and `.`. Thousands separators, parentheses, currency symbols and exponents are rejected. Enter values **in the platform's sign convention**:
  - costs and expenses positive;
  - cash outflows negative (e.g. capex);
  - income, assets, liabilities and equity positive.

  A negative expense is a warning.
- **Balances** (`BS.*`) belong in the `FY` or `Q1`–`Q3` column ending on the balance-sheet date, never in `H1`, `9M` or `Q4` columns.
- **Fiscal periods** must agree with the company's fiscal calendar:
  - the fiscal year is labelled by the calendar year in which it ends (a 52/53-week year ending in the first 7 days of January belongs to the previous year);
  - quarters and year-to-date periods must fall inside a fiscal year that is in the file or already stored;
  - a fiscal year overlapping a stored, different fiscal year is refused (calendar changes are not supported);
  - no period may end after today.
- **Not importable:** derived-only items (`CF.FCF`). EBITDA, gross profit and other subtotals *may* be imported as reported; the integrity checks then compare them with their components.

## Workflow

The CLI runs in the worker: `docker exec -i erp-dev-worker-1 python -m erp_backend.cli <command>`.

1. **Validate and preview** (reads only, writes nothing):
   ```bash
   … statement-import-validate --file - --filename acme_fy2024.csv < acme_fy2024.csv
   ```
   The report shows the resolved issuer, the metadata, each period, and every record with its status and reasons, then prints the file's SHA-256. Use `--json` for the full report and `--all` for every issue. The exit code is 1 when the file has file-level errors.
2. **Ingest explicitly**, naming the reviewed SHA-256 and the data's origin:
   ```bash
   … statement-import --file - --filename acme_fy2024.csv \
     --source "Analyst transcription of the 2024 annual report" \
     --source-reference "issuer website PDF, retrieved 2026-11-07" \
     --expect-sha256 <sha256 from step 1> < acme_fy2024.csv
   ```
   Ingestion re-validates the same bytes under a lock in one transaction, and is **refused with nothing written** when:
   - the SHA-256 differs;
   - the file has file-level errors;
   - any record would be skipped (`unmapped`, `ambiguous` or `rejected`) without `--allow-partial`;
   - any record is `conflicting` without `--allow-conflicts`.
3. **Review the dataset.** Ingestion builds the company's next draft `reported` dataset (`--no-build` skips it; `dataset-build --basis reported --ticker …` builds it later). Then:
   - `dataset-list --ticker ACME`
   - `conflict-list --id <dataset>`
   - `conflict-resolve --id <dataset> --key <key> (--select ID [--candidate-source import_value|reported_fact] | --value V --source S | --override | --revoke) --reason …`
   - `dataset-verify --id <dataset>`
   - `dataset-lock --id <dataset> --reason …`
4. **History:**
   - `statement-import-list [--ticker T --mic M]`
   - `statement-import-show --id <import> [--values]`: metadata, counts, skipped records with reasons, stored values with their source location, and the datasets built from them.

## Record statuses

| Status | Meaning | Stored? |
|---|---|---|
| `accepted` | new value | yes |
| `conflicting` | new, but differs from a value already imported for the same item and period, or from the SEC latest-reported value for the same window; materiality is shown | yes, as a second source (needs `--allow-conflicts`); opens a conflict in the dataset |
| `duplicate` | identical to a stored import, or repeats an earlier row of the file | no |
| `unmapped` | the row key is neither a standard code nor a standard label | no (needs `--allow-partial`) |
| `ambiguous` | a label naming several items, or one item and period twice in the file with different values (neither is used) | no (needs `--allow-partial`) |
| `rejected` | invalid value or placement (number format, derived-only item, balance in a YTD column, future period, negative share count) | no (needs `--allow-partial`) |

Re-ingesting the same file stores nothing ("nothing new to store") and creates no import row: duplicates make imports idempotent.

## What ingestion stores

- **The exact file bytes** in the content-addressed raw archive (`manual_import/<aa>/<sha256>.zst`), verified against the SHA-256 whenever read.
- **One `source.imports` row** (append-only): template, file name, SHA-256, archive path, size, source and reference, status (`complete` / `partial`), the validation counts, issuer, metadata and periods, and every skipped record with its reasons.
- **One `source.statement_import_values` row per stored cell** (append-only):
  - line item;
  - fiscal period;
  - `reported_value` exactly as written, the scale exponent, and the normalized `value` (a CHECK enforces `value = reported_value × 10^scale`);
  - unit and currency;
  - source document and page;
  - the cell's location (`line N, column 'FY2024'`).
- **An audit event** `financial_statements_imported`, plus the dataset events (`dataset_created`, `conflicts_detected`, and `dataset_discarded` when an open draft is superseded).

## The `reported` dataset

- **Basis `reported`**, its own version line per company: one open draft, locked versions immutable, parent = latest locked `reported` version. The SEC `standardized` datasets are separate and never changed by an import.
- **Values** carry `provenance_kind = manual_import` with `import_id` + `import_value_id`, from which the file, row and source page can be traced.
  - When several imports supply one item and period, the dataset's selection policy decides: `latest_reported` (default) or `first_reported`. The other records remain listed on the value.
- **Conflicts** (§10.3, same materiality rule and resolution workflow as SEC data):
  - `cross_import`: two imports disagree;
  - `cross_source`: an SEC value exists for the same window and differs; the SEC fact is a candidate.

  Immaterial conflicts are auto-resolved (the policy value stays) and stay visible. **Open material conflicts block locking.**
- **Derivations and integrity checks** are the existing ones (D51): Q4 / quarters from YTD, subtotal formulas, LTM, BALANCE, SUBTOTAL, cash roll-forward, EPS, completeness. Failures are visible and never block viewing.
- **Reproducibility:** `import_cutoff_id` (the last import value used), `resolution_cutoff_id` and `facts_as_of` are stored.
  - `facts_as_of` is the SEC comparison cut-off. Without SEC data, it is the date of the latest import.
  - `dataset-verify` rebuilds from the stored records and reproduces the bytes.

## Not supported yet

- **XLSX:** needs an approved spreadsheet dependency (P14). Export the sheet as UTF-8 CSV.
- **Segment, operating-metric and stub-period imports;** fiscal-year-end changes.
- **Corrections:** a wrong import is superseded by importing the corrected value (it becomes a conflict to resolve). Stored records are never edited or deleted.
- **SEC datasets:** a standardized (SEC) dataset is not compared with imports. Cross-source conflicts live in the `reported` dataset.
