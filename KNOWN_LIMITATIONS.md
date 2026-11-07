# Known Limitations

**Status:** Revision 2 — architecture APPROVED 2026-11-06; pre-implementation at approval. Maintained through every release.
**Related:** `TECHNICAL_BLUEPRINT.md`, `DECISIONS.md`.

Legend: **[V]** verified by live probe on 2026-11-06 · **[A]** assessment, not yet verified.

---

## 1. Data coverage

| Area | Limitation |
|---|---|
| US companies | **[V]** SEC XBRL companyfacts covers ~15+ years of annual data (Apple FY2007–FY2026). Pre-2009 filings have no XBRL. Smaller filers began XBRL later (~2011). |
| Canadian interlisted, IFRS | **[V]** SEC accepts IFRS XBRL only from about FY2017/18 (RY, CNQ). That's about 8 years of automated history, not 15. Longer history needs import. |
| Canadian interlisted, US GAAP | **[V]** Covered (CNI from 2009, Shopify from 2015). |
| Canadian quarterly (40-F filers) | **[V/A]** Quarterly reports are furnished on 6-K. XBRL there is **not uniform**. It was present for RY and CNI and is assumed absent elsewhere until detected. 6-K data may be unaudited. |
| TSX/TSXV-only companies | **[A]** No free, official, structured fundamentals source. SEDAR+ has no public API **[V: JS application]**, and Canada has no mandatory XBRL. Financials come only from Excel/CSV import (R1), or from deterministic PDF extraction later (R3). |
| TSX ticker discovery | Not automated. Yahoo search was **not tested**. Tickers and exchanges are entered manually. Only symbol *validation* uses the verified chart endpoint. |
| Consensus estimates, forward multiples | Not available free. Analyst inputs only. |
| Credit ratings, bond yields | Not available free in structured form. Spreads come from Damodaran's synthetic-rating table, or are entered by the analyst. |
| Precedent transactions | No free database. Manual entry (R4). |
| Total-return indices | **[A]** Free total-return indices for S&P 500 and S&P/TSX via the price source are unverified. Regression beta uses price indices unless a TR series is imported. |
| Segment data | Mostly in XBRL dimensions and inconsistent. Not in R1 (R2/R3). |
| XBRL quality | Company-specific extension concepts, changes of concept over time (Apple revenue used 3 concepts), and missing items are common. Some line items will need manual mapping or entry. |
| Lease detail | ASC 842 operating lease cost and IFRS 16 lease cash flows are tagged inconsistently. Some lease treatments will need manual inputs. |
| Share counts | Cover-page shares outstanding are point-in-time but sometimes split by class or missing. They may need analyst entry. |
| Non-North-American markets | Architecture supports them. No automated provider in R1. |

## 2. Provider dependencies

| Provider | Status | Risk | Fallback |
|---|---|---|---|
| SEC EDGAR | Official, free, 10 req/s | Requires a contact User-Agent **[V: 403 without it]**. Format changes are rare. | Raw payloads archived; parsers re-runnable |
| Yahoo chart endpoint | **Unofficial**, undocumented | Can break, throttle or change terms without notice. Terms of use may not permit automated use. | Manual CSV price import; adapter swappable |
| Stooq | **Excluded** | **[V]** Now serves a JavaScript proof-of-work page | — |
| US Treasury yield CSV | Official | URL/format changes | FRED |
| Bank of Canada Valet | Official | Series renames | Manual entry |
| FRED (no key) | Official | **[V]** First request timed out, retry succeeded | Treasury.gov |
| Damodaran datasets | Free academic reference | Annual editions, page/format changes, no API | Values archived with edition date; analyst entry |
| Cloudflare Tunnel / Access | Existing infrastructure | Outage blocks remote access only | LAN/Tailscale HTTPS path |

## 3. Methodological limitations (Release 1)

- **Interest:**
  - Calculated on opening balances, so cash/debt circularity is deliberately avoided.
  - Interest on average balances needs the R2 circularity solver.
