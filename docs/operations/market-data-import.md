# Market-data file import (CSV)

Decision D55; Blueprint §4.2 (`csv_import`), §4.6, §3.3 (`source.imports`), §10.1 (`manual_import` provenance); D16 (manual price import as the Yahoo fallback).

This imports **daily prices and corporate actions for existing listings**. Financial-statement import (the §4.6 line-item template that creates a `reported` dataset) is a separate workflow: `financial-statement-import.md` (D57).

## Workflow

1. **Declare the listing's trading currency** (once per listing; audited; never inferred from a file):
   ```bash
   docker exec erp-dev-worker-1 python -m erp_backend.cli listing-set-currency \
     --ticker AAPL --mic XNAS --currency USD --reason "Nasdaq listing trades in USD"
   ```
   An already-declared currency is never changed by this command.
2. **Validate and preview.** This only reads the database and writes nothing:
   ```bash
   docker exec -i erp-dev-worker-1 python -m erp_backend.cli market-import-validate \
     --template market_prices.v1 --file - --filename aapl_prices.csv < aapl_prices.csv
   ```
   The report lists every record as `accepted`, `duplicate`, `conflict` or `rejected`, with reasons, and prints the file's SHA-256. Use `--json` for the full machine-readable report and `--all` to list every issue. The exit code is 1 only when the file as a whole is unreadable or has an invalid header.
3. **Ingest explicitly**, naming the SHA-256 you reviewed and the source of the data:
   ```bash
   docker exec -i erp-dev-worker-1 python -m erp_backend.cli market-import \
     --template market_prices.v1 --file - --filename aapl_prices.csv \
     --source "Exchange historical data export" --source-reference "downloaded 2026-11-07" \
     --expect-sha256 <sha256 from step 2> < aapl_prices.csv
   ```
   Ingestion re-validates the same bytes in one transaction and is **refused, with nothing written**, when:
   - the SHA-256 differs, so the file changed since review;
   - the file has file-level errors;
   - any record would be skipped (`rejected` or `conflict`) and `--allow-partial` is not given.

   With `--allow-partial`, only `accepted` records are stored, and every skipped record and its reasons are kept on the import record.
4. **History:** `market-import-list` lists ingested files.

`--file PATH` also works for a file inside the container. The worker mounts only the raw archive, so `--file -` (stdin) is the normal route.

## What ingestion stores

- **The exact file bytes**, in the content-addressed raw archive (`manual_import/<aa>/<sha256>.zst`), verified against the SHA-256 whenever they are read.
- **One `source.imports` row** (append-only) with:
  - template version, original file name, SHA-256, size and archive path;
  - source name and reference;
  - status (`complete` / `partial`) and the validation counts;
  - the skipped records;
  - the importing user (when known) and the timestamp.
- **Accepted records**, written to `market.price_observations` or `market.corporate_actions` with `provider_code = manual_import`, `import_id` and `import_row` (the 1-based data-row number). `fetch_id` stays empty: each observation has exactly one origin, enforced by a CHECK constraint.
- **An audit event**, `market_data_imported`.

A file in which every record is already stored creates no new import row ("nothing new to store").

## Format (both templates)

- **Encoding and layout:** UTF-8 (a BOM is tolerated), comma-delimited, `"` quoting. The first non-empty line is the header, and blank lines are ignored. The maximum size is 20 MB.
- **Header:**
  - names are case-insensitive, and surrounding spaces are ignored;
  - columns may be in any order, and optional columns may be omitted;
  - unknown, duplicate or empty column names reject the whole file. This is what catches a typo such as `adjclose`.
- **Listing:** `ticker` + `exchange_mic` (ISO 10383, e.g. `XNAS`, `XNYS`, `XTSE`, `XTSX`).
  - Both are required, and a ticker alone is never accepted.
  - Matching is case-insensitive.
  - The pair must match **exactly one** existing listing. Unknown or ambiguous pairs are rejected, and imports never create listings.
- **Dates:**
  - format `YYYY-MM-DD` only, as the trading date on the exchange's own calendar;
  - future dates are rejected;
  - weekend dates are accepted with a warning.
