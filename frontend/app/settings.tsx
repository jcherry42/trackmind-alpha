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
import { BrokerConnectionModal } from "../components/BrokerConnectionModal";
import { RiskLimitsModal, type RiskLimits } from "../components/RiskLimitsModal";
import { ResponsiveContent } from "../components/ResponsiveContent";
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
  venues: Record<"polymarket" | "kalshi", { configured: boolean; source: "user" | "env" | null }>;
  current_user: { live_mode: boolean; live_broker: string | null };
};

type BrokerConnection = {
  venue: "polymarket" | "kalshi";
  fingerprint: string;
  status: "active" | "untested" | "failed";
  last_tested_at: string | null;
  last_test_result: string | null;
};

type NotifyPrefs = {
  master_signal: boolean;
  position_settled: boolean;
  auto_deploy: boolean;
  daily_loss_tripped: boolean;
  kill_switch: boolean;
};

type PreflightCheck = { key: string; label: string; ok: boolean; detail: string };
type PreflightResult = { ok: boolean; checks: PreflightCheck[]; ready_for_live: boolean };

const NOTIFY_ROWS: Array<{ key: keyof NotifyPrefs; label: string; hint: string; icon: any }> = [
  { key: "master_signal", label: "Master Signal", hint: "High-conviction multi-engine alert", icon: "zap" },
  { key: "position_settled", label: "Position Settled", hint: "Win/loss + realized PnL", icon: "check-circle" },
  { key: "auto_deploy", label: "Auto-Deploy", hint: "When one-tap deploy places orders", icon: "play-circle" },
  { key: "daily_loss_tripped", label: "Daily Loss Circuit", hint: "Once-per-day when trading auto-pauses", icon: "alert-octagon" },
  { key: "kill_switch", label: "Kill Switch", hint: "Confirmation when you toggle global halt", icon: "power" },
];

