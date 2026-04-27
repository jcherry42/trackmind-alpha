import React, { useEffect } from "react";
import { ActivityIndicator, StyleSheet, View } from "react-native";
import { useRouter } from "expo-router";
import { useAuth } from "../lib/auth";
import { colors } from "../lib/theme";

export default function Index() {
  const { user } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (user === undefined) return;
    if (user) router.replace("/(tabs)");
    else router.replace("/(auth)/login");
  }, [user, router]);

  return (
    <View testID="splash-view" style={styles.container}>
      <ActivityIndicator color={colors.textPrimary} />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.bg,
    alignItems: "center",
    justifyContent: "center",
  },
});
