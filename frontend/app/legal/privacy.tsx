/**
 * TRACKMIND — Privacy Policy (placeholder).
 * Replace placeholder text below with your finalised policy before App Store submission.
 */
import React from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { Feather } from "@expo/vector-icons";
import { colors, space, type as typo } from "../../lib/theme";

export default function PrivacyScreen() {
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
        <Text style={styles.title}>Privacy Policy</Text>
        <View style={{ width: 36 }} />
      </View>
      <ScrollView contentContainerStyle={styles.body}>
        <Text style={styles.updated}>Last updated: 1 June 2025</Text>

        <Section title="1 — Who we are">
          TRACKMIND is an institutional-grade prediction-market intelligence and execution
          platform operated by TRACKMIND Capital Inc. (“TRACKMIND”, “we”, “our”). This Privacy
          Policy explains how we collect, use, share, and protect your information when you
          use the TRACKMIND mobile app or any associated services (the “Service”).
        </Section>

        <Section title="2 — Information we collect">
          {`• Account data — your email address, hashed password, display name, and the
  preferences you choose (engines paused, kill switch, biometrics, live mode).
• Trading activity — every signal you approve, position you open, and the realised /
  unrealised P&L generated within the Service. We retain audit records for 30 days.
• Device data — your platform (iOS / Android), an Expo push token (if you opt in to
  notifications), and the IP address used to authenticate.
• Operational telemetry — anonymised request IDs, latencies, and error rates used to
  keep the Service reliable. We do not associate this telemetry with your identity in
  external analytics tools.`}
        </Section>

        <Section title="3 — How we use your information">
          {`• To run the Service: authenticate you, route orders, settle positions, and
  enforce daily LLM quotas + risk limits.
• To send Master Signal alerts when, and only when, the strongest live signal crosses
  your configured threshold. You can disable alerts at any time from your OS settings.
• To investigate fraud, abuse, or violations of the Terms of Service.
• To comply with legal requests when we are required to do so.`}
        </Section>

        <Section title="4 — Sharing">
          We do not sell your personal information. We share it only with: (a) infrastructure
          providers strictly necessary to operate the Service (e.g. our cloud hosting and push
          notification provider, Expo); (b) law enforcement when compelled by valid legal
          process; (c) successors in the event of a merger or acquisition.
        </Section>

        <Section title="5 — Data retention">
          {`• Audit logs: 30 days, automatic TTL deletion.
• Closed positions: indefinitely, for analytics and reconciliation.
• Push tokens: until you sign out from a given device.
You can request full deletion of your account by emailing privacy@trackmind.io.`}
        </Section>

        <Section title="6 — Security">
          We encrypt your password with bcrypt, store auth tokens in your device's secure
          keystore (iOS Keychain / Android Keystore), enforce per-IP rate limits, and rotate
          refresh tokens on every use.
        </Section>

        <Section title="7 — Your rights">
          Depending on your jurisdiction (e.g. GDPR, CCPA) you may request access to,
          correction of, deletion of, or portability of your personal data. Contact
          privacy@trackmind.io.
        </Section>

        <Section title="8 — Changes">
          We may update this Privacy Policy. Material changes will be surfaced in-app and the
          "Last updated" date above will change.
        </Section>

        <Section title="9 — Contact">
          privacy@trackmind.io · TRACKMIND Capital Inc.
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
  updated: {
    color: colors.textSecondary,
    marginBottom: space.sm,
    fontSize: 12,
  },
  section: { marginBottom: space.md },
  sectionTitle: {
    ...(typo.h3 as any),
    color: colors.textPrimary,
    marginBottom: space.sm,
  },
});
