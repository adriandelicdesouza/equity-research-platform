# Rates, FX and Damodaran reference data

Decision D58; Blueprint §0.4, §3.2–§3.3, §4.2, §7.5.1–§7.5.4, §10.1.

This guide covers two separate workflows:

1. **Rates and FX from providers** (US Treasury, Bank of Canada). The adapters are built and tested on synthetic payloads only. **Both providers are disabled.** The first live request needs the owner's approval (P9).
2. **Damodaran reference datasets** loaded from CSV files. You validate a file first, then ingest it explicitly as a versioned dataset.

Nothing here feeds a valuation yet. When the WACC wiring is built (§7.5), Damodaran values will enter as `suggested` inputs that need analyst approval.

The CLI runs as `docker exec [-i] erp-dev-worker-1 python -m erp_backend.cli <command>`.

## 1. Rate and FX series

### Series identity

Each series stands for one provider series, with one rate type, currency, tenor and quotation convention. Two series with the same tenor are **not interchangeable**.

| Code | Provider series | Meaning |
|---|---|---|
| `UST_PAR_1M` … `UST_PAR_30Y` (13 tenors) | Treasury CSV columns `1 Mo` … `30 Yr` | US Treasury **par yield** (CMT), bond-equivalent basis, USD |
| `GOC_BMK_2Y`, `_3Y`, `_5Y`, `_7Y`, `_10Y`, `_LONG` | Valet `BD.CDN.<n>YR.DQ.YLD`, `BD.CDN.LONG.DQ.YLD` | Government of Canada **benchmark bond yield**, CAD (`LONG` has no fixed maturity) |

- The registry is in code, in `erp_backend/rates/model.py`. Migration `0014` seeds it into `market.rate_series`, and a test keeps the two in step.
- Blueprint §3.3 uses `UST_10Y` and `GOC_10Y` as example codes. The platform names them `UST_PAR_10Y` and `GOC_BMK_10Y` so that each code also says which rate family it belongs to.
- Run `rate-series-list` to see every registered series.

**Units:** rates are stored in **percentage points, exactly as published**: `4.15` means 4.15 %. Converting them to a fraction will happen at the valuation-input boundary.

**FX:** `market.fx_rates` stores `rate` units of `quote` currency per 1 `base`.

- `FXUSDCAD` means 1 USD = *rate* CAD. The registered FX series are `FXUSDCAD`, `FXEURCAD` and `FXGBPCAD`.
- The payload's `seriesDetail.label` must read `USD/CAD`. A contradicting label rejects the whole series, and a missing label gives a warning.
- Rates are never inverted or crossed. `fx-show --base CAD --quote USD` returns nothing when only USD→CAD is stored.

### Parsing rules

**Treasury CSV**
- `Date` must be `MM/DD/YYYY`.
- A blank cell, `N/A` or a missing column counts as *missing*. It is reported and not stored.
- An unregistered column is reported and ignored.

**Valet JSON**
- Dates must be ISO `YYYY-MM-DD`.
- An absent cell, `null` or `{"v": ""}` counts as *missing*.
- Unregistered series are reported and not stored.

**Both providers**
- A future date, an invalid date or a malformed number is rejected.
- A rate outside [-10, 50] percentage points is rejected, because it is a unit or parsing error. A rate outside [0, 20] is kept with a warning.
- Weekend dates get a warning.
- The same date twice with the same value is stored once.
- The same date twice with different values is an in-payload conflict, and **neither** value is kept.

### Storage

`market.rate_observations` and `market.fx_rates` are append-only, the same pattern as D54 prices:

- every row references the `source.provider_fetches` row whose payload was archived (SHA-256) before parsing;
- an exact repeat inserts nothing;
- a changed value is stored as a **new row** and counted as `revised`, and readers take the latest row per date.

### Commands

```bash
# Offline preview of a payload file: no database writes
rates-parse-file --provider us_treasury --file - --filename par.csv [--json] < par.csv

# Live fetch: refused while the provider is disabled (P9)
rates-fetch --provider us_treasury --year 2026
rates-fetch --provider bank_of_canada --start 2026-10-01 --end 2026-10-31 [--series FXUSDCAD ...]

# Re-parse an archived fetch: no network access
rates-ingest-archived --provider bank_of_canada --fetch-id <uuid>

rate-series-list
rates-show --series UST_PAR_10Y [--start ... --end ... --limit 30]
fx-show --base USD --quote CAD
```

## 2. Damodaran reference datasets

The published spreadsheets change layout between editions, so they are **not parsed directly**. Copy the needed columns into one of the four strict CSV templates. Blank headers are in `templates/reference/`.

| Template | Kind | Lineage parameters | Columns (required in **bold**) |
|---|---|---|---|
| `damodaran_implied_erp.v1` | `implied_erp` | edition | **as_of_date**, **index** (`SP500`), **implied_erp** %, t_bond_rate %, method |
| `damodaran_country_risk.v1` | `country_risk` | edition | **country_name**, country_iso2, region, moodys_rating, default_spread %, **country_risk_premium** %, **total_erp** %, corporate_tax_rate % |
| `damodaran_industry_betas.v1` | `industry_betas` | edition + `--region` | **industry_name**, **number_of_firms**, beta, de_ratio %, effective_tax_rate %, **unlevered_beta**, cash_firm_value %, unlevered_beta_cash_corrected |
| `damodaran_rating_spreads.v1` | `rating_spreads` | edition + `--variant` | **coverage_min**, **coverage_max**, **rating**, **spread** % |

