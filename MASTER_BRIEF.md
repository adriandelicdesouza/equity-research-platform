# MASTER DEVELOPMENT BRIEF
# Institutional Equity Research & Valuation Platform

**Project status:** Approved for development  
**Development mode:** Fully autonomous, phased implementation  
**Hosting:** Self-hosted on home server  
**Primary language:** Python  
**Frontend:** React + TypeScript  
**Backend:** FastAPI  
**Calculation architecture:** Modular Python engine with shared calculation core  
**Database:** PostgreSQL + DuckDB  
**Deployment:** Docker Compose  
**AI functionality:** Not approved; disabled  
**Branding:** Deferred  

---

# 1. PROJECT OBJECTIVE

Build a comprehensive, institutional-grade equity research and valuation platform.

The application should provide an integrated environment for researching publicly traded companies, analyzing their financial performance, constructing sophisticated financial models, performing valuations using multiple methodologies, and developing defensible investment conclusions.

The ultimate objective is to build a professional equity research application that can support the entire analytical process:

1. Identify a company.
2. Research its business and industry.
3. Retrieve and analyze historical financial information.
4. Construct integrated financial models.
5. Develop operating forecasts.
6. Establish valuation assumptions.
7. Execute multiple valuation methodologies.
8. Analyze scenarios and sensitivities.
9. Determine intrinsic value and market-implied expectations.
10. Develop an investment thesis and conclusion.
11. Generate professional research reports and investment committee materials.

This is not intended to be a basic DCF calculator, a spreadsheet application, a portfolio tracker or a generic financial dashboard.

It should become a unified equity research and valuation environment.

The application should feel like a premium institutional financial research product: sophisticated, intuitive, information-rich and visually modern.

**The quality of the financial modelling engine is more important than the number of features.**

Do not sacrifice financial accuracy, calculation transparency or auditability to accelerate development.

# 2. PRODUCT PRINCIPLES

All development must follow these principles.

### 2.1 Financial accuracy

Financial calculations must be deterministic, independently testable and reproducible.

Never fabricate financial data, financial statements, market prices or source references.

Automatically retrieved assumptions must be distinguished from verified financial information and analyst-entered assumptions.

### 2.2 Analyst control

The analyst retains control over financial assumptions, forecast methodologies, valuation parameters and investment conclusions.

No automatically generated assumption should become a verified input without explicit analyst approval.

### 2.3 Calculation transparency

Every important financial output must be traceable to its originating data, assumptions, calculation steps and calculation engine version.

### 2.4 Modularity

Build reusable components and independent financial modelling modules around a common calculation architecture.

Avoid duplicated financial logic.

### 2.5 Extensibility

The initial application should be designed to accommodate future methodologies, data providers, accounting treatments and industry-specific modelling structures.

### 2.6 Professional usability

Complex modelling should feel intuitive.

Do not require users to work directly with Python, SQL or Excel formulas to complete a valuation.

### 2.7 No unnecessary complexity

Do not build features outside the approved scope.

Prioritize a genuinely functional valuation platform over an unnecessarily complicated technology stack.

---

# 3. APPROVED TECHNOLOGY STACK

Use the following architecture unless a demonstrable technical limitation requires an alternative. Any material deviation must be documented.

| Component | Technology |
|---|---|
| Frontend | React |
| Frontend language | TypeScript |
| Backend | Python |
| API framework | FastAPI |
| Validation | Pydantic |
| ORM | SQLAlchemy |
| Database migrations | Alembic |
| Primary database | PostgreSQL |
| Analytical database | DuckDB |
| Calculation engine | Native Python |
| Deployment | Docker Compose |
| Container management | Docker |
| Source control | Git |
| Document storage | Local filesystem |
| Remote access | Cloudflare Tunnel |
| Remote authentication | Cloudflare Access |
| Charts | Modern interactive charting library |
| Testing | pytest, frontend testing framework |
| API documentation | OpenAPI |

Use established libraries where appropriate.

Do not build custom infrastructure unnecessarily when reliable, maintained libraries already exist.

---

# 4. SYSTEM ARCHITECTURE

Build a modular application composed of independently maintainable services.

### 4.1 Frontend

Responsible for:

- Application navigation.
- Company workspaces.
- Research interfaces.
- Financial statement editors.
- Forecasting interfaces.
- Assumption management.
- Valuation outputs.
- Interactive charts.
- Sensitivity matrices.
- Calculation inspection.
- Export interfaces.

### 4.2 Backend API

Responsible for:

- Application business logic.
- Authentication and authorization.
- Company management.
- Valuation project management.
- Database access.
- API validation.
- Calculation orchestration.
- Data provider coordination.
- Audit logging.

### 4.3 Calculation engine

A dedicated Python calculation service.

Responsible for:

- Financial calculations.
- Financial statement integration.
- Forecasting.
- Valuation methodologies.
- Scenario execution.
- Sensitivity analysis.
- Calculation validation.
- Dependency resolution.
- Calculation trace generation.