- **Stub Mode A (prorated):**
  - Ignores seasonality and working-capital timing, and assumes even accrual within the year.
  - Used only as a fallback, with a warning.
  - Without interim data, the model values at the last fiscal year-end and rolls forward. Results become stale as the gap grows (warning above 183 days).
- **Roll-forward:** compounds at the cost of equity and deducts dividends paid. It ignores other intra-gap capital transactions unless they're entered.
- **FCFE:**
  - Cash contributes only through modelled interest income and distributions. Excess cash must be modelled explicitly.
  - FCFF and FCFE results will differ unless the capital structure assumptions are consistent.
- **WACC:**
  - A single constant WACC by default. A per-period vector is supported but entered manually.
  - Book debt is a proxy for market value unless the analyst enters a market value.
- **Beta:**
  - Regression beta uses price indices (see above).
  - Daily frequency isn't offered.
  - Thin-trading corrections (Dimson) aren't in R1.
  - Damodaran publishes no Canada-specific industry betas.
- **Relevering:** Hamada and Harris–Pringle assume zero debt beta. That can misstate risk for highly levered companies.
- **Taxes:** no NOL modelling in R1. Negative EBIT gives no tax credit, which is conservative and documented.
- **Financial businesses:**
  - Banks, insurers, specialty lenders and investment holdings can't be valued in R1 (FCFF/FCFE blocked).
  - REITs and asset managers get warnings only. NAV and FFO models arrive in R4.
  - Classification relies on SIC and XBRL heuristics and must be confirmed by the analyst.
- **Dilution:**
  - Instruments must be entered from filings. Option tranche detail isn't reliably in XBRL.
  - The 5% reconciliation check catches gross errors, not small ones.
- **Leases:** `as_operating` for IFRS 16 reporters needs lease cash payment data that is often only in notes.
- **Monte Carlo, reverse DCF, comps and other methods:** not in R1.
- **Numerics:** Decimal precision 34. Transcendental operations (fractional exponents) are deterministic but not exact. They're checked against the `mpmath` reference to 1e-10.

## 4. Infrastructure and operational risks

| Risk | Status / mitigation |
|---|---|
| Single 128 GB SSD, no RAID | Off-server backup is a production gate (D7). The destination isn't chosen yet. |
| RAM shared with game servers | Caps of about 1.6 GB. Memory figures are **estimates until measured** (M0/M5). Not yet tested with the game server running. |
| Dev and prod can't run together while the game server runs | Run one environment at a time. |
| Disk headroom: 64 GB free | Disk guard refuses bulk ingestion below 10 GB free. Docker images from other projects aren't pruned. |
| Caddy internal CA | Clients must install the root certificate. If it's not trusted, browsers warn. |
| Safari on `http://localhost` | Its handling of `Secure` cookies varies. The documented path is Chromium/Firefox, or the HTTPS listener. |
| Unofficial price source | See §2. A sudden outage stops price updates; valuations keep their dated prices. |
| Single developer | Mitigated by the reference-first rule (D26). |

## 4a. Implementation state (M0, Session 2)

- **No financial functionality yet (as of M0):** superseded by §4b.
- **No worker or job queue:** added in M2 (D32).
- **Image tags not pinned:** base images use tags, not digests. Digest pinning is scheduled for M5.
- **Rate limiter:** in-memory, so it resets when the API restarts.
- **Memory under load:** concurrent logins are the API's largest memory consumer, because each Argon2 verification takes 19 MiB. See `docs/operations/memory.md`.
- **Upstream warning:** a Starlette deprecation warning (`httpx` test client) appears in backend tests. It's harmless and will be revisited when FastAPI's test-client guidance settles.
- **Game-server co-residency:** not yet measured; deferred to M5 by the owner (2026-11-06).

## 4b. Engine state (M1, Session 3)

- **Implemented:** the FCFF/FCFE DCF engine, WACC, the bridge and dilution. It's validated against the independent reference and benchmarks B1–B4.
- **Not wired to the API or UI yet:** that comes in M3. The engine is callable from Python only.
- **FCFE with an interim balance sheet:** refused (D39). FCFF supports stubs fully.
- **Exit-multiple terminal value:** FCFF only. FCFE would need terminal net debt.
- **Stub Mode B with lease adjustments:** YTD actuals must already be on the run's lease basis. The engine notes this; it can't verify it.
- **Regression-beta CI:** uses an analytic t-quantile approximation (D38).
- **Sensitivity grids and scenarios:** M4.

