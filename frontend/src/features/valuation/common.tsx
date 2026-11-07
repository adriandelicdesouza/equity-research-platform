// Shared valuation display: exact decimal strings formatted by string arithmetic only (D61),
// issue lists for gates/warnings, and the DCF output and cost-of-capital tables used by both
// the preview and stored runs. Nothing here calculates a financial value: every number comes
// from the server's authoritative engine run (D25).

import type { ReactNode } from "react";
import { Badge, KV } from "../../components/ui";
import { formatDecimal, formatPercent, isDecimalString } from "../../lib/decimal";

export type Json = Record<string, unknown>;

export function str(value: unknown): string | null {
  return typeof value === "string" ? value : typeof value === "number" ? String(value) : null;
}

/** Money in the run currency, 2 decimals for display; the exact value is in the tooltip. */
export function Money({ value, currency }: { value: unknown; currency?: string }) {
  const s = str(value);
  if (s === null || !isDecimalString(s)) return <span className="muted">—</span>;
  return (
    <span className="num" title={`${s}${currency ? ` ${currency}` : ""} (exact)`}>
      {formatDecimal(s, { decimals: 2, parentheses: true })}
    </span>
  );
}

export function PerShare({ value, currency }: { value: unknown; currency?: string }) {
  const s = str(value);
  if (s === null || !isDecimalString(s)) return <span className="muted">—</span>;
  return (
    <span className="num" title={`${s} (exact)`}>
      {formatDecimal(s, { decimals: 4 })}
      {currency ? ` ${currency}` : ""}
    </span>
  );
}

/** A fraction ("0.0425") shown as a percentage; the exact fraction is in the tooltip. */
export function Pct({ value, decimals = 2 }: { value: unknown; decimals?: number }) {
  const s = str(value);
  if (s === null || !isDecimalString(s)) return <span className="muted">—</span>;
  return (
    <span className="num" title={`${s} (fraction, exact)`}>
      {formatPercent(s, decimals)}
    </span>
  );
}

export function Num({ value, decimals = 4, suffix = "" }: { value: unknown; decimals?: number; suffix?: string }) {
  const s = str(value);
  if (s === null || !isDecimalString(s)) return <span className="muted">—</span>;
  return (
    <span className="num" title={`${s} (exact)`}>
      {formatDecimal(s, { decimals })}
      {suffix}
    </span>
  );
}

export interface Issue {
  level: string;
  code: string;
  message: string;
  details?: Json;
}

const LEVEL_TEXT: Record<string, string> = {
  error: "Cannot compute",
  gate: "Blocks saving",
  warning: "Warning",
};

export function IssueList({ issues, empty }: { issues: Issue[]; empty?: ReactNode }) {
  if (issues.length === 0) return empty ? <>{empty}</> : null;
  return (
    <ul className="issues" aria-label="Issues">
      {issues.map((i, k) => (
        <li key={`${i.code}-${k}`} className={`issue issue-${i.level}`}>
          <Badge value={LEVEL_TEXT[i.level] ?? i.level} tone={i.level === "warning" ? "warn" : "bad"} />{" "}
          <code>{i.code}</code> — {i.message}
        </li>
      ))}
    </ul>
  );
}

export function KindLabel({ kind }: { kind: unknown }) {
  const k = str(kind) ?? "";
  return <>{k.replaceAll("_", " ")}</>;
}

