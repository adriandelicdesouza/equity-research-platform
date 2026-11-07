import { formatDecimal, formatExact, formatPercent, isDecimalString, parseDecimal } from "./decimal";

describe("exact decimal formatting", () => {
  it("keeps every digit of large values (no float conversion)", () => {
    expect(formatExact("394328000000")).toBe("394,328,000,000");
    expect(formatExact("12345678901234567890.123456789")).toBe("12,345,678,901,234,567,890.123456789");
    // 2^53 + 1 cannot be represented as a double; it must survive formatting.
    expect(formatExact("9007199254740993")).toBe("9,007,199,254,740,993");
  });

  it("scales by moving the decimal point and rounds half away from zero", () => {
    expect(formatDecimal("394328000000", { scale: 6, decimals: 1 })).toBe("394,328.0");
    expect(formatDecimal("1234550000", { scale: 6, decimals: 1 })).toBe("1,234.6");
    expect(formatDecimal("1234549999", { scale: 6, decimals: 1 })).toBe("1,234.5");
    expect(formatDecimal("999950000", { scale: 6, decimals: 1 })).toBe("1,000.0");
    expect(formatDecimal("0.1", { decimals: 3 })).toBe("0.100");
    expect(formatDecimal("12", { scale: 3 })).toBe("0.012");
  });

  it("shows negatives in parentheses on request and never prints a negative zero", () => {
    expect(formatDecimal("-2500000", { scale: 6, decimals: 1, parentheses: true })).toBe("(2.5)");
    expect(formatDecimal("-2500000", { scale: 6, decimals: 1 })).toBe("-2.5");
    expect(formatDecimal("-0.00001", { decimals: 2, parentheses: true })).toBe("0.00");
    expect(formatDecimal("0", { scale: 6, decimals: 1 })).toBe("0.0");
  });

  it("formats fractions as percentages exactly", () => {
    expect(formatPercent("0.0125")).toBe("1.25%");
    expect(formatPercent("0.005", 1)).toBe("0.5%");
  });

  it("validates decimal strings and leaves non-decimals unchanged", () => {
    expect(isDecimalString("-12.50")).toBe(true);
    expect(isDecimalString("1e5")).toBe(false);
    expect(isDecimalString("12,5")).toBe(false);
    expect(parseDecimal("")).toBeNull();
    expect(formatDecimal("n/a")).toBe("n/a");
  });
});