The calculation engine must be independent of the frontend.

### 4.4 Data ingestion service

Responsible for:

- External financial data providers.
- Historical financial statement retrieval.
- Market data retrieval.
- Currency data.
- Corporate actions.
- Data normalization.
- Source attribution.
- Data validation.
- Historical synchronization.

### 4.5 Document processing service

Responsible for:

- Document ingestion.
- Text extraction.
- PDF processing.
- Spreadsheet imports.
- Document indexing.
- Document metadata.
- Searchable research materials.
- Document version management.

AI-based document analysis is not authorized.

Use deterministic extraction and processing methods.

### 4.6 Background workers

Responsible for:

- Long-running calculations.
- Financial data synchronization.
- Document processing.
- Historical data ingestion.
- Report generation.
- Backup jobs.
- Other asynchronous operations.

### 4.7 Database services

PostgreSQL is the authoritative operational database.

DuckDB handles analytical processing and larger financial datasets.

Do not create unnecessary copies of authoritative data.

### 4.8 Internal networking

Services should communicate through an internal Docker network.

Expose only the services required for user access.

Never publicly expose PostgreSQL, internal APIs or administrative interfaces.

---

# 5. SERVER AND DEPLOYMENT ENVIRONMENT

The application will run directly on my existing home server.

Known environment:

- Small-form-factor desktop with a 4-core x86-64 CPU.
- 8 GB RAM.
- 128 GB SSD.
- Ubuntu-based Linux.
- SSH-based administration.
- Existing Docker services.
- Existing Cloudflare Tunnel infrastructure.
- Existing Tailscale infrastructure.

The server already hosts other unrelated services, including game servers.

**Do not assume this server is dedicated exclusively to the application.**

### 5.1 Resource allocation

Use balanced resource allocation.

Requirements:

- Set sensible container resource limits.
- Monitor memory, CPU and storage.
- Limit background worker concurrency.
- Prevent analytical workloads from exhausting system resources.
- Prioritize application responsiveness.
- Queue computationally intensive tasks.
- Avoid unnecessary concurrent calculations.
- Make resource limits configurable.

The initial deployment must work within existing hardware constraints.

Do not assume that all proposed services need independent heavyweight containers. Separate logical responsibilities while keeping actual resource consumption appropriate for the hardware.

### 5.2 Storage

Initially store application data on the existing home server SSD.

Use configurable storage paths.

Separate:

- Application source code.
- PostgreSQL data.
- Analytical datasets.
- Imported financial data.
- Research documents.
- Generated reports.
- Exports.
- Backups.

Support future migration to external storage, NAS or remote backup destinations.

Implement automated backups and recovery procedures.

Monitor disk usage carefully given the 128 GB SSD.

### 5.3 Cloudflare

Remote access must use authenticated Cloudflare Access through Cloudflare Tunnel.

Requirements:

- No publicly exposed application ports.
- Encrypted connections.
- Authenticated remote access.
- Application-level authorization.
- Secure session management.
- No hardcoded credentials.
- No exposure of internal services.

The application should remain usable locally when internet connectivity or Cloudflare is unavailable.

Previously configured tunnels and existing services must not be modified without a clear technical reason and approval.

### 5.4 Development and production

Maintain separate development and production configurations.

Development changes must not automatically compromise production data.

Use:

- Environment-specific configuration.
- Docker Compose profiles or equivalent isolation.
- Database migrations.
- Persistent volumes.
- Health checks.
- Automatic restart policies.
- Structured logs.
- Backup and rollback procedures.

---

# 6. APPLICATION ORGANIZATION

The company is the central organizational unit.

Each company must have a dedicated workspace.

### 6.1 Company search

Keep company discovery straightforward.

Support:

- Company name.
- Ticker symbol.
- Exchange identifier.

Do not build an advanced equity screener in the initial application.

### 6.2 Company workspace

Each workspace should contain:

**Company profile**
- Legal and common company names.
- Ticker.
- Exchange.
- Country.
- Industry.
- Sector.
- Reporting currency.
- Fiscal year-end.
- Accounting standard.
- Company identifiers.

**Financial information**
- Historical income statements.
- Historical balance sheets.
- Historical cash flow statements.
- Segment information.
- Financial ratios.
- Operating metrics.
- Market data.

**Valuation projects**
- Multiple independent valuations.
- Separate investment theses.
- Independent assumptions.
- Independent scenarios.
- Saved valuation versions.
- Historical valuation snapshots.

**Research library**
- Company filings.
- Annual reports.
- Quarterly reports.
- Earnings transcripts.
- Research notes.
- Supporting Excel files.
- Other source documents.

**Research history**
- Chronological research activity.
- Document revisions.
- Analyst adjustments.
- Valuation changes.
- Source references.

**Watchlists and tracking**
- Basic company-level research tracking.
- Company research status.
- Saved companies.

Do not expand this into portfolio holdings or portfolio management.