/** Valuation outputs of one DCF execution (preview or stored run). */
export function DcfOutputs({ outputs }: { outputs: Json }) {
  const currency = str(outputs.currency) ?? "";
  const primary = (outputs.primary ?? {}) as Json;
  const terminal = (outputs.terminal ?? {}) as Json;
  const market = (outputs.market ?? null) as Json | null;
  const flows = (outputs.flows ?? []) as Json[];
  const isFcff = outputs.methodology === "dcf_fcff";
  return (
    <div className="stack">
      <section aria-label="Valuation summary">
        <h3>Value</h3>
        <KV
          items={[
            [
              `Primary output (${String(primary.kind ?? "").replaceAll("_", " ")})`,
              <Money key="p" value={primary.value} currency={currency} />,
            ],
            ["Equity value at balance-sheet date", <Money key="eb" value={outputs.equity_value_b} currency={currency} />],
            ["Equity value at valuation date", <Money key="ev" value={outputs.equity_value_v} currency={currency} />],
            ["Diluted shares", <Num key="ds" value={outputs.diluted_shares} decimals={0} />],
            ["Implied value per share", <PerShare key="ps" value={outputs.per_share} currency={currency} />],
            [
              "Market price (reporting currency)",
              market ? <PerShare key="mp" value={market.price_reporting} currency={currency} /> : <span className="muted">no price selected</span>,
            ],
            ["Upside / downside", market ? <Pct key="up" value={market.upside} /> : <span className="muted">—</span>],
            ["Σ PV of forecast cash flows", <Money key="spv" value={outputs.sum_pv_flows} currency={currency} />],
            ["PV of terminal value", <Money key="ptv" value={outputs.pv_terminal} currency={currency} />],
            ["Terminal value share", <Pct key="ts" value={outputs.terminal_share} />],
          ]}
        />
        {!isFcff && (
          <p className="small muted">
            FCFE discounted at the cost of equity gives equity value directly: cash, debt and other claims are not
            added or subtracted afterwards (D19).
          </p>
        )}
      </section>
      <section aria-label="Terminal value">
        <h3>Terminal value</h3>
        <KV
          items={[
            ["Terminal value", <Money key="tv" value={terminal.value} currency={currency} />],
            ["Discount time", <Num key="t" value={terminal.time} decimals={4} />],
            ["Implied exit multiple", <Num key="m" value={terminal.implied_exit_multiple} decimals={2} suffix="×" />],
            ["Implied perpetual growth", <Pct key="g" value={terminal.implied_growth} />],
            ["Implied RONIC", <Pct key="r" value={terminal.implied_ronic} />],
          ]}
        />
      </section>
      <section aria-label="Forecast cash flows">
        <h3>{isFcff ? "Free cash flow to the firm" : "Free cash flow to equity"}</h3>
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th scope="col">Year</th>
                <th scope="col" className="num">
                  Cash flow ({currency})
                </th>
                <th scope="col" className="num">
                  Discount time
                </th>
                <th scope="col" className="num">
                  Discount factor
                </th>
                <th scope="col" className="num">
                  Present value ({currency})
                </th>
              </tr>
            </thead>
            <tbody>
              {flows.map((f) => (
                <tr key={String(f.year)}>
                  <th scope="row">{String(f.year)}</th>
                  <td className="num">
                    <Money value={f.flow} />
                  </td>
                  <td className="num">
                    <Num value={f.time} />
                  </td>
                  <td className="num">
                    <Num value={f.discount_factor} decimals={6} />
                  </td>
                  <td className="num">
                    <Money value={f.present_value} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <CostOfCapital build={(outputs.cost_of_capital ?? {}) as Json} fcff={isFcff} />
    </div>
  );
}

export function CostOfCapital({ build, fcff }: { build: Json; fcff: boolean }) {
  const checks = (build.checks ?? []) as Json[];
  return (
    <section aria-label="Cost of capital">
      <h3>Cost of capital</h3>
      <KV
        items={[
          ["Risk-free rate", <Pct key="rf" value={build.risk_free_rate} />],
          ["Levered beta", <Num key="b" value={build.beta_levered} />],
          ["Equity risk premium used", <Pct key="erp" value={build.erp_used} />],
          ["Country risk term", <Pct key="crp" value={build.crp_term} />],
          ["Cost of equity", <Pct key="ke" value={build.cost_of_equity} />],
          ["Pre-tax cost of debt", <Pct key="kd" value={build.pre_tax_cost_of_debt} />],
          ["Marginal tax rate", <Pct key="t" value={build.marginal_tax_rate} />],
          ...(fcff
            ? ([
                ["WACC weights basis", <>{str(build.capital_structure_basis) ?? "—"}</>],
                ["Equity weight", <Pct key="we" value={build.weight_equity} />],
                ["Debt weight", <Pct key="wd" value={build.weight_debt} />],
                ["After-tax cost of debt", <Pct key="atkd" value={build.after_tax_cost_of_debt} />],
                ["WACC (constant)", <Pct key="w" value={build.wacc} />],
              ] as [string, ReactNode][])
            : []),
        ]}
      />
      {checks.length > 0 && (
        <ul className="small">
          {checks.map((c, i) => (
            <li key={i}>
              <code>{String(c.code)}</code> — {String(c.message)}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function ValidationList({ checks }: { checks: Json[] }) {
  if (checks.length === 0) return <p className="muted small">No validation messages.</p>;
  return (
    <ul className="small" aria-label="Validation">
      {checks.map((c, i) => (
        <li key={i}>
          <Badge value={String(c.severity)} /> <code>{String(c.code)}</code> — {String(c.message)}
        </li>
      ))}
    </ul>
  );
}
