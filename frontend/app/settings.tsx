import React, { useEffect, useState } from "react";
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
import { ProgressBar } from "../components/ProgressBar";
import { useSnackbar } from "../components/Snackbar";
import { colors, radius, space, type as typo } from "../lib/theme";
import { formatUsd } from "../lib/format";

type Quota = {
  date_utc: string;
  used: number;
  quota: number;
  remaining: number;
  rolls_over_at: string;
};

type BrokersStatus = {
  live_ingest: boolean;
  venues: Record<"polymarket" | "kalshi", { configured: boolean }>;
  current_user: { live_mode: boolean; live_broker: string | null };
};

export default function SettingsScreen() {
  const { user, logout, refreshUser } = useAuth();
  const router = useRouter();
  const snack = useSnackbar();
  const [busy, setBusy] = useState(false);
  const [quota, setQuota] = useState<Quota | null>(null);
  const [brokers, setBrokers] = useState<BrokersStatus | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const [q, b] = await Promise.all([
          api<Quota>("/me/quota"),
          api<BrokersStatus>("/brokers/status"),
        ]);
        if (cancelled) return;
        setQuota(q);
        setBrokers(b);
      } catch {
        /* silent — non-critical */
      }
    };
    load();
    const id = setInterval(load, 30_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  async function pickBroker(name: "polymarket" | "kalshi" | null) {
    try {
      setBusy(true);
      await api("/me/live-broker", { method: "PATCH", body: { broker: name } });
      const b = await api<BrokersStatus>("/brokers/status");
      setBrokers(b);
      snack.show({
        message: name ? `Live broker set to ${name}` : "Live broker cleared",
        kind: "success",
      });
    } catch (e: any) {
      snack.show({
        message: e?.message || "Could not switch broker",
        kind: "error",
      });
    } finally {
      setBusy(false);
    }
  }

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
          <Text style={styles.metricLabel}>API QUOTA</Text>
          {quota ? (
            <>
              <View style={styles.row}>
                <Text style={styles.rowLabel}>LLM calls used today</Text>
                <Text style={styles.rowValue}>
                  {quota.used.toLocaleString()} / {quota.quota.toLocaleString()}
                </Text>
              </View>
              <View style={{ marginTop: space.sm }}>
                <ProgressBar
                  value={Math.min(1, quota.used / Math.max(1, quota.quota))}
                  color={
                    quota.remaining <= quota.quota * 0.1
                      ? colors.negative
                      : quota.remaining <= quota.quota * 0.3
                      ? colors.alpha
                      : colors.primary
                  }
                />
              </View>
              <Text style={[styles.subtle, { marginTop: space.sm }]}>
                Resets at midnight UTC · {quota.remaining.toLocaleString()} calls remaining
              </Text>
            </>
          ) : (
            <Text style={styles.subtle}>Loading…</Text>
          )}
        </Card>

        <Card>
          <Text style={styles.metricLabel}>EXECUTION MODE</Text>
          <View style={styles.row}>
            <View style={{ flex: 1, paddingRight: space.sm }}>
              <Text style={styles.rowLabel}>Live Trading</Text>
              <Text style={styles.subtle}>
                Live mode routes orders to your selected venue. Falls back to paper
                if the chosen venue isn't configured server-side.
              </Text>
            </View>
            <Switch
              testID="settings-live-mode-switch"
              value={!!user?.live_mode}
              onValueChange={(v) => {
                if (v && !brokers?.current_user.live_broker) {
                  snack.show({
                    message: "Pick a live broker first.",
                    kind: "warning",
                  });
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
          <Text style={styles.metricLabel}>LIVE BROKER</Text>
          <Text style={[styles.subtle, { marginBottom: space.sm }]}>
            Pick a venue. Selection is rejected if the server isn't configured for it.
          </Text>
          {(["polymarket", "kalshi"] as const).map((name) => {
            const ready = !!brokers?.venues?.[name]?.configured;
            const active = brokers?.current_user.live_broker === name;
            return (
              <Pressable
                key={name}
                accessibilityRole="button"
                accessibilityState={{ selected: active, disabled: !ready }}
                onPress={() => ready && !busy && pickBroker(active ? null : name)}
                style={({ pressed }) => [
                  styles.brokerRow,
                  {
                    borderColor: active ? colors.primary : colors.borderSubtle,
                    opacity: !ready ? 0.55 : pressed ? 0.75 : 1,
                  },
                ]}
              >
                <View style={{ flex: 1 }}>
                  <Text style={styles.brokerName}>
                    {name === "polymarket" ? "Polymarket" : "Kalshi"}
                  </Text>
                  <Text style={styles.brokerHint}>
                    {ready ? "Server credentials configured" : "Not configured on server"}
                  </Text>
                </View>
                <View
                  style={[
                    styles.statusDot,
                    { backgroundColor: ready ? colors.positive : colors.textTertiary },
                  ]}
                />
                {active && (
                  <Feather
                    name="check-circle"
                    size={18}
                    color={colors.primary}
                    style={{ marginLeft: 10 }}
                  />
                )}
              </Pressable>
            );
          })}
          {brokers?.current_user.live_broker && (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Clear live broker selection"
              onPress={() => pickBroker(null)}
              style={{ marginTop: space.sm }}
            >
              <Text style={{ color: colors.textSecondary, fontSize: 12 }}>
                Clear selection →
              </Text>
            </Pressable>
          )}
          {brokers?.live_ingest === false && (
            <Text style={[styles.subtle, { marginTop: space.sm, color: colors.alpha }]}>
              ⚠ Live market data ingest is OFF. Set LIVE_INGEST_ENABLED=true in backend .env.
            </Text>
          )}
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
  brokerRow: {
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: 12,
    paddingHorizontal: 12,
    borderRadius: 10,
    borderWidth: 1,
    marginBottom: 8,
  },
  brokerName: { ...(typo.body as any), color: colors.textPrimary, fontWeight: "600" },
  brokerHint: { ...(typo.caption as any), color: colors.textSecondary, marginTop: 2 },
  statusDot: { width: 8, height: 8, borderRadius: 4 },
});