## 4c. SEC adapter (M2, Session 3)

- **Filing history:** submissions ingestion reads the `filings.recent` block only. Older filing pages (`filings.files`) aren't fetched yet. Facts still cover all periods, and filings referenced by facts are recorded from the facts.
- **Coverage template:** interim coverage uses an industrial template. Banks and insurers often lack an operating-income line, so they can show `partial`/`none` even when they file interim XBRL.
- **Inferred fields:** reporting currency and accounting standard are inferred (flagged `accounting_standard_inferred`) until an analyst confirms them. Country comes from the SEC business-address code, falling back to the incorporation code when the address is absent (US states and Canadian provinces/federal only). Incorporation can differ from headquarters.
- **companyfacts limits:** no dimensional (segment) facts and no `decimals`, so restatement detection compares exact values.
- **Listings:** securities come from SEC submissions (US exchanges). TSX listings are added manually (D15). Exchanges without a known MIC keep the SEC exchange name.
- **M2 standardization steps built:** mapping (D49), versioned datasets (D50), integrity checks and derivations (D51), data conflicts (D52), classifier (D53). Remaining M2 work: market/rates providers and the Excel/CSV import.

## 4d. Standardization mapping v1 (M2)

- **Extension concepts:** SEC companyfacts holds only standard-taxonomy facts. Company-specific concepts never appear, so items a company tags with its own concepts stay missing. Example: CNQ capex has no `ifrs-full` capex fact. Such gaps need manual entry or import.
- **Items a company does not present** stay missing rather than being approximated. Examples: CNQ and RY have no operating-income line; MSFT reports S&M and G&A but no combined SG&A, and no combined cash-flow D&A; SHOP has no total-liabilities tag. Sums and differences are derivations (Blueprint §4.4 step 5), not built yet.
- **IFRS long-term debt:** `LongtermBorrowings` includes the current portion, so it is not mapped to `BS.LT_DEBT`. The non-current amount is a later derivation.
- **No-NCI fallbacks** (D49) assume no non-controlling interests when the "attributable to parent" concept is absent.
- **Banks and insurers:** the v1 taxonomy is industrial. RY maps only about half of the items (no current/non-current split, no COGS or EBIT), as expected for a financial (D24 classification blocks DCF).
- **Cover-page shares** (`dei:EntityCommonStockSharesOutstanding`) are not mapped. `BS.SHARES_OUTSTANDING` uses the balance-sheet tag; point-in-time share data comes with `fin.share_counts` (§7.7).

## 4e. Versioned datasets (M2)

- **Derived values:** see §4f. Stub periods are not built.
- **Gaps stay gaps:** a fiscal year with no reported annual window (e.g. CNI 2021 in the stored facts) is absent, not interpolated. Values on dates that are not period ends, and windows before the first known fiscal year, are counted as unassigned in the dataset summary.
- **Fiscal-year changes:** overlapping annual windows keep the better-supported one. Transition periods (10-KT) are not modelled.
- **Conflicts:** within-filing ambiguity blocks locking. Cross-provider conflict records (`fin.data_conflicts`, §10.3) and their resolution/override workflow are not built yet, so ambiguous values cannot be resolved in-app yet.
- **Cut-off granularity:** `facts_as_of` is a date (SEC filing dates carry no time). A filing ingested later with the same filing date as the cut-off would change a rebuild; verification reports this as an input change (`input_sha256`).
- **Approval:** locking checks reproducibility and ambiguity. Integrity checks are recorded (§4f) but gate nothing yet; the approval-for-valuation gate with override reasons belongs to the valuation workflow.

## 4f. Integrity checks and derived values (M2)

