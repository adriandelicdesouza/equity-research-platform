# DCF Methodology Specification (Release 1)

**Status:** **APPROVED** by the owner on 2026-11-06 (Session 2), with the §15 resolutions (Q1 amends D27; Q2 sets the terminal-form default). This document is authoritative for R1 DCF calculations.
**Implements:** `TECHNICAL_BLUEPRINT.md` Revision 2 (approved) §6.6, §7, §8.2, §13.1, and decisions D18–D24, D26, D27 (as amended), D35.
**Role:** this document is the **specification** that both the engine (`erp_engine`) and the independent reference (`engine/tests/reference/`) are written from (Blueprint §13.1.1). Where it is more specific than the blueprint, it adds detail. Owner resolutions made during review are recorded in §15.

---

## 1. Notation and global conventions

| Symbol | Meaning |
|---|---|
| `B` | Balance-sheet date: the date of the balance sheet used for net debt, cash, NWC and shares (Blueprint §7.4) |
| `V` | Valuation date (default: date of the market price used); `V ≥ B` |
| `FYEₖ` | End of forecast fiscal year *k* (k = 1 … N) |
| `t` | Time in years from `B`, day count **Actual/365.25** |
| `Rev, EBIT, D&A, Capex, NWC` | Forecast operating lines (currency, period flows; NWC is a stock) |
| `τ` | Cash tax rate |
| `WACCₖ` | Discount rate applying during period *k* (constant WACC is the special case) |
| `Ke, Kd` | Cost of equity, pre-tax cost of debt |
| `g` | Perpetual growth rate in the terminal period |

**Numerics** (Blueprint §6.3, D9):
- The engine uses `Decimal`, precision 34, `ROUND_HALF_EVEN`. Nothing is rounded in stored outputs; rounding happens only at presentation.
- Non-integer powers use `Decimal` `exp`/`ln` at the same precision, so they're deterministic.

**Representation:**
- **Rates** are stored as decimals (0.10 = 10%). Basis points appear only in the UI.
- **Signs:** cash inflows to capital providers are positive. Capex is entered and stored as a positive *spend* and subtracted in formulas. A rise in NWC (ΔNWC > 0) is a cash outflow.
- **Currency:** every input and output carries currency and scale. Mixed currencies within one run are a validation **error**: Rf, cash flows and market price must be in the same currency, or explicitly converted at a dated FX rate.

---

## 2. Operating forecast (Blueprint §7.1)

For forecast year *k* (k = 1 … N, N ∈ [1, 20]), with year 0 = the last historical fiscal year:

```
Revₖ    = Revₖ₋₁ × (1 + growthₖ)
EBITₖ   = Revₖ × EBIT_marginₖ            (or (EBITDA_marginₖ − D&A%ₖ) × Revₖ)
D&Aₖ    = Revₖ × D&A%ₖ
Capexₖ  = Revₖ × Capex%ₖ
NWCₖ    = Revₖ × NWC%ₖ                    (or days-based: DSO/DIO/DPO)
ΔNWCₖ   = NWCₖ − NWCₖ₋₁,   NWC₀ = actual NWC at B
Taxesₖ  = τₖ × max(EBITₖ, 0)              (R1: no tax credit on negative EBIT; no NOLs)
```

- Every driver is a **materialized per-year value** after scenario resolution. Flat, fade and explicit-path inputs all resolve to explicit years before the engine runs.
- Each driver carries provenance and an `approved` status. A `suggested` driver can't enter a saved run (Blueprint §10.1).
- **SBC:** the default `expense` treatment leaves SBC inside EBIT as a cash-equivalent cost. The `add_back` treatment adds SBC back to FCFF and *requires* an explicit future-dilution assumption (§10.3, safeguard 2).

## 3. FCFF (Blueprint §7.2)

```
NOPATₖ = EBITₖ − Taxesₖ
FCFFₖ  = NOPATₖ + D&Aₖ − Capexₖ − ΔNWCₖ
```

FCFF excludes interest income on cash; cash is added in the bridge (§9). Lease adjustments to EBIT, D&A and FCFF are in §8.

