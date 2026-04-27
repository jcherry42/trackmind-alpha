import React, { useState } from "react";
import {
  Alert,
  Pressable,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  View,
} from "react-native";
import { useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { Feather } from "@expo/vector-icons";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { colors, radius, space, type as typo } from "../lib/theme";
import { formatUsd } from "../lib/format";

export default function SettingsScreen() {
  const { user, logout, refreshUser } = useAuth();
  const router = useRouter();
  const [busy, setBusy] = useState(false);

  async function setFlag(key: "biometrics_enabled" | "live_mode", value: boolean) {
    setBusy(true);
    try {
      await api("/controls/update", { method: "POST", body: { [key]: value } });
      await refreshUser();
    } catch (e: any) {
      Alert.alert("Error", e?.message || "Failed");
    } finally {
      setBusy(false);
    }
  }

  async function onLogout() {
    await logout();
    router.replace("/(auth)/login");
  }

  return (
    <SafeAreaView style={styles.safe} edges={["top", "bottom"]}>
      <View style={styles.header}>
        <Pressable testID="close-settings-button" onPress={() => router.back()} style={styles.closeBtn}>
          <Feather name="x" size={22} color={colors.textPrimary} />
        </Pressable>
        <Text style={styles.title}>SETTINGS</Text>
        <View style={{ width: 40 }} />
      </View>

      <ScrollView contentContainerStyle={styles.scroll}>
        <Card>
          <Text style={styles.metricLabel}>ACCOUNT</Text>
          <Text style={styles.value}>{user?.email}</Text>
          <Text style={styles.subtle}>{user?.name}</Text>
          <View style={styles.divider} />
          <View style={styles.row}>
            <Text style={styles.rowLabel}>Bankroll</Text>
            <Text style={styles.rowValue}>{formatUsd(user?.bankroll || 0)}</Text>
          </View>
          <View style={styles.row}>
            <Text style={styles.rowLabel}>Role</Text>
            <Text style={styles.rowValue}>{(user?.role || "user").toUpperCase()}</Text>
          </View>
        </Card>

        <Card>
          <Text style={styles.metricLabel}>SECURITY</Text>
          <View style={styles.row}>
            <View>
              <Text style={styles.rowLabel}>Face ID / Biometrics</Text>
              <Text style={styles.subtle}>Require device auth at login</Text>
            </View>
            <Switch
              testID="settings-biometric-switch"
              value={!!user?.biometrics_enabled}
              onValueChange={(v) => setFlag("biometrics_enabled", v)}
              disabled={busy}
              trackColor={{ false: colors.modal, true: colors.primary }}
              thumbColor={user?.biometrics_enabled ? colors.primary : colors.textTertiary}
            />
          </View>
        </Card>

        <Card>
          <Text style={styles.metricLabel}>EXECUTION MODE</Text>
          <View style={styles.row}>
            <View>
              <Text style={styles.rowLabel}>Live Trading</Text>
              <Text style={styles.subtle}>
                Live venues require Polymarket / Kalshi API setup. Paper-only by default.
              </Text>
            </View>
            <Switch
              testID="settings-live-mode-switch"
              value={!!user?.live_mode}
              onValueChange={(v) => {
                if (v) {
                  Alert.alert(
                    "Live mode unavailable",
                    "Live execution requires venue API keys. Paper mode remains active."
                  );
                  return;
                }
                setFlag("live_mode", v);
              }}
              disabled={busy}
              trackColor={{ false: colors.modal, true: colors.negative }}
              thumbColor={user?.live_mode ? colors.negative : colors.textTertiary}
            />
          </View>
        </Card>

        <Card>
          <Text style={styles.metricLabel}>NOTIFICATIONS</Text>
          <Text style={styles.subtle}>
            Push alerts coming next: master signal alerts, position settlements,
            kill-switch confirmations.
          </Text>
        </Card>

        <View style={{ marginTop: space.md }}>
          <Button
            testID="logout-button"
            title="SIGN OUT"
            variant="secondary"
            onPress={onLogout}
          />
        </View>
        <Text style={[styles.subtle, { textAlign: "center", marginTop: space.lg }]}>
          TRACKMIND v1.0.0 · Paper Mode
        </Text>
        <View style={{ height: space.xl }} />
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.bg },
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: space.md,
    paddingVertical: space.sm,
  },
  closeBtn: {
    width: 40,
    height: 40,
    borderRadius: radius.md,
    alignItems: "center",
    justifyContent: "center",
    borderColor: colors.borderSubtle,
    borderWidth: 1,
  },
  title: { ...typo.micro, color: colors.textPrimary, letterSpacing: 3 },
  scroll: { padding: space.md, gap: space.sm },
  metricLabel: { ...typo.micro, color: colors.textSecondary, marginBottom: 4 },
  value: { color: colors.textPrimary, fontSize: 16, fontWeight: "600" },
  subtle: { color: colors.textSecondary, fontSize: 12, marginTop: 2 },
  divider: { height: 1, backgroundColor: colors.borderSubtle, marginVertical: space.sm },
  row: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingVertical: 8,
  },
  rowLabel: { color: colors.textPrimary, fontSize: 14 },
  rowValue: { color: colors.textPrimary, fontWeight: "600" },
});
