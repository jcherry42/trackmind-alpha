import React, { useCallback, useState } from "react";
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
import { colors, engineColor, hexAlpha, space, type as typo } from "../../lib/theme";
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

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await api<DashboardData>("/dashboard"));
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
    haptics.medium();
    setDeploying(true);
    try {
      const r = await api<{ deployed: any[] }>("/trade/auto-deploy", { method: "POST" });
      haptics.success();
      Alert.alert("Deployed", `${r.deployed.length} position(s) opened.`);
      await load();
    } catch (e: any) {
      haptics.error();
      Alert.alert("Deploy failed", e?.message || "Try again");
    } finally {
      setDeploying(false);
    }
  }

  const pnl = data?.today_pnl ?? 0;
  const pnlPct = data?.today_pnl_pct ?? 0;
  const ordersCount = data?.plan?.orders_count ?? 0;
  const killed = !!data?.kill_switch;

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <ScrollView
        contentContainerStyle={styles.scroll}
        refreshControl={
          <RefreshControl tintColor={colors.textTertiary} refreshing={loading} onRefresh={load} />
        }
      >
        {/* Top bar */}
        <View style={styles.topBar}>
          <View style={styles.markRow}>
            <View style={styles.markDot} />
            <Text style={styles.brand}>TRACKMIND</Text>
            <Text style={styles.modeChip}>
              {killed ? "KILLED" : data?.mode === "live" ? "LIVE" : "PAPER"}
            </Text>
          </View>
          <View style={styles.topActions}>
            <IconBtn
              testID="open-controls-button"
              icon="sliders"
              onPress={() => router.push("/controls")}
            />
            <IconBtn
              testID="open-settings-button"
              icon="user"
              onPress={() => router.push("/settings")}
            />
          </View>
        </View>
        </FadeSlideIn>

        {/* Hero — Bankroll */}
        <FadeSlideIn delay={80}>
        <View style={styles.hero}>
          <Text style={styles.heroLabel}>Bankroll · {user?.name?.toUpperCase() || "TRADER"}</Text>
          <MetricText
            testID="bankroll-value-text"
            size={56}
            color={colors.textPrimary}
            style={{ letterSpacing: -2, marginTop: 2 }}
          >
            {formatUsd(data?.bankroll ?? 0, { decimals: 2 })}
          </MetricText>
          <View style={styles.heroRow}>
            <Text testID="today-pnl-value-text" style={[styles.heroPnl, { color: pnlColor(pnl) }]}>
              {formatUsd(pnl, { sign: true })}
            </Text>
            <View style={styles.dot} />
            <Text style={[styles.heroPnl, { color: pnlColor(pnl) }]}>
              {formatPct(pnlPct, { sign: true })}
            </Text>
            <View style={styles.dot} />
            <Text style={styles.heroSub}>
              {data?.open_positions ?? 0} open
            </Text>
          </View>
        </View>
        </FadeSlideIn>

        {/* Engines — minimalist horizontal list */}
        <FadeSlideIn delay={180}>
        <View style={styles.engineList}>
          {(data?.engine_status || PLACEHOLDER).map((e, i) => (
            <EngineRow key={e.engine} status={e} last={i === 3} />
          ))}
        </View>
        </FadeSlideIn>

        {/* Top signal */}
        {data?.top_signal ? (
          <FadeSlideIn delay={260}>
          <Pressable
            testID="top-signal-card"
            onPress={() => {
              haptics.select();
              router.push("/(tabs)/signals");
            }}
            style={styles.topSignalSection}
          >
            <View style={styles.sectionHeader}>
              <Text style={styles.sectionLabel}>Top Signal</Text>
              <Text style={styles.sectionLink}>see all →</Text>
            </View>
            <TopSignal sig={data.top_signal} />
          </Pressable>
          </FadeSlideIn>
        ) : null}

        {/* Allocator CTA */}
        <FadeSlideIn delay={340}>
        <View style={styles.allocSection}>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionLabel}>Master Allocator</Text>
            <Text
              style={[
                styles.sectionLink,
                { color: data?.plan?.defensive_mode ? colors.negative : colors.positive },
              ]}
            >
              {data?.plan?.defensive_mode ? "DEFENSIVE" : "OFFENSIVE"}
            </Text>
          </View>
          <View style={styles.allocStats}>
            <Stat label="Orders ready" value={`${ordersCount}`} />
            <Stat label="Cash buffer" value={formatUsd(data?.plan?.cash_buffer ?? 0)} />
          </View>
          <Pressable
            testID="auto-deploy-button"
            onPress={autoDeploy}
            disabled={!ordersCount || killed || deploying}
            style={({ pressed }) => [
              styles.deployBtn,
              (!ordersCount || killed) && { opacity: 0.4 },
              pressed && { opacity: 0.85 },
            ]}
          >
            <Text style={styles.deployText}>
              {deploying
                ? "DEPLOYING…"
                : `AUTO-DEPLOY ${ordersCount} ORDER${ordersCount === 1 ? "" : "S"}`}
            </Text>
            <Feather name="arrow-up-right" size={16} color={colors.primaryText} />
          </Pressable>
        </View>
        </FadeSlideIn>

        <View style={{ height: space.xl }} />
      </ScrollView>
    </SafeAreaView>
  );
}