- **Not evaluable is common** where companies don't tag an input:
  - The net income split needs an NCI line, and most companies without NCI don't tag one.
  - SG&A's children (S&M, G&A) are often missing.
  - Apple reports no FX-effect line, so its cash roll-forward is never evaluated.

  These are reported as `not_evaluable`, never assumed to be zero.
- **Real findings the checks surface:**
  - **Stock splits (AAPL):** latest-reported EPS is restated for the 2014 7:1 and 2020 4:1 splits, while the weighted share counts from the original filings are not. EPS × shares then misses net income by the split factor.
  - **Concept definitions:** Apple's IS `DepreciationAndAmortization` is smaller than its CF D&A concept.
  - **Restricted cash:** the cash-flow statement includes it and the balance sheet line doesn't (warning).
  - **Mixed filing vintages** (MSFT FY2016–FY2017 equity, RY 2023 equity, AAPL FY2008–2009): now detected as `mixed_vintage` conflicts that block locking until resolved or overridden (D52, §4g).
  - **EPS numerator with preferred shares (RY):** EPS uses net income after preferred dividends, but `IS.NET_INCOME_COMMON` is "attributable to the parent", which includes preferred holders. EPS checks fail by the preferred dividends. A dedicated "available to common" line item needs mapping v3.

  Split-adjusting share counts needs corporate actions. The methodology and a fixture regression exist (D54), but the stored datasets are unchanged until split data is retrieved and validated.
- **Tolerances** follow Blueprint §5.3. 0.1% of total assets is generous for small income-statement items in large balance sheets; thresholds are constants in `validation.py`, not yet configurable.
- **Derived values** are not re-checked against reported totals. Quarterly flows (Q2/Q3/Q4) are consistent with their year-to-date inputs by construction.
- **Not derived:** net debt, interest-bearing debt totals, total liabilities from L&E − equity, and quarterly balance-sheet items for Q4 (it equals the FY balance sheet). LTM exists only at Q1/H1/9M ends with the same concept in all three inputs.
- **Free cash flow** is the simple CFO + capex (`CF.FCF`, mapping v2), not FCFF/FCFE (computed by the engine from forecasts).

## 4g. Data conflicts (M2)

- **Single provider:** with SEC only, conflicts come from within-filing ambiguity and mixed filing vintages. Provider-vs-provider and import-vs-provider conflicts (§10.3) start when the Yahoo provider or the Excel import supplies overlapping values.
- **Mixed-vintage scope:** detection follows the accounting identities used by the checks (balance sheet, subtotals, cash roll-forward, IS/CF net income). A restated item outside these relationships is flagged only as `restated` on the value.
- **Candidates:** first- and latest-reported only. Intermediate filings are not offered; an analyst can enter their value with a source.
- **Resolving one item may move the gap:** selecting the original vintage for equity fixes the balance sheet but can break equity against parent equity + NCI if those were restated. The checks re-run on every build, so this stays visible.
- **No UI or API yet:** resolution is CLI or service only, and actors are recorded only when a user id is passed (the CLI passes none).
- **Thresholds** are constants in `conflicts.py`, not yet configurable per installation.
- **Reporting precision** in the check tolerance (D51) is inferred from trailing zeros; perfectly round values (rare in filings) widen it.
- **Dev data:** one demonstration resolution exists for MSFT FY2016 equity, using the first-reported value because liabilities + original equity = reported assets exactly. Every other conflict is open; nothing is locked.

## 4h. Financial-business classification (M2)

- **SIC list** comes from Blueprint §7.6.1 and the standard SIC titles. It was not re-checked against the live SEC SIC page (no new network request was made).
- **XBRL evidence covers balance-sheet dominance only.** Income-statement signals (interest or premium revenue share) and asset managers/brokers (fee revenue) are not measured, so `asset_manager_broker` comes from SIC alone.
- **Thresholds** (25% / 50%) are judgement defaults, not calibrated on a broad sample; only six companies were checked (RY → bank; five industrials → non_financial).
- **Run on demand:** suggestions are created by `classify`, not automatically after ingestion. Datasets don't record the classification.
- **No confirmations exist in dev data.** All six companies are effectively `needs_review` until an analyst confirms.
- **Interim coverage** still uses the industrial template, so banks can show `partial`/`none` (§4c). A bank template keyed on the confirmed class is open.