## 4. FCFE (Blueprint §7.3, D19)

```
InterestExpₖ = Kd × Debtₖ₋₁                      (opening-balance convention)
InterestIncₖ = r_cash × Cashₖ₋₁                  (opening-balance convention)
PTIₖ         = EBITₖ − InterestExpₖ + InterestIncₖ
NIₖ          = PTIₖ − τₖ × max(PTIₖ, 0) − PrefDivₖ − NCI_shareₖ
NetBorrowingₖ= Debtₖ − Debtₖ₋₁
FCFEₖ        = NIₖ + D&Aₖ − Capexₖ − ΔNWCₖ + NetBorrowingₖ
```

- **Debt path:** constant, target D/E, or explicit per year.
- **Cash path:** constant by default. Any planned distribution or drawdown of excess cash has to be modelled explicitly in the cash path.
- **FCFE produces `EQUITY_VALUE` directly** (§12). It **never** passes through the bridge: cash, non-operating assets, debt, preferred and NCI are not added or subtracted afterwards.
- **Per-share value:** still goes through `dilution/` (§10).

## 5. Timeline, discounting and stub periods (Blueprint §7.4, D20)

### 5.1 Discount times

| Case | Period *k* cash-flow time `tₖ` |
|---|---|
| No stub (`B` = a fiscal year-end), end-of-period | `tₖ = k` |
| No stub, mid-period | `tₖ = k − ½` |
| Stub, end-of-period | `t₁ = t_stub`; `tₖ = t_stub + (k − 1)` for k ≥ 2 |
| Stub, mid-period | `t₁ = t_stub / 2`; `tₖ = t_stub + (k − 1) − ½` for k ≥ 2 |

Here `t_stub = days(B → FYE₁) / 365.25`.

### 5.2 Discount factor (per-period WACC supported)

```
DF(t) = 1 / Π over periods i of (1 + WACCᵢ)^(Δtᵢ ∩ [0, t])
```

With a constant WACC this reduces to `DF(t) = (1 + WACC)^(−t)`.

When the rate changes between periods, compounding is **cumulative**: the rate of period *i* applies only to the portion of `[0, t]` that falls within period *i*. A mid-period flow in period *k* is discounted with periods 1 … k−1 at their own rates, plus half (or the stub fraction) of period *k* at `WACCₖ`.

### 5.3 Stub period — the two modes (`B` is an interim date)

**Mode B — Actual partial-period (default when interim data exists)**

For each flow component X ∈ {EBIT, D&A, Capex}:

```
X_stub    = X_FY1(forecast, full year) − X_YTD(actual, FY start → B)
Taxes_stub = τ₁ × max(EBIT_stub, 0)       (rate-based; YTD actual tax is not subtracted, see note)
ΔNWC_stub = NWC_FYE1(forecast) − NWC_B(actual interim balance sheet)
FCFF_stub = (EBIT_stub − Taxes_stub) + D&A_stub − Capex_stub − ΔNWC_stub
```

- **Note on taxes:** stub taxes use the forecast rate on stub EBIT, not FY tax minus YTD actual tax. YTD actual tax often reflects discrete items and timing.
- **FCFE in Mode B:** the same rule applies, with YTD actual interest and net borrowing.

**Mode A — Prorated annual (fallback)**

```
f = days(B → FYE₁) / days(FY₁)            (calendar days in fiscal year 1: 365 or 366; 52/53-week years use their actual length)
FCFF_stub = f × FCFF_FY1                  (every component, including ΔNWC, scaled by f)
```

- **Limitations:**
  - It ignores seasonality and intra-year working-capital timing.
  - It treats the full-year forecast as accruing evenly over the year.
  - It ignores where `B` sits in the seasonal NWC cycle.
- **Warnings:**
  - Using Mode A with an interim `B` always raises one.
  - If Mode A is used because interim data is missing, the warning says so.

**No interim data:** there is **no stub**. `B` = the last fiscal year-end, the forecast runs in full years from there, and the value is rolled forward to `V`.

