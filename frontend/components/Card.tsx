import React from "react";
import { View, StyleSheet, ViewProps } from "react-native";
import { colors, radius, space } from "../lib/theme";

type CardProps = ViewProps & { padded?: boolean };

export function Card({ style, padded = true, children, ...rest }: CardProps) {
  return (
    <View {...rest} style={[styles.card, padded && styles.padded, style]}>
      {children}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: colors.surface,
    borderColor: colors.borderSubtle,
    borderWidth: 1,
    borderRadius: radius.lg,
  },
  padded: {
    padding: space.md,
  },
});