**Format**
- CSV, UTF-8, comma-delimited, with a header row. Columns can be in any order, but unknown columns reject the file. XLSX is refused until a spreadsheet dependency is approved (P14).
- **Percentage cells need a `%` sign** (`4.33%`) and are stored in percentage points. A bare number in a percentage column is rejected, so `0.0433` and `4.33` cannot be confused. Ratio, multiple and count columns must not contain `%`.
- Numbers are plain decimals: no thousands separators or exponents. `number_of_firms` must be a whole number.

**Parameters**
- `--edition-date` is the Damodaran edition, for example the January update date. It cannot be in the future.
- `--region` is one of `us`, `europe`, `japan`, `aus_nz_canada`, `emerging`, `china`, `india` or `global`. Damodaran publishes no Canada-only table; choosing the region is the analyst's decision (§7.5.3).
- `--variant` is `large_cap` or `small_cap` (§7.5.4).

**Record identifiers and statuses.** Each kind has a record key:

| Kind | Record key | `unmapped` when |
|---|---|---|
| ERP | index + as_of_date | the index is not `SP500` |
| CRP | the ISO 3166-1 alpha-2 code | the code is missing, for example a regional aggregate such as "Western Europe", or not officially assigned |
| Betas | the industry name, whitespace-normalized | never |
| Spreads | the rating, on the S&P-style scale `AAA` … `D` | the rating is not on the scale |

Validation gives each record one of these statuses:

| Status | Meaning |
|---|---|
| `accepted` | new key |
| `duplicate` | identical to the current version, or an exact repeat within the file |
| `conflicting` | differs from the current version, or appears twice in the file with different values |
| `unmapped` | identifier outside the registry |
| `rejected` | invalid |

**Checks**

*Hard errors (the record is rejected):*
- **All kinds:** values outside a plausible range.
- **ERP:** an `as_of_date` after the edition date.
- **CRP:** a negative CRP; total ERP below the CRP.
- **Betas:** `number_of_firms` = 0.
- **Spreads:** overlapping coverage ranges; `coverage_max ≤ coverage_min`.

*Warnings (the record is kept):*
- **CRP:**
  - `total_erp − CRP` that differs from the most common implied mature ERP by more than 0.01 percentage points (a double-counting guard, §7.5.2);
  - a non-zero CRP for US or CA (§7.5.2 rule 4).
- **Betas:** an unlevered beta above the levered beta.
- **Spreads:** gaps between coverage ranges; spreads that don't fall as coverage rises.
- **All kinds:** values outside the usual range.

### Workflow

1. **Validate**. This only reads the database:
   ```bash
   docker exec -i erp-dev-worker-1 python -m erp_backend.cli reference-import-validate \
     --template damodaran_country_risk.v1 --edition-date 2026-01-05 \
     --file - --filename ctryprem_2026.csv < ctryprem_2026.csv
   ```
   The report shows:
   - the lineage;
   - the count for each status;
   - the current version, if any;
   - the record keys that would be removed;
   - the file's SHA-256;
   - the **outcome**: `new_dataset`, `identical_to_current`, `new_version_requires_supersede`, `nothing_to_store` or `file_errors`.

   Use `--json` for the full report.
2. **Ingest explicitly**, naming the reviewed SHA-256 and the source:
   ```bash
   ... reference-import --template damodaran_country_risk.v1 --edition-date 2026-01-05 \
     --file - --filename ctryprem_2026.csv --source "Damodaran Online, ctryprem.xlsx" \
     --source-reference "downloaded 2026-01-10, sheet 'Regional Weighted'" \
     --expect-sha256 <sha256> < ctryprem_2026.csv
   ```
   Ingestion is **refused, with nothing written**, when:
   - the hash differs from the reviewed one;
   - the file has file-level errors;
   - records would be skipped (`rejected`, `unmapped` or in-file `conflicting`) and `--allow-partial` is not given;
   - the lineage already has a version that this file changes and `--supersede --reason "…"` (at least 10 characters) is not given.

   Ingesting a file identical to the current version stores nothing.
3. **Inspect:**
   - `reference-dataset-list [--kind country_risk]`;
   - `reference-dataset-show <id> [--values]`: lineage, version, the version it supersedes and why, file, archive path, source, skipped records, and every value with its location in the file.

### Versioning and provenance

- **Lineage:** a lineage is the kind, edition date, region and variant. A new edition is a new lineage.
- **Corrections:** a corrected file for the same edition becomes version *n+1*. It must name the version it supersedes and give a reason. Every earlier version stays unchanged and readable.
- **Tables:** `ref.reference_datasets` and `ref.reference_values` (migration `0015`) are append-only, and database CHECKs enforce the version and lineage rules.
- **What each version records:**
  - the `source.imports` row (`provider_code = 'damodaran'`);
  - the original bytes in the raw archive (`damodaran/<aa>/<sha256>.zst`, hash-verified on read);
  - a canonical content SHA-256 that ignores row order and number formatting;
  - the audit event `reference_dataset_imported`.
- **Provider gate:** a file import makes no network request, so it runs with `damodaran` disabled, the same as `manual_import` in D55/D57 (P13).