---

# 7. FINANCIAL DATA ARCHITECTURE

Build a provider-agnostic financial data ingestion framework.

The application must not be dependent on one financial data provider.

### 7.1 Provider architecture

Implement interchangeable provider adapters.

Each adapter must normalize external information into standardized internal financial structures.

Support:

- Free providers.
- Paid providers.
- Public filings.
- Manual imports.
- Excel imports.
- CSV imports.
- Future institutional data APIs.

Provider configuration should include:

- API credentials.
- Provider status.
- Data coverage.
- Supported markets.
- Supported data categories.
- Rate limits.
- Priority.
- Fallback behavior.

Do not purchase subscriptions or activate paid APIs without explicit approval.

### 7.2 Data source hierarchy

Support configurable source priorities.

When several providers supply the same information:

- Preserve original source data.
- Identify conflicting values.
- Record source timestamps.
- Avoid silently overwriting values.
- Allow reconciliation.
- Maintain the source selected for each valuation input.

### 7.3 Geographic coverage

Design for global equity coverage:

- United States.
- Canada.
- Europe.
- Asia-Pacific.
- Emerging markets.

Support international reporting conventions, currencies, accounting standards and market identifiers.

Initial automated coverage may vary by provider.

Manual imports must remain available for unsupported companies or markets.

### 7.4 Financial statement history

Retrieve the maximum available historical financial history.

Target 10–15+ years where available.

Support:

- Annual financial statements.
- Quarterly financial statements.
- Income statements.
- Balance sheets.
- Cash flow statements.
- Segment disclosures.
- Historical financial ratios.
- Historical operating metrics.
- Historical share counts.
- Historical debt.
- Historical dividends.
- Capital allocation information.

Preserve:

- Fiscal period.
- Publication date.
- Reporting currency.
- Accounting standard.
- Original reported values.
- Standardized values.
- Source.
- Retrieval timestamp.
- Restatement history.

Restated financial statements must not silently replace previously used historical inputs.

### 7.5 Market data

Retain daily:

- Open.
- High.
- Low.
- Close.
- Volume.
- Adjusted closing prices.
- Dividends.
- Stock splits.
- Reverse splits.
- Other relevant corporate actions.
- Historical market capitalization where available.

Preserve original unadjusted prices separately from adjusted prices.

Ensure corporate actions are handled correctly in historical return calculations.

### 7.6 Refresh frequency

Use daily automated updates.

- Market prices: daily.
- FX rates: daily.
- Financial disclosures: check daily for new filings.
- Financial statements: update when new reporting periods become available.
- Historical data: synchronize when required.

Maintain timestamps and source attribution.

New information must never silently alter historical valuation snapshots.

---

# 8. FINANCIAL STATEMENT MODELLING

Build an institutional-grade financial statement modelling framework.

### 8.1 Integrated three-statement model

Support:

- Income statement.
- Balance sheet.
- Cash flow statement.

Statements must be linked through a consistent calculation engine.

### 8.2 Supporting schedules

Include configurable schedules for:

- Revenue.
- Cost of goods sold.
- Gross profit.
- Operating expenses.
- EBITDA.
- EBIT.
- Depreciation.
- Amortization.
- PP&E.
- Intangible assets.
- Working capital.
- Accounts receivable.
- Inventory.
- Accounts payable.
- Debt.
- Interest expense.
- Taxes.
- Deferred taxes.
- Cash.
- Share capital.
- Retained earnings.
- Dividends.
- Stock-based compensation.
- Share repurchases.
- Share issuance.

### 8.3 Advanced accounting treatments

The architecture must support:

- Operating and finance leases.
- Acquisitions.
- Disposals.
- Impairments.
- Restructuring.
- Goodwill.
- Purchase price allocation.
- Complex debt structures.
- Convertible securities.
- Dilution.
- Non-controlling interests.
- Minority interests.
- Non-recurring items.
- Industry-specific accounting treatments.

### 8.4 Segment modelling

Allow companies to be modelled at the segment level.

Each segment may have independent:

- Revenue drivers.
- Growth assumptions.
- Gross margins.
- Operating margins.
- Expenses.
- Capital expenditures.
- Working capital requirements.
- Profitability forecasts.

Aggregate segment forecasts into consolidated statements.

### 8.5 Analyst adjustments

Maintain clear distinctions between:

- Reported financials.
- Standardized financials.
- Analyst adjustments.
- Adjusted financials.

Every adjustment must have a description, source and audit history.

### 8.6 Model integrity

Implement automated checks for:

- Balance sheet balancing.
- Cash flow reconciliation.
- Debt reconciliation.
- Share count reconciliation.
- Cash roll-forward.
- Retained earnings.
- Working capital.
- Cross-statement consistency.
- Circular dependencies.
- Missing assumptions.

Validation failures must be visible to the analyst.

---

# 9. FINANCIAL FORECASTING ENGINE

Build a flexible forecasting framework.

