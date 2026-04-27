import React from "react";
import { Text } from "react-native";
import Svg, { Polyline } from "react-native-svg";
import { colors } from "../lib/theme";

/**
 * Tiny inline sparkline. Accepts an array of numbers.
 */
export function Sparkline({
  data,
  width = 80,
  height = 24,
  color = colors.textPrimary,
  strokeWidth = 1.5,
}: {
  data: number[];
  width?: number;
  height?: number;
  color?: string;
  strokeWidth?: number;
}) {
  if (!data || data.length < 2) {
    return <Text style={{ width, height, color: colors.textTertiary, fontSize: 10 }}>—</Text>;
  }
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const stepX = width / (data.length - 1);
  const points = data
    .map((v, i) => {
      const x = (i * stepX).toFixed(2);
      const y = (height - ((v - min) / range) * height).toFixed(2);
      return `${x},${y}`;
    })
    .join(" ");

  return (
    <Svg width={width} height={height}>
      <Polyline
        points={points}
        fill="none"
        stroke={color}
        strokeWidth={strokeWidth}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </Svg>
  );
}