## 4i. Market data (M2, offline preparation)

- **No live data yet:** the Yahoo adapter was built and tested only on synthetic payloads; `yahoo_chart` stays disabled. Nothing about Yahoo's field semantics is verified beyond the 2026-11-06 probe (§0.4: HTTP 200, CAD prices, exchange "TOR" for RY.TO).
- **Decisions deferred to live validation:**
  - whether Yahoo's `close` is raw or split-adjusted (run `infer_close_basis` across a known split);
  - how `adjclose` is built (compare `recompute_adjusted_close`);
  - dividend currency for cross-listed or foreign-paying securities;
  - TSX/TSXV symbol coverage, delisted symbols, and the terms of use for automated access.
- **Not wired into datasets or valuations yet:** no split-adjusted share counts in dataset builds and no EPS-check change. The Apple EPS mismatches stay open until real corporate actions are stored and checked.
- **No scheduler, CLI or listing UI** for prices yet. Provider symbols must be set on the security (D15).
- **Quotes:** the provider's latest quote is stored for reference only, never used as a valuation price.

## 4j. Market-data CSV import (M2, Session 4)

- **Prices and corporate actions only.** Financial-statement import (§4.6 line-item template → `reported` dataset) is not built.
- **CSV only.** `.xlsx` needs a spreadsheet dependency (owner approval); until then it is refused with a message to export as CSV.
- **CLI only:** no API endpoint or UI yet. The worker mounts only the raw archive, so files are passed on stdin (`--file -`). The importing user is not recorded from the CLI (`imported_by` is empty, as with other CLI actions).
- **Existing listings only:**
  - imports never create listings, so a TSX listing (e.g. `RY` on `XTSE`) must be registered first (`listing-register`, D56, §4k);
  - the six SEC listings have **no declared trading currency**, so the analyst must run `listing-set-currency` before importing (nothing was set on their behalf).
- **No revision path:** a corrected value for an already-imported date is reported as a conflict and skipped. Revising imported data (with a reason, as a new observation) is future work.
- **Corporate-action keys:** one action per listing, kind and ex-date. Two cash dividends on the same ex-date (e.g. regular + special) conflict; there is no `special_dividend` kind yet. Splits are compared by numerator and denominator as written, so `4,1` vs `8,2` is reported as a conflict.
- **Currency list:** codes are checked for ISO 4217 form and minor-unit codes, not against a reference table of active currencies (there is no `ref.currencies` yet). Prices are protected by the equality check against the listing's declared currency; dividend currencies rely on the form check.
- **Calendar:** weekend dates are warned, not rejected. Exchange holiday calendars are not checked.
- **Imported prices are not wired** into valuations or dataset builds, the same as provider prices (§4i).

## 4k. Manual listing registration (M2, Session 4)

- **CLI only**, with no API or UI. `registered_by` is empty from the CLI.
- **Exchange registry** is code-level and small (XNYS, XNAS, XASE, ARCX, XTSE, XTSX); `ref.exchanges` is not built. OTC, CBOE and non-North-American venues are refused for manual registration (the SEC path still stores what EDGAR reports).
- **No symbol validation against a provider:** Blueprint §4.3 step 4 (fetch a short history and confirm the currency and exchange) needs live Yahoo access (P9). Registered provider symbols are therefore **unverified**.
- **No edit, retire or merge** of listings or issuers. A wrong registration cannot be corrected through the application yet: the row stays, and a correction workflow, which would be another append-only record, is future work.
- **Issuer matching** compares normalized names and tickers only, so differently worded legal names, such as "Royal Bank of Canada" vs "RBC", are not caught. `--confirm-new-issuer` relies on the analyst.
- **ISIN** is checked for format and check digit only, not against an external register.
- **Dev DB:** nothing is registered. RY on XTSE is tested only on `erp_test` with synthetic prices.

## 4l. Financial-statement import (M2, Session 5)
- **CSV only.** XLSX needs an approved spreadsheet dependency (P14).
- **Not covered:**
  - segment data, operating metrics, stub periods, fiscal-year-end changes;
  - per-cell source pages (one page per line-item row, one document per period column).