### 9.1 Forecast horizon

Support configurable forecast horizons from 1 to 20 years.

### 9.2 Forecast structures

Support:

- Annual forecasting.
- Quarterly forecasting.
- Flexible fiscal year-end dates.
- Stub periods.
- Mid-year conventions.
- End-year conventions.
- Explicit forecast periods.
- High-growth periods.
- Transition periods.
- Terminal periods.

### 9.3 Forecast drivers

Support forecasts using:

- Revenue growth.
- Unit volumes.
- Pricing.
- Customer counts.
- Product mix.
- Gross margins.
- Operating margins.
- Expense ratios.
- Capital expenditures.
- Working capital assumptions.
- Tax rates.
- Debt assumptions.
- Share count assumptions.

Allow different forecast methodologies depending on company and industry.

### 9.4 Forecast transparency

Every projected financial item must be traceable to:

- Historical financial information.
- An operating driver.
- An analyst assumption.
- A supporting schedule.
- A calculation dependency.

Forecasts must not rely on unexplained hardcoded values.

---

# 10. DCF VALUATION ENGINE

DCF is the first major development priority.

Build a complete, production-quality DCF workflow.

### 10.1 DCF methodologies

Support:

- FCFF.
- FCFE.

### 10.2 FCFF calculations

Implement a transparent calculation framework covering:

- EBIT.
- Effective tax rate.
- NOPAT.
- Depreciation and amortization.
- Capital expenditures.
- Changes in net working capital.
- Free cash flow to the firm.

### 10.3 FCFE calculations

Support:

- Net income.
- Non-cash adjustments.
- Capital expenditures.
- Changes in working capital.
- Net borrowing.
- Free cash flow to equity.

### 10.4 WACC

Build a detailed cost of capital module.

Support:

- Risk-free rate.
- Equity risk premium.
- Levered beta.
- Unlevered beta.
- Relevered beta.
- Cost of equity.
- Cost of debt.
- Credit spreads.
- Tax rates.
- Capital structure.
- Market-value debt.
- Market-value equity.
- Preferred equity where applicable.
- Country risk premiums where relevant.

Allow multiple approaches to calculating beta and cost of capital.

Every WACC input must have source attribution or be explicitly identified as an analyst assumption.

### 10.5 Terminal value

Support:

- Perpetual growth method.
- Exit multiple method.

Allow independent assumptions and sensitivities.

Validate that terminal assumptions are economically coherent.

### 10.6 Equity value bridge

Explicitly calculate:

- Enterprise value.
- Cash.
- Debt.
- Lease liabilities where applicable.
- Preferred equity.
- Minority interests.
- Other relevant claims.
- Equity value.
- Diluted shares outstanding.
- Implied share price.

### 10.7 DCF outputs

Display:

- Enterprise value.
- Equity value.
- Implied share price.
- Current market price.
- Upside/downside.
- Terminal value.
- Terminal value contribution.
- Forecast cash flows.
- WACC.
- Terminal growth rate.
- Implied valuation multiples.

### 10.8 DCF validation

Test the engine against independently calculated benchmark examples.

Include edge cases:

- Negative free cash flow.
- Negative terminal growth.
- High leverage.
- Negative net income.
- Changing capital structures.
- Unusual fiscal years.
- Different discounting conventions.
- Extreme WACC and terminal growth combinations.

---

# 11. SCENARIO AND SENSITIVITY ENGINE

Build a comprehensive scenario management system.

### 11.1 Scenarios

Support:

- Bull.
- Base.
- Bear.
- Unlimited custom scenarios.
- Nested scenarios.
- Scenario inheritance.
- Scenario combinations.
- Independent assumptions.
- Probability weighting.
- Scenario comparisons.
- Historical scenario versions.

### 11.2 Scenario controls

Analysts must be able to modify:

- Revenue growth.
- Margins.
- Capital expenditures.
- Working capital.
- Tax rates.
- WACC.
- Terminal growth.
- Exit multiples.
- Other model drivers.

### 11.3 Sensitivity analysis

Support:

- One-variable sensitivity.
- Two-variable sensitivity.
- Multi-variable sensitivity where practical.
- WACC versus terminal growth.
- WACC versus exit multiple.
- Revenue growth versus operating margin.
- Margin versus reinvestment.
- Other methodology-specific sensitivities.

Generate dynamic matrices and visualizations.

### 11.4 Advanced scenario analytics

Support Monte Carlo simulation and valuation distributions.

Allow analysts to define distributions and correlations for relevant operating and valuation assumptions.

Display:

- Expected valuation.
- Median valuation.
- Percentiles.
- Probability of upside.
- Probability of downside.
- Valuation ranges.
- Assumption distributions.

All stochastic calculations must be reproducible using saved random seeds and configuration versions.

---

# 12. REVERSE DCF

Implement a reverse DCF module.

The module should solve for operating assumptions implied by the current market price.

Support solving for:

