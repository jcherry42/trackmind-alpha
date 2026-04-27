import React, { useCallback, useState } from "react";
import {
  Alert,
  FlatList,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { useFocusEffect } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { Feather } from "@expo/vector-icons";
import { api } from "../../lib/api";
import { colors, engineColor, radius, space, type as typo } from "../../lib/theme";
import { Card } from "../../components/Card";
import { Tag } from "../../components/Tag";
import { ProgressBar } from "../../components/ProgressBar";
import { MetricText } from "../../lib/format";

type Signal = {
  id: string;
  engine: string;
  market: string;
  side: string;
  price: number;
  confidence: number;
  expected_edge: number;
  risk: number;
  liquidity: number;
  size_hint: number;
  rationale: string;
  catalysts?: string[];
};

const FILTERS = ["all", "alpha", "quant", "whale", "micro"] as const;

export default function Signals() {
  const [signals, setSignals] = useState<Signal[]>([]);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState<typeof FILTERS[number]>("all");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api<{ signals: Signal[] }>("/signals");
      setSignals(r.signals);
    } catch (e: any) {
      Alert.alert("Error", e?.message || "Failed to load signals");
    } finally {
      setLoading(false);
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      load();
      const id = setInterval(load, 30000);
      return () => clearInterval(id);
    }, [load])
  );

  async function approve(sig: Signal) {
    try {
      await api("/trade/approve", { method: "POST", body: { signal_id: sig.id } });
      Alert.alert("Approved", `Position opened on ${sig.market}.`);
      await load();
    } catch (e: any) {
      Alert.alert("Failed", e?.message || "Try again");
    }
  }

  const filtered = filter === "all" ? signals : signals.filter((s) => s.engine === filter);

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <Text style={styles.title}>SIGNALS</Text>
        <Text style={styles.subtle}>{filtered.length} active</Text>
      </View>

      <View style={styles.filters}>
        {FILTERS.map((f) => {
          const active = filter === f;
          const c = f === "all" ? colors.textPrimary : engineColor(f);
          return (
            <Pressable
              key={f}
              testID={`filter-${f}`}
              onPress={() => setFilter(f)}
              style={[
                styles.filterChip,
                active && { borderColor: c, backgroundColor: `${c}20` },
              ]}
            >
              <Text
                style={[
                  styles.filterText,
                  { color: active ? c : colors.textSecondary },
                ]}
              >
                {f.toUpperCase()}
              </Text>
            </Pressable>
          );
        })}
      </View>

      <FlatList
        contentContainerStyle={styles.list}
        data={filtered}
        keyExtractor={(s) => s.id}
        refreshControl={
          <RefreshControl tintColor={colors.textSecondary} refreshing={loading} onRefresh={load} />
        }
        ListEmptyComponent={
          <Text style={[styles.subtle, { textAlign: "center", marginTop: 40 }]}>
            No signals from this engine. The allocator is monitoring.
          </Text>
        }
        renderItem={({ item }) => <SignalRow sig={item} onApprove={() => approve(item)} />}
        ItemSeparatorComponent={() => <View style={{ height: space.sm }} />}
      />
    </SafeAreaView>
  );
}

function SignalRow({ sig, onApprove }: { sig: Signal; onApprove: () => void }) {
  const c = engineColor(sig.engine);
  return (
    <Card
      testID={`signal-row-${sig.id}`}
      style={{ borderLeftWidth: 3, borderLeftColor: c }}
    >
      <View style={styles.rowTop}>
        <Tag label={sig.engine.toUpperCase()} color={c} />
        <Tag label={sig.side} color={sig.side === "NO" ? colors.negative : colors.positive} />
        <Text style={[styles.subtle, { marginLeft: "auto" }]}>
          @ {sig.price.toFixed(3)}
        </Text>
      </View>
      <Text style={styles.market}>{sig.market}</Text>
      <Text style={styles.subtle}>{sig.rationale}</Text>
      <View style={styles.metrics}>
        <View style={styles.metricBlock}>
          <Text style={styles.metricLabel}>CONF</Text>
          <MetricText size={15}>{(sig.confidence * 100).toFixed(0)}%</MetricText>
        </View>
        <View style={styles.metricBlock}>
          <Text style={styles.metricLabel}>EDGE</Text>
          <MetricText size={15}>{(sig.expected_edge * 100).toFixed(2)}%</MetricText>
        </View>
        <View style={styles.metricBlock}>
          <Text style={styles.metricLabel}>RISK</Text>
          <MetricText size={15}>{(sig.risk * 100).toFixed(2)}%</MetricText>
        </View>
        <View style={styles.metricBlock}>
          <Text style={styles.metricLabel}>SIZE</Text>
          <MetricText size={15}>{(sig.size_hint * 100).toFixed(0)}%</MetricText>
        </View>
      </View>
      <ProgressBar value={sig.confidence} color={c} />
      <View style={styles.actions}>
        <Pressable
          testID={`approve-${sig.id}`}
          style={styles.approveBtn}
          onPress={onApprove}
        >
          <Feather name="zap" size={14} color={colors.primaryText} />
          <Text style={styles.approveText}>APPROVE</Text>
        </Pressable>
      </View>
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
    alignItems: "baseline",
    justifyContent: "space-between",
  },
  title: { ...typo.h2, color: colors.textPrimary, letterSpacing: 1.5 },
  subtle: { color: colors.textSecondary, fontSize: 12 },
  filters: {
    flexDirection: "row",
    paddingHorizontal: space.md,
    gap: space.sm,
    marginVertical: space.sm,
  },
  filterChip: {
    paddingHorizontal: 14,
    paddingVertical: 6,
    borderRadius: radius.pill,
    borderWidth: 1,
    borderColor: colors.borderSubtle,
    backgroundColor: colors.surface,
  },
  filterText: { ...typo.micro },
  list: { padding: space.md, gap: space.sm, paddingBottom: 60 },
  rowTop: { flexDirection: "row", alignItems: "center", gap: 6 },
  market: { color: colors.textPrimary, fontSize: 16, fontWeight: "600", marginTop: 8 },
  metrics: { flexDirection: "row", marginTop: space.md, marginBottom: space.sm, gap: space.lg },
  metricBlock: { gap: 2 },
  metricLabel: { ...typo.micro, color: colors.textSecondary },
  actions: { marginTop: space.md, alignItems: "flex-end" },
  approveBtn: {
    flexDirection: "row",
    backgroundColor: colors.primary,
    paddingHorizontal: 18,
    paddingVertical: 10,
    borderRadius: radius.md,
    alignItems: "center",
    gap: 6,
  },
  approveText: { color: colors.primaryText, fontWeight: "800", letterSpacing: 1 },
});
