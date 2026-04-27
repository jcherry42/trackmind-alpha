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
import { colors, engineColor, radius, space, type as typo } from "../lib/theme";

const ENGINES = ["alpha", "quant", "whale", "micro"] as const;

export default function ControlsScreen() {
  const { user, refreshUser } = useAuth();
  const router = useRouter();
  const [paused, setPaused] = useState<string[]>(user?.paused_engines || []);
  const [killSwitch, setKillSwitch] = useState<boolean>(user?.kill_switch || false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (user) {
      setPaused(user.paused_engines || []);
      setKillSwitch(user.kill_switch || false);
    }
  }, [user]);

  async function toggleEngine(engine: string) {
    const next = paused.includes(engine)
      ? paused.filter((e) => e !== engine)
      : [...paused, engine];
    setPaused(next);
    setBusy(true);
    try {
      await api("/controls/update", { method: "POST", body: { paused_engines: next } });
      await refreshUser();
    } catch (e: any) {
      Alert.alert("Error", e?.message || "Failed");
    } finally {
      setBusy(false);
    }
  }

  async function toggleKill() {
    const next = !killSwitch;
    setKillSwitch(next);
    setBusy(true);
    try {
      await api("/controls/update", { method: "POST", body: { kill_switch: next } });
      await refreshUser();
    } finally {
      setBusy(false);
    }
  }

  async function resetAccount() {
    Alert.alert(
      "Reset paper account?",
      "Closes all positions and resets bankroll to $10,000.",
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Reset",
          style: "destructive",
          onPress: async () => {
            await api("/controls/reset", { method: "POST" });
            await refreshUser();
            Alert.alert("Reset", "Bankroll restored to $10,000.");
          },
        },
      ]
    );
  }

  return (
    <SafeAreaView style={styles.safe} edges={["top", "bottom"]}>
      <View style={styles.header}>
        <Pressable testID="close-controls-button" onPress={() => router.back()} style={styles.closeBtn}>
          <Feather name="x" size={22} color={colors.textPrimary} />
        </Pressable>
        <Text style={styles.title}>CONTROLS</Text>
        <View style={{ width: 40 }} />
      </View>

      <ScrollView contentContainerStyle={styles.scroll}>
        <Card style={[styles.killCard, killSwitch && { borderColor: colors.danger }]}>
          <Text style={styles.metricLabel}>MASTER KILL SWITCH</Text>
          <Text style={styles.killTitle}>
            {killSwitch ? "ALL ENGINES STOPPED" : "ALL ENGINES OPERATIONAL"}
          </Text>
          <Text style={styles.subtle}>
            {killSwitch
              ? "No new orders will be deployed. Open positions remain."
              : "Toggle to halt all engines instantly."}
          </Text>
          <Button
            testID="kill-switch-button"
            title={killSwitch ? "RESUME ENGINES" : "ENGAGE KILL SWITCH"}
            variant={killSwitch ? "primary" : "danger"}
            onPress={toggleKill}
            loading={busy}
          />
        </Card>

        <Text style={styles.sectionLabel}>ENGINE STATE</Text>
        {ENGINES.map((e) => {
          const active = !paused.includes(e);
          const c = engineColor(e);
          return (
            <Card
              key={e}
              testID={`engine-toggle-${e}-card`}
              style={{ borderLeftWidth: 3, borderLeftColor: active ? c : colors.borderSubtle }}
            >
              <View style={styles.engineRow}>
                <View>
                  <Text style={[styles.engineName, { color: c }]}>{e.toUpperCase()}</Text>
                  <Text style={styles.subtle}>
                    {e === "alpha" && "Sentiment & narrative repricing"}
                    {e === "quant" && "Markov persistence + drift edges"}
                    {e === "whale" && "Elite wallet consensus"}
                    {e === "micro" && "Cross-venue YES/NO arbitrage"}
                  </Text>
                </View>
                <Switch
                  testID={`engine-${e}-toggle`}
                  value={active}
                  onValueChange={() => toggleEngine(e)}
                  trackColor={{ false: colors.modal, true: c }}
                  thumbColor={active ? "#fff" : colors.textTertiary}
                />
              </View>
            </Card>
          );
        })}

        <Text style={styles.sectionLabel}>RISK CONSTRAINTS</Text>
        <Card>
          <RiskRow label="Max single trade" value="15% of bankroll" />
          <RiskRow label="Max daily drawdown" value="5%" />
          <RiskRow label="Max correlated exposure" value="35% per market" />
          <RiskRow label="Min cash buffer" value="20%" />
          <Text style={[styles.subtle, { marginTop: space.sm }]}>
            Sizes auto-dampen as drawdown approaches limit; engines deactivate in defensive mode.
          </Text>
        </Card>

        <View style={{ marginTop: space.lg }}>
          <Button
            testID="reset-account-button"
            title="RESET PAPER ACCOUNT"
            variant="secondary"
            onPress={resetAccount}
          />
        </View>

        <View style={{ height: space.xl }} />
      </ScrollView>
    </SafeAreaView>
  );
}

function RiskRow({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.riskRow}>
      <Text style={styles.riskLabel}>{label}</Text>
      <Text style={styles.riskValue}>{value}</Text>
    </View>
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
  metricLabel: { ...typo.micro, color: colors.textSecondary },
  subtle: { color: colors.textSecondary, fontSize: 12, marginTop: 4 },
  killCard: { gap: space.sm },
  killTitle: { color: colors.textPrimary, fontSize: 18, fontWeight: "800" },
  sectionLabel: { ...typo.micro, color: colors.textSecondary, marginTop: space.md },
  engineRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  engineName: { ...typo.micro, fontSize: 12 },
  riskRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    paddingVertical: 6,
  },
  riskLabel: { color: colors.textSecondary, fontSize: 13 },
  riskValue: { color: colors.textPrimary, fontSize: 13, fontWeight: "600" },
});