- Revenue growth.
- Operating margins.
- Long-term profitability.
- Terminal growth.
- Other relevant operating assumptions.

Show the market-implied assumptions and compare them against:

- Historical performance.
- Analyst forecasts.
- Industry benchmarks.
- Saved scenarios.

Make the solving method and constraints inspectable.

---

# 13. COMPREHENSIVE VALUATION METHODOLOGIES

After the initial DCF and financial modelling releases, expand into the complete valuation suite.

### 13.1 Discounted cash flow

- FCFF.
- FCFE.

### 13.2 Trading comparables

Support:

- EV/Revenue.
- EV/EBITDA.
- EV/EBIT.
- P/E.
- P/B.
- P/S.
- FCF yield.
- Other relevant multiples.

Include peer selection, financial normalization and multiple distributions.

### 13.3 Precedent transactions

Support:

- Transaction selection.
- Transaction financial metrics.
- Implied multiples.
- Transaction premiums.
- Deal comparisons.
- Implied valuation ranges.

### 13.4 Sum-of-the-parts

Support independent valuation of business segments.

Aggregate segment enterprise values and reconcile to consolidated equity value.

### 13.5 Asset-based valuation

Support:

- Book value.
- Adjusted net asset value.
- Replacement value.
- Liquidation value.
- Asset-specific adjustments.

### 13.6 Dividend discount models

Support:

- Gordon growth.
- Multi-stage DDM.
- Variable dividend growth.
- Payout assumptions.

### 13.7 Residual income

Support residual income and economic profit methodologies.

### 13.8 LBO

Support:

- Entry valuation.
- Debt financing.
- Interest expense.
- Debt repayment.
- Cash generation.
- Exit valuation.
- Holding period.
- IRR.
- MOIC.
- Returns sensitivities.

### 13.9 NAV

Build NAV methodologies for applicable industries, including real estate, mining and investment companies.

### 13.10 Specialized financial institution models

Support specialized valuation structures for:

- Banks.
- Insurance companies.
- Other relevant financial institutions.

Do not force inappropriate conventional DCF assumptions onto financial institutions.

### 13.11 Valuation reconciliation

Allow analysts to:

- Compare methodologies.
- Assign weights.
- Calculate weighted valuations.
- Compare implied prices.
- Display valuation ranges.
- Explain differences between methodologies.

No methodology should be treated as universally appropriate.

---

# 14. INVESTMENT CONCLUSIONS

Every completed valuation should be capable of producing a comprehensive investment analysis.

### 14.1 Valuation summary

Include:

- Target price.
- Fair value range.
- Implied upside/downside.
- Bull, base and bear valuations.
- Probability-weighted valuation.
- Methodology breakdown.
- Key assumptions.

### 14.2 Investment conclusion

Support:

- Buy.
- Hold.
- Sell.

Include:

- Conviction assessment.
- Investment thesis.
- Key drivers.
- Principal risks.
- Catalysts.
- Expected timelines.
- Margin of safety.
- Downside protection.
- Risk/reward assessment.

Investment ratings and conclusions must remain analyst-controlled or derive from explicitly configured, transparent deterministic rules.

### 14.3 Advanced investment analytics

Include:

- Expected return distributions.
- Risk/reward attribution.
- Scenario probabilities.
- Valuation sensitivity.
- Market-implied expectations.
- Historical valuation comparisons.

### 14.4 Investment committee outputs

Generate:

- Professional research reports.
- Valuation summaries.
- Investment committee presentations.
- Supporting financial model outputs.
- Source references.
- Audit documentation.

---

# 15. RESEARCH DOCUMENT MANAGEMENT

Build a complete research library within each company workspace.

### 15.1 Supported documents

- PDF.
- Excel.
- CSV.
- Word.
- Text.
- Other commonly used research formats where practical.

### 15.2 Document capabilities

Implement:

- Document upload.
- Document categorization.
- Manual tagging.
- Search.
- Metadata extraction.
- Text extraction.
- Version history.
- Document comparison.
- Chronological research history.
- Structured analyst notes.

### 15.3 Research sources

Support:

- Annual reports.
- Quarterly reports.
- Regulatory filings.
- Earnings call transcripts.
- Investor presentations.
- Financial statements.
- Research reports.
- Analyst notes.
- Supporting datasets.

### 15.4 Source-to-model traceability

An analyst must be able to identify the original source of a financial input.

Where applicable, retain:

- Source document.
- Page number.
- Relevant section.
- Extracted value.
- Reporting period.
- Analyst adjustment.
- Date of entry.
- Source version.

Selecting a model input should expose its source and history.

### 15.5 AI restriction

No AI document summarization, automated investment research, AI-generated assumptions or autonomous research agent in the initial application.

These capabilities are reserved for a potential future release and require explicit approval.

---

# 16. CALCULATION ENGINE ARCHITECTURE

This is one of the most important technical requirements.

**Do not build an Excel clone or an Excel-like formula engine.**

All authoritative financial calculations must be implemented in native Python.

