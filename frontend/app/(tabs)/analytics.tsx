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
import { Card } from "../../components/Card";
import { Sparkline } from "../../components/Sparkline";
import { ProgressBar } from "../../components/ProgressBar";
import { MetricText, formatPct, formatUsd, pnlColor } from "../../lib/format";
import { colors, engineColor, space, type as typo } from "../../lib/theme";

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

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <Text style={styles.title}>ANALYTICS</Text>
      </View>
      <ScrollView
        contentContainerStyle={styles.scroll}
        refreshControl={
          <RefreshControl tintColor={colors.textSecondary} refreshing={loading} onRefresh={load} />
        }
      >
        <Card>
          <Text style={styles.metricLabel}>EQUITY CURVE</Text>
          {data && data.equity_curve.length > 1 ? (
            <View style={{ marginTop: space.sm }}>
              <Sparkline
                data={data.equity_curve}
                width={320}
                height={90}
                color={data.total_pnl >= 0 ? colors.positive : colors.negative}
                strokeWidth={2}
              />
              <View style={{ flexDirection: "row", justifyContent: "space-between", marginTop: space.sm }}>
                <Text style={styles.subtle}>
                  Start: {formatUsd(data.equity_curve[0] || 0)}
                </Text>
                <Text style={styles.subtle}>
                  Now: {formatUsd(data.equity_curve[data.equity_curve.length - 1] || 0)}
                </Text>
              </View>
            </View>
          ) : (
            <Text style={[styles.subtle, { marginTop: 6 }]}>
              Trade activity will populate the equity curve.
            </Text>
          )}
        </Card>

        <View style={styles.statsGrid}>
          <Stat
            label="TOTAL P&L"
            value={formatUsd(data?.total_pnl ?? 0, { sign: true })}
            color={pnlColor(data?.total_pnl ?? 0)}
          />
          <Stat
            label="WIN RATE"
            value={`${((data?.win_rate ?? 0) * 100).toFixed(1)}%`}
          />
          <Stat label="TRADES" value={`${data?.trades_total ?? 0}`} />
          <Stat label="SHARPE" value={`${data?.sharpe ?? 0}`} />
          <Stat
            label="MAX DD"
            value={formatPct(-(data?.max_drawdown ?? 0), { decimals: 2 })}
            color={colors.negative}
          />
          <Stat
            label="AVG WIN"
            value={formatUsd(data?.avg_win ?? 0)}
            color={colors.positive}
          />
        </View>

        <Text style={styles.sectionLabel}>BREAKDOWN BY ENGINE</Text>
        {(["alpha", "quant", "whale", "micro"] as const).map((e) => {
          const stats = data?.by_engine[e];
          const c = engineColor(e);
          const totalAbs = Math.max(
            ...(["alpha", "quant", "whale", "micro"] as const).map(
              (k) => Math.abs(data?.by_engine[k]?.pnl ?? 0)
            ),
            1
          );
          const pnl = stats?.pnl ?? 0;
          return (
            <Card key={e} style={{ borderLeftWidth: 3, borderLeftColor: c }}>
              <View style={styles.engineRow}>
                <View>
                  <Text style={[styles.engineName, { color: c }]}>{e.toUpperCase()}</Text>
                  <Text style={styles.subtle}>
                    {stats?.trades ?? 0} trades · win {((stats?.win_rate ?? 0) * 100).toFixed(0)}%
                  </Text>
                </View>
                <MetricText size={18} color={pnlColor(pnl)}>
                  {formatUsd(pnl, { sign: true })}
                </MetricText>
              </View>
              <ProgressBar value={Math.abs(pnl) / totalAbs} color={c} />
            </Card>
          );
        })}

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
    <View testID={`stat-${label.toLowerCase().replace(/[^a-z]/g, "-")}`} style={styles.statCard}>
      <Text style={styles.metricLabel}>{label}</Text>
      <MetricText size={18} color={color}>{value}</MetricText>
    </View>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.bg },
  header: { paddingHorizontal: space.md, paddingTop: space.sm },
  title: { ...typo.h2, color: colors.textPrimary, letterSpacing: 1.5 },
  scroll: { padding: space.md, gap: space.sm, paddingBottom: 60 },
  metricLabel: { ...typo.micro, color: colors.textSecondary },
  subtle: { color: colors.textSecondary, fontSize: 12 },
  statsGrid: { flexDirection: "row", flexWrap: "wrap", gap: space.sm },
  statCard: {
    flexBasis: "31%",
    flexGrow: 1,
    borderWidth: 1,
    borderColor: colors.borderSubtle,
    borderRadius: 12,
    padding: space.sm,
    backgroundColor: colors.surface,
    gap: 4,
  },
  sectionLabel: { ...typo.micro, color: colors.textSecondary, marginTop: space.md },
  engineRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: space.sm,
  },
  engineName: { ...typo.micro, fontSize: 12 },
});