- **No correction workflow.** A wrong stored import is superseded by importing the corrected value and resolving the resulting `cross_import` conflict. Stored records are never edited or deleted.
- **SEC side:** cross-source conflicts are recorded in the import (`reported`) dataset only; a `standardized` SEC dataset is not compared with imports. The SEC comparison uses latest-reported values without SEC-side conflict resolutions.
- **Verification:** if SEC facts with filing dates on or before an import dataset's `facts_as_of` are ingested after it was built, `dataset-verify` reports changed inputs (same caveat as SEC datasets). Build a new version.
- **Interim coverage** of import datasets is `partial` whenever interim columns exist, never `full`.
- **Not exercised on real data:** no statement file has been imported in dev, so only a read-only validation smoke test against stored AAPL data has run (matching SEC value → accepted).
- **Valuation:** import datasets do not feed valuations yet, like every dataset in M2.

## 4m. Rates, FX and Damodaran reference data (M2, Session 6)

- **No live data:**
  - Treasury and Bank of Canada adapters are tested on synthetic payloads only;
  - both providers stay disabled, and the first live request needs P9;
  - the formats are taken from the §0.4 probe and documentation and are not re-verified (Treasury URL parameters, Valet missing-value form, labels of FX series other than `FXUSDCAD`).
- **Coverage:**
  - Treasury par yields only: no real yields, bill rates or long-term averages;
  - GoC benchmark yields 2Y–LONG only: no policy rate, T-bills or real-return bonds;
  - FX USD/EUR/GBP against CAD only;
  - FRED fallback not built;
  - no scheduler: fetches are manual CLI actions once enabled.
- **No FX conversion or inversion** anywhere; cross rates are not derived.
- **Damodaran:**
  - CSV transcription of the published spreadsheets, not direct parsing (XLSX needs P14);
  - industry names are not mapped to the platform's industry/SIC classification;
  - regional aggregates in the CRP table are `unmapped` by design.
- **Dev DB:** `0015` applied; series registry seeded; no observations, fetches or reference datasets stored.
- **Not wired into valuations:** Rf, ERP, CRP, betas and spreads don't yet feed WACC inputs (§7.5).

## 4n. Read-only data API (M3 phase 1, Session 7)

- **Read only:** workflow actions are a separate API (§4o); the UI is described in §4p.
- **Authentication:** cookie sessions only (browser login). No API tokens for scripts, and no per-company or row-level permissions: `company:read` grants every data endpoint.
- **No dedicated read-only DB role:** writes are blocked by READ ONLY transactions and ORM guards, while the connection still uses the app role.
- **Integrity endpoint** re-hashes stored rows only. The full rebuild comparison is the CLI `dataset-verify`.
- **Pagination** is offset-based with exact totals, which is fine at the current data sizes (about 7k values per dataset). Very large tables would need keyset pagination.
- **No request-level rate limit** on data routes, beyond the page-size cap (500), the statement timeout (10 s) and the 60-period cap on statement grids.
- **Responses are not cached,** and there are no ETags.
- **Values:**
  - `adjusted` (analyst-adjusted) datasets don't exist yet, so `basis` covers `standardized` and `reported` only;
  - valuations don't consume any of these endpoints yet.
- **Dev only:** nothing is exposed publicly. Remote access still needs P3.

## 4o. Workflow actions API (M3 phase 2, Session 8)

- **Pending P15:** dataset locking and file ingestion via the API are refused for every role (`403 pending_approval`). Use the CLI until the owner assigns the permissions.
- **API ingestion** also needs the raw-archive mount on the API container. Until then it returns `503 archive_unavailable`.
- **Synchronous actions:** no job queue. Builds and verification take seconds at the current data sizes; much larger datasets would need background jobs.
- **Idempotency is opt-in** (`Idempotency-Key`). Without a key, a retried resolution or registration is a second action. Builds and imports are idempotent by content.
- **Not exposed:**
  - SEC ingestion and live provider fetches (P9; disabled providers);
  - classification *suggestion*;
  - user-facing listing edits or retirements (no domain service exists);
  - XLSX uploads (P14).
