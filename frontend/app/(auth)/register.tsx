import React, { useState } from "react";
import {
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { Link, useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { Feather } from "@expo/vector-icons";
import { useAuth } from "../../lib/auth";
import { Button } from "../../components/Button";
import { colors, space, type as typo } from "../../lib/theme";

export default function Register() {
  const { register } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit() {
    if (!email || !password) {
      setError("Email and password required");
      return;
    }
    setError(null);
    setLoading(true);
    try {
      await register(email.trim(), password, name.trim());
      router.replace("/(tabs)");
    } catch (e: any) {
      setError(e?.message || "Registration failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <SafeAreaView style={styles.safe} edges={["top", "bottom"]}>
      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : "height"}
        style={{ flex: 1 }}
      >
        <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
          <Pressable
            testID="back-button"
            onPress={() => router.back()}
            style={styles.back}
          >
            <Feather name="arrow-left" color={colors.textPrimary} size={22} />
          </Pressable>

          <View style={styles.header}>
            <Text style={styles.title}>Open Account</Text>
            <Text style={styles.subtitle}>
              Paper bankroll funded with $10,000 to start.
            </Text>
          </View>

          <View style={styles.form}>
            <View style={styles.fieldGroup}>
              <Text style={styles.label}>Name (optional)</Text>
              <TextInput
                testID="name-input"
                style={styles.input}
                value={name}
                onChangeText={setName}
                placeholder="Your name"
                placeholderTextColor={colors.textTertiary}
              />
            </View>
            <View style={styles.fieldGroup}>
              <Text style={styles.label}>Email</Text>
              <TextInput
                testID="email-input"
                style={styles.input}
                value={email}
                onChangeText={setEmail}
                placeholder="you@trackmind.io"
                placeholderTextColor={colors.textTertiary}
                autoCapitalize="none"
                keyboardType="email-address"
              />
            </View>
            <View style={styles.fieldGroup}>
              <Text style={styles.label}>Password</Text>
              <TextInput
                testID="password-input"
                style={styles.input}
                value={password}
                onChangeText={setPassword}
                placeholder="At least 6 characters"
                placeholderTextColor={colors.textTertiary}
                secureTextEntry
                autoCapitalize="none"
              />
            </View>

            {error ? <Text testID="register-error" style={styles.error}>{error}</Text> : null}

            <Button
              testID="register-submit-button"
              title="CREATE ACCOUNT"
              onPress={onSubmit}
              loading={loading}
            />

            <Link href="/(auth)/login" asChild>
              <Pressable testID="go-to-login-link" style={styles.linkRow}>
                <Text style={styles.linkText}>
                  Already have an account?{" "}
                  <Text style={{ color: colors.textPrimary }}>Sign in</Text>
                </Text>
              </Pressable>
            </Link>
          </View>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.bg },
  container: { padding: space.lg, gap: space.lg, flexGrow: 1 },
  back: { width: 40, height: 40, alignItems: "center", justifyContent: "center" },
  header: { gap: 8, marginTop: space.md },
  title: { ...typo.h1, color: colors.textPrimary },
  subtitle: { color: colors.textSecondary, fontSize: 14 },
  form: { gap: space.lg, marginTop: space.md },
  fieldGroup: { gap: 6 },
  label: { ...typo.micro, color: colors.textSecondary },
  input: {
    color: colors.textPrimary,
    fontSize: 17,
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: colors.borderFocus,
  },
  error: { color: colors.negative, fontSize: 13 },
  linkRow: { alignItems: "center", paddingVertical: space.sm },
  linkText: { color: colors.textSecondary, fontSize: 14 },
});