### 16.1 Shared calculation core

Build a shared engine containing:

- Standardized financial data structures.
- Typed inputs and outputs.
- Dependency management.
- Calculation validation.
- Currency handling.
- Unit handling.
- Period handling.
- Calculation metadata.
- Versioning.
- Audit logging.
- Reproducibility controls.

### 16.2 Independent calculation modules

Organize independent modules for:

- Financial statements.
- Forecasting.
- Revenue modelling.
- Operating schedules.
- Working capital.
- Debt.
- Taxes.
- WACC.
- DCF.
- Trading comparables.
- Precedent transactions.
- SOTP.
- LBO.
- DDM.
- Residual income.
- NAV.
- Sensitivities.
- Monte Carlo simulation.

### 16.3 Calculation execution

Each module must:

1. Receive validated structured inputs.
2. Resolve required dependencies.
3. Execute deterministic calculations.
4. Validate outputs.
5. Generate structured results.
6. Generate calculation trace information.
7. Record its calculation version.

### 16.4 Calculation transparency

Every important result must support inspection of:

- Inputs.
- Assumptions.
- Intermediate calculations.
- Dependencies.
- Source data.
- Calculation steps.
- Validation results.
- Engine version.

Implement dependency graphs and drill-down inspection.

### 16.5 Browser calculations

Browser-side calculations may provide immediate previews.

However:

- Python remains authoritative.
- The server recalculates before saving.
- The server validates before exporting.
- Preview calculations must not be represented as finalized valuations.

Identical saved inputs and calculation versions must produce identical results.

### 16.6 Formula interface restriction

Do not implement:

- Excel formula syntax.
- User-authored spreadsheet formulas.
- Spreadsheet scripting.
- A generic cell formula engine.

The interface should expose structured financial inputs, assumptions, schedules and calculated outputs.

Calculation steps should be inspectable through a dedicated interface.

---

# 17. AUDITABILITY AND VERSION CONTROL

Implement full valuation auditability.

Every valuation must preserve:

- Original imported financial data.
- Source references.
- Historical financial inputs.
- Standardized financial inputs.
- Analyst adjustments.
- Assumptions.
- Manual overrides.
- User attribution.
- Timestamps.
- Scenario configurations.
- Sensitivity configurations.
- Calculation engine version.
- Calculation outputs.
- Historical valuation snapshots.

### 17.1 Historical reproduction

It must be possible to reproduce a previous valuation exactly.

This requires retaining the relevant:

- Input data versions.
- Assumptions.
- Scenario configuration.
- Calculation module versions.
- Calculation engine version.
- Calculation settings.

Do not rely on the latest financial data when reproducing an older valuation.

### 17.2 Audit interface

Allow analysts to inspect changes over time.

Display:

- Previous value.
- New value.
- User.
- Timestamp.
- Reason or description.
- Impact on valuation where calculable.

Audit records must not be silently modified or deleted through normal application functionality.

---

# 18. FRONTEND AND USER EXPERIENCE

Build the application using React and TypeScript.

The interface should resemble a premium institutional investment research platform.

It should be modern, clean and information-rich.

Avoid traditional Bloomberg terminal aesthetics and generic SaaS dashboard templates.

### 18.1 Visual direction

- Modern light and dark themes.
- Sophisticated typography.
- Restrained professional colours.
- Consistent spacing.
- Clean layouts.
- High-quality financial charts.
- Interactive data tables.
- Subtle transitions.
- Consistent navigation.

### 18.2 Navigation

Create an intuitive application structure.

Suggested main navigation:

- Home.
- Companies.
- Research.
- Valuations.
- Documents.
- Settings.

Company-specific navigation should expose:

- Overview.
- Financials.
- Modelling.
- Forecasts.
- Valuation.
- Scenarios.
- Research library.
- History.

### 18.3 Modelling interface

Use a hybrid financial modelling interface.

Include:

- Spreadsheet-style financial statement editors.
- Structured forecasting tables.
- Interactive assumption panels.
- Linked financial schedules.
- Formula/calculation inspection.
- Dynamic charts.
- Sensitivity matrices.
- Detailed valuation outputs.

This is a presentation and interaction model, not an Excel calculation engine.

### 18.4 Usability

Prioritize:

- Clear navigation.
- Fast interactions.
- Logical workflows.
- Contextual explanations.
- Keyboard accessibility.
- Searchable information.
- Consistent controls.
- Useful error messages.
- Clear validation indicators.

Complex financial concepts should be presented clearly without removing analytical detail.

---

# 19. AUTHENTICATION AND USER MANAGEMENT

Build a multi-user architecture from the beginning.

Initially, the application will be operated by one administrator.

Support future additional users.

### Roles

**Administrator**
- Full system access.
- User management.
- Provider configuration.
- System configuration.
- Data management.

**Analyst**
- Create and edit research.
- Create valuations.
- Modify assumptions.
- Manage documents.
- Generate reports.

