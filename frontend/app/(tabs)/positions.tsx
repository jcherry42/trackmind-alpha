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
import { colors, engineColor, space, type as typo } from "../../lib/theme";
import { MetricText, formatUsd, pnlColor } from "../../lib/format";
import { FadeSlideIn } from "../../components/Animated";

type Position = {
  id: string;
  engine: string;
  market: string;
  side: string;
  entry_price: number;
  current_price: number;
  exit_price?: number | null;
  contracts: number;
  notional: number;
  status: "open" | "closed";
  unrealized_pnl: number;
  realized_pnl?: number | null;
  expected_edge: number;
  confidence: number;
  opened_at: string;
};

export default function Positions() {
  const [open, setOpen] = useState<Position[]>([]);
  const [closed, setClosed] = useState<Position[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api<{ open: Position[]; closed: Position[] }>("/positions");
      setOpen(r.open);
      setClosed(r.closed);
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

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <Text style={styles.title}>Positions</Text>
        <View style={styles.headerCounts}>
          <Text style={styles.countOpen}>{open.length}</Text>
          <Text style={styles.countSep}>·</Text>
          <Text style={styles.countClosed}>{closed.length}</Text>
        </View>
      </View>

      <ScrollView
        contentContainerStyle={styles.scroll}
        refreshControl={
          <RefreshControl tintColor={colors.textTertiary} refreshing={loading} onRefresh={load} />
        }
      >
        <SectionLabel text="Open" />
        {open.length === 0 ? (
          <Text style={styles.empty}>No open positions yet.</Text>
        ) : (
          open.map((p, i) => (
            <FadeSlideIn key={p.id} delay={Math.min(i, 5) * 50} distance={8}>
              <PositionCard pos={p} />
            </FadeSlideIn>
          ))
        )}

        <SectionLabel text="Closed" />
        {closed.length === 0 ? (
          <Text style={styles.empty}>No closed positions.</Text>
        ) : (
          closed.map((p, i) => (
            <FadeSlideIn key={p.id} delay={Math.min(i, 5) * 50} distance={8}>
              <PositionCard pos={p} />
            </FadeSlideIn>
          ))
        )}
        <View style={{ height: space.xl }} />
      </ScrollView>
    </SafeAreaView>
  );
}

function SectionLabel({ text }: { text: string }) {
  return <Text style={styles.sectionLabel}>{text}</Text>;
}

function PositionCard({ pos }: { pos: Position }) {
  const c = engineColor(pos.engine);
  const pnl = pos.status === "open" ? pos.unrealized_pnl : pos.realized_pnl ?? 0;
  const pnlPct = pos.notional > 0 ? pnl / pos.notional : 0;
  return (
    <View testID={`position-${pos.id}`} style={styles.card}>
      <View style={styles.cardHead}>
        <View style={styles.cardHeadLeft}>
          <View style={[styles.engineDot, { backgroundColor: c }]} />
          <Text style={[styles.engineName, { color: c }]}>{pos.engine.toUpperCase()}</Text>
          <Text style={styles.sep}>·</Text>
          <Text
            style={[
              styles.side,
              { color: pos.side === "NO" ? colors.negative : colors.positive },
            ]}
          >
            {pos.side}
          </Text>
          {pos.status === "closed" && (
            <>
              <Text style={styles.sep}>·</Text>
              <Text style={styles.closedTag}>CLOSED</Text>
            </>
          )}
        </View>
        <Text style={styles.notional}>{formatUsd(pos.notional, { decimals: 0 })}</Text>
      </View>
      <Text style={styles.market} numberOfLines={2}>
        {pos.market}
      </Text>
      <View style={styles.cardBottom}>
        <View>
          <MetricText size={26} color={pnlColor(pnl)}>
            {formatUsd(pnl, { sign: true })}
          </MetricText>
          <Text style={[styles.pnlPct, { color: pnlColor(pnl) }]}>
            {pnlPct >= 0 ? "+" : ""}
            {(pnlPct * 100).toFixed(2)}%
          </Text>
        </View>
        <View style={styles.priceCol}>
          <Text style={styles.priceLabel}>entry → {pos.status === "open" ? "mark" : "exit"}</Text>
          <Text style={styles.priceVal}>
            {pos.entry_price.toFixed(3)} →{" "}
            {(pos.status === "open" ? pos.current_price : pos.exit_price ?? 0).toFixed(3)}
          </Text>
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.bg },
  header: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "baseline",
    paddingHorizontal: space.lg,
    paddingTop: space.md,
  },
  title: { ...typo.h2, color: colors.textPrimary },
  headerCounts: { flexDirection: "row", alignItems: "center", gap: 6 },
  countOpen: { ...typo.metric, fontSize: 18, color: colors.positive },
  countClosed: { ...typo.metric, fontSize: 18, color: colors.textTertiary },
  countSep: { color: colors.textMuted },
  scroll: { paddingHorizontal: space.lg, paddingTop: space.md, width: "100%", maxWidth: 760, alignSelf: "center" },
  sectionLabel: {
    ...typo.micro,
    color: colors.textTertiary,
    marginTop: space.lg,
    marginBottom: space.sm,
  },
  empty: { color: colors.textMuted, fontSize: 13 },
  card: {
    backgroundColor: colors.surface,
    borderRadius: 16,
    padding: space.md,
    marginBottom: space.sm,
    gap: space.sm,
  },
  cardHead: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  cardHeadLeft: { flexDirection: "row", alignItems: "center", gap: 6 },
  engineDot: { width: 6, height: 6, borderRadius: 3 },
  engineName: { fontSize: 10, fontWeight: "800", letterSpacing: 1.4 },
  sep: { color: colors.textMuted, fontSize: 11 },
  side: { fontSize: 10, fontWeight: "800", letterSpacing: 1.2 },
  closedTag: { color: colors.textTertiary, fontSize: 10, fontWeight: "700", letterSpacing: 1.2 },
  notional: {
    color: colors.textSecondary,
    fontSize: 13,
    fontWeight: "600",
    fontVariant: ["tabular-nums"],
  },
  market: { color: colors.textPrimary, fontSize: 16, fontWeight: "600" },
  cardBottom: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "flex-end",
    marginTop: space.sm,
  },
  pnlPct: { fontSize: 13, fontWeight: "600", marginTop: 2 },
  priceCol: { alignItems: "flex-end" },
  priceLabel: { ...typo.micro, color: colors.textTertiary },
  priceVal: {
    color: colors.textSecondary,
    fontSize: 13,
    fontVariant: ["tabular-nums"],
    marginTop: 2,
  },
});