- **Action log visibility:** users see their own actions; administrators see any by id. There is no cross-user listing endpoint.
- **UI:** see §4p (M3 phase 3).

## 4p. Workflow UI (M3 phase 3, Session 9; phase 4, Session 11; rates & FX, Session 12)

- **Pending P15:** dataset locking and file ingestion are shown as unavailable for every role, administrators included. The CLI remains the route until the owner assigns `dataset:lock` / `data:import` (and, for API ingestion, mounts the raw archive in the API container).
- **Not in the UI** (the API or CLI only):
  - SEC ingestion (CLI/worker);
  - company search by listing: the listings index has no company filter control, and `GET /listings` has no name search (ticker/MIC only).
- **Market data views (phase 4):**
  - prices are a table only (no chart: ECharts is an unapproved D28 dependency) and show one provider/close basis at a time;
  - prices are not converted to other currencies, and split adjustments are not applied in the UI: the stored close basis is shown as is;
  - the provider filter is free text (the API has no provider list endpoint).
- **Rates & FX (Session 12):**
  - the dev database holds the 19 registered rate series but **no observations and no FX rates**: the adapters are disabled (P9), so the views show their empty states until the owner approves a first fetch;
  - there is no API list of stored FX pairs: the user names base and quote (the configured Bank of Canada pairs are offered as shortcuts);
  - no charts or yield-curve view (ECharts is an unapproved D28 dependency); tables only;
  - business-day gaps are not enumerated: only stored dates are listed, with explicit empty states;
  - revision marks (*in effect* / *superseded*) appear only when all of a date's revisions are on the same page;
  - fetch IDs are shown, but there is no read endpoint for provider fetch records.
- **Listing registration:** the issuer is named by company ID, an existing listing, a CIK or as new; there is no company picker inside the form (the company workspace link presets it). Provider symbols are typed as `provider=SYMBOL` lines.
- **Trading currency:** declared once; the UI offers no correction path because the backend refuses a different value (a correction would need an owner-approved workflow).
- **Reference records:** at most 50 records per page; one column per field seen on the page, so a field absent from every record of a page is not shown for that page.
- **No attribution of other users' actions:** the conflict-decision history and action records don't carry the acting user, because the read API does not return it (it is stored in `audit.audit_events` / `audit.api_actions`). Users see their own actions; administrators open others' by ID.
- **Import history is not company-scoped:** `GET /api/imports` has no company filter. The workspace lists only the imports that its reported datasets reference (`source_summary.imports`).
- **Statement grid:**
  - read-only, with at most 60 periods per request (API limit; narrow the fiscal-year range);
  - no editable cells, charts, ratios or export;
  - conflict markers read up to 500 conflicts per dataset (the most in any dev dataset is 10).
- **D28 libraries not installed** (TanStack Router, AG Grid, Tailwind/Radix, forms, ECharts): a small hash router and plain tables are used instead (D61). Editable grids and charts will need an approved dependency decision.
- **Display only:**
  - values are scaled and rounded for display by string arithmetic;
  - per-share rows are never scaled;
  - a row's unit comes from its first stored value.
- **End-to-end tests:**
  - component tests mock `fetch`;
  - the integrated check against the dev stack was scripted over HTTP, plus a headless-browser render check under the production CSP;
  - there is no browser-driver (Playwright) suite, which would be a new dev dependency.
- **Synchronous actions:** verify and builds block their dialog for a few seconds; there is no progress indicator beyond "Working…".
- **Session expiry** returns to sign-in and loses unsaved form input.

## 4q. Valuation workflow (M3 phase 5, Session 14; D62)

