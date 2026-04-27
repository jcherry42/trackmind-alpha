import React from "react";
import { View, StyleSheet } from "react-native";
import { Skeleton } from "./Animated";
import { colors, space } from "../lib/theme";

/**
 * Single skeleton row matching the new minimalist signal row layout
 * (engine dot · market title · meta · right-aligned conf+bar).
 */
export function SignalRowSkeleton() {
  return (
    <View style={styles.row}>
      <View style={styles.main}>
        <Skeleton width={8} height={8} radius={4} />
        <View style={{ flex: 1, gap: 6 }}>
          <Skeleton width="78%" height={15} />
          <Skeleton width="48%" height={11} />
        </View>
        <View style={{ alignItems: "flex-end", gap: 4 }}>
          <Skeleton width={32} height={12} />
          <Skeleton width={48} height={2} radius={1} />
        </View>
      </View>
    </View>
  );
}

export function SignalsListSkeleton({ count = 6 }: { count?: number }) {
  return (
    <View testID="signals-skeleton">
      {Array.from({ length: count }).map((_, i) => (
        <SignalRowSkeleton key={i} />
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  row: {
    paddingVertical: space.md,
    borderBottomWidth: 1,
    borderBottomColor: colors.borderHairline,
  },
  main: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.md,
  },
});
