import React, { useState } from "react";
import {
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
} from "react-native";
import { Link, useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { Feather } from "@expo/vector-icons";
import * as LocalAuthentication from "expo-local-authentication";
import { useAuth } from "../../lib/auth";
import { Button } from "../../components/Button";
import { colors, space, type as typo } from "../../lib/theme";

export default function Login() {
  const { login } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("admin@trackmind.io");
  const [password, setPassword] = useState("Admin123!");
  const [showPassword, setShowPassword] = useState(false);
  const [biometric, setBiometric] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit() {
    setError(null);
    setLoading(true);
    try {
      if (biometric) {
        const has = await LocalAuthentication.hasHardwareAsync();
        const enrolled = await LocalAuthentication.isEnrolledAsync();
        if (has && enrolled) {
          const r = await LocalAuthentication.authenticateAsync({
            promptMessage: "Unlock TRACKMIND",
            fallbackLabel: "Use password",
          });
          if (!r.success) {
            setError("Biometric auth cancelled");
            setLoading(false);
            return;
          }
        }
      }
      await login(email.trim(), password);
      router.replace("/(tabs)");
    } catch (e: any) {
      setError(e?.message || "Login failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <SafeAreaView style={styles.safe} edges={["top", "bottom"]}>
      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : "height"}
        style={styles.flex}
      >
        <ScrollView
          contentContainerStyle={styles.container}
          keyboardShouldPersistTaps="handled"
        >
          <View style={styles.brand}>
            <Text testID="brand-logo" style={styles.brandLogo}>TRACKMIND</Text>
            <Text style={styles.brandTag}>PREDICTION MARKET INTELLIGENCE</Text>
          </View>

          <View style={styles.form}>
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
                autoCorrect={false}
              />
            </View>

            <View style={styles.fieldGroup}>
              <Text style={styles.label}>Password</Text>
              <View style={styles.passwordRow}>
                <TextInput
                  testID="password-input"
                  style={[styles.input, styles.flex]}
                  value={password}
                  onChangeText={setPassword}
                  placeholder="••••••••"
                  placeholderTextColor={colors.textTertiary}
                  secureTextEntry={!showPassword}
                  autoCapitalize="none"
                />
                <Pressable
                  testID="toggle-password-visibility"
                  onPress={() => setShowPassword((v) => !v)}
                  style={styles.eyeBtn}
                >
                  <Feather
                    name={showPassword ? "eye-off" : "eye"}
                    color={colors.textSecondary}
                    size={18}
                  />
                </Pressable>
              </View>
            </View>

            <View style={styles.bioRow}>
              <View>
                <Text style={styles.label}>Face ID / Biometric</Text>
                <Text style={styles.subtle}>Require device auth before login</Text>
              </View>
              <Switch
                testID="biometric-switch"
                value={biometric}
                onValueChange={setBiometric}
                trackColor={{ false: colors.modal, true: colors.primary }}
                thumbColor={biometric ? colors.primary : colors.textTertiary}
              />
            </View>

            {error ? (
              <Text testID="login-error" style={styles.error}>
                {error}
              </Text>
            ) : null}

            <Button
              testID="login-submit-button"
              title="ENTER TERMINAL"
              onPress={onSubmit}
              loading={loading}
            />

            <Link href="/(auth)/register" asChild>
              <Pressable testID="go-to-register-link" style={styles.linkRow}>
                <Text style={styles.linkText}>
                  No account?{" "}
                  <Text style={{ color: colors.textPrimary }}>Create one</Text>
                </Text>
              </Pressable>
            </Link>
          </View>

          <Text testID="login-demo-creds" style={styles.demo}>
            Demo: admin@trackmind.io / Admin123!
          </Text>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.bg },
  flex: { flex: 1 },
  container: { padding: space.lg, gap: space.xl, flexGrow: 1 },
  brand: { marginTop: space.xl, alignItems: "center", gap: 8 },
  brandLogo: {
    ...typo.h1,
    fontSize: 38,
    color: colors.textPrimary,
    letterSpacing: 6,
  },
  brandTag: { ...typo.micro, color: colors.textSecondary },
  form: { gap: space.lg },
  fieldGroup: { gap: 6 },
  label: { ...typo.micro, color: colors.textSecondary },
  input: {
    color: colors.textPrimary,
    fontSize: 17,
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: colors.borderFocus,
  },
  passwordRow: { flexDirection: "row", alignItems: "center" },
  eyeBtn: { padding: space.sm },
  bioRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  subtle: { color: colors.textTertiary, fontSize: 12, marginTop: 2 },
  error: { color: colors.negative, fontSize: 13 },
  linkRow: { alignItems: "center", paddingVertical: space.sm },
  linkText: { color: colors.textSecondary, fontSize: 14 },
  demo: { textAlign: "center", color: colors.textTertiary, fontSize: 11 },
});