export default function SettingsScreen() {
  const { user, logout, refreshUser } = useAuth();
  const router = useRouter();
  const snack = useSnackbar();
  const [busy, setBusy] = useState(false);
  const [quota, setQuota] = useState<Quota | null>(null);
  const [brokers, setBrokers] = useState<BrokersStatus | null>(null);
  const [connections, setConnections] = useState<BrokerConnection[]>([]);
  const [risk, setRisk] = useState<RiskLimits | null>(null);
  const [notify, setNotify] = useState<NotifyPrefs | null>(null);
  const [preflight, setPreflight] = useState<PreflightResult | null>(null);
  const [modalVenue, setModalVenue] = useState<"polymarket" | "kalshi" | null>(null);
  const [riskOpen, setRiskOpen] = useState(false);

  const reload = async () => {
    try {
      const [q, b, c, r, n, pf] = await Promise.all([
        api<Quota>("/me/quota"),
        api<BrokersStatus>("/brokers/status"),
        api<{ connections: BrokerConnection[] }>("/me/broker-connections"),
        api<RiskLimits>("/me/risk-limits"),
        api<{ prefs: NotifyPrefs }>("/me/notification-prefs"),
        api<PreflightResult>("/system/preflight"),
      ]);
      setQuota(q);
      setBrokers(b);
      setConnections(c.connections || []);
      setRisk(r);
      setNotify(n.prefs);
      setPreflight(pf);
    } catch {
      /* silent — non-critical */
    }
  };

  useEffect(() => {
    reload();
    const id = setInterval(reload, 30_000);
    return () => clearInterval(id);
  }, []);

  async function pickBroker(name: "polymarket" | "kalshi" | null) {
    try {
      setBusy(true);
      await api("/me/live-broker", { method: "PATCH", body: { broker: name } });
      await reload();
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

  async function disconnectBroker(name: "polymarket" | "kalshi") {
    try {
      setBusy(true);
      await api(`/me/broker-connections/${name}`, { method: "DELETE" });
      await reload();
      await refreshUser();
      snack.show({ message: `${name} disconnected`, kind: "success" });
    } catch (e: any) {
      snack.show({ message: e?.message || "Disconnect failed", kind: "error" });
    } finally {
      setBusy(false);
    }
  }

  async function testSavedBroker(name: "polymarket" | "kalshi") {
    try {
      setBusy(true);
      const r = await api<{ ok: boolean; detail: string }>(
        "/me/broker-connections/test",
        { method: "POST", body: { venue: name } },
      );
      snack.show({
        message: r.ok ? `✓ ${name} authenticated` : `✗ ${r.detail}`,
        kind: r.ok ? "success" : "error",
      });
      await reload();
    } catch (e: any) {
      snack.show({ message: e?.message || "Test failed", kind: "error" });
    } finally {
      setBusy(false);
    }
  }

  async function toggleNotifyPref(key: keyof NotifyPrefs, next: boolean) {
    // Optimistic update so the Switch animates immediately.
    setNotify((prev) => (prev ? { ...prev, [key]: next } : prev));
    try {
      const r = await api<{ prefs: NotifyPrefs }>("/me/notification-prefs", {
        method: "PATCH",
        body: { [key]: next },
      });
      setNotify(r.prefs);
    } catch (e: any) {
      // Roll back optimistic update on failure.
      setNotify((prev) => (prev ? { ...prev, [key]: !next } : prev));
      snack.show({ message: e?.message || "Could not update notifications", kind: "error" });
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
        <ResponsiveContent>
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
                {!brokers?.current_user.live_broker
                  ? "Connect a broker + route to it below before flipping this on."
                  : user?.live_mode
                  ? `LIVE · routing orders to ${brokers.current_user.live_broker.toUpperCase()}`
                  : `Paper · ready to flip to ${brokers.current_user.live_broker.toUpperCase()}`}
              </Text>
            </View>
            <Switch
              testID="settings-live-mode-switch"
              value={!!user?.live_mode}
              onValueChange={(v) => {
                if (v && !brokers?.current_user.live_broker) {
                  Alert.alert(
                    "Pick a live broker first",
                    "Live mode routes real orders to your selected venue. " +
                      "To enable it:\n\n" +
                      "1. Tap '+' next to Polymarket or Kalshi below and paste credentials\n" +
                      "2. Tap the Polymarket/Kalshi pill under 'ROUTE LIVE ORDERS TO'\n" +
                      "3. Then come back and toggle this switch on.",
                    [{ text: "OK" }],
                  );
                  return;
                }
                if (v) {
                  // Confirm transition to live with a brief warning so users
                  // can't accidentally arm real-money trading.
                  Alert.alert(
                    `Go LIVE on ${brokers!.current_user.live_broker!.toUpperCase()}?`,
                    `Trades will be routed to a real broker. ` +
                      `Daily safety cap: $50 notional. Per-trade cap respects your risk-limits setting. ` +
                      `You can flip back to paper anytime.`,
                    [
                      { text: "Cancel", style: "cancel" },
                      {
                        text: "ARM LIVE TRADING",
                        style: "destructive",
                        onPress: () => setFlag("live_mode", true),
                      },
                    ],
                  );
                  return;
                }
                setFlag("live_mode", false);
              }}
              disabled={busy || !brokers?.current_user.live_broker}
              trackColor={{ false: colors.modal, true: colors.negative }}
              thumbColor={
                !brokers?.current_user.live_broker
                  ? colors.textTertiary
                  : user?.live_mode
                  ? colors.negative
                  : colors.textTertiary
              }
              style={
                !brokers?.current_user.live_broker
                  ? { opacity: 0.45 }
                  : undefined
              }
            />
          </View>
          {!brokers?.current_user.live_broker && (
            <View
              style={{
                flexDirection: "row",
                alignItems: "center",
                marginTop: space.sm,
                paddingTop: space.sm,
                borderTopWidth: 1,
                borderTopColor: colors.borderSubtle,
                gap: 8,
              }}
            >
              <Feather name="info" size={14} color={colors.alpha} />
              <Text style={[styles.subtle, { flex: 1, color: colors.alpha }]}>
                Live mode is gated by an active broker route. Set one in the
                section below.
              </Text>
            </View>
          )}
        </Card>

        <Card>
          <View style={{ flexDirection: "row", alignItems: "center", marginBottom: space.sm }}>
            <Text style={[styles.metricLabel, { flex: 1 }]}>SYSTEM CHECK</Text>
            <View
              style={[
                styles.statusDot,
                { backgroundColor: preflight?.ready_for_live ? colors.positive : colors.alpha, marginRight: 6 },
              ]}
            />
            <Text style={{ color: preflight?.ready_for_live ? colors.positive : colors.alpha, fontSize: 11, fontWeight: "700", letterSpacing: 0.6 }}>
              {preflight?.ready_for_live ? "READY FOR LIVE" : "NOT READY"}
            </Text>
          </View>
          {preflight ? (
            preflight.checks.map((c, idx) => (
              <View
                key={c.key}
                style={[
                  styles.notifyRow,
                  idx === preflight.checks.length - 1 && { borderBottomWidth: 0 },
                ]}
              >
                <View style={styles.notifyIcon}>
                  <Feather
                    name={c.ok ? "check-circle" : "alert-circle"}
                    size={16}
                    color={c.ok ? colors.positive : colors.alpha}
                  />
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={styles.rowLabel}>{c.label}</Text>
                  <Text style={styles.subtle}>{c.detail}</Text>
                </View>
              </View>
            ))
          ) : (
            <Text style={styles.subtle}>Running checks…</Text>
          )}
        </Card>

        <Card>
          <Text style={[styles.subtle, { marginBottom: space.sm }]}>
            Credentials are encrypted at-rest with Fernet (AES-128 + HMAC) using your master
            key. Tested before any live order is routed.
          </Text>
          {(["polymarket", "kalshi"] as const).map((name) => {
            const venue = brokers?.venues?.[name];
            const conn = connections.find((c) => c.venue === name);
            const ready = !!venue?.configured;
            const active = brokers?.current_user.live_broker === name;
            const statusColor =
              conn?.status === "active"
                ? colors.positive
                : conn?.status === "failed"
                ? colors.negative
                : ready
                ? colors.alpha
                : colors.textTertiary;
            const statusText = conn
              ? conn.status === "active"
                ? "Authenticated"
                : conn.status === "failed"
                ? `Failed · ${conn.last_test_result?.slice(0, 36) || "unknown"}`
                : "Untested"
              : venue?.source === "env"
              ? "Operator (env)"
              : "Not connected";
            return (
              <View key={name} style={styles.brokerRow}>
                <View style={{ flex: 1 }}>
                  <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                    <View style={[styles.statusDot, { backgroundColor: statusColor }]} />
                    <Text style={styles.brokerName}>
                      {name === "polymarket" ? "Polymarket" : "Kalshi"}
                    </Text>
                    {active && (
                      <View style={styles.activeBadge}>
                        <Text style={styles.activeBadgeText}>ACTIVE</Text>
                      </View>
                    )}
                  </View>
                  <Text style={styles.brokerHint}>
                    {statusText}
                    {conn?.fingerprint ? ` · ${conn.fingerprint}` : ""}
                  </Text>
                </View>
                <View style={{ flexDirection: "row", gap: 6 }}>
                  {conn && (
                    <Pressable
                      accessibilityRole="button"
                      onPress={() => testSavedBroker(name)}
                      disabled={busy}
                      style={({ pressed }) => [
                        styles.smallBtn,
                        { opacity: pressed ? 0.6 : 1 },
                      ]}
                    >
                      <Feather name="zap" size={14} color={colors.textPrimary} />
                    </Pressable>
                  )}
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel={conn ? `Edit ${name}` : `Connect ${name}`}
                    onPress={() => setModalVenue(name)}
                    disabled={busy}
                    style={({ pressed }) => [
                      styles.smallBtn,
                      { opacity: pressed ? 0.6 : 1 },
                    ]}
                  >
                    <Feather
                      name={conn ? "edit-2" : "plus"}
                      size={14}
                      color={colors.textPrimary}
                    />
                  </Pressable>
                  {conn && (
                    <Pressable
                      accessibilityRole="button"
                      accessibilityLabel={`Disconnect ${name}`}
                      onPress={() =>
                        Alert.alert(
                          `Disconnect ${name}?`,
                          "This will erase your encrypted credentials and clear live mode if active.",
                          [
                            { text: "Cancel", style: "cancel" },
                            {
                              text: "Disconnect",
                              style: "destructive",
                              onPress: () => disconnectBroker(name),
                            },
                          ],
                        )
                      }
                      disabled={busy}
                      style={({ pressed }) => [
                        styles.smallBtn,
                        { opacity: pressed ? 0.6 : 1, borderColor: colors.negativeSoft },
                      ]}
                    >
                      <Feather name="trash-2" size={14} color={colors.negative} />
                    </Pressable>
                  )}
                </View>
              </View>
            );
          })}

          <Text style={[styles.metricLabel, { marginTop: space.md }]}>ROUTE LIVE ORDERS TO</Text>
          <View style={{ flexDirection: "row", gap: 8, marginTop: space.xs }}>
            {(["polymarket", "kalshi", null] as const).map((name) => {
              const ready = name ? !!brokers?.venues?.[name]?.configured : true;
              const active = brokers?.current_user.live_broker === name;
              const label =
                name === null ? "None" : name === "polymarket" ? "Polymarket" : "Kalshi";
              return (
                <Pressable
                  key={name ?? "none"}
                  accessibilityRole="button"
                  accessibilityState={{ selected: active, disabled: !ready }}
                  onPress={() => ready && !busy && pickBroker(name)}
                  style={({ pressed }) => [
                    styles.routePill,
                    {
                      borderColor: active ? colors.primary : colors.borderSubtle,
                      backgroundColor: active ? colors.primary : "transparent",
                      opacity: !ready ? 0.4 : pressed ? 0.7 : 1,
                    },
                  ]}
                >
                  <Text
                    style={{
                      color: active ? colors.textInverse : colors.textPrimary,
                      fontSize: 12,
                      fontWeight: "600",
                    }}
                  >
                    {label}
                  </Text>
                </Pressable>
              );
            })}
          </View>

          {brokers?.live_ingest === false && (
            <Text style={[styles.subtle, { marginTop: space.sm, color: colors.alpha }]}>
              ⚠ Live market data ingest is OFF. Set LIVE_INGEST_ENABLED=true in backend .env.
            </Text>
          )}
        </Card>

        <Card>
          <View style={{ flexDirection: "row", alignItems: "center" }}>
            <Text style={[styles.metricLabel, { flex: 1 }]}>RISK LIMITS</Text>
            <Pressable
              accessibilityRole="button"
              onPress={() => setRiskOpen(true)}
              style={({ pressed }) => [styles.editPill, { opacity: pressed ? 0.6 : 1 }]}
            >
              <Feather name="sliders" size={12} color={colors.textPrimary} />
              <Text style={{ color: colors.textPrimary, fontSize: 11, fontWeight: "600" }}>
                EDIT
              </Text>
            </Pressable>
          </View>
          {risk ? (
            <>
              <View style={styles.row}>
                <View style={{ flex: 1 }}>
                  <Text style={styles.rowLabel}>Daily Loss Circuit</Text>
                  <Text style={styles.subtle}>
                    Kills trades for the day if PnL ≤ {formatUsd(risk.preview.daily_loss_dollar)}
                  </Text>
                </View>
                <Text style={styles.rowValue}>{(risk.daily_loss_pct * 100).toFixed(2)}%</Text>
              </View>
              <View style={styles.row}>
                <View style={{ flex: 1 }}>
                  <Text style={styles.rowLabel}>Per-Trade Cap</Text>
                  <Text style={styles.subtle}>
                    Max one trade: {formatUsd(risk.preview.max_trade_dollar)}
                  </Text>
                </View>
                <Text style={styles.rowValue}>{(risk.max_trade_pct * 100).toFixed(2)}%</Text>
              </View>
              {risk.preview.circuit_tripped && (
                <View style={styles.tripped}>
                  <Feather name="alert-octagon" size={14} color={colors.negative} />
                  <Text style={{ color: colors.negative, fontSize: 12, flex: 1 }}>
                    Circuit TRIPPED — trading paused. Resets at UTC midnight.
                  </Text>
                </View>
              )}
            </>
          ) : (
            <Text style={styles.subtle}>Loading…</Text>
          )}
        </Card>

        <Card>
          <Pressable
            onPress={() => router.push("/whales")}
            style={{
              flexDirection: "row",
              alignItems: "center",
              justifyContent: "space-between",
              paddingVertical: space.sm,
            }}
            accessibilityLabel="Open whale tracking"
          >
            <View style={{ flex: 1 }}>
              <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                <Feather name="anchor" size={16} color={colors.whale} />
                <Text style={styles.metricLabel}>WHALE TRACKING</Text>
              </View>
              <Text style={[styles.subtle, { marginTop: 6 }]}>
                Live Polymarket whale activity · Mirror trades into your account
              </Text>
            </View>
            <Feather name="chevron-right" size={20} color={colors.textTertiary} />
          </Pressable>
        </Card>

        <Card>
          <Text style={styles.metricLabel}>NOTIFICATIONS</Text>
          <Text style={[styles.subtle, { marginBottom: space.sm }]}>
            Per-event push controls. Defaults are all on — toggle any category off to silence it.
          </Text>
          {NOTIFY_ROWS.map((cfg, idx) => {
            const isOn = notify ? notify[cfg.key] : true;
            return (
              <View
                key={cfg.key}
                style={[
                  styles.notifyRow,
                  idx === NOTIFY_ROWS.length - 1 && { borderBottomWidth: 0 },
                ]}
              >
                <View style={styles.notifyIcon}>
                  <Feather
                    name={cfg.icon}
                    size={16}
                    color={isOn ? colors.textPrimary : colors.textTertiary}
                  />
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={[styles.rowLabel, !isOn && { color: colors.textSecondary }]}>
                    {cfg.label}
                  </Text>
                  <Text style={styles.subtle}>{cfg.hint}</Text>
                </View>
                <Switch
                  value={isOn}
                  onValueChange={(v) => toggleNotifyPref(cfg.key, v)}
                  trackColor={{ false: colors.borderSubtle, true: colors.primary }}
                  thumbColor="#fff"
                  disabled={!notify}
                />
              </View>
            );
          })}

          <Pressable
            onPress={async () => {
              try {
                await api("/me/alerts/test", { method: "POST" });
                snack.show({
                  message: "Test alert sent — banner appears within ~20s.",
                  kind: "success",
                });
              } catch (e: any) {
                snack.show({
                  message: e?.message || "Test alert failed",
                  kind: "error",
                });
              }
            }}
            disabled={busy}
            style={({ pressed }) => ({
              marginTop: space.md,
              paddingVertical: 12,
              paddingHorizontal: space.md,
              borderRadius: 8,
              borderWidth: 1,
              borderColor: colors.borderSubtle,
              flexDirection: "row",
              alignItems: "center",
              justifyContent: "center",
              gap: 8,
              opacity: pressed ? 0.6 : 1,
            })}
          >
            <Feather name="bell" size={14} color={colors.textPrimary} />
            <Text
              style={{
                color: colors.textPrimary,
                fontSize: 12,
                letterSpacing: 1.2,
                fontWeight: "700",
              }}
            >
              SEND TEST ALERT
            </Text>
          </Pressable>
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
        </ResponsiveContent>
      </ScrollView>

      {modalVenue && (
        <BrokerConnectionModal
          visible={!!modalVenue}
          venue={modalVenue}
          onClose={() => setModalVenue(null)}
          onSaved={reload}
        />
      )}
      <RiskLimitsModal
        visible={riskOpen}
        onClose={() => setRiskOpen(false)}
        onSaved={reload}
      />
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
  rowValue: { color: colors.textPrimary, fontWeight: "600", fontVariant: ["tabular-nums"] },
  brokerRow: {
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: 10,
    paddingHorizontal: 12,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: colors.borderSubtle,
    marginBottom: 8,
    gap: 8,
  },
  brokerName: { ...(typo.body as any), color: colors.textPrimary, fontWeight: "600" },
  brokerHint: { ...(typo.caption as any), color: colors.textSecondary, marginTop: 2 },
  statusDot: { width: 8, height: 8, borderRadius: 4 },
  activeBadge: {
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 4,
    backgroundColor: colors.positiveSoft,
  },
  activeBadgeText: {
    color: colors.positive,
    fontSize: 9,
    fontWeight: "700",
    letterSpacing: 0.6,
  },
  smallBtn: {
    width: 32,
    height: 32,
    borderRadius: 8,
    borderColor: colors.borderSubtle,
    borderWidth: 1,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.modal,
  },
  routePill: {
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: radius.pill,
    borderWidth: 1,
  },
  editPill: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingHorizontal: 12,
    paddingVertical: 10,
    minHeight: 44,
    borderRadius: radius.pill,
    borderWidth: 1,
    borderColor: colors.borderSubtle,
  },
  tripped: {
    flexDirection: "row",
    gap: 6,
    alignItems: "center",
    padding: 8,
    borderRadius: 8,
    backgroundColor: colors.negativeSoft,
    marginTop: space.sm,
  },
  notifyRow: {
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: 10,
    gap: 12,
    borderBottomColor: colors.borderSubtle,
    borderBottomWidth: 1,
    minHeight: 56,
  },
  notifyIcon: {
    width: 32,
    height: 32,
    borderRadius: 8,
    backgroundColor: colors.modal,
    alignItems: "center",
    justifyContent: "center",
  },
});
