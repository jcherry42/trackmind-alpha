import React, { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { useFocusEffect, useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { Feather } from "@expo/vector-icons";
import { api } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { colors, engineColor, radius, space, type as typo } from "../../lib/theme";
import { Card } from "../../components/Card";
import { Button } from "../../components/Button";
import { Tag } from "../../components/Tag";
import { ProgressBar } from "../../components/ProgressBar";
import { MetricText, formatPct, formatUsd, pnlColor } from "../../lib/format";

type EngineStatus = {
  engine: string;
  active: boolean;
  signals: number;
  avg_confidence: number;
  allocation_pct: number;
  notional: number;
};
type DashboardData = {
  bankroll: number;
  starting_bankroll: number;
  today_pnl: number;
  today_pnl_pct: number;
  realized_pnl_today: number;
  unrealized_pnl: number;
  open_positions: number;
  mode: "live" | "paper";
  kill_switch: boolean;
  engines_updated_at: number;
  engine_status: EngineStatus[];
  plan: {
    deployed: number;
    deployable_cash: number;
    cash_buffer: number;
    defensive_mode: boolean;
    drawdown_ratio: number;
    orders_count: number;
  };
  top_signal: any | null;
};

export default function Dashboard() {
  const { user } = useAuth();
  const router = useRouter();
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(false);
  const [deploying, setDeploying] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const d = await api<DashboardData>("/dashboard");
      setData(d);
    } catch (e: any) {
      setError(e?.message || "Failed to load");
    } finally {
      setLoading(false);
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      load();
      const id = setInterval(load, 15000);
      return () => clearInterval(id);
    }, [load])
  );

  async function autoDeploy() {
    if (!data) return;
    setDeploying(true);
    try {
      const r = await api<{ deployed: any[] }>("/trade/auto-deploy", { method: "POST" });
      Alert.alert(
        "Deployed",
        `Master allocator deployed ${r.deployed.length} position(s).`
      );
      await load();
    } catch (e: any) {
      Alert.alert("Deploy failed", e?.message || "Try again");
    } finally {
      setDeploying(false);
    }
  }

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <ScrollView
        contentContainerStyle={styles.scroll}
        refreshControl={
          <RefreshControl tintColor={colors.textSecondary} refreshing={loading} onRefresh={load} />
        }
      >
        {/* Header */}
        <View style={styles.header}>
          <View>
            <Text style={styles.brand}>TRACKMIND</Text>
            <Text style={styles.subBrand}>
              {user?.name?.toUpperCase() || "TRADER"} · {user?.email}
            </Text>
          </View>
          <View style={styles.headerActions}>
            <ModeBadge mode={data?.mode || "paper"} kill={!!data?.kill_switch} />
            <Pressable
              testID="open-controls-button"
              onPress={() => router.push("/controls")}
              style={styles.iconBtn}
            >
              <Feather name="sliders" color={colors.textPrimary} size={18} />
            </Pressable>
            <Pressable
              testID="open-settings-button"
              onPress={() => router.push("/settings")}
              style={styles.iconBtn}
            >
              <Feather name="user" color={colors.textPrimary} size={18} />
            </Pressable>
          </View>
        </View>

        {error ? <Text testID="dashboard-error" style={styles.error}>{error}</Text> : null}

        {/* Bankroll + PnL */}
        <Card>
          <Text style={styles.metricLabel}>BANKROLL</Text>
          <MetricText
            testID="bankroll-value-text"
            size={42}
            color={colors.textPrimary}
            style={{ marginTop: 4 }}
          >
            {formatUsd(data?.bankroll ?? 0)}
          </MetricText>

          <View style={styles.pnlRow}>
            <View style={styles.pnlBlock}>
              <Text style={styles.metricLabel}>TODAY P&amp;L</Text>
              <MetricText
                testID="today-pnl-value-text"
                size={22}
                color={pnlColor(data?.today_pnl ?? 0)}
              >
                {formatUsd(data?.today_pnl ?? 0, { sign: true })}
              </MetricText>
              <Text style={[styles.subtle, { color: pnlColor(data?.today_pnl ?? 0) }]}>
                {formatPct(data?.today_pnl_pct ?? 0, { sign: true })}
              </Text>
            </View>
            <View style={styles.pnlBlock}>
              <Text style={styles.metricLabel}>OPEN POSITIONS</Text>
              <MetricText size={22}>{data?.open_positions ?? 0}</MetricText>
              <Text style={styles.subtle}>
                Unrealized {formatUsd(data?.unrealized_pnl ?? 0, { sign: true })}
              </Text>
            </View>
          </View>
        </Card>

        {/* Engines grid */}
        <View style={styles.sectionHeader}>
          <Text style={styles.sectionTitle}>ENGINES</Text>
          <Text style={styles.subtle}>
            Updated {data?.engines_updated_at
              ? `${Math.max(0, Math.round((Date.now() / 1000) - data.engines_updated_at))}s ago`
              : "—"}
          </Text>
        </View>
        <View style={styles.enginesGrid}>
          {(data?.engine_status || ENGINES_PLACEHOLDER).map((e) => (
            <EngineCard key={e.engine} status={e} />
          ))}
        </View>

        {/* Top signal */}
        <View style={styles.sectionHeader}>
          <Text style={styles.sectionTitle}>TOP SIGNAL</Text>
          <Pressable
            testID="see-all-signals-link"
            onPress={() => router.push("/(tabs)/signals")}
          >
            <Text style={styles.linkRight}>SEE ALL</Text>
          </Pressable>
        </View>

        {data?.top_signal ? (
          <TopSignalCard sig={data.top_signal} />
        ) : (
          <Card>
            <Text style={styles.subtle}>No active signal.</Text>
          </Card>
        )}

        {/* Allocator panel */}
        <Card style={styles.allocCard}>
          <Text style={styles.metricLabel}>MASTER ALLOCATOR</Text>
          <View style={styles.allocRow}>
            <View>
              <Text style={styles.subtle}>Orders Ready</Text>
              <MetricText size={20}>{data?.plan?.orders_count ?? 0}</MetricText>
            </View>
            <View>
              <Text style={styles.subtle}>Cash Buffer</Text>
              <MetricText size={20}>
                {formatUsd(data?.plan?.cash_buffer ?? 0)}
              </MetricText>
            </View>
            <View>
              <Text style={styles.subtle}>Mode</Text>
              <Text
                style={[
                  styles.modeText,
                  { color: data?.plan?.defensive_mode ? colors.negative : colors.positive },
                ]}
              >
                {data?.plan?.defensive_mode ? "DEFENSIVE" : "OFFENSIVE"}
              </Text>
            </View>
          </View>
          <Button
            testID="auto-deploy-button"
            title={`AUTO-DEPLOY ${data?.plan?.orders_count ?? 0} ORDER${(data?.plan?.orders_count || 0) === 1 ? "" : "S"}`}
            onPress={autoDeploy}
            loading={deploying}
            disabled={!data?.plan?.orders_count || data?.kill_switch}
          />
        </Card>

        <View style={{ height: space.xl }} />
      </ScrollView>
    </SafeAreaView>
  );
}

