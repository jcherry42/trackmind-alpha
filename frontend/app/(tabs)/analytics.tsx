import React, { useCallback, useState } from "react";
import {
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { useFocusEffect } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { api } from "../../lib/api";
import { Sparkline } from "../../components/Sparkline";
import { MetricText, formatPct, formatUsd, pnlColor } from "../../lib/format";
import { colors, engineColor, space, type as typo } from "../../lib/theme";
import { FadeSlideIn } from "../../components/Animated";

type Analytics = {
  trades_total: number;
  wins: number;
  losses: number;
  win_rate: number;
  total_pnl: number;
  avg_win: number;
  avg_loss: number;
  max_drawdown: number;
  sharpe: number;
  equity_curve: number[];
  by_engine: Record<string, { trades: number; pnl: number; wins: number; win_rate: number }>;
};

export default function AnalyticsScreen() {
  const [data, setData] = useState<Analytics | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await api<Analytics>("/analytics"));
    } finally {
      setLoading(false);
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load])
  );

  const totalPnl = data?.total_pnl ?? 0;
  const startEquity = data?.equity_curve?.[0] ?? 0;
  const endEquity = data?.equity_curve?.[data?.equity_curve.length - 1] ?? 0;

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <ScrollView
        contentContainerStyle={styles.scroll}
        refreshControl={
          <RefreshControl tintColor={colors.textTertiary} refreshing={loading} onRefresh={load} />
        }
      >
        <View style={styles.header}>
          <Text style={styles.title}>Analytics</Text>
        </View>

        {/* Hero — equity curve */}
        <FadeSlideIn delay={60}>
        <View style={styles.heroCard}>
          <Text style={styles.heroLabel}>Total P&amp;L</Text>
          <MetricText size={48} color={pnlColor(totalPnl)} style={{ letterSpacing: -1.5 }}>
            {formatUsd(totalPnl, { sign: true })}
          </MetricText>
          <View style={styles.heroSubRow}>
            <Text style={styles.subText}>{data?.trades_total ?? 0} trades</Text>
            <View style={styles.subDot} />
            <Text style={styles.subText}>
              win {((data?.win_rate ?? 0) * 100).toFixed(0)}%
            </Text>
            <View style={styles.subDot} />
            <Text style={styles.subText}>sharpe {data?.sharpe ?? 0}</Text>
          </View>

          <View style={styles.sparkContainer}>
            {data && data.equity_curve.length > 1 ? (
              <Sparkline
                data={data.equity_curve}
                width={320}
                height={110}
                color={totalPnl >= 0 ? colors.positive : colors.negative}
                strokeWidth={2}
              />
            ) : (
              <Text style={styles.sparkEmpty}>Trade activity will populate the curve.</Text>
            )}
          </View>

          <View style={styles.curveFooter}>
            <Text style={styles.curveValue}>{formatUsd(startEquity, { decimals: 0 })}</Text>
            <Text style={styles.curveValue}>{formatUsd(endEquity, { decimals: 0 })}</Text>
          </View>
        </View>
        </FadeSlideIn>

        {/* Stats — three rows of two */}
        <FadeSlideIn delay={160}>
        <View style={styles.statGrid}>
          <Stat label="Avg win" value={formatUsd(data?.avg_win ?? 0)} color={colors.positive} />
          <Stat label="Avg loss" value={formatUsd(data?.avg_loss ?? 0)} color={colors.negative} />
          <Stat label="Max DD" value={formatPct(-(data?.max_drawdown ?? 0), { decimals: 2 })} color={colors.negative} />
          <Stat label="Trades" value={`${data?.trades_total ?? 0}`} />
        </View>
        </FadeSlideIn>

        {/* By engine */}
        <FadeSlideIn delay={260}>
        <Text style={styles.section}>By Engine</Text>
        <View style={styles.engineList}>
          {(["alpha", "quant", "whale", "micro"] as const).map((e, i) => {
            const stats = data?.by_engine[e];
            const c = engineColor(e);
            const pnl = stats?.pnl ?? 0;
            const totalAbs = Math.max(
              ...(["alpha", "quant", "whale", "micro"] as const).map(
                (k) => Math.abs(data?.by_engine[k]?.pnl ?? 0)
              ),
              1
            );
            return (
              <View key={e} style={[styles.engineRow, i < 3 && styles.engineRowDivider]}>
                <View style={styles.engineLeft}>
                  <View style={[styles.engineDot, { backgroundColor: c }]} />
                  <Text style={styles.engineName}>{e.toUpperCase()}</Text>
                  <Text style={styles.engineMeta}>
                    {stats?.trades ?? 0} · win{" "}
                    {((stats?.win_rate ?? 0) * 100).toFixed(0)}%
                  </Text>
                </View>
                <View style={styles.engineRight}>
                  <View style={styles.engineBarTrack}>
                    <View
                      style={[
                        styles.engineBarFill,
                        {
                          width: `${(Math.abs(pnl) / totalAbs) * 100}%`,
                          backgroundColor: c,
                        },
                      ]}
                    />
                  </View>
                  <Text style={[styles.enginePnl, { color: pnlColor(pnl) }]}>
                    {formatUsd(pnl, { sign: true, decimals: 0 })}
                  </Text>
                </View>
              </View>
            );
          })}
        </View>
        </FadeSlideIn>

        <View style={{ height: space.xl }} />
      </ScrollView>
    </SafeAreaView>
  );
}