Mode B checks:
- **Implied remaining-year revenue:** a warning if `Rev_FY1 − Rev_YTD ≤ 0`, or if it's implausibly low relative to the YTD run-rate (below 25% of the prorated amount).
- **Fiscal-year match:** YTD actuals and `FY1` must be the same fiscal year (error).
- **Units:** interim and annual data must share currency and scale (error).

### 5.4 Roll-forward from `B` to `V`

```
Equity_V = Equity_B × (1 + Ke)^((V − B)/365.25) − Dividends(B, V]
Dividends(B, V] = Σ DPS(ex-date ∈ (B, V]) × basic shares at the base date (§10.1)
```

- **Applies to both methods:** used for FCFF (after the bridge, at the equity level) and for FCFE.
- **Staleness:** a warning if `V − B > 183 days`.
- **Trace:** records the gap in days, the factor and the dividends.
- **Ordering:** the roll-forward happens *before* the per-share conversion, so dilution is evaluated at `V`.

## 6. Terminal value (Blueprint §7.6, D27)

### 6.1 Forms

| Form | Formula |
|---|---|
| Gordon — value driver (**default**, D35) | `TV = NOPAT_N × (1 + g) × (1 − g / RONIC) / (WACC_N − g)` |
| Gordon — simple growth (selectable) | `TV = FCFF_N × (1 + g) / (WACC_N − g)` |
| Exit multiple | `TV = multiple × Metric_N`, where Metric ∈ {EBITDA, EBIT, Revenue} of final forecast year N (trailing basis) |

- **FCFE equivalents:** the simple form uses `FCFE_N × (1+g)/(Ke − g)`. The value-driver form uses `NI_N × (1+g) × (1 − g/ROE_new)/(Ke − g)`.
- **Debt in the FCFE terminal:** debt is assumed to grow at `g`.
- **Default form (D35):** value driver.
  - `RONIC` (or `ROE_new` for FCFE) is a **required analyst assumption**.
  - Its system suggestion is `RONIC = WACC_N` (`ROE_new = Ke`), meaning new investment neither creates nor destroys value. Under that suggestion `TV = NOPAT_N × (1+g) / WACC_N`.
  - As with every suggestion, it can't enter a saved run until approved (§2).
  - The simple-growth form stays selectable per run, and the choice is recorded.

### 6.2 Timing (D27, amended 2026-11-06)

- **Gordon TV follows the cash-flow convention.**
  - End-of-period convention: discounted at `t_N` (`N`, or `t_stub + N − 1`).
  - Mid-period convention: discounted at `t_N − ½`.
  - Why: the perpetuity's flows arrive mid-period too, so its value at `t_N` is `TV × (1+WACC_N)^½`.
- **Override:** the analyst can force end-of-period timing for a conservative view. The override is recorded and shown on outputs.
- **Exit-multiple TV:** always discounted at the end of the final period. It represents a transaction value at that date.

### 6.3 Implied counterparts (always shown when inputs exist)

```
Implied exit multiple (from Gordon)  = TV_Gordon / Metric_N
Implied perpetual growth (from exit) : g* such that FCFF_N(1+g*)/(WACC_N − g*) = TV_exit
                                       ⇒ g* = (TV_exit × WACC_N − FCFF_N) / (TV_exit + FCFF_N)
Implied RONIC (simple Gordon form)   : RONIC* = g / (1 − FCFF_{N+1}/NOPAT_{N+1})  (shown as n/a if reinvestment ≤ 0)
```

### 6.4 Coherence checks

| Check | Severity |
|---|---|
| `g ≥ WACC_N` (or `g ≥ Ke` for FCFE) | **Error**: calculation refused; the sensitivity cell shows "n/a — g ≥ WACC" |
| `g > Rf` | Warning |
| `g < 0` | Info (permitted) |
| PV(TV) > 85% of EV | Warning |
| Terminal FCFF ≤ 0 | Warning |
| Implied RONIC < WACC with g > 0 | Warning (growth destroys value) |
| Reinvestment rate `1 − FCFF_{N+1}/NOPAT_{N+1}` inconsistent with `g / RONIC` (abs diff > 5 pp) | Warning |
| Negative equity value | Shown, never hidden |

