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
import { Card } from "../../components/Card";
import { MetricText } from "../../lib/format";
import { colors, radius, space, type as typo } from "../../lib/theme";

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
          <Text style={styles.title}>MICRO MODE</Text>
          <Text style={styles.subtle}>Cross-venue YES + NO &lt; $1.00 scanner</Text>
        </View>
        <Text style={styles.count}>{signals.length}</Text>
      </View>

      <FlatList
        contentContainerStyle={styles.list}
        data={signals}
        keyExtractor={(s) => s.id}
        refreshControl={
          <RefreshControl tintColor={colors.textSecondary} refreshing={loading} onRefresh={load} />
        }
        ListEmptyComponent={
          <Card style={{ marginTop: space.md }}>
            <Text style={styles.subtle}>
              No arbitrage opportunities right now. Spreads will return as venue prices drift apart.
            </Text>
          </Card>
        }
        renderItem={({ item }) => <ArbCard sig={item} onExecute={() => execute(item)} />}
        ItemSeparatorComponent={() => <View style={{ height: space.sm }} />}
      />
    </SafeAreaView>
  );
}

function ArbCard({ sig, onExecute }: { sig: MicroSig; onExecute: () => void }) {
  const cost = sig.legs.yes.price + sig.legs.no.price;
  return (
    <Card
      testID={`arb-${sig.id}`}
      style={{ borderLeftWidth: 3, borderLeftColor: colors.micro }}
    >
      <View style={styles.legsRow}>
        <Leg
          label={sig.legs.yes.venue}
          side="YES"
          price={sig.legs.yes.price}
          market={sig.legs.yes.market}
          color={colors.positive}
          testID="arb-leg-yes"
        />
        <View style={styles.spreadCol}>
          <Feather name="git-merge" size={20} color={colors.micro} />
          <MetricText size={20} color={colors.micro}>
            {cost.toFixed(3)}
          </MetricText>
          <Text style={[styles.subtle, { color: colors.micro }]}>SUM</Text>
        </View>
        <Leg
          label={sig.legs.no.venue}
          side="NO"
          price={sig.legs.no.price}
          market={sig.legs.no.market}
          color={colors.negative}
          testID="arb-leg-no"
        />
      </View>

      <View style={styles.metricsRow}>
        <View>
          <Text style={styles.label}>EDGE</Text>
          <MetricText size={16} color={colors.positive}>
            +{(sig.expected_edge * 100).toFixed(2)}%
          </MetricText>
        </View>
        <View>
          <Text style={styles.label}>CONF</Text>
          <MetricText size={16}>{(sig.confidence * 100).toFixed(0)}%</MetricText>
        </View>
        <View>
          <Text style={styles.label}>LIQ</Text>
          <MetricText size={16}>{(sig.liquidity * 100).toFixed(0)}%</MetricText>
        </View>
        <View>
          <Text style={styles.label}>SIZE</Text>
          <MetricText size={16}>{(sig.size_hint * 100).toFixed(0)}%</MetricText>
        </View>
      </View>
      <Text style={styles.subtle}>{sig.rationale}</Text>
      <Pressable
        testID={`arb-execute-${sig.id}`}
        style={styles.execBtn}
        onPress={onExecute}
      >
        <Feather name="zap" size={14} color={colors.primaryText} />
        <Text style={styles.execText}>EXECUTE ARB</Text>
      </Pressable>
    </Card>
  );
}

function Leg({
  label,
  side,
  price,
  market,
  color,
  testID,
}: {
  label: string;
  side: string;
  price: number;
  market: string;
  color: string;
  testID?: string;
}) {
  return (
    <View testID={testID} style={styles.legCol}>
      <Text style={[styles.label, { color }]}>{label.toUpperCase()}</Text>
      <Text style={[styles.legSide, { color }]}>{side}</Text>
      <MetricText size={20}>{price.toFixed(3)}</MetricText>
      <Text numberOfLines={2} style={[styles.subtle, { textAlign: "center" }]}>
        {market}
      </Text>
    </View>
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
    alignItems: "center",
  },
  title: { ...typo.h2, color: colors.textPrimary, letterSpacing: 1.5 },
  subtle: { color: colors.textSecondary, fontSize: 12, marginTop: 2 },
  count: { ...typo.h2, color: colors.micro },
  list: { padding: space.md, gap: space.sm, paddingBottom: 60 },
  legsRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    gap: space.sm,
  },
  legCol: { flex: 1, alignItems: "center", gap: 4 },
  legSide: { fontWeight: "800", letterSpacing: 1 },
  spreadCol: { alignItems: "center", paddingHorizontal: 6, gap: 4 },
  metricsRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    marginTop: space.md,
    marginBottom: space.sm,
  },
  label: { ...typo.micro, color: colors.textSecondary },
  execBtn: {
    flexDirection: "row",
    backgroundColor: colors.primary,
    paddingVertical: 10,
    borderRadius: radius.md,
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    marginTop: space.sm,
  },
  execText: { color: colors.primaryText, fontWeight: "800", letterSpacing: 1 },
});
