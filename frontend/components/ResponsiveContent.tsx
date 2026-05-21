/**
 * <ResponsiveContent> — wraps screen content so the existing phone-portrait
 * layout doesn't look stretched on iPad-landscape. SafeAreaView/background stay
 * full-width; this component sets a max-width on the actual scroll/content
 * column and centers it.
 *
 * Usage:
 *   <SafeAreaView style={{ flex: 1 }}>
 *     <ResponsiveContent>
 *       <Card>…</Card>
 *       <Card>…</Card>
 *     </ResponsiveContent>
 *   </SafeAreaView>
 *
 * On phones this is a no-op. On iPad landscape the content sits inside a
 * 720pt-max column, centered horizontally.
 */
import React from "react";
import { View, ViewStyle, StyleProp } from "react-native";
import { useResponsive } from "../lib/useResponsive";

type Props = {
  children: React.ReactNode;
  /** Override the default max-width (720pt). */
  maxWidth?: number;
  style?: StyleProp<ViewStyle>;
};

export function ResponsiveContent({ children, maxWidth, style }: Props) {
  const r = useResponsive();
  if (!r.isTablet) {
    // Phones: no-op pass-through to keep the existing layout pixel-identical.
    return <View style={[{ width: "100%" }, style]}>{children}</View>;
  }
  const cap = maxWidth ?? 720;
  return (
    <View
      style={[
        {
          width: "100%",
          maxWidth: cap,
          alignSelf: "center",
        },
        style,
      ]}
    >
      {children}
    </View>
  );
}