function ModeBadge({ mode, kill }: { mode: "live" | "paper"; kill: boolean }) {
  if (kill)
    return <Tag testID="kill-switch-badge" label="KILLED" color={colors.negative} />;
  return (
    <Tag
      testID="live-paper-switch"
      label={mode === "live" ? "LIVE" : "PAPER"}
      color={mode === "live" ? colors.negative : colors.quant}
    />
  );
}

function EngineCard({ status }: { status: EngineStatus }) {
  const c = engineColor(status.engine);
  return (
    <View
      testID={`engine-${status.engine}-card`}
      style={[styles.engineCard, { borderColor: status.active ? c : colors.borderSubtle }]}
    >
      <View style={styles.engineHeader}>
        <View style={[styles.engineDot, { backgroundColor: status.active ? c : colors.textTertiary }]} />
        <Text style={[styles.engineName, { color: c }]}>{status.engine.toUpperCase()}</Text>
      </View>
      <MetricText size={22}>{status.allocation_pct.toFixed(0)}%</MetricText>
      <Text style={styles.subtle}>{status.signals} signals</Text>
      <Text style={styles.subtle}>conf {(status.avg_confidence * 100).toFixed(0)}%</Text>
    </View>
  );
}

function TopSignalCard({ sig }: { sig: any }) {
  const router = useRouter();
  const c = engineColor(sig.engine);
  return (
    <Pressable
      testID="top-signal-card"
      onPress={() => router.push("/(tabs)/signals")}
    >
      <Card style={{ borderLeftWidth: 3, borderLeftColor: c }}>
        <View style={styles.signalHead}>
          <Tag label={sig.engine.toUpperCase()} color={c} />
          <Tag label={sig.side} color={sig.side === "NO" ? colors.negative : colors.positive} />
        </View>
        <Text style={styles.signalMarket}>{sig.market}</Text>
        <Text style={styles.subtle}>{sig.rationale}</Text>
        <View style={styles.signalMetaRow}>
          <View>
            <Text style={styles.metricLabel}>CONF</Text>
            <MetricText size={16}>{(sig.confidence * 100).toFixed(0)}%</MetricText>
          </View>
          <View>
            <Text style={styles.metricLabel}>EDGE</Text>
            <MetricText size={16}>{(sig.expected_edge * 100).toFixed(2)}%</MetricText>
          </View>
          <View>
            <Text style={styles.metricLabel}>RISK</Text>
            <MetricText size={16}>{(sig.risk * 100).toFixed(2)}%</MetricText>
          </View>
        </View>
        <ProgressBar value={sig.confidence} color={c} />
      </Card>
    </Pressable>
  );
}

