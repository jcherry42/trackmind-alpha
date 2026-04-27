import React from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from "react-native";
import { colors, radius, space, type as typo } from "../lib/theme";

type Variant = "primary" | "secondary" | "danger" | "ghost";

export function Button({
  title,
  onPress,
  variant = "primary",
  loading,
  disabled,
  testID,
  icon,
}: {
  title: string;
  onPress?: () => void;
  variant?: Variant;
  loading?: boolean;
  disabled?: boolean;
  testID?: string;
  icon?: React.ReactNode;
}) {
  const isDisabled = disabled || loading;
  const variantStyle = variantStyles[variant];

  return (
    <Pressable
      testID={testID}
      onPress={onPress}
      disabled={isDisabled}
      style={({ pressed }) => [
        styles.base,
        variantStyle.bg,
        pressed && !isDisabled && { opacity: 0.85 },
        isDisabled && styles.disabled,
      ]}
    >
      {loading ? (
        <ActivityIndicator color={variantStyle.text.color} />
      ) : (
        <View style={styles.row}>
          {icon ? <View style={{ marginRight: space.sm }}>{icon}</View> : null}
          <Text style={[styles.text, variantStyle.text]}>{title}</Text>
        </View>
      )}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  base: {
    paddingVertical: 14,
    paddingHorizontal: 24,
    borderRadius: radius.md,
    alignItems: "center",
    justifyContent: "center",
    flexDirection: "row",
  },
  text: {
    ...typo.bodyLarge,
    fontWeight: "700",
    letterSpacing: 0.2,
  },
  row: { flexDirection: "row", alignItems: "center" },
  disabled: { opacity: 0.45 },
});

const variantStyles: Record<Variant, { bg: any; text: any }> = {
  primary: {
    bg: { backgroundColor: colors.primary },
    text: { color: colors.primaryText },
  },
  secondary: {
    bg: { backgroundColor: colors.modal, borderWidth: 1, borderColor: colors.borderFocus },
    text: { color: colors.textPrimary },
  },
  danger: {
    bg: { backgroundColor: colors.danger },
    text: { color: colors.textPrimary },
  },
  ghost: {
    bg: { backgroundColor: "transparent" },
    text: { color: colors.textPrimary },
  },
};
