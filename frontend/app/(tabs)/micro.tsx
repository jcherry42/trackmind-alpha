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
import { colors, hexAlpha, space, type as typo } from "../../lib/theme";

type MicroSig = {
  id: string;
  market: string;
  confidence: number;
  expected_edge: number;
  liquidity: number;
  size_hint: number;
  rationale: string;
  legs: {
    yes: { venue: string; price: number; market: string };
    no: { venue: string; price: number; market: string };
    fees: number;
  };
};

export default function MicroMode() {
  const [signals, setSignals] = useState<MicroSig[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api<{ signals: MicroSig[] }>("/signals/micro");
      setSignals(r.signals || []);
    } finally {
      setLoading(false);
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      load();
      const id = setInterval(load, 20000);
      return () => clearInterval(id);
    }, [load])
  );

  async function execute(sig: MicroSig) {
    try {
      await api("/trade/approve", { method: "POST", body: { signal_id: sig.id } });
      Alert.alert("Arb deployed", `Cross-venue arb opened on ${sig.market}`);
      await load();
    } catch (e: any) {
      Alert.alert("Failed", e?.message || "Try again");
    }
  }

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <View>
          <Text style={styles.title}>Micro</Text>
          <Text style={styles.tagline}>Cross-venue YES + NO &lt; $1.00</Text>
        </View>
        <Text style={styles.count}>{signals.length}</Text>
      </View>

      <FlatList
        contentContainerStyle={styles.list}
        data={signals}
        keyExtractor={(s) => s.id}
        refreshControl={
          <RefreshControl tintColor={colors.textTertiary} refreshing={loading} onRefresh={load} />
        }
        ListEmptyComponent={
          <Text style={styles.empty}>No arbitrage opportunities right now.</Text>
        }
        renderItem={({ item }) => <ArbCard sig={item} onExecute={() => execute(item)} />}
      />
    </SafeAreaView>
  );
}

function ArbCard({ sig, onExecute }: { sig: MicroSig; onExecute: () => void }) {
  const cost = sig.legs.yes.price + sig.legs.no.price;
  const edge = sig.expected_edge;
  return (
    <View testID={`arb-${sig.id}`} style={styles.card}>
      {/* Hero — Edge */}
      <View style={styles.hero}>
        <Text style={styles.heroLabel}>Net Edge</Text>
        <Text style={styles.heroEdge}>+{(edge * 100).toFixed(2)}%</Text>
        <Text style={styles.heroSub}>
          YES {sig.legs.yes.price.toFixed(3)} + NO {sig.legs.no.price.toFixed(3)} ={" "}
          {cost.toFixed(3)}
        </Text>
      </View>

      {/* Legs */}
      <View style={styles.legsRow}>
        <Leg
          label={sig.legs.yes.venue}
          side="YES"
          price={sig.legs.yes.price}
          accent={colors.positive}
          testID="arb-leg-yes"
        />
        <View style={styles.legSep}>
          <Feather name="git-merge" size={14} color={colors.micro} />
        </View>
        <Leg
          label={sig.legs.no.venue}
          side="NO"
          price={sig.legs.no.price}
          accent={colors.negative}
          testID="arb-leg-no"
        />
      </View>

      <Text style={styles.rationale}>{sig.rationale}</Text>

      <View style={styles.metaRow}>
        <Meta label="conf" value={`${(sig.confidence * 100).toFixed(0)}%`} />
        <Meta label="liq" value={`${(sig.liquidity * 100).toFixed(0)}%`} />
        <Meta label="size" value={`${(sig.size_hint * 100).toFixed(0)}%`} />
      </View>

      <Pressable
        testID={`arb-execute-${sig.id}`}
        onPress={onExecute}
        style={({ pressed }) => [styles.executeBtn, pressed && { opacity: 0.85 }]}
      >
        <Text style={styles.executeText}>EXECUTE ARB</Text>
        <Feather name="arrow-up-right" size={14} color={colors.primaryText} />
      </Pressable>
    </View>
  );
}

function Leg({
  label,
  side,
  price,
  accent,
  testID,
}: {
  label: string;
  side: string;
  price: number;
  accent: string;
  testID?: string;
}) {
  return (
    <View testID={testID} style={[styles.leg, { borderColor: hexAlpha(accent, 0.3) }]}>
      <Text style={[styles.legSide, { color: accent }]}>{side}</Text>
      <Text style={styles.legPrice}>{price.toFixed(3)}</Text>
      <Text style={styles.legVenue}>{label.toUpperCase()}</Text>
    </View>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <View>
      <Text style={styles.metaLabel}>{label}</Text>
      <Text style={styles.metaValue}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.bg },
  header: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "flex-start",
    paddingHorizontal: space.lg,
    paddingTop: space.md,
  },
  title: { ...typo.h2, color: colors.textPrimary },
  tagline: { color: colors.textTertiary, fontSize: 12, marginTop: 2 },
  count: { ...typo.metric, fontSize: 28, color: colors.micro },
  list: { paddingHorizontal: space.lg, paddingTop: space.md, paddingBottom: 80, gap: space.md },
  empty: { color: colors.textTertiary, textAlign: "center", marginTop: space.xxl },
  card: {
    backgroundColor: colors.surface,
    borderRadius: 18,
    padding: space.md,
    gap: space.md,
    marginBottom: space.md,
  },
  hero: { gap: 4 },
  heroLabel: { ...typo.micro, color: colors.textTertiary },
  heroEdge: {
    color: colors.positive,
    fontSize: 38,
    fontWeight: "800",
    letterSpacing: -1,
    fontVariant: ["tabular-nums"],
    marginTop: 2,
  },
  heroSub: {
    color: colors.textTertiary,
    fontSize: 12,
    fontVariant: ["tabular-nums"],
    marginTop: 2,
  },
  legsRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.sm,
  },
  leg: {
    flex: 1,
    borderWidth: 1,
    borderRadius: 12,
    padding: space.md,
    alignItems: "center",
    gap: 6,
  },
  legSide: { fontSize: 11, fontWeight: "800", letterSpacing: 1.5 },
  legPrice: {
    color: colors.textPrimary,
    fontSize: 22,
    fontWeight: "700",
    fontVariant: ["tabular-nums"],
  },
  legVenue: { color: colors.textTertiary, fontSize: 10, fontWeight: "700", letterSpacing: 1 },
  legSep: { width: 24, alignItems: "center" },
  rationale: { color: colors.textSecondary, fontSize: 12, lineHeight: 16 },
  metaRow: {
    flexDirection: "row",
    gap: space.xl,
  },
  metaLabel: { ...typo.micro, color: colors.textTertiary },
  metaValue: {
    color: colors.textPrimary,
    fontSize: 13,
    fontWeight: "700",
    fontVariant: ["tabular-nums"],
    marginTop: 2,
  },
  executeBtn: {
    backgroundColor: colors.primary,
    height: 48,
    borderRadius: 12,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
  },
  executeText: { color: colors.primaryText, fontWeight: "800", letterSpacing: 1.2, fontSize: 12 },
});