const ENGINES_PLACEHOLDER: EngineStatus[] = [
  { engine: "alpha", active: true, signals: 0, avg_confidence: 0, allocation_pct: 0, notional: 0 },
  { engine: "quant", active: true, signals: 0, avg_confidence: 0, allocation_pct: 0, notional: 0 },
  { engine: "whale", active: true, signals: 0, avg_confidence: 0, allocation_pct: 0, notional: 0 },
  { engine: "micro", active: true, signals: 0, avg_confidence: 0, allocation_pct: 0, notional: 0 },
];

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.bg },
  scroll: { padding: space.md, gap: space.md },
  header: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: space.sm,
  },
  brand: { ...typo.h3, color: colors.textPrimary, letterSpacing: 3, fontSize: 20 },
  subBrand: { ...typo.micro, color: colors.textTertiary, marginTop: 4, letterSpacing: 1 },
  headerActions: { flexDirection: "row", gap: space.sm, alignItems: "center" },
  iconBtn: {
    width: 36,
    height: 36,
    alignItems: "center",
    justifyContent: "center",
    borderWidth: 1,
    borderColor: colors.borderSubtle,
    borderRadius: radius.md,
  },
  error: { color: colors.negative },
  metricLabel: { ...typo.micro, color: colors.textSecondary },
  pnlRow: { flexDirection: "row", marginTop: space.lg, gap: space.lg },
  pnlBlock: { flex: 1 },
  subtle: { color: colors.textSecondary, fontSize: 12, marginTop: 2 },
  sectionHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginTop: space.md,
  },
  sectionTitle: { ...typo.micro, color: colors.textSecondary },
  linkRight: { ...typo.micro, color: colors.textPrimary },
  enginesGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: space.sm,
  },
  engineCard: {
    flexBasis: "48%",
    flexGrow: 1,
    borderWidth: 1,
    borderRadius: radius.lg,
    backgroundColor: colors.surface,
    padding: space.md,
    gap: 4,
  },
  engineHeader: { flexDirection: "row", alignItems: "center", gap: 6 },
  engineDot: { width: 8, height: 8, borderRadius: 4 },
  engineName: { ...typo.micro, fontSize: 11 },
  signalHead: { flexDirection: "row", gap: 8 },
  signalMarket: {
    color: colors.textPrimary,
    fontSize: 16,
    fontWeight: "600",
    marginTop: 8,
  },
  signalMetaRow: {
    flexDirection: "row",
    gap: space.lg,
    marginTop: space.md,
    marginBottom: space.sm,
  },
  allocCard: { gap: space.md },
  allocRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  modeText: { fontWeight: "800", letterSpacing: 1, fontSize: 14, marginTop: 4 },
});