- **Phase 5b, not implemented:** scenarios other than the base (inheritance, bull/bear/custom, probability weights), sensitivity grids and tornado, assumption impact on value (`valuation_impact` in audit events stays empty), regression beta.
- **Methodology scope (Q9):**
  - constant WACC (no per-period vector); beta analyst-entered or bottom-up; one CRP country per scenario;
  - no stub periods: the balance-sheet date is a fiscal year-end;
  - leases: `none`, `ifrs16_as_debt`, `asc842_as_operating` (FCFF only); the other combinations need per-year lease forecasts and are refused;
  - market-value WACC weights use price × basic shares at B (not diluted) and book debt; preferred equity with market weights is refused;
  - dividends between the balance-sheet and valuation dates are not deducted in the roll-forward (warning `dividends_not_modelled`).
- **Inputs:**
  - every dataset input must exist at the balance-sheet date; missing components need a reasoned override (by design). In the dev data, Shopify's latest draft lacks debt, NCI, preferred and the point-in-time share count at FY-end, so an analyst must supply or declare them;
  - dilutive instruments are scenario assumptions (Q10); there is no company-level register (`fin.capital_instruments`) or point-in-time share-count table (`fin.share_counts`) yet;
  - no stored rate observations, prices or FX in the dev database (providers disabled, P9): the risk-free rate is entered by an analyst with a written source and date;
  - reference values and rate observations are named by id in the assumption form; there is no record picker yet.
- **Dev database:** CNI (classification confirmed, v3 locked via the CLI) has the first saved real-data run (Session 15 acceptance); other companies still need an owner lock and classification (API locking awaits P15). The Session 14 integrated check left one archived project ("Integration check — Session 13 (not an analyst valuation)", 34 assumption revisions, 0 runs); projects cannot be deleted by design.
- **Analyst-source dates in lineage:** for non-market keys (ERP, betas, cost of debt, D/E) the written source and `as_of` are stored in the input document (`source`) and on the assumption revision, but the `val.run_inputs` row and the UI provenance column show `assumption_revision:<id>` with no `as_of_date`; only the risk-free rate is promoted to `source_type analyst`. Analyst ERP/beta/cost-of-debt dates get look-ahead checks but no staleness warning (the 13-month rule covers stored reference editions).
- **CNI acceptance run caveats:** the FY2025 balance sheet is 309 days before V (`stale_balance_sheet` warning; no stub periods); dividends paid between 2025-12-31 and 2026-11-05 are not deducted (`dividends_not_modelled`); pension surplus (≈ CAD 5.36B) and equity-method investments (≈ CAD 111M) were excluded by owner decision; target D/E uses book debt as a proxy for market debt; the 26.5 % marginal tax rate is a Canadian statutory proxy, not CNI's consolidated marginal rate.
- **Identity and reproduction:** the content identity includes the scenario revision, so re-entering identical values after another change gives a new run. Exact re-execution is guaranteed on the current engine version only (old engine versions are not installed side by side); after an upgrade, reproduction lists the differences.
- **Historical context** is per fiscal year only; NWC there and in the model is receivables + inventory − payables.
- **UI:** tables only (no charts; D28 libraries unapproved); the assumption editor edits the base scenario; no Playwright suite (component tests mock `fetch`; the integrated check was scripted over HTTPS).

## 5. Unresolved items

Status as of 2026-11-06. See `DECISIONS.md` → Pending decisions.

1. ~~SEC User-Agent contact string~~: resolved (P1).
2. Off-server backup destination: deferred to M5 (P2).
3. Cloudflare hostname + Access policy: needed for remote access (P3).
4. Permission to start the game server for the co-residency test: deferred to M5 (P4).
5. Whether to bind the HTTPS listener to the LAN as well as Tailscale (P5).
6. Exact SIC ranges for classification are taken from the Blueprint and standard SIC titles; not yet re-checked against the live SEC list (§4h).
7. Whether free total-return indices are available (to verify with the Yahoo provider, M2).
8. ~~Not yet published~~: all four branches are on GitHub as of 2026-11-06 (P12). That is an off-machine copy of the code only; runtime data still has no off-server backup (P2).
9. ~~Commit author metadata contained the owner's Gmail~~: resolved by the history rewrites (D47).
10. ~~No license~~: MIT since 2026-11-06 (P10).

## 6. Outside this project

Host-level security observations made during the initial inspection were reported to the owner directly and are tracked outside this repository.