function Stat({
  label,
  value,
  color = colors.textPrimary,
}: {
  label: string;
  value: string;
  color?: string;
}) {
  return (
    <View testID={`stat-${label.toLowerCase().replace(/[^a-z]/g, "-")}`} style={styles.statCell}>
      <Text style={styles.statLabel}>{label}</Text>
      <MetricText size={20} color={color}>{value}</MetricText>
    </View>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.bg },
  scroll: { paddingHorizontal: space.lg, paddingBottom: space.xl, paddingTop: space.md, gap: space.lg },
  header: { paddingTop: space.sm },
  title: { ...typo.h2, color: colors.textPrimary },
  heroCard: {
    backgroundColor: colors.surface,
    borderRadius: 18,
    padding: space.lg,
    gap: space.sm,
  },
  heroLabel: { ...typo.micro, color: colors.textTertiary },
  heroSubRow: { flexDirection: "row", alignItems: "center", gap: 8, marginTop: 4 },
  subText: { color: colors.textTertiary, fontSize: 12 },
  subDot: { width: 3, height: 3, borderRadius: 1.5, backgroundColor: colors.textMuted },
  sparkContainer: { marginTop: space.md, alignItems: "center" },
  sparkEmpty: { color: colors.textTertiary, fontSize: 13, paddingVertical: space.lg },
  curveFooter: {
    flexDirection: "row",
    justifyContent: "space-between",
    marginTop: 4,
  },
  curveValue: { color: colors.textTertiary, fontSize: 11, fontVariant: ["tabular-nums"] },

  statGrid: { flexDirection: "row", flexWrap: "wrap", gap: space.sm },
  statCell: {
    flexBasis: "48%",
    flexGrow: 1,
    backgroundColor: colors.surface,
    borderRadius: 14,
    padding: space.md,
    gap: 4,
  },
  statLabel: { ...typo.micro, color: colors.textTertiary },

  section: { ...typo.micro, color: colors.textTertiary, marginTop: space.sm },
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
  engineDot: { width: 6, height: 6, borderRadius: 3 },
  engineName: { color: colors.textPrimary, fontSize: 12, fontWeight: "800", letterSpacing: 1.4 },
  engineMeta: { color: colors.textTertiary, fontSize: 11 },
  engineRight: { flexDirection: "row", alignItems: "center", gap: 10 },
  engineBarTrack: {
    width: 60,
    height: 3,
    borderRadius: 2,
    backgroundColor: colors.borderSubtle,
    overflow: "hidden",
  },
  engineBarFill: { height: 3, borderRadius: 2 },
  enginePnl: {
    fontSize: 13,
    fontWeight: "700",
    fontVariant: ["tabular-nums"],
    width: 60,
    textAlign: "right",
  },
});