function IconBtn({
  icon,
  onPress,
  testID,
}: {
  icon: any;
  onPress: () => void;
  testID?: string;
}) {
  return (
    <Pressable
      testID={testID}
      onPress={onPress}
      style={({ pressed }) => [styles.iconBtn, pressed && { opacity: 0.6 }]}
      hitSlop={6}
    >
      <Feather name={icon} color={colors.textPrimary} size={16} />
    </Pressable>
  );
}

function EngineRow({ status, last }: { status: EngineStatus; last?: boolean }) {
  const c = engineColor(status.engine);
  const active = status.active;
  return (
    <View
      testID={`engine-${status.engine}-card`}
      style={[styles.engineRow, !last && styles.engineRowDivider]}
    >
      <View style={styles.engineLeft}>
        <View
          style={[
            styles.engineDot,
            { backgroundColor: active ? c : colors.textMuted, shadowColor: c },
            active && { shadowOpacity: 0.7, shadowRadius: 6 },
          ]}
        />
        <Text style={[styles.engineName, !active && { color: colors.textTertiary }]}>
          {status.engine.toUpperCase()}
        </Text>
        <Text style={styles.engineMeta}>
          {status.signals} signal{status.signals === 1 ? "" : "s"}
        </Text>
      </View>
      <View style={styles.engineRight}>
        <View style={styles.engineBarTrack}>
          <View
            style={[
              styles.engineBarFill,
              {
                width: `${Math.min(100, status.allocation_pct)}%`,
                backgroundColor: active ? c : colors.textMuted,
              },
            ]}
          />
        </View>
        <Text style={styles.engineAlloc}>
          {status.allocation_pct.toFixed(0)}%
        </Text>
      </View>
    </View>
  );
}

function TopSignal({ sig }: { sig: any }) {
  const c = engineColor(sig.engine);
  return (
    <View style={styles.topSignal}>
      <View style={styles.topSignalHead}>
        <View style={styles.topSignalTagRow}>
          <View style={[styles.engineDotSm, { backgroundColor: c }]} />
          <Text style={[styles.topSignalEngine, { color: c }]}>
            {sig.engine.toUpperCase()}
          </Text>
          <Text style={styles.topSignalSep}>·</Text>
          <Text
            style={[
              styles.topSignalSide,
              { color: sig.side === "NO" ? colors.negative : colors.positive },
            ]}
          >
            {sig.side}
          </Text>
        </View>
        <Text style={styles.topSignalPrice}>@ {Number(sig.price).toFixed(3)}</Text>
      </View>
      <Text style={styles.topSignalMarket} numberOfLines={2}>
        {sig.market}
      </Text>
      <View style={styles.topSignalMetrics}>
        <MiniMetric label="conf" value={`${(sig.confidence * 100).toFixed(0)}%`} />
        <MiniMetric label="edge" value={`${(sig.expected_edge * 100).toFixed(2)}%`} />
        <MiniMetric label="risk" value={`${(sig.risk * 100).toFixed(2)}%`} />
      </View>
      <View style={styles.confTrack}>
        <View
          style={[
            styles.confFill,
            { width: `${sig.confidence * 100}%`, backgroundColor: c },
          ]}
        />
      </View>
    </View>
  );
}

function MiniMetric({ label, value }: { label: string; value: string }) {
  return (
    <View>
      <Text style={styles.miniLabel}>{label}</Text>
      <Text style={styles.miniValue}>{value}</Text>
    </View>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.statBlock}>
      <Text style={styles.miniLabel}>{label}</Text>
      <Text style={styles.statValue}>{value}</Text>
    </View>
  );
}

