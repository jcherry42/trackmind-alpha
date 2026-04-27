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
import { colors, engineColor, hexAlpha, space, type as typo } from "../../lib/theme";

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
};

const FILTERS = ["all", "alpha", "quant", "whale", "micro"] as const;

export default function Signals() {
  const [signals, setSignals] = useState<Signal[]>([]);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState<typeof FILTERS[number]>("all");
  const [expanded, setExpanded] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api<{ signals: Signal[] }>("/signals");
      setSignals(r.signals);
    } catch (e: any) {
      Alert.alert("Error", e?.message || "Failed");
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
        <Text style={styles.title}>Signals</Text>
        <Text style={styles.count}>{filtered.length}</Text>
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
                active && { borderColor: c, backgroundColor: hexAlpha(c, 0.1) },
              ]}
            >
              <Text
                style={[
                  styles.filterText,
                  { color: active ? c : colors.textTertiary },
                ]}
              >
                {f}
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
          <RefreshControl tintColor={colors.textTertiary} refreshing={loading} onRefresh={load} />
        }
        ListEmptyComponent={
          <Text style={styles.empty}>No signals from this engine.</Text>
        }
        renderItem={({ item }) => (
          <SignalRow
            sig={item}
            expanded={expanded === item.id}
            onToggle={() => setExpanded(expanded === item.id ? null : item.id)}
            onApprove={() => approve(item)}
          />
        )}
      />
    </SafeAreaView>
  );
}

function SignalRow({
  sig,
  expanded,
  onToggle,
  onApprove,
}: {
  sig: Signal;
  expanded: boolean;
  onToggle: () => void;
  onApprove: () => void;
}) {
  const c = engineColor(sig.engine);
  return (
    <Pressable testID={`signal-row-${sig.id}`} onPress={onToggle} style={styles.row}>
      <View style={styles.rowMain}>
        <View style={[styles.engineDot, { backgroundColor: c }]} />
        <View style={{ flex: 1 }}>
          <Text style={styles.rowMarket} numberOfLines={1}>
            {sig.market}
          </Text>
          <View style={styles.rowMeta}>
            <Text style={[styles.rowEngine, { color: c }]}>
              {sig.engine.toUpperCase()}
            </Text>
            <Text style={styles.rowSep}>·</Text>
            <Text
              style={[
                styles.rowSide,
                { color: sig.side === "NO" ? colors.negative : colors.positive },
              ]}
            >
              {sig.side}
            </Text>
            <Text style={styles.rowSep}>·</Text>
            <Text style={styles.rowMetric}>
              edge {(sig.expected_edge * 100).toFixed(2)}%
            </Text>
          </View>
        </View>
        <View style={styles.confColumn}>
          <Text style={styles.confValue}>{(sig.confidence * 100).toFixed(0)}%</Text>
          <View style={styles.confTrack}>
            <View
              style={[
                styles.confFill,
                { width: `${sig.confidence * 100}%`, backgroundColor: c },
              ]}
            />
          </View>
        </View>
      </View>

      {expanded ? (
        <View style={styles.expanded}>
          <Text style={styles.rationale}>{sig.rationale}</Text>
          <View style={styles.expandedRow}>
            <Stat label="conf" value={`${(sig.confidence * 100).toFixed(0)}%`} />
            <Stat label="edge" value={`${(sig.expected_edge * 100).toFixed(2)}%`} />
            <Stat label="risk" value={`${(sig.risk * 100).toFixed(2)}%`} />
            <Stat label="size" value={`${(sig.size_hint * 100).toFixed(0)}%`} />
            <Stat label="@" value={sig.price.toFixed(3)} />
          </View>
          <Pressable
            testID={`approve-${sig.id}`}
            onPress={onApprove}
            style={({ pressed }) => [styles.approve, pressed && { opacity: 0.85 }]}
          >
            <Text style={styles.approveText}>APPROVE</Text>
            <Feather name="arrow-up-right" size={14} color={colors.primaryText} />
          </Pressable>
        </View>
      ) : null}
    </Pressable>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <View>
      <Text style={styles.statLabel}>{label}</Text>
      <Text style={styles.statValue}>{value}</Text>
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
  count: { ...typo.metric, fontSize: 22, color: colors.textTertiary },
  filters: {
    flexDirection: "row",
    paddingHorizontal: space.lg,
    gap: space.sm,
    marginTop: space.md,
    marginBottom: space.md,
  },
  filterChip: {
    paddingHorizontal: 14,
    paddingVertical: 6,
    borderRadius: 20,
    borderWidth: 1,
    borderColor: colors.borderSubtle,
  },
  filterText: { fontSize: 11, fontWeight: "600", letterSpacing: 1.2, textTransform: "uppercase" },
  list: { paddingHorizontal: space.lg, paddingBottom: 80 },
  empty: { color: colors.textTertiary, textAlign: "center", marginTop: space.xxl },
  row: {
    paddingVertical: space.md,
    borderBottomWidth: 1,
    borderBottomColor: colors.borderHairline,
  },
  rowMain: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.md,
  },
  engineDot: { width: 8, height: 8, borderRadius: 4 },
  rowMarket: { color: colors.textPrimary, fontSize: 15, fontWeight: "600" },
  rowMeta: { flexDirection: "row", alignItems: "center", gap: 6, marginTop: 4 },
  rowEngine: { fontSize: 10, fontWeight: "800", letterSpacing: 1.4 },
  rowSep: { color: colors.textMuted, fontSize: 11 },
  rowSide: { fontSize: 10, fontWeight: "800", letterSpacing: 1.2 },
  rowMetric: { color: colors.textTertiary, fontSize: 11, fontVariant: ["tabular-nums"] },
  confColumn: { alignItems: "flex-end", gap: 4, width: 56 },
  confValue: { color: colors.textPrimary, fontSize: 13, fontWeight: "700", fontVariant: ["tabular-nums"] },
  confTrack: { width: 48, height: 2, backgroundColor: colors.borderSubtle, borderRadius: 1 },
  confFill: { height: 2, borderRadius: 1 },
  expanded: { marginTop: space.md, gap: space.md },
  rationale: { color: colors.textSecondary, fontSize: 13, lineHeight: 18 },
  expandedRow: {
    flexDirection: "row",
    justifyContent: "space-between",
  },
  statLabel: { ...typo.micro, color: colors.textTertiary },
  statValue: {
    color: colors.textPrimary,
    fontSize: 14,
    fontWeight: "700",
    fontVariant: ["tabular-nums"],
    marginTop: 2,
  },
  approve: {
    backgroundColor: colors.primary,
    height: 44,
    borderRadius: 12,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
  },
  approveText: { color: colors.primaryText, fontWeight: "800", letterSpacing: 1.2, fontSize: 12 },
});