**Read-only reviewer**
- View research.
- Inspect financial models.
- Inspect calculations.
- Review valuation outputs.

Implement granular permissions where practical.

Every material change must have user attribution.

Cloudflare Access is the remote access layer, not a substitute for application-level authorization.

---

# 20. AI FUNCTIONALITY — NOT APPROVED

AI functionality is explicitly excluded from the initial implementation.

Do not implement:

- AI chatbots.
- AI research assistants.
- AI-generated investment theses.
- AI-generated financial assumptions.
- AI valuation recommendations.
- Autonomous research agents.
- AI document analysis.
- AI-driven financial model modifications.

The architecture may reserve integration points for future AI functionality.

If approved in the future, the intended scope is an integrated autonomous research agent capable of conducting end-to-end equity research, building valuations, identifying risks and presenting recommendations.

Even then, analyst approval must remain mandatory at critical stages.

For now, all functionality must operate without AI.

---

# 21. DATA EXPORT AND IMPORT

Support Excel-compatible data exchange without relying on Excel for calculations.

### Imports

- Excel financial statements.
- CSV financial statements.
- Manual financial inputs.
- Historical market datasets.
- Supporting research datasets.

### Exports

- Excel-compatible financial data.
- Financial statement tables.
- Forecast tables.
- DCF outputs.
- Valuation sensitivities.
- Scenario comparisons.
- Research reports.
- PDF presentations.
- Investment committee materials.

Exports must use authoritative server-calculated outputs.

Maintain source attribution and calculation version information in relevant exports.

---

# 22. DEVELOPMENT PRIORITIES

Development must follow this exact strategic priority.

## RELEASE 1 — COMPLETE DCF VALUATION

This is the first priority.

Build a fully functional DCF valuation workflow.

Include:

- Company identification and basic setup.
- Historical financial input support.
- Historical financial statement structures.
- Editable financial inputs.
- Revenue and operating forecasts.
- Forecast assumptions.
- Supporting calculations required by DCF.
- WACC.
- FCFF.
- FCFE.
- Terminal value.
- Equity value bridge.
- Diluted shares.
- Implied share price.
- Bull, base and bear scenarios.
- Custom scenarios.
- Sensitivity analysis.
- Valuation outputs.
- Calculation transparency.
- Saved valuations.
- Basic audit history.
- Excel-compatible imports and exports.

The first release must be genuinely usable.

It must not be a collection of disconnected mockup pages.

## RELEASE 2 — ADVANCED FINANCIAL MODELLING

Expand into the complete institutional-grade modelling framework.

Include:

- Fully integrated three-statement modelling.
- Comprehensive supporting schedules.
- Segment-level models.
- Complex debt.
- Leases.
- Stock-based compensation.
- Acquisitions.
- Dilution.
- Restructuring.
- Industry-specific accounting treatments.
- Advanced forecast structures.

Integrate these capabilities into the existing DCF engine without duplicating calculations.

## RELEASE 3 — COMPANY RESEARCH INFRASTRUCTURE

Implement the full company-centric research environment.

Include:

- Company search.
- Company profiles.
- Global market identifiers.
- Historical financial data ingestion.
- Market data ingestion.
- Provider adapters.
- Financial statement standardization.
- Research library.
- Document processing.
- Document versioning.
- Source references.
- Research notes.
- Company workspaces.

## RELEASE 4 — COMPLETE APPLICATION

Integrate and complete the remaining platform.

Include:

- Trading comparables.
- Precedent transactions.
- SOTP.
- Asset-based valuation.
- Liquidation valuation.
- DDM.
- Residual income.
- LBO.
- NAV.
- Specialized financial institution valuation.
- Reverse DCF.
- Monte Carlo simulations.
- Advanced scenario combinations.
- Investment conclusions.
- Research reports.
- Investment committee presentations.
- Full historical reproducibility.
- Advanced document and source management.

## FINAL HARDENING

Complete:

- Security testing.
- Calculation validation.
- Performance testing.
- Database optimization.
- Storage optimization.
- Backup validation.
- Recovery testing.
- Deployment documentation.
- User documentation.
- Operational monitoring.

---

# 23. DEVELOPMENT AUTONOMY

Operate as a fully autonomous coding agent.

You are authorized to:

- Design the architecture.
- Create project files.
- Implement application components.
- Install appropriate dependencies.
- Configure Docker.
- Create databases.
- Implement calculation modules.
- Build interfaces.
- Write tests.
- Execute tests.
- Debug defects.
- Refactor code.
- Improve performance.
- Document implementation.
- Deploy completed releases.
- Validate deployments.

Do not ask for approval for routine implementation decisions.

Make reasonable engineering decisions and document them.

However, approval is required before:

- Purchasing subscriptions.
- Activating paid APIs.
- Incurring external costs.
- Enabling AI functionality.
- Making destructive database changes.
- Deleting existing user data.
- Modifying unrelated home server services.
- Exposing additional services publicly.
- Making significant changes to approved product scope.

