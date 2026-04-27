import React from "react";
import { StyleSheet, Text, View } from "react-native";
import { colors, radius, type as typo } from "../lib/theme";

export function Tag({
  label,
  color = colors.textSecondary,
  bg,
  testID,
}: {
  label: string;
  color?: string;
  bg?: string;
  testID?: string;
}) {
  return (
    <View
      testID={testID}
      style={[
        styles.tag,
        { backgroundColor: bg || hexAlpha(color, 0.13), borderColor: hexAlpha(color, 0.4) },
      ]}
    >
      <Text style={[styles.label, { color }]}>{label}</Text>
    </View>
  );
}

function hexAlpha(hex: string, alpha: number) {
  // accepts #RRGGBB only
  if (!hex.startsWith("#") || hex.length !== 7) return hex;
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

const styles = StyleSheet.create({
  tag: {
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: radius.pill,
    borderWidth: 1,
    alignSelf: "flex-start",
  },
  label: {
    ...typo.micro,
    fontSize: 10,
  },
});
