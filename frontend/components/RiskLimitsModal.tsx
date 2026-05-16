import React, { useEffect, useState } from "react";
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
import { formatUsd } from "../lib/format";

type Props = {
  visible: boolean;
  onClose: () => void;
  onSaved: () => void;
};

export type RiskLimits = {
  daily_loss_pct: number;
  max_trade_pct: number;
  defaults: { daily_loss_pct: number; max_trade_pct: number };
  caps: { daily_loss_pct_max: number; max_trade_pct_max: number };
  preview: {
    daily_loss_dollar: number;
    max_trade_dollar: number;
    realized_pnl_today: number;
    circuit_tripped: boolean;
  };
};

export function RiskLimitsModal({ visible, onClose, onSaved }: Props) {
  const snack = useSnackbar();
  const [busy, setBusy] = useState(false);
  const [limits, setLimits] = useState<RiskLimits | null>(null);
  const [dailyLoss, setDailyLoss] = useState("");
  const [maxTrade, setMaxTrade] = useState("");

  useEffect(() => {
    if (!visible) return;
    let cancelled = false;
    (async () => {
      try {
        const r = await api<RiskLimits>("/me/risk-limits");
        if (cancelled) return;
        setLimits(r);
        setDailyLoss((r.daily_loss_pct * 100).toFixed(2));
        setMaxTrade((r.max_trade_pct * 100).toFixed(2));
      } catch {
        /* silent */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [visible]);

  const dlCapPct = (limits?.caps?.daily_loss_pct_max ?? 0.25) * 100;
  const mtCapPct = (limits?.caps?.max_trade_pct_max ?? 0.2) * 100;

  const onSave = async () => {
    const dlNum = parseFloat(dailyLoss);
    const mtNum = parseFloat(maxTrade);
    if (Number.isNaN(dlNum) || dlNum <= 0 || dlNum > dlCapPct) {
      snack.show({ message: `Daily loss must be in (0, ${dlCapPct}]%`, kind: "warning" });
      return;
    }
    if (Number.isNaN(mtNum) || mtNum <= 0 || mtNum > mtCapPct) {
      snack.show({ message: `Per-trade cap must be in (0, ${mtCapPct}]%`, kind: "warning" });
      return;
    }
    try {
      setBusy(true);
      const r = await api<RiskLimits>("/me/risk-limits", {
        method: "PATCH",
        body: { daily_loss_pct: dlNum / 100, max_trade_pct: mtNum / 100 },
      });
      setLimits(r);
      snack.show({ message: "Risk limits saved", kind: "success" });
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
                <Text style={styles.title}>RISK LIMITS</Text>
                <Text style={styles.subtitle}>
                  Hard guardrails enforced on every trade. Independent of paper/live mode.
                </Text>
              </View>
              <Pressable onPress={onClose} style={styles.closeBtn}>
                <Feather name="x" size={20} color={colors.textPrimary} />
              </Pressable>
            </View>

            <ScrollView
              keyboardShouldPersistTaps="handled"
              contentContainerStyle={{ paddingBottom: space.xl }}
            >
              <View style={{ marginBottom: space.md }}>
                <Text style={styles.fieldLabel}>
                  Daily Loss Circuit (% of starting bankroll)
                </Text>
                <TextInput
                  style={styles.input}
                  value={dailyLoss}
                  onChangeText={setDailyLoss}
                  keyboardType="decimal-pad"
                  placeholder="5.00"
                  placeholderTextColor={colors.textTertiary}
                />
                <Text style={styles.helper}>
                  Max {dlCapPct}% · default 5%. Trading is paused for the day once today's
                  realized PnL drops below this.
                </Text>
                {limits && (
                  <Text style={styles.preview}>
                    ≈ {formatUsd(parseFloat(dailyLoss || "0") / 100 *
                      Math.abs(limits.preview.daily_loss_dollar / Math.max(0.0001, limits.daily_loss_pct)))}
                    {"  "}loss/day cap
                  </Text>
                )}
              </View>

              <View style={{ marginBottom: space.md }}>
                <Text style={styles.fieldLabel}>
                  Per-Trade Size Cap (% of bankroll)
                </Text>
                <TextInput
                  style={styles.input}
                  value={maxTrade}
                  onChangeText={setMaxTrade}
                  keyboardType="decimal-pad"
                  placeholder="2.00"
                  placeholderTextColor={colors.textTertiary}
                />
                <Text style={styles.helper}>
                  Max {mtCapPct}% · default 2%. Single trade can never exceed this fraction
                  of your bankroll.
                </Text>
                {limits && (
                  <Text style={styles.preview}>
                    ≈ {formatUsd(parseFloat(maxTrade || "0") / 100 *
                      (limits.preview.max_trade_dollar / Math.max(0.0001, limits.max_trade_pct)))}
                    {"  "}per trade
                  </Text>
                )}
              </View>

              {limits?.preview.circuit_tripped && (
                <View style={styles.warningBlock}>
                  <Feather name="alert-octagon" size={16} color={colors.negative} />
                  <Text style={styles.warningText}>
                    Daily loss circuit currently tripped (realized {formatUsd(limits.preview.realized_pnl_today)}).
                    Trading resumes at UTC midnight.
                  </Text>
                </View>
              )}

              <View style={{ marginTop: space.sm }}>
                <Button title="SAVE RISK LIMITS" onPress={onSave} disabled={busy} />
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

const styles = StyleSheet.create({
  backdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.7)", justifyContent: "flex-end" },
  sheetWrap: { width: "100%" },
  sheet: {
    backgroundColor: colors.surfaceElevated,
    borderTopLeftRadius: radius.xl,
    borderTopRightRadius: radius.xl,
    paddingHorizontal: space.md,
    paddingTop: space.sm,
    paddingBottom: space.lg,
    maxHeight: "90%",
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
  fieldLabel: { ...typo.caption, color: colors.textSecondary, marginBottom: 6 },
  input: {
    backgroundColor: colors.modal,
    borderColor: colors.borderSubtle,
    borderWidth: 1,
    borderRadius: radius.md,
    paddingHorizontal: 12,
    paddingVertical: 12,
    color: colors.textPrimary,
    fontSize: 18,
    fontVariant: ["tabular-nums"],
    fontWeight: "600",
  },
  helper: { ...typo.caption, color: colors.textTertiary, marginTop: 6 },
  preview: {
    ...typo.caption,
    color: colors.textSecondary,
    marginTop: 4,
    fontVariant: ["tabular-nums"],
  },
  warningBlock: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    padding: space.sm,
    borderRadius: radius.md,
    backgroundColor: colors.negativeSoft,
    marginVertical: space.sm,
  },
  warningText: {
    flex: 1,
    ...typo.caption,
    color: colors.negative,
    lineHeight: 16,
  },
});
