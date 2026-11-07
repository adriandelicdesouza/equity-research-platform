# Manual listing registration

Decision D56; Blueprint §4.3 step 3; D15. This is how a listing the SEC path does not create gets added, for example a TSX or TSX Venture listing. Once registered, it can receive CSV imports (`market-data-import.md`).

## Identity model

| Concept | Where it lives |
|---|---|
| Issuer (company) | `market.companies` |
| Exchange listing | one `market.securities` row: one ticker on one exchange |
| Ticker | `securities.ticker`, upper case, unique per MIC |
| Exchange | `securities.exchange_mic` (ISO 10383) + `exchange_name` |
| Trading currency | `securities.trading_currency`, declared and never inferred |
| Instrument | `securities.security_type`, `share_class`, `isin` (one ISIN belongs to one issuer; it may be listed on several exchanges) |
| Provider symbol | `securities.provider_symbols` (`{"yahoo_chart": "RY.TO"}`) |
| Registration provenance | `market.listing_registrations` (append-only) + audit event `listing_registered` |

A cross-listed share, such as RY on XNYS and XTSE, is **two listings under one issuer**. The SEC path creates the US listing. The TSX listing is registered here with `--same-issuer-as RY:XNYS`, so the issuer is never duplicated.

## Commands

All are run with `docker exec erp-dev-worker-1 python -m erp_backend.cli …`.

```bash
# 1. Check (read-only): prints the normalized listing, issuer resolution, errors and warnings
listing-validate --ticker RY --mic XTSE --currency CAD --security-type common_equity \
  --same-issuer-as RY:XNYS --provider-symbol yahoo_chart=RY.TO \
  --source "TSX listed-company directory" --source-reference "<URL or document>" \
  --reason "Register the TSX listing of the cross-listed issuer"   # add --json for machine output

# 2. Register: same arguments; re-checked under a per-listing lock, refused on any error
listing-register  …same arguments…

# 3. Inspect
listing-list [--ticker RY]
listing-show --ticker RY --mic XTSE     # metadata, issuer, other listings, registrations, counts
```

**Issuer resolution: exactly one is required.**
- `--company-id UUID`.
- `--same-issuer-as TICKER:MIC`: an existing listing of the issuer.
- `--cik N`: the issuer's SEC CIK.
- `--new-issuer --legal-name … --country CA [--common-name …] [--reporting-currency CAD]`. If a company with the same normalized name, or any listing with the same ticker, already exists, the new issuer is refused. Resolve it with one of the options above, or pass `--confirm-new-issuer` when it really is a different issuer; the dismissed candidates are recorded as a warning on the registration.

## Rules

- **Exchanges:** registry in `backend/src/erp_backend/market/exchanges.py`.

  | MIC | Exchange | Currencies |
  |---|---|---|
  | `XNYS` | NYSE | USD |
  | `XNAS` | Nasdaq | USD |
  | `XASE` | NYSE American | USD |
  | `ARCX` | NYSE Arca | USD |
  | `XTSE` | TSX | CAD, USD |
  | `XTSX` | TSX Venture | CAD |

  Any other MIC, or a currency the exchange doesn't trade in, is refused. Extending the registry is a reviewed code change.
- **Currency:** ISO 4217, upper case as written. It is always required and never defaulted from the exchange.
- **Ticker:** 1–16 characters from `A–Z 0–9 . -`, stored upper-case.
- **Security type:** `common_equity`, `preferred_equity`, `depositary_receipt`, `fund_unit` or `other`.
- **Share class:** optional, `A–Z 0–9`.
- **ISIN:** optional, with the ISO 6166 check digit validated.
- **Provider symbols:** stored only when given as `--provider-symbol PROVIDER=SYMBOL`.
  - The provider must exist in `source.providers`; it does not need to be enabled, and nothing is fetched.
  - A symbol already used by another listing is refused.
  - For `yahoo_chart`, the D15 rule (`RY` + `XTSE` → `RY.TO`) is shown as a suggestion, and an explicit symbol that differs from it is a warning.
- **Refused registrations:**
  - a ticker + MIC that is already registered, whatever its issuer or origin;
  - the same instrument (same ISIN, or same share class and type) of an issuer already listed on that exchange under another ticker.
- **Reason:** at least 10 characters. A source is required.
- **What registration doesn't do:** it does not modify existing listings. In particular, it does not set the SEC listings' trading currency; use `listing-set-currency` for that.