## 7. Cost of capital (Blueprint §7.5, D21)

### 7.1 Formulas

```
Ke   = Rf + βL × MatureERP + CRP_term + SpecificPremium
WACC = E/V × Ke + D/V × Kd × (1 − τ_marginal) + P/V × Kp      (V = D + E + P at market values)
```

- **Rf:** default-free and in the cash-flow currency. USD → UST 10Y; CAD → GoC 10Y.
- **Country default risk:** if `includes_default_spread` is set on the Rf source, CRP is refused unless the default spread has been removed.

### 7.2 ERP and CRP

- **ERP basis:** exactly one is chosen.
  - `mature_plus_crp` (default): MatureERP plus a separate CRP.
  - `total_country_erp`: in this mode, a non-zero CRP is an **error**.
- **CRP exposure:** exactly one method is used.
  - `lambda`: `CRP_term = λ × CRP`.
  - `beta`: `CRP_term = βL × CRP`.
  - `flat`: `CRP_term = CRP`.
  - Two active methods is an error.
- **Operating-exposure weighting:** `CRP = Σ wⱼ × CRPⱼ`, with `Σ wⱼ = 1` (tolerance 1e-9, else an error).
- **Defaults:** CRP defaults to 0 for US/Canada. A non-zero value there needs a reason.
- **SpecificPremium:** defaults to 0 and needs a rationale. It's displayed as its own line.

### 7.3 Beta

**Regression beta** — OLS of simple total returns, raw (not excess):

```
β̂ = Cov(R_s, R_m) / Var(R_m)
SE(β̂) = sqrt( Σε² / (n − 2) / Σ(R_m − R̄_m)² )
95% CI = β̂ ± t(0.975, n−2) × SE
```

- **Sampling:**
  - Weekly over 2 years (n ≥ 80), or monthly over 5 years (n ≥ 48).
  - Uses the last trading day of each week or month, with common dates only and no filling.
- **Warnings:** if SE > 0.25 or R² < 0.10.
- **Blume adjustment:** `β_adj = 0.67 × β_raw + 0.33`.
- **Leverage:**
  1. The regression beta is first unlevered at the window's average market D/E, using `D/E_avg = mean over sampling dates of (Debt_book / MarketCap)` with debt from the latest balance sheet at or before each date.
  2. It is then relevered at the target D/E.
- **Relevering** (the same formula is used both ways within a run):
  - Hamada (default): `βL = βU × [1 + (1 − τ) × D/E]`
  - Harris–Pringle: `βL = βU × (1 + D/E)`
- **Bottom-up beta:** Damodaran industry βU (region and edition recorded; cash-corrected where available), relevered at the target D/E.

### 7.4 Kd and weights

- **Kd:** one of
  - Rf + spread;
  - an analyst-entered YTM;
  - a synthetic rating (interest coverage → Damodaran spread table, edition recorded).
- **After-tax Kd:** uses the marginal τ.
- **Weights:**
  - E = price at `V` × diluted shares (§10).
  - D = book value (labelled as a proxy) or analyst-entered market value, or a target structure.
  - Lease liabilities are included in D only under `as_debt` (§8).

**Circularity note:** with `E` from the market price (default), WACC doesn't depend on the implied value, so there's no circularity. Using a target structure also avoids it. Using the *implied* equity value in the weights is not offered in R1.

## 8. Leases (Blueprint §7.7.1, D22)

One explicit setting, `lease_treatment`, adjusts every affected element consistently:

