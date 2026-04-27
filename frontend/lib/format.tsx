import React from "react";
import { Text, TextProps } from "react-native";
import { colors, type } from "./theme";

export function formatUsd(n: number, opts: { sign?: boolean; decimals?: number } = {}) {
  const { sign = false, decimals = 2 } = opts;
  const fixed = Math.abs(n).toLocaleString("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
  const prefix = n < 0 ? "-$" : sign && n > 0 ? "+$" : "$";
  return `${prefix}${fixed}`;
}

export function formatPct(n: number, opts: { sign?: boolean; decimals?: number } = {}) {
  const { sign = false, decimals = 2 } = opts;
  const v = (n * 100).toFixed(decimals);
  const prefix = n < 0 ? "" : sign && n > 0 ? "+" : "";
  return `${prefix}${v}%`;
}

export function pnlColor(n: number): string {
  if (n > 0) return colors.positive;
  if (n < 0) return colors.negative;
  return colors.neutral;
}

export function MetricText({
  children,
  size = 24,
  color,
  style,
  ...rest
}: TextProps & { size?: number; color?: string }) {
  return (
    <Text
      {...rest}
      style={[
        { fontSize: size, color: color || colors.textPrimary, ...type.metric },
        style,
      ]}
    >
      {children}
    </Text>
  );
}
