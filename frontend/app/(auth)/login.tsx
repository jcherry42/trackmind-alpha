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
            <View style={styles.markRow}>
              <View style={styles.markDot} />
              <Text testID="brand-logo" style={styles.brandLogo}>
                TRACKMIND
              </Text>
            </View>
            <Text style={styles.brandTag}>Prediction market intelligence</Text>
          </View>

          <View style={styles.form}>
            <Field
              label="Email"
              testID="email-input"
              value={email}
              onChangeText={setEmail}
              placeholder="you@trackmind.io"
              autoCapitalize="none"
              keyboardType="email-address"
              autoCorrect={false}
            />

            <View style={styles.field}>
              <Text style={styles.label}>Password</Text>
              <View style={styles.passwordRow}>
                <TextInput
                  testID="password-input"
                  style={[styles.input, styles.flex]}
                  value={password}
                  onChangeText={setPassword}
                  placeholder="••••••••"
                  placeholderTextColor={colors.textMuted}
                  secureTextEntry={!showPassword}
                  autoCapitalize="none"
                />
                <Pressable
                  testID="toggle-password-visibility"
                  onPress={() => setShowPassword((v) => !v)}
                  style={styles.eyeBtn}
                  hitSlop={10}
                >
                  <Feather
                    name={showPassword ? "eye-off" : "eye"}
                    color={colors.textSecondary}
                    size={16}
                  />
                </Pressable>
              </View>
              <View style={styles.divider} />
            </View>

            <Pressable
              onPress={() => setBiometric((v) => !v)}
              style={styles.bioRow}
            >
              <View style={styles.bioLeft}>
                <Feather
                  name="shield"
                  size={14}
                  color={biometric ? colors.textPrimary : colors.textTertiary}
                />
                <Text
                  style={[
                    styles.bioText,
                    { color: biometric ? colors.textPrimary : colors.textSecondary },
                  ]}
                >
                  Require Face ID / Biometrics
                </Text>
              </View>
              <Switch
                testID="biometric-switch"
                value={biometric}
                onValueChange={setBiometric}
                trackColor={{ false: colors.modal, true: colors.textPrimary }}
                thumbColor={biometric ? colors.bg : colors.textTertiary}
                ios_backgroundColor={colors.modal}
              />
            </Pressable>

            {error ? (
              <Text testID="login-error" style={styles.error}>
                {error}
              </Text>
            ) : null}

            <Pressable
              testID="login-submit-button"
              onPress={onSubmit}
              disabled={loading}
              style={({ pressed }) => [
                styles.submit,
                pressed && { opacity: 0.85 },
                loading && { opacity: 0.6 },
              ]}
            >
              <Text style={styles.submitText}>
                {loading ? "AUTHENTICATING…" : "ENTER TERMINAL"}
              </Text>
              <Feather name="arrow-right" size={16} color={colors.primaryText} />
            </Pressable>
          </View>

          <View style={styles.footer}>
            <Link href="/(auth)/register" asChild>
              <Pressable testID="go-to-register-link" hitSlop={10}>
                <Text style={styles.linkText}>
                  No account?{" "}
                  <Text style={{ color: colors.textPrimary }}>Create one</Text>
                </Text>
              </Pressable>
            </Link>
            <Text testID="login-demo-creds" style={styles.demo}>
              admin@trackmind.io · Admin123!
            </Text>
          </View>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

function Field({
  label,
  testID,
  ...rest
}: React.ComponentProps<typeof TextInput> & { label: string; testID?: string }) {
  return (
    <View style={styles.field}>
      <Text style={styles.label}>{label}</Text>
      <TextInput
        testID={testID}
        style={styles.input}
        placeholderTextColor={colors.textMuted}
        {...rest}
      />
      <View style={styles.divider} />
    </View>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.bg },
  flex: { flex: 1 },
  container: {
    flexGrow: 1,
    paddingHorizontal: space.xl,
    paddingTop: space.xxxl,
    paddingBottom: space.xl,
    justifyContent: "space-between",
  },
  brand: { gap: space.sm, marginBottom: space.xxl },
  markRow: { flexDirection: "row", alignItems: "center", gap: 10 },
  markDot: {
    width: 10,
    height: 10,
    borderRadius: 5,
    backgroundColor: colors.textPrimary,
  },
  brandLogo: {
    color: colors.textPrimary,
    fontSize: 26,
    fontWeight: "800",
    letterSpacing: 5,
  },
  brandTag: {
    color: colors.textSecondary,
    fontSize: 13,
    marginLeft: 20,
  },
  form: { gap: space.xl },
  field: { gap: 6 },
  label: { ...typo.micro, color: colors.textTertiary },
  input: {
    color: colors.textPrimary,
    fontSize: 18,
    paddingVertical: 8,
    fontWeight: "500",
  },
  divider: { height: 1, backgroundColor: colors.borderSubtle },
  passwordRow: { flexDirection: "row", alignItems: "center" },
  eyeBtn: { paddingHorizontal: 4 },
  bioRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingVertical: space.sm,
  },
  bioLeft: { flexDirection: "row", alignItems: "center", gap: 10 },
  bioText: { fontSize: 14, fontWeight: "500" },
  error: { color: colors.negative, fontSize: 13 },
  submit: {
    backgroundColor: colors.primary,
    height: 56,
    borderRadius: 14,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    marginTop: space.sm,
  },
  submitText: {
    color: colors.primaryText,
    fontWeight: "800",
    letterSpacing: 1.5,
    fontSize: 14,
  },
  footer: { gap: space.md, alignItems: "center", marginTop: space.lg },
  linkText: { color: colors.textSecondary, fontSize: 13 },
  demo: { color: colors.textMuted, fontSize: 11, letterSpacing: 0.5 },
});