| Element | `as_debt` | `as_operating` |
|---|---|---|
| EBIT used for NOPAT | IFRS 16: as reported. ASC 842 operating leases: add back operating lease cost, subtract ROU-equivalent depreciation (lease-adjusted EBIT) | IFRS 16: reported EBIT − lease interest − lease principal + ROU depreciation (i.e. after full lease cash cost). ASC 842: as reported |
| D&A | Includes ROU depreciation | IFRS 16: excludes ROU depreciation |
| Capex | Excludes ROU additions | Excludes ROU additions (lease cost already in EBIT) |
| Bridge | Subtract all lease liabilities | IFRS 16: none. ASC 842: finance lease liabilities only |
| WACC | Lease liabilities in D; lease rate in Kd | Leases excluded from D (finance leases under ASC 842 included as debt) |
| EBITDA for exit multiple | Pre-lease-cost basis (`lease_basis = pre_lease`) | Post-lease-cost basis (`lease_basis = post_lease`) |

- **Required inputs:** missing inputs for the chosen treatment block the run, naming the missing items.
- **ROU additions:** counting them in both capex and lease cost is an error.
- **Exit multiples:** must carry a `lease_basis` equal to the subject's EBITDA basis, or the run fails with an error.

## 9. Equity bridge — FCFF only (Blueprint §7.7, D18)

```
EV_B = Σ PV(FCFFₖ) + PV(TV)
Equity_B = EV_B
         + cash & equivalents + short-term investments      (excl. restricted / client / segregated cash)
         + non-operating assets                              (analyst-entered, sourced)
         − total debt                                        (excl. convertibles treated as if-converted)
         − lease liabilities per §8
         − preferred equity                                  (excl. convertible preferred treated as if-converted)
         − non-controlling interests
         − other claims                                      (analyst-entered, after tax where applicable)
Equity_V = roll-forward (§5.4)
```

- **Where values come from:** all bridge values are taken at `B`.
- **Restriction:** the bridge accepts only `ENTERPRISE_VALUE` inputs. Passing it an `EQUITY_VALUE` is a type error.

## 10. Diluted shares (Blueprint §7.7.2, D23)

### 10.1 Base

- **Source:** point-in-time basic shares outstanding from `fin.share_counts`, plus dated, sourced buybacks or issuance between the base date and `V`.
- **Weighted-average counts:** using one (basic or diluted) as the base is an **error**.

### 10.2 Instrument register and treatments

Each instrument appears exactly once, with exactly one treatment.

- **Options/warrants:** TSM (default) or excluded.
- **RSU/PSU/DSU:** shares added, or excluded with a reason.
- **Convertible debt or convertible preferred:** `claim` (in the bridge) or `if_converted` (in the share count, removed from the bridge). The default is whichever is worth more to the holder at the implied price, recorded in the trace.
- **Exchangeable shares:** in basic or as NCI/claim, never both.

**TSM closed form at the implied price** (for the set *I* of in-the-money tranches, nᵢ shares at strike Kᵢ):

```
P = (Equity_V + Σ_{i∈I} nᵢ Kᵢ) / (S + Σ_{i∈I} nᵢ)
```

The formula follows from `P = Equity_V / (S + Σ nᵢ(1 − Kᵢ/P))`.

Fixed-point iteration:
1. Start with `I₀ = {i : Kᵢ < Equity_V / S}`.
2. Compute P.
3. Set `I₁ = {i : Kᵢ < P}`.
4. Repeat until `I` is stable. This terminates in at most as many steps as there are tranches.
5. The result must satisfy `Kᵢ < P` for every i ∈ I and `Kᵢ ≥ P` for every i ∉ I.

If-converted instruments are decided *before* TSM. Their shares are added to `S`, and their claim is removed from the bridge.

### 10.3 Safeguards (errors unless stated)

1. **One place only:** an instrument id can't appear both in the bridge claims and in the share count.
2. **SBC consistency:** `expense` combined with modelled future dilution is a double count; `add_back` without modelled dilution is an omission. Each raises a warning, and saving is blocked without an override reason.
3. **Reported-count sanity check:** a *warning* if implied diluted shares differ from the latest reported diluted weighted-average count by more than 5%.
4. **Dated adjustments:** share adjustments must be dated after the base date and sourced.

## 11. Implied multiples and outputs (Blueprint §7.8)

- **Implied multiples:**
  - EV-based: `EV_V / Metric` on LTM and NTM bases, using the run's lease basis for EBITDA.
  - `P/E = implied price / diluted EPS`.