const PLACEHOLDER: EngineStatus[] = [
  { engine: "alpha", active: true, signals: 0, avg_confidence: 0, allocation_pct: 0, notional: 0 },
  { engine: "quant", active: true, signals: 0, avg_confidence: 0, allocation_pct: 0, notional: 0 },
  { engine: "whale", active: true, signals: 0, avg_confidence: 0, allocation_pct: 0, notional: 0 },
  { engine: "micro", active: true, signals: 0, avg_confidence: 0, allocation_pct: 0, notional: 0 },
];

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.bg },
  scroll: { paddingHorizontal: space.lg, paddingBottom: space.xl, gap: space.xl },
  topBar: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingTop: space.sm,
  },
  markRow: { flexDirection: "row", alignItems: "center", gap: 10 },
  markDot: { width: 8, height: 8, borderRadius: 4, backgroundColor: colors.textPrimary },
  brand: { color: colors.textPrimary, fontSize: 14, fontWeight: "800", letterSpacing: 3 },
  modeChip: {
    color: colors.textTertiary,
    fontSize: 9,
    letterSpacing: 1.5,
    fontWeight: "700",
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderWidth: 1,
    borderColor: colors.borderSubtle,
    borderRadius: 4,
    overflow: "hidden",
    marginLeft: 4,
  },
  topActions: { flexDirection: "row", gap: 14 },
  iconBtn: {
    width: 32,
    height: 32,
    borderRadius: 16,
    alignItems: "center",
    justifyContent: "center",
  },

  hero: { marginTop: space.md, gap: 4 },
  heroLabel: { ...typo.micro, color: colors.textTertiary },
  heroRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    marginTop: space.sm,
  },
  heroPnl: {
    fontSize: 16,
    fontWeight: "700",
    fontVariant: ["tabular-nums"],
  },
  dot: { width: 3, height: 3, borderRadius: 1.5, backgroundColor: colors.textMuted },
  heroSub: { color: colors.textTertiary, fontSize: 13 },

  engineList: {
    backgroundColor: colors.surface,
    borderRadius: 16,
    paddingHorizontal: space.md,
  },
  engineRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingVertical: space.md,
  },
  engineRowDivider: { borderBottomWidth: 1, borderBottomColor: colors.borderHairline },
  engineLeft: { flexDirection: "row", alignItems: "center", gap: 10, flex: 1 },
  engineDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    shadowOffset: { width: 0, height: 0 },
  },
  engineDotSm: { width: 6, height: 6, borderRadius: 3 },
  engineName: { color: colors.textPrimary, fontSize: 13, fontWeight: "700", letterSpacing: 1 },
  engineMeta: { color: colors.textTertiary, fontSize: 11 },
  engineRight: { flexDirection: "row", alignItems: "center", gap: 10, flex: 1, justifyContent: "flex-end" },
  engineBarTrack: {
    width: 80,
    height: 3,
    borderRadius: 2,
    backgroundColor: colors.borderSubtle,
    overflow: "hidden",
  },
  engineBarFill: { height: 3, borderRadius: 2 },
  engineAlloc: {
    color: colors.textPrimary,
    fontSize: 13,
    fontWeight: "700",
    fontVariant: ["tabular-nums"],
    width: 42,
    textAlign: "right",
  },

  topSignalSection: { gap: space.md },
  sectionHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "baseline",
  },
  sectionLabel: { color: colors.textPrimary, fontSize: 13, fontWeight: "700", letterSpacing: 1 },
  sectionLink: { ...typo.micro, color: colors.textTertiary },
  topSignal: {
    backgroundColor: colors.surface,
    borderRadius: 16,
    padding: space.md,
    gap: space.md,
  },
  topSignalHead: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  topSignalTagRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  topSignalEngine: { fontSize: 11, fontWeight: "800", letterSpacing: 1.5 },
  topSignalSep: { color: colors.textMuted },
  topSignalSide: { fontSize: 11, fontWeight: "800", letterSpacing: 1.5 },
  topSignalPrice: {
    color: colors.textTertiary,
    fontSize: 12,
    fontVariant: ["tabular-nums"],
  },
  topSignalMarket: { color: colors.textPrimary, fontSize: 17, fontWeight: "600" },
  topSignalMetrics: { flexDirection: "row", gap: space.lg },
  confTrack: { height: 2, backgroundColor: colors.borderSubtle, borderRadius: 1 },
  confFill: { height: 2, borderRadius: 1 },
  miniLabel: { ...typo.micro, color: colors.textTertiary },
  miniValue: {
    color: colors.textPrimary,
    fontSize: 14,
    fontWeight: "700",
    fontVariant: ["tabular-nums"],
    marginTop: 2,
  },

  allocSection: { gap: space.md },
  allocStats: { flexDirection: "row", gap: space.xxl },
  statBlock: { gap: 4 },
  statValue: {
    color: colors.textPrimary,
    fontSize: 18,
    fontWeight: "700",
    fontVariant: ["tabular-nums"],
  },
  deployBtn: {
    backgroundColor: colors.primary,
    height: 56,
    borderRadius: 14,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
  },
  deployText: {
    color: colors.primaryText,
    fontWeight: "800",
    letterSpacing: 1.2,
    fontSize: 13,
  },
});
