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
import { Tag } from "../../components/Tag";
import { ProgressBar } from "../../components/ProgressBar";
import { MetricText, formatUsd, pnlColor } from "../../lib/format";
import { colors, engineColor, space, type as typo } from "../../lib/theme";

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
  closed_at?: string | null;
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
        <Text style={styles.title}>POSITIONS</Text>
        <Text style={styles.subtle}>
          {open.length} open · {closed.length} closed
        </Text>
      </View>

      <ScrollView
        contentContainerStyle={styles.scroll}
        refreshControl={
          <RefreshControl tintColor={colors.textSecondary} refreshing={loading} onRefresh={load} />
        }
      >
        <Text style={styles.sectionLabel}>OPEN</Text>
        {open.length === 0 ? (
          <Card>
            <Text style={styles.subtle}>No open positions. Approve a signal to deploy capital.</Text>
          </Card>
        ) : (
          open.map((p) => <PositionCard key={p.id} pos={p} />)
        )}

        <Text style={[styles.sectionLabel, { marginTop: space.lg }]}>CLOSED</Text>
        {closed.length === 0 ? (
          <Card>
            <Text style={styles.subtle}>No closed positions yet.</Text>
          </Card>
        ) : (
          closed.map((p) => <PositionCard key={p.id} pos={p} />)
        )}

        <View style={{ height: space.xl }} />
      </ScrollView>
    </SafeAreaView>
  );
}

function PositionCard({ pos }: { pos: Position }) {
  const c = engineColor(pos.engine);
  const pnl = pos.status === "open" ? pos.unrealized_pnl : pos.realized_pnl ?? 0;
  const pnlPct = pos.notional > 0 ? pnl / pos.notional : 0;
  return (
    <Card
      testID={`position-${pos.id}`}
      style={[styles.card, { borderLeftWidth: 3, borderLeftColor: c }]}
    >
      <View style={styles.rowTop}>
        <Tag label={pos.engine.toUpperCase()} color={c} />
        <Tag label={pos.side} color={pos.side === "NO" ? colors.negative : colors.positive} />
        <Tag
          label={pos.status.toUpperCase()}
          color={pos.status === "open" ? colors.positive : colors.textSecondary}
        />
      </View>
      <Text style={styles.market}>{pos.market}</Text>

      <View style={styles.metricsRow}>
        <View>
          <Text style={styles.label}>ENTRY</Text>
          <MetricText size={16}>{pos.entry_price.toFixed(3)}</MetricText>
        </View>
        <View>
          <Text style={styles.label}>{pos.status === "open" ? "MARK" : "EXIT"}</Text>
          <MetricText size={16}>
            {(pos.status === "open" ? pos.current_price : pos.exit_price ?? 0).toFixed(3)}
          </MetricText>
        </View>
        <View>
          <Text style={styles.label}>NOTIONAL</Text>
          <MetricText size={16}>{formatUsd(pos.notional)}</MetricText>
        </View>
        <View>
          <Text style={styles.label}>P&amp;L</Text>
          <MetricText size={18} color={pnlColor(pnl)}>
            {formatUsd(pnl, { sign: true })}
          </MetricText>
        </View>
      </View>

      <View style={styles.bottomRow}>
        <Text style={styles.subtle}>
          {pos.contracts.toFixed(2)} contracts · edge {(pos.expected_edge * 100).toFixed(2)}% · conf{" "}
          {(pos.confidence * 100).toFixed(0)}%
        </Text>
        <Text style={[styles.subtle, { color: pnlColor(pnl) }]}>
          {pnlPct >= 0 ? "+" : ""}
          {(pnlPct * 100).toFixed(2)}%
        </Text>
      </View>

      {pos.status === "open" ? (
        <ProgressBar
          value={Math.min(1, Math.abs(pos.current_price - pos.entry_price) * 10)}
          color={c}
        />
      ) : null}
    </Card>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.bg },
  header: {
    paddingHorizontal: space.md,
    paddingTop: space.sm,
    paddingBottom: space.xs,
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "baseline",
  },
  title: { ...typo.h2, color: colors.textPrimary, letterSpacing: 1.5 },
  subtle: { color: colors.textSecondary, fontSize: 12 },
  scroll: { padding: space.md, gap: space.sm },
  sectionLabel: { ...typo.micro, color: colors.textSecondary, marginTop: space.sm },
  card: { gap: space.sm },
  rowTop: { flexDirection: "row", gap: 6 },
  market: { color: colors.textPrimary, fontSize: 16, fontWeight: "600" },
  metricsRow: { flexDirection: "row", justifyContent: "space-between", marginTop: space.sm },
  label: { ...typo.micro, color: colors.textSecondary },
  bottomRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginTop: space.sm,
  },
});