- **Numbers:** plain decimals: digits with an optional `.` and fraction. Thousands separators, exponents, currency symbols, `NaN` and `inf` are all rejected.
  - Prices, ratios and amounts must be > 0.
  - Volume must be a whole number ≥ 0.
  - Values are stored exactly as `NUMERIC`, never as binary floats.
- **Currencies:** ISO 4217 alphabetic codes in major units, upper-case exactly as written (`usd` is rejected rather than corrected). Minor-unit quotation codes (`GBX`, `ZAC`, `ILA`) are rejected. **Nothing is converted.**

### `market_prices.v1` — `templates/market/market_prices.v1.csv`

| Column | Required | Rule |
|---|---|---|
| `ticker`, `exchange_mic` | yes | identify one listing |
| `date` | yes | trading date |
| `open`, `high`, `low` | no | > 0. `high` ≥ `low`, otherwise the record is rejected. A close outside [low, high] is a warning |
| `close` | yes | > 0, the close **as stated by `close_basis`**. Never filled from `adj_close` |
| `close_basis` | yes | `raw` (as traded) or `split_adjusted` (restated for later splits, not dividends) |
| `adj_close` | no | > 0, stored separately from `close` |
| `adj_close_basis` | with `adj_close` | `split_and_dividend_adjusted`, or `unverified` when the source does not document its method. The column must be present whenever `adj_close` is, and the value must be given if and only if `adj_close` is |
| `volume` | no | whole number ≥ 0 |
| `currency` | yes | must equal the listing's declared trading currency |

**Basis consistency:** one `close_basis`, and one `adj_close_basis`, per listing per file. A file that mixes them for a listing has all of that listing's records rejected (`mixed_price_basis`). Raw and split-adjusted series of the same listing are separate series: import them in separate files, and read them with `current_prices(..., close_basis=...)`.

### `corporate_actions.v1` — `templates/market/corporate_actions.v1.csv`

| Column | Required | Rule |
|---|---|---|
| `ticker`, `exchange_mic` | yes | identify one listing |
| `ex_date` | yes | ex-date |
| `kind` | yes | `split` or `cash_dividend` |
| `ratio_numerator`, `ratio_denominator` | for `split` | new shares per old shares: 4-for-1 is `4,1`, and a 1-for-10 reverse split is `1,10`. Both > 0 and not equal. Must be empty for dividends |
| `amount` | for `cash_dividend` | per share, > 0. Must be empty for splits |
| `currency` | for `cash_dividend` | ISO code as declared. A currency different from the listing's trading currency is stored as declared, with a warning, and is never converted. Must be empty for splits |

## Duplicates and conflicts

- **Price key:** listing + date + `close_basis`. **Action key:** listing + `kind` + ex-date.
- **Within the file:** an identical repeat is a `duplicate`, and only the first occurrence is stored. The same key with different values makes every such record a `conflict`.
- **Against stored `manual_import` observations:**
  - an identical stored record makes the new one a `duplicate`, and nothing is written;
  - a different stored record makes it a `conflict`. **Imports never revise stored observations.** The stored value stays, and the conflicting record is reported and skipped.
- **Against other providers** (e.g. a future `yahoo_chart` series with the same close basis): a close more than 0.5% away is a **warning only** (§4.5). Different sources coexist, and nothing is overwritten.
- **Comparison is numeric**, so `10.2` equals `10.20`. The database's unique indexes (migration `0010`) are the final idempotency guard.

## Adding XLSX later

`erp_backend/market/tabular.py` is the format boundary. Everything downstream works on a `RawTable` of cell text. An XLSX reader must read one named sheet and must:

- render dates as ISO text and numbers as the exact decimal text, never a float rendering;
- refuse formulas rather than evaluate them;
- use `"<sheet>!<row>"` as the locator;
- register itself in `READERS`.

It needs a spreadsheet library, and so needs **owner approval** first. Until then, `.xlsx` files are rejected with a message telling the user to export to CSV.
