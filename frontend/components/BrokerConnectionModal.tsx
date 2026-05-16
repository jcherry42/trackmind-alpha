import React, { useState } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { Feather } from "@expo/vector-icons";
import { api } from "../lib/api";
import { Button } from "./Button";
import { useSnackbar } from "./Snackbar";
import { colors, radius, space, type as typo } from "../lib/theme";

type Venue = "polymarket" | "kalshi";

type Props = {
  visible: boolean;
  venue: Venue;
  onClose: () => void;
  onSaved: () => void;
};

type PolyFields = {
  private_key: string;
  api_key: string;
  api_secret: string;
  api_passphrase: string;
};
type KalshiFields = {
  key_id: string;
  private_key_pem: string;
};

const POLY_LABELS: Array<[keyof PolyFields, string, string, boolean]> = [
  ["private_key", "Private Key", "0x... (64-char hex)", true],
  ["api_key", "API Key", "L2 api_key", false],
  ["api_secret", "API Secret", "L2 api_secret", true],
  ["api_passphrase", "API Passphrase", "L2 api_passphrase", true],
];

const KALSHI_LABELS: Array<[keyof KalshiFields, string, string, boolean, boolean]> = [
  ["key_id", "Key ID", "UUID from Kalshi dashboard", false, false],
  ["private_key_pem", "Private Key (PEM)", "-----BEGIN PRIVATE KEY-----\n...", true, true],
];

