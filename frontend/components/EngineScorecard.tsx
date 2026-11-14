import React, { useCallback, useState } from "react";
import { StyleSheet, Text, View } from "react-native";
import { useFocusEffect } from "expo-router";
import { api } from "../lib/api";
import { colors, engineColor, space, type as typo } from "../lib/theme";

type EngineScore = {
  n_resolved: number;
  n_pending: number;
  n_scored?: number;
  hit_rate?: number;
  brier?: number;
  brier_market?: number;
  skill_vs_market?: number | null;
  realized_pnl_per_contract?: number;
  realized_pnl_se?: number;
  claimed_edge?: number;
  verdict: string;
};

type Report = {
  source: "live" | "mock";
  days: number;
  min_sample_for_verdict: number;
  engines: Record<string, EngineScore>;
};

const ENGINES = ["alpha", "quant", "whale", "micro"] as const;

function cents(x: number | undefined): string {
  if (x === undefined || x === null) return "—";
  const v = x * 100;
  return `${v >= 0 ? "+" : ""}${v.toFixed(1)}¢`;
}

function verdictColor(v: string): string {
  if (v.startsWith("profitable")) return colors.positive;
  if (v.startsWith("losing")) return colors.negative;
  return colors.textTertiary;
}

/** Did each engine's calls come true? Scored on resolved markets (see backend/scorecard.py). */
export function EngineScorecard() {
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setReport(await api<Report>("/analytics/engines"));
      setError(null);
    } catch (e: any) {
      setError(e?.message || "Couldn't load scorecard");
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load])
  );

  return (
    <View testID="engine-scorecard">
      <Text style={styles.section}>Engine Scorecard · last {report?.days ?? 30}d</Text>
      {report?.source === "mock" ? (
        <Text testID="scorecard-demo-note" style={styles.note}>
          DEMO DATA · outcomes are simulated from market prices, so these numbers show the
          engines have no real edge on demo markets. Turn on live data for a real test.
        </Text>
      ) : null}
      {error ? <Text style={styles.note}>{error}</Text> : null}
      <View style={styles.list}>
        {ENGINES.map((e, i) => {
          const s = report?.engines?.[e];
          const pnl = s?.realized_pnl_per_contract;
          const se = s?.realized_pnl_se ?? 0;
          return (
            <View key={e} testID={`scorecard-${e}`} style={[styles.row, i < 3 && styles.divider]}>
              <View style={styles.rowTop}>
                <View style={[styles.dot, { backgroundColor: engineColor(e) }]} />
                <Text style={styles.name}>{e.toUpperCase()}</Text>
                <Text style={[styles.verdict, { color: verdictColor(s?.verdict ?? "") }]} numberOfLines={2}>
                  {s?.verdict ?? "—"}
                </Text>
              </View>
              <View style={styles.metrics}>
                <Metric label="resolved" value={`${s?.n_resolved ?? 0}`} sub={`${s?.n_pending ?? 0} pending`} />
                <Metric
                  label="realized / $1"
                  value={cents(pnl)}
                  sub={pnl !== undefined ? `±${(2 * se * 100).toFixed(1)}¢` : undefined}
                  color={pnl === undefined ? undefined : pnl >= 0 ? colors.positive : colors.negative}
                />
                <Metric label="claimed" value={cents(s?.claimed_edge)} />
                <Metric
                  label="skill vs mkt"
                  value={
                    s?.skill_vs_market === undefined || s?.skill_vs_market === null
                      ? "—"
                      : `${(s.skill_vs_market * 100).toFixed(1)}%`
                  }
                  sub={s?.hit_rate !== undefined ? `hit ${(s.hit_rate * 100).toFixed(0)}%` : undefined}
                />
              </View>
            </View>
          );
        })}
      </View>
      <Text style={styles.footnote}>
        Realized = average profit per $1 contract at the price the engine first flagged, after fees
        (±2 standard errors). Skill compares the engine's probabilities with the market's own odds;
        above 0% means it beat the market. Verdicts need {report?.min_sample_for_verdict ?? 30}+ resolved calls.
      </Text>
    </View>
  );
}

function Metric({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <View style={styles.metric}>
      <Text style={styles.metricLabel}>{label}</Text>
      <Text style={[styles.metricValue, color ? { color } : null]}>{value}</Text>
      {sub ? <Text style={styles.metricSub}>{sub}</Text> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  section: { ...typo.micro, color: colors.textTertiary, marginTop: space.sm, marginBottom: space.sm },
  note: { color: colors.textTertiary, fontSize: 11, lineHeight: 16, marginBottom: space.sm },
  list: { backgroundColor: colors.surface, borderRadius: 16, paddingHorizontal: space.md },
  row: { paddingVertical: space.md, gap: space.sm },
  divider: { borderBottomWidth: 1, borderBottomColor: colors.borderHairline },
  rowTop: { flexDirection: "row", alignItems: "center", gap: 10 },
  dot: { width: 6, height: 6, borderRadius: 3 },
  name: { color: colors.textPrimary, fontSize: 12, fontWeight: "800", letterSpacing: 1.4, width: 56 },
  verdict: { flex: 1, fontSize: 11, textAlign: "right" },
  metrics: { flexDirection: "row", justifyContent: "space-between" },
  metric: { flex: 1, gap: 2 },
  metricLabel: { color: colors.textTertiary, fontSize: 9, letterSpacing: 1, textTransform: "uppercase" },
  metricValue: { color: colors.textPrimary, fontSize: 14, fontWeight: "700", fontVariant: ["tabular-nums"] },
  metricSub: { color: colors.textTertiary, fontSize: 10, fontVariant: ["tabular-nums"] },
  footnote: { color: colors.textTertiary, fontSize: 10, lineHeight: 15, marginTop: space.sm },
});
