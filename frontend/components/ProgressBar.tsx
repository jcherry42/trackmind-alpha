import React from "react";
import { StyleSheet, View } from "react-native";
import { colors, radius } from "../lib/theme";

export function ProgressBar({
  value,
  height = 4,
  color = colors.textPrimary,
  trackColor = colors.modal,
}: {
  value: number; // 0..1
  height?: number;
  color?: string;
  trackColor?: string;
}) {
  const pct = Math.max(0, Math.min(1, value));
  return (
    <View
      style={[
        styles.track,
        { height, backgroundColor: trackColor, borderRadius: height / 2 },
      ]}
    >
      <View
        style={{
          height,
          width: `${pct * 100}%`,
          backgroundColor: color,
          borderRadius: height / 2,
        }}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  track: { width: "100%", overflow: "hidden", borderRadius: radius.sm },
});