- **Outputs:**
  - typed outputs (§12);
  - TV and its share of EV;
  - the forecast table with `tₖ`, `DFₖ` and PVs;
  - the WACC build-up;
  - implied multiples;
  - a validation report;
  - the trace.

## 12. Output kinds (Blueprint §6.6, D18)

| Methodology | Produces | Conversions applied by orchestrator |
|---|---|---|
| DCF — FCFF | `ENTERPRISE_VALUE` | bridge → roll-forward → dilution |
| DCF — FCFE | `EQUITY_VALUE` | roll-forward → dilution (**no bridge**) |

Every output records `kind`, value, currency, `V`, methodology, basis (`minority, marketable`) and trace id. Reconciliation (R4) compares methods only at one kind, by default `PER_SHARE_VALUE`.

## 13. Financial-business policy (Blueprint §7.6.1, D24)

The confirmed classification gates the run:
- **`bank`, `insurer`, `specialty_lender`, `investment_holding`:** FCFF and FCFE refused (error, no override in R1).
- **`reit`:** FCFF allowed, with a warning and a check that fair-value gains are excluded.
- **`asset_manager_broker`:** FCFF allowed, with a warning and a check that client cash is excluded from the bridge.
- **`needs_review`:** blocks saving until the classification is confirmed.

## 14. Benchmarks (hand-worked, independent of code)

**How the numbers were produced:**
- Every intermediate value below was computed in exact rational arithmetic. Fractional powers used 50-digit decimal arithmetic.
- Python `fractions`/`decimal` served purely as a calculator in a scratch session. No engine code exists, and none was used.
- Values are shown to 4–6 decimals, or 10–12 for discount factors.
- **Test tolerance:** `1e-9` relative on final values. The fixtures store the exact rational where one exists.

### B1 — FCFF, end-of-period, no stub, constant WACC, Gordon (simple form, selected explicitly)

- **Inputs:**
  - `Rev₀ = 1,000`; growth 10%, 8%, 6% (N = 3).
  - EBIT margin 20%, τ 25%.
  - D&A 5%, Capex 7%, NWC 10% of revenue; `NWC₀ = 100`.
  - WACC 10%, g 2%.
- **Bridge:** cash 150, debt 400; no leases, preferred, NCI or dilution.
- **Shares and dates:** basic shares 100; `B = V` = last FYE.

| k | Rev | EBIT | Taxes | NOPAT | D&A | Capex | NWC | ΔNWC | FCFF | DF = 1.1⁻ᵏ | PV |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1,100.0000 | 220.0000 | 55.0000 | 165.0000 | 55.0000 | 77.0000 | 110.0000 | 10.0000 | 133.0000 | 0.9090909091 | 120.909091 |
| 2 | 1,188.0000 | 237.6000 | 59.4000 | 178.2000 | 59.4000 | 83.1600 | 118.8000 | 8.8000 | 145.6400 | 0.8264462810 | 120.363636 |
| 3 | 1,259.2800 | 251.8560 | 62.9640 | 188.8920 | 62.9640 | 88.1496 | 125.9280 | 7.1280 | 156.5784 | 0.7513148009 | 117.639669 |

| Step | Value |
|---|---|
| Σ PV(FCFF) | 358.912397 |
| FCFF₄ = 156.5784 × 1.02 | 159.709968 |
| TV = 159.709968 / (0.10 − 0.02) | 1,996.374600 |
| PV(TV) = TV × 0.7513148009 | 1,499.905785 |
| **EV** | **1,858.818182** |
| TV share of EV | 80.6914% (below the 85% warning threshold) |
| Equity = EV + 150 − 400 | 1,608.818182 |
| **Implied price** = Equity / 100 | **16.088182** |
| Implied exit EV/EBITDA = TV / EBITDA₃ (314.8200) | 6.341321× |

**B1-VD — the same inputs with the default value-driver form and the suggested RONIC = WACC = 10%:**