export function BrokerConnectionModal({ visible, venue, onClose, onSaved }: Props) {
  const snack = useSnackbar();
  const [busy, setBusy] = useState(false);
  const [poly, setPoly] = useState<PolyFields>({
    private_key: "",
    api_key: "",
    api_secret: "",
    api_passphrase: "",
  });
  const [kalshi, setKalshi] = useState<KalshiFields>({ key_id: "", private_key_pem: "" });
  const [shown, setShown] = useState<Record<string, boolean>>({});

  const reset = () => {
    setPoly({ private_key: "", api_key: "", api_secret: "", api_passphrase: "" });
    setKalshi({ key_id: "", private_key_pem: "" });
    setShown({});
  };

  const buildPayload = () =>
    venue === "polymarket" ? { venue, ...poly } : { venue, ...kalshi };

  const onTest = async () => {
    try {
      setBusy(true);
      const result = await api<{ ok: boolean; detail: string }>(
        "/me/broker-connections/test",
        { method: "POST", body: buildPayload() },
      );
      snack.show({
        message: result.ok ? `✓ ${venue} authenticated` : `✗ ${result.detail || "Failed"}`,
        kind: result.ok ? "success" : "error",
      });
    } catch (e: any) {
      snack.show({ message: e?.message || "Test failed", kind: "error" });
    } finally {
      setBusy(false);
    }
  };

  const onSave = async () => {
    try {
      setBusy(true);
      await api("/me/broker-connections", { method: "POST", body: buildPayload() });
      snack.show({
        message: `${venue} credentials saved (encrypted)`,
        kind: "success",
      });
      reset();
      onSaved();
      onClose();
    } catch (e: any) {
      snack.show({ message: e?.message || "Save failed", kind: "error" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      visible={visible}
      animationType="slide"
      transparent
      onRequestClose={onClose}
      statusBarTranslucent
    >
      <View style={styles.backdrop}>
        <KeyboardAvoidingView
          behavior={Platform.OS === "ios" ? "padding" : undefined}
          style={styles.sheetWrap}
        >
          <View style={styles.sheet}>
            <View style={styles.handle} />
            <View style={styles.header}>
              <View style={{ flex: 1 }}>
                <Text style={styles.title}>
                  CONNECT {venue.toUpperCase()}
                </Text>
                <Text style={styles.subtitle}>
                  Credentials are encrypted with Fernet (AES-128 + HMAC) and stored
                  only in your account.
                </Text>
              </View>
              <Pressable
                onPress={onClose}
                style={styles.closeBtn}
                accessibilityLabel="Close"
              >
                <Feather name="x" size={20} color={colors.textPrimary} />
              </Pressable>
            </View>

            <ScrollView
              keyboardShouldPersistTaps="handled"
              contentContainerStyle={{ paddingBottom: space.xl }}
            >
              {venue === "polymarket" ? (
                <>
                  {POLY_LABELS.map(([key, label, ph, secret]) => (
                    <Field
                      key={key}
                      label={label}
                      placeholder={ph}
                      secret={secret}
                      shown={!!shown[key]}
                      onToggleShown={() =>
                        setShown((s) => ({ ...s, [key]: !s[key] }))
                      }
                      value={poly[key]}
                      onChange={(v) => setPoly((p) => ({ ...p, [key]: v }))}
                    />
                  ))}
                  <HintBlock
                    text={
                      "Need creds? Run py-clob-client's derive_api_key() against your funded Polygon EOA."
                    }
                  />
                </>
              ) : (
                <>
                  {KALSHI_LABELS.map(([key, label, ph, secret, multiline]) => (
                    <Field
                      key={key}
                      label={label}
                      placeholder={ph}
                      secret={secret}
                      shown={!!shown[key]}
                      multiline={multiline}
                      onToggleShown={() =>
                        setShown((s) => ({ ...s, [key]: !s[key] }))
                      }
                      value={kalshi[key]}
                      onChange={(v) => setKalshi((p) => ({ ...p, [key]: v }))}
                    />
                  ))}
                  <HintBlock
                    text={
                      "Kalshi → Settings → API Keys → Create. Download the PEM file and paste its full contents (including BEGIN/END lines)."
                    }
                  />
                </>
              )}

              <View style={styles.actions}>
                <Button
                  title="TEST CONNECTION"
                  variant="secondary"
                  onPress={onTest}
                  disabled={busy}
                />
                <Button
                  title="SAVE ENCRYPTED"
                  onPress={onSave}
                  disabled={busy}
                />
              </View>
              {busy && (
                <View style={{ marginTop: space.sm, alignItems: "center" }}>
                  <ActivityIndicator color={colors.primary} />
                </View>
              )}
            </ScrollView>
          </View>
        </KeyboardAvoidingView>
      </View>
    </Modal>
  );
}

function Field({
  label,
  placeholder,
  secret,
  shown,
  onToggleShown,
  value,
  onChange,
  multiline,
}: {
  label: string;
  placeholder: string;
  secret: boolean;
  shown: boolean;
  onToggleShown: () => void;
  value: string;
  onChange: (v: string) => void;
  multiline?: boolean;
}) {
  return (
    <View style={{ marginBottom: space.md }}>
      <View style={styles.fieldHeader}>
        <Text style={styles.fieldLabel}>{label}</Text>
        {secret && (
          <Pressable onPress={onToggleShown} hitSlop={10}>
            <Feather
              name={shown ? "eye-off" : "eye"}
              size={16}
              color={colors.textSecondary}
            />
          </Pressable>
        )}
      </View>
      <TextInput
        style={[styles.input, multiline && styles.inputMultiline]}
        placeholder={placeholder}
        placeholderTextColor={colors.textTertiary}
        value={value}
        onChangeText={onChange}
        secureTextEntry={secret && !shown && !multiline}
        autoCapitalize="none"
        autoCorrect={false}
        spellCheck={false}
        multiline={multiline}
        numberOfLines={multiline ? 6 : 1}
      />
    </View>
  );
}

function HintBlock({ text }: { text: string }) {
  return (
    <View style={styles.hint}>
      <Feather name="info" size={14} color={colors.textSecondary} />
      <Text style={styles.hintText}>{text}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.7)",
    justifyContent: "flex-end",
  },
  sheetWrap: { width: "100%" },
  sheet: {
    backgroundColor: colors.surfaceElevated,
    borderTopLeftRadius: radius.xl,
    borderTopRightRadius: radius.xl,
    paddingHorizontal: space.md,
    paddingTop: space.sm,
    paddingBottom: space.lg,
    maxHeight: "92%",
  },
  handle: {
    width: 40,
    height: 4,
    backgroundColor: colors.borderSubtle,
    borderRadius: 2,
    alignSelf: "center",
    marginBottom: space.sm,
  },
  header: {
    flexDirection: "row",
    alignItems: "flex-start",
    marginBottom: space.md,
    gap: space.sm,
  },
  closeBtn: {
    width: 36,
    height: 36,
    borderRadius: radius.md,
    alignItems: "center",
    justifyContent: "center",
    borderColor: colors.borderSubtle,
    borderWidth: 1,
  },
  title: { ...typo.micro, color: colors.textPrimary, letterSpacing: 2 },
  subtitle: { ...typo.caption, color: colors.textSecondary, marginTop: 4 },
  fieldHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 6,
  },
  fieldLabel: { ...typo.caption, color: colors.textSecondary, letterSpacing: 0.5 },
  input: {
    backgroundColor: colors.modal,
    borderColor: colors.borderSubtle,
    borderWidth: 1,
    borderRadius: radius.md,
    paddingHorizontal: 12,
    paddingVertical: 12,
    color: colors.textPrimary,
    fontSize: 14,
    fontFamily: Platform.select({ ios: "Menlo", android: "monospace" }),
  },
  inputMultiline: {
    minHeight: 120,
    textAlignVertical: "top",
    paddingTop: 12,
    fontSize: 11,
  },
  hint: {
    flexDirection: "row",
    gap: 8,
    padding: space.sm,
    borderRadius: radius.md,
    backgroundColor: colors.modal,
    marginTop: space.xs,
    marginBottom: space.md,
  },
  hintText: {
    flex: 1,
    ...typo.caption,
    color: colors.textSecondary,
    lineHeight: 16,
  },
  actions: { gap: space.sm, marginTop: space.sm },
});
