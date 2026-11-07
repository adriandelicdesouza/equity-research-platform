// Exact decimal display. The API serializes every amount as a decimal *string* (D59); these
// helpers only move the decimal point and round for display using string arithmetic, so a
// value is never converted to a binary float. The exact stored value is always available
// separately (tooltips and the provenance panel show it unformatted).

const DECIMAL_RE = /^([+-]?)(\d*)(?:\.(\d*))?$/;

export interface ParsedDecimal {
  negative: boolean;
  /** All significant digits, no sign, no point; "" means zero. */
  digits: string;
  /** Number of digits after the decimal point. */
  exponent: number;
}

export function parseDecimal(text: string): ParsedDecimal | null {
  const m = DECIMAL_RE.exec(text.trim());
  if (!m) return null;
  const [, sign = "", intPart = "", fracPart = ""] = m;
  if (intPart === "" && fracPart === "") return null;
  let digits = (intPart + fracPart).replace(/^0+/, "");
  let exponent = fracPart.length;
  // Trim trailing zeros of the fraction so the representation is canonical.
  while (exponent > 0 && digits.endsWith("0")) {
    digits = digits.slice(0, -1);
    exponent -= 1;
  }
  return { negative: sign === "-" && digits !== "", digits, exponent };
}

export function isDecimalString(text: string): boolean {
  return parseDecimal(text) !== null;
}

function addOne(digits: string): string {
  const out = digits.split("");
  let i = out.length - 1;
  while (i >= 0) {
    if (out[i] === "9") {
      out[i] = "0";
      i -= 1;
    } else {
      out[i] = String(Number(out[i]) + 1);
      return out.join("");
    }
  }
  return `1${out.join("")}`;
}

function groupThousands(intPart: string): string {
  return intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

export interface FormatOptions {
  /** Divide by 10^scale before display (3 = thousands, 6 = millions, 9 = billions). */
  scale?: number;
  /** Fixed number of decimals shown (rounded half away from zero). Omit for exact. */
  decimals?: number;
  /** Show negatives as (1,234) instead of -1,234. */
  parentheses?: boolean;
  /** Thousands separators (default true). */
  group?: boolean;
}

/**
 * Format a decimal string for display. Returns the input unchanged if it is not a decimal.
 * With `decimals` omitted the value is exact (all significant digits are kept).
 */
export function formatDecimal(text: string, opts: FormatOptions = {}): string {
  const p = parseDecimal(text);
  if (!p) return text;
  const { scale = 0, decimals, parentheses = false, group = true } = opts;
  let { digits } = p;
  let exponent = p.exponent + scale;
  if (exponent < 0) {
    // Multiplying (negative scale, e.g. fraction → percent): append zeros.
    digits = digits === "" ? "" : digits + "0".repeat(-exponent);
    exponent = 0;
  }

  let intPart: string;
  let fracPart: string;
  if (digits.length > exponent) {
    intPart = digits.slice(0, digits.length - exponent);
    fracPart = digits.slice(digits.length - exponent);
  } else {
    intPart = "0";
    fracPart = "0".repeat(exponent - digits.length) + digits;
  }

  if (decimals !== undefined) {
    if (fracPart.length > decimals) {
      const keep = fracPart.slice(0, decimals);
      const roundUp = (fracPart[decimals] ?? "0") >= "5";
      let combined = intPart + keep;
      if (roundUp) combined = addOne(combined);
      intPart = combined.slice(0, combined.length - decimals) || "0";
      fracPart = combined.slice(combined.length - decimals);
    } else {
      fracPart = fracPart.padEnd(decimals, "0");
    }
  }
  intPart = intPart.replace(/^0+(?=\d)/, "");
  digits = intPart + fracPart;
  const isZero = /^0*$/.test(digits);
  const body = (group ? groupThousands(intPart) : intPart) + (fracPart ? `.${fracPart}` : "");
  if (!p.negative || isZero) return body;
  return parentheses ? `(${body})` : `-${body}`;
}

/** Exact value with grouping, e.g. "394,328,000,000" or "-0.0125". */
export function formatExact(text: string): string {
  return formatDecimal(text);
}

export const DISPLAY_SCALES = [
  { value: 0, label: "Units", suffix: "" },
  { value: 3, label: "Thousands", suffix: "k" },
  { value: 6, label: "Millions", suffix: "m" },
  { value: 9, label: "Billions", suffix: "bn" },
] as const;

export function scaleSuffix(scale: number): string {
  return DISPLAY_SCALES.find((s) => s.value === scale)?.suffix ?? `e${scale}`;
}

/** Ratio stored as a fraction ("0.0125") shown as a percentage, exactly then rounded. */
export function formatPercent(text: string, decimals = 2): string {
  const p = parseDecimal(text);
  if (!p) return text;
  return `${formatDecimal(text, { scale: -2, decimals, group: true })}%`;
}