| Step | Value |
|---|---|
| NOPAT₄ = 188.8920 × 1.02 | 192.669840 |
| TV = 192.669840 × (1 − 0.02/0.10) / 0.08 = NOPAT₄ / WACC | 1,926.698400 |
| PV(TV) = TV / 1.331 | 1,447.557025 |
| EV = 358.912397 + 1,447.557025 | 1,806.469421 |
| Equity = EV + 150 − 400 | 1,556.469421 |
| **Implied price** | **15.564694** |
| TV share of EV | 80.1318% |

The value-driver TV is lower than the simple form (1,926.70 vs 1,996.37). The simple form carries forward year 3's reinvestment rate (17.1%), which isn't consistent with 2% growth at RONIC = WACC (implied rate 20%).

Expected validation (B1, simple form):
- No errors.
- The reinvestment-consistency check (§6.4) has no RONIC input to compare against, because the simple Gordon form takes none. It reports the implied RONIC only.

### B2 — FCFF, mid-period, interim balance sheet (stub Mode B), roll-forward, Gordon simple form selected explicitly

- **Base:** the same operating forecast as B1, with FY = calendar year and FY₁ = FY2025.
- **Dates:** `B` = 2025-06-30, `V` = 2025-09-30.
- **H1 2025 actuals (YTD):**
  - Revenue 520, EBIT 98, D&A 26, Capex 30.
  - Interim NWC at B = 125 (seasonal build; the FY forecast NWC₁ is 110).
- **Bridge at B:** cash 120, debt 400.
- **Roll-forward:** Ke 12%; dividend 0.50/share with ex-date 2025-08-15.
- **Other:** basic shares 100; WACC 10%, g 2%.

Stub (Mode B):

| Item | FY₁ forecast | YTD actual | Stub |
|---|---|---|---|
| EBIT | 220.0000 | 98.0000 | 122.0000 |
| Taxes (25% × stub EBIT) | | | 30.5000 |
| NOPAT | | | 91.5000 |
| D&A | 55.0000 | 26.0000 | 29.0000 |
| Capex | 77.0000 | 30.0000 | 47.0000 |
| ΔNWC = 110 − 125 | | | −15.0000 |
| **FCFF_stub** | | | **88.5000** |

Mode A comparison: `f = 184/365 = 0.5041095890`, `FCFF_stub,A = 0.5041095890 × 133 = 67.046575`. Prorating misses the seasonal H2 cash release, understating the stub by 21.45.

Discounting: `t_stub = 184/365.25 = 0.5037645448` (2025-06-30 → 2025-12-31 is 184 days).

| k | Flow | t (mid-period) | DF = 1.1⁻ᵗ | PV |
|---|---|---|---|---|
| 1 (stub) | 88.5000 | 0.2518822724 | 0.976278929796 | 86.400685 |
| 2 | 145.6400 | 1.0037645448 | 0.908764786287 | 132.352503 |
| 3 | 156.5784 | 2.0037645448 | 0.826149805716 | 129.357215 |

| Step | Value |
|---|---|
| Σ PV(flows) | 348.110404 |
| TV (as B1) | 1,996.374600 |
| TV time = t_N − ½ = (t_stub + 2) − ½ (D27 amended: follows the mid-period convention) | 2.0037645448 |
| DF | 0.826149805716 |
| PV(TV) | 1,649.304488 |
| **EV_B** | **1,997.414891** |
| TV share of EV | 82.5720% |
| Equity_B = EV_B + 120 − 400 | 1,717.414891 |
| Roll factor = 1.12^(92/365.25) | 1.028956813707 |
| Dividends (B, V] = 0.50 × 100 | 50.0000 |
| Equity_V = 1,717.414891 × 1.028956813707 − 50 | 1,717.145754 |
| **Implied price** | **17.171458** |
| *Conservative override only* (TV at end of final period, t = 2.5037645448, DF 0.787702932862) | PV(TV) 1,572.550128 → EV_B 1,920.660531 → Equity_V 1,638.168832 → price 16.381688 |

### B3 — FCFE with a changing capital structure, end-of-period, Gordon simple form selected explicitly

