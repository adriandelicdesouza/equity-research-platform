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
- **Not built yet:** derivations other than Q4, integrity checks, conflict records and the classifier. These are the next M2 steps. Concept mapping v1 (D49) and versioned datasets (D50) exist.

## 4d. Standardization mapping v1 (M2)

- **Extension concepts:** SEC companyfacts holds only standard-taxonomy facts. Company-specific concepts never appear, so items a company tags with its own concepts stay missing. Example: CNQ capex has no `ifrs-full` capex fact. Such gaps need manual entry or import.
- **Items a company does not present** stay missing rather than being approximated. Examples: CNQ and RY have no operating-income line; MSFT reports S&M and G&A but no combined SG&A, and no combined cash-flow D&A; SHOP has no total-liabilities tag. Sums and differences are derivations (Blueprint §4.4 step 5), not built yet.
- **IFRS long-term debt:** `LongtermBorrowings` includes the current portion, so it is not mapped to `BS.LT_DEBT`. The non-current amount is a later derivation.
- **No-NCI fallbacks** (D49) assume no non-controlling interests when the "attributable to parent" concept is absent.
- **Banks and insurers:** the v1 taxonomy is industrial. RY maps only about half of the items (no current/non-current split, no COGS or EBIT), as expected for a financial (D24 classification blocks DCF).
- **Cover-page shares** (`dei:EntityCommonStockSharesOutstanding`) are not mapped. `BS.SHARES_OUTSTANDING` uses the balance-sheet tag; point-in-time share data comes with `fin.share_counts` (§7.7).

## 4e. Versioned datasets (M2)

- **Derived values:** only Q4 = FY − 9M exists. Quarterly cash-flow amounts (10-Q cash flows are year-to-date), gross profit, EBITDA, SG&A sums, net debt and FCF are not derived yet. LTM and stub periods are not built.
- **Gaps stay gaps:** a fiscal year with no reported annual window (e.g. CNI 2021 in the stored facts) is absent, not interpolated. Values on dates that are not period ends, and windows before the first known fiscal year, are counted as unassigned in the dataset summary.
- **Fiscal-year changes:** overlapping annual windows keep the better-supported one. Transition periods (10-KT) are not modelled.
- **Conflicts:** within-filing ambiguity blocks locking. Cross-provider conflict records (`fin.data_conflicts`, §10.3) and their resolution/override workflow are not built yet, so ambiguous values cannot be resolved in-app yet.
- **Cut-off granularity:** `facts_as_of` is a date (SEC filing dates carry no time). A filing ingested later with the same filing date as the cut-off would change a rebuild; verification reports this as an input change (`input_sha256`).
- **Approval:** locking checks reproducibility and ambiguity only. The §5.3 integrity checks and the analyst-override rule are a later step.

## 5. Unresolved items

Status as of 2026-11-06. See `DECISIONS.md` → Pending decisions.

1. ~~SEC User-Agent contact string~~: resolved (P1).
2. Off-server backup destination: deferred to M5 (P2).
3. Cloudflare hostname + Access policy: needed for remote access (P3).
4. Permission to start the game server for the co-residency test: deferred to M5 (P4).
5. Whether to bind the HTTPS listener to the LAN as well as Tailscale (P5).
6. Exact SIC ranges for classification still need checking against the SEC list (M2, classifier step).
7. Whether free total-return indices are available (to verify with the Yahoo provider, M2).
8. ~~Not yet published~~: all four branches are on GitHub as of 2026-11-06 (P12). That is an off-machine copy of the code only; runtime data still has no off-server backup (P2).
9. ~~Commit author metadata contained the owner's Gmail~~: resolved by the history rewrites (D47).
10. ~~No license~~: MIT since 2026-11-06 (P10).

## 6. Outside this project

Host-level security observations made during the initial inspection were reported to the owner directly and are tracked outside this repository.