Never assume approval for these actions.

---

# 24. DEVELOPMENT QUALITY REQUIREMENTS

### 24.1 Testing

Use automated tests throughout development.

Financial calculations require:

- Unit tests.
- Integration tests.
- Regression tests.
- Boundary tests.
- Reconciliation tests.
- Independent benchmark comparisons.

Frontend functionality requires:

- Component tests.
- Integration tests.
- Workflow tests.
- Validation tests.

### 24.2 Financial correctness

Every financial module must have documented inputs, outputs, calculation conventions and test cases.

Do not mark a financial methodology complete simply because the interface displays an output.

The underlying calculation must be correct and independently validated.

### 24.3 Code quality

Maintain:

- Clear module boundaries.
- Type annotations.
- Documentation.
- Consistent naming.
- Structured error handling.
- Reusable components.
- Dependency management.
- Automated linting.
- Automated tests.

### 24.4 Git

Use Git throughout development.

Commit at meaningful milestones.

Maintain a stable main branch.

Avoid committing:

- API credentials.
- Passwords.
- Private documents.
- Database files.
- Sensitive user data.
- Unnecessary generated datasets.

### 24.5 Documentation

Maintain:

- README.
- Architecture documentation.
- Database documentation.
- API documentation.
- Calculation methodology documentation.
- Deployment instructions.
- Backup and recovery instructions.
- Development instructions.
- Known limitations.

### 24.6 Acceptance criteria

Each release must have defined acceptance criteria.

A release is complete only when:

1. Its functionality is implemented.
2. Its underlying calculations work.
3. Its tests pass.
4. Its interfaces work end to end.
5. Its data persists correctly.
6. Its audit requirements are satisfied.
7. Its documentation is updated.
8. It can be deployed and recovered reliably.

Do not leave incomplete placeholder functionality and represent it as finished.

---

# 25. FIRST EXECUTION INSTRUCTIONS

Do not immediately begin writing application code.

Start by inspecting the actual home server environment.

### Step 1 — Inspect the environment

Determine:

- Operating system.
- CPU.
- Available memory.
- Disk capacity.
- Existing Docker installation.
- Existing containers.
- Existing Docker networks.
- Existing Cloudflare Tunnel configuration.
- Existing ports in use.
- Existing Git installation.
- Available Python version.
- Available Node.js version.
- Existing storage paths.

Do not reveal credentials or secrets in logs or reports.

Do not modify existing services.

### Step 2 — Assess resource feasibility

Determine an appropriate initial resource allocation for the application.

Account for existing server workloads.

Identify any infrastructure limitations.

Do not assume that additional hardware is available.

### Step 3 — Establish project structure

Create a dedicated project directory if one does not already exist.

Do not overwrite unrelated projects.

Use a clean Git repository.

Establish development and production configuration.

### Step 4 — Produce the implementation blueprint

Before extensive coding, document:

- System architecture.
- Service architecture.
- Database schema.
- Financial data structures.
- Calculation engine structure.
- API structure.
- Frontend structure.
- Docker deployment.
- Security model.
- Development milestones.

Identify architectural decisions that could create expensive future migrations.

### Step 5 — Begin Release 1

Once the foundation is documented, begin implementing the complete DCF workflow.

Do not spend excessive time creating elaborate infrastructure before producing functional financial modelling.

### Step 6 — Validate continuously

Run tests after every meaningful implementation milestone.

Resolve failures before progressing.

Keep the application deployable throughout development.

---

# 26. FINAL PRODUCT STANDARD

The completed application should be capable of supporting serious professional equity research.

An analyst should be able to:

1. Search for a publicly traded company.
2. Open its research workspace.
3. Retrieve or import historical financial information.
4. Review and standardize historical financial statements.
5. Build a comprehensive operating forecast.
6. Construct integrated financial statements.
7. Develop independent operating scenarios.
8. Calculate WACC.
9. Execute FCFF and FCFE valuations.
10. Run sensitivities and reverse DCF.
11. Execute additional valuation methodologies.
12. Compare valuation results.
13. Develop an investment thesis.
14. Identify key risks and catalysts.
15. Determine a defensible target price.
16. Inspect every important calculation.
17. Reproduce historical valuations.
18. Generate professional research and investment committee materials.

The final application must be coherent, integrated and professionally usable.

It must not feel like several separate applications assembled together.

**Build one unified equity research and valuation platform, with a shared financial data architecture, shared calculation core, shared company workspaces and consistent user experience.**

# FINAL INSTRUCTION

Begin with environment inspection and the architecture blueprint.

Then proceed autonomously through the approved development sequence.

Prioritize financial correctness, a robust Python calculation engine, maintainable architecture and professional usability.

Do not introduce AI functionality, portfolio management, advanced equity screening or unnecessary product features without explicit approval.

Do not stop after generating a plan or creating a visual prototype.

The objective is a working, tested, deployable financial research application running on the home server.