- **Base:** the same operating forecast as B1.
- **Debt path:** Debt₀ = 400 → 450 → 500 → 510; Kd 6% on the opening balance.
- **Cash:** constant at 150, earning 3%.
- **Other inputs:** τ 25%; Ke 12%; g 2% (debt grows at g in the terminal period).
- **Shares:** basic 100; `B = V`.

| k | EBIT | Int. exp | Int. inc | PTI | Taxes | NI | D&A | Capex | ΔNWC | Net borr. | FCFE | DF = 1.12⁻ᵏ | PV |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 220.0000 | 24.0000 | 4.5000 | 200.5000 | 50.1250 | 150.3750 | 55.0000 | 77.0000 | 10.0000 | 50.0000 | 168.3750 | 0.8928571429 | 150.334821 |
| 2 | 237.6000 | 27.0000 | 4.5000 | 215.1000 | 53.7750 | 161.3250 | 59.4000 | 83.1600 | 8.8000 | 50.0000 | 178.7650 | 0.7971938776 | 142.510364 |
| 3 | 251.8560 | 30.0000 | 4.5000 | 226.3560 | 56.5890 | 169.7670 | 62.9640 | 88.1496 | 7.1280 | 10.0000 | 147.4534 | 0.7117802478 | 104.954418 |

| Step | Value |
|---|---|
| Σ PV(FCFE) | 397.799603 |
| FCFE₄ = 147.4534 × 1.02 | 150.402468 |
| TV_equity = 150.402468 / (0.12 − 0.02) | 1,504.024680 |
| PV(TV_equity) | 1,070.535059 |
| **Equity value** (no bridge: cash 150 and debt are **not** added or subtracted) | **1,468.334662** |
| **Implied price** | **14.683347** |

**Test assertion:** the test must fail if any bridge item is applied to this output (§4, D19).

### B4 — Treasury-stock method fixed point

- **Inputs:** Equity_V = 2,000; basic shares S = 100.
- **Tranche T1:** 10 options at K = 15. **Tranche T2:** 5 options at K = 30.

| Step | Set I | P | Check |
|---|---|---|---|
| Start: `K < E/S = 20` | {T1} | (2,000 + 150) / 110 = 19.5454545455 | T1: 15 < P ✓; T2: 30 ≥ P ✓ → stable |
| (Shown for contrast) all tranches | {T1, T2} | 2,300 / 115 = 20.0000 | T2: 30 ≥ 20, so T2 can't be in I |

- **Diluted shares:** 100 + 10 × (1 − 15/19.5454545455) = 102.3255813953.
- **Consistency:** E / diluted shares = 19.5454545455 = P ✓.

## 15. Owner resolutions (2026-11-06)

**Q1. Gordon TV timing under the mid-period convention.** Resolved: Gordon TV timing **follows the cash-flow convention** (mid-period TV when mid-period discounting is on). End-of-period remains available as a recorded conservative override. Exit-multiple TV is always end-of-period. This **amends D27**. Effect in B2: EV_B 1,997.41 instead of 1,920.66.

**Q2. Default terminal FCF form.** Resolved: **value driver is the default (D35)**. `RONIC` (`ROE_new` for FCFE) is a required analyst assumption, suggested `= WACC_N` (`= Ke`) until approved. The simple-growth form stays selectable per run. B1, B2 and B3 select the simple form explicitly so that they test its arithmetic. B1-VD tests the default.

## 16. Traceability to the blueprint

| Section here | Blueprint | Decisions |
|---|---|---|
| §2–3 | §7.1–7.2 | — |
| §4 | §7.3 | D19 |
| §5 | §7.4 | D20 |
| §6 | §7.6 | D27 (amended), D35 |
| §7 | §7.5 | D21 |
| §8 | §7.7.1 | D22 |
| §9 | §7.7 | D18 |
| §10 | §7.7.2 | D23 |
| §12 | §6.6 | D18 |
| §13 | §7.6.1 | D24 |
| §14 | §13.1, §13.1.1 | D26 |
