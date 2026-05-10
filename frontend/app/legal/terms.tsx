/**
 * TRACKMIND — Terms of Service (placeholder).
 * Replace placeholder text below with your finalised terms before App Store submission.
 */
import React from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { Feather } from "@expo/vector-icons";
import { colors, space, type as typo } from "../../lib/theme";

export default function TermsScreen() {
  const router = useRouter();
  return (
    <SafeAreaView style={styles.root} edges={["top"]}>
      <View style={styles.header}>
        <Pressable
          onPress={() => router.back()}
          accessibilityRole="button"
          accessibilityLabel="Close"
          style={styles.iconBtn}
        >
          <Feather name="x" size={20} color={colors.textPrimary} />
        </Pressable>
        <Text style={styles.title}>Terms of Service</Text>
        <View style={{ width: 36 }} />
      </View>
      <ScrollView contentContainerStyle={styles.body}>
        <Text style={styles.updated}>Last updated: 1 June 2025</Text>

        <Section title="1 — Acceptance">
          By creating an account or using TRACKMIND you agree to these Terms of Service. If
          you do not agree, do not use the Service.
        </Section>

        <Section title="2 — Eligibility">
          You must be at least 18 years old, legally able to enter binding contracts in your
          jurisdiction, and not located in a country where prediction markets, futures, or
          related financial instruments are prohibited.
        </Section>

        <Section title="3 — Paper trading vs live trading">
          {`The default mode is paper trading: orders are simulated against current market
quotes and never reach a live venue. Live mode (when enabled) routes orders to one or
more third-party venues (e.g. Polymarket, Kalshi). YOU are solely responsible for any
funds and any losses incurred while in live mode.`}
        </Section>

        <Section title="4 — No investment advice">
          The signals, allocations, and rationale presented inside TRACKMIND are
          algorithmically generated for informational purposes only. They are NOT investment
          advice, NOT a solicitation to trade, and NOT a guarantee of any outcome. You assume
          full responsibility for any trade you approve.
        </Section>

        <Section title="5 — Acceptable use">
          {`You agree not to:
• Reverse-engineer the engines, allocator, or risk system.
• Bypass rate limits, idempotency keys, or daily LLM quotas.
• Interfere with the integrity or performance of the Service.
• Use the Service to manipulate or attempt to manipulate any market.`}
        </Section>

        <Section title="6 — Account security">
          You are responsible for safeguarding your password and any device that has an
          authenticated session. Notify us immediately at security@trackmind.io if you
          suspect unauthorised access.
        </Section>

        <Section title="7 — Fees & subscriptions">
          The Service may include free and paid tiers. Paid tiers are billed via the App Store
          / Play Store and governed by their respective billing rules.
        </Section>

        <Section title="8 — Termination">
          We may suspend or terminate your access at any time for breach of these Terms,
          fraud, or misuse. You may close your account at any time from Settings.
        </Section>

        <Section title="9 — Disclaimers & liability">
          {`THE SERVICE IS PROVIDED "AS IS" WITHOUT WARRANTIES OF ANY KIND. TO THE MAXIMUM EXTENT
PERMITTED BY LAW, TRACKMIND IS NOT LIABLE FOR ANY INDIRECT, INCIDENTAL, OR
CONSEQUENTIAL DAMAGES, INCLUDING TRADING LOSSES.`}
        </Section>

        <Section title="10 — Governing law">
          These Terms are governed by the laws of Delaware, USA, without regard to its
          conflict-of-law principles.
        </Section>

        <Section title="11 — Contact">
          legal@trackmind.io · TRACKMIND Capital Inc.
        </Section>
      </ScrollView>
    </SafeAreaView>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>{title}</Text>
      <Text style={styles.body}>{children}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.bg },
  header: {
    paddingHorizontal: space.md,
    paddingVertical: space.sm,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    borderBottomWidth: 1,
    borderBottomColor: colors.borderSubtle,
  },
  title: { ...(typo.h2 as any), color: colors.textPrimary },
  iconBtn: { width: 36, height: 36, alignItems: "center", justifyContent: "center" },
  body: {
    padding: space.lg,
    gap: space.md,
    color: colors.textSecondary,
    lineHeight: 22,
  },
  updated: { color: colors.textSecondary, marginBottom: space.sm, fontSize: 12 },
  section: { marginBottom: space.md },
  sectionTitle: { ...(typo.h3 as any), color: colors.textPrimary, marginBottom: space.sm },
});
