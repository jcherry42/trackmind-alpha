/**
 * format.tsx unit tests — pure functions, so we can pin exact outputs.
 */
import { formatUsd, formatPct, pnlColor } from "../format";
import { colors } from "../theme";

describe("format", () => {
  describe("formatUsd", () => {
    it("formats integers without decimals", () => {
      expect(formatUsd(1234)).toMatch(/\$1,234/);
    });
    it("handles negative values with a minus sign", () => {
      expect(formatUsd(-42.5)).toMatch(/-\$42/);
    });
    it("rounds to two decimals when fractional", () => {
      expect(formatUsd(9.234)).toMatch(/\$9\.23/);
    });
  });

  describe("formatPct", () => {
    it("formats positive delta with + when sign option set", () => {
      expect(formatPct(0.123, { sign: true })).toBe("+12.30%");
    });
    it("defaults without sign", () => {
      expect(formatPct(0.5)).toBe("50.00%");
    });
    it("formats zero without a sign", () => {
      expect(formatPct(0)).toBe("0.00%");
    });
    it("formats negative with its own minus", () => {
      expect(formatPct(-0.0042)).toBe("-0.42%");
    });
  });

  describe("pnlColor", () => {
    it("uses positive for >0", () => {
      expect(pnlColor(1)).toBe(colors.positive);
    });
    it("uses negative for <0", () => {
      expect(pnlColor(-1)).toBe(colors.negative);
    });
    it("uses neutral for 0", () => {
      expect(pnlColor(0)).toBe(colors.neutral);
    });
  });
});
