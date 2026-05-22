/**
 * Whales screen — real on-chain whale activity feed with mirror-trade execution.
 *
 * Shows recent BUY trades observed from tracked Polymarket wallets (via the
 * public Data API). Each entry has a "MIRROR" button that opens a confirm dialog
 * and routes through the standard /trade pipeline (atomic debit + per-trade cap
 * + daily-loss circuit). Adding a custom wallet (any 0x… EOA address) is one tap.
 */
import React, { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  FlatList,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { Feather } from "@expo/vector-icons";
import { api } from "../lib/api";
import { colors, radius, space, type as typo } from "../lib/theme";
import { useSnackbar } from "../components/Snackbar";
import { Card } from "../components/Card";
import { ResponsiveContent } from "../components/ResponsiveContent";
import { formatUsd } from "../lib/format";

type WhaleEntry = {
  id: string;
  transaction_hash: string;
  wallet_address: string;
  wallet_label: string | null;
  condition_id: string;
  canonical_id: string | null;
  side: string;
  outcome: string;
  price: number;
  usdc_size: number;
  size_shares: number;
  title: string;
  slug: string;
  ts: number;
  observed_at: string;
};

type TrackedWallet = {
  id: string;
  address: string;
  label: string;
  note: string | null;
  source: "admin" | "user";
  is_active: boolean;
  entries_count: number;
  last_polled_at: string | null;
  last_entry_at: string | null;
  added_by_user_id: string | null;
};

type Status = {
  total_tracked_wallets: number;
  user_added_wallets: number;
  entries_24h: number;
  last_tick_age_sec: number | null;
  poll_interval_sec: number;
};

const ETH_ADDR_RE = /^0x[0-9a-fA-F]{40}$/;

function shortAddr(a: string): string {
  if (!a) return "—";
  return a.length >= 10 ? `${a.slice(0, 6)}…${a.slice(-4)}` : a;
}

function timeAgo(tsSec: number): string {
  const diff = Math.max(0, Date.now() / 1000 - tsSec);
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

export default function WhalesScreen() {
  const router = useRouter();
  const { show } = useSnackbar();
  const [entries, setEntries] = useState<WhaleEntry[]>([]);
  const [wallets, setWallets] = useState<TrackedWallet[]>([]);
  const [status, setStatus] = useState<Status | null>(null);
  const [tab, setTab] = useState<"feed" | "wallets">("feed");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [addAddress, setAddAddress] = useState("");
  const [addLabel, setAddLabel] = useState("");
  const [adding, setAdding] = useState(false);
  const [mirroring, setMirroring] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [e, w, s] = await Promise.all([
        api<{ entries: WhaleEntry[] }>("/whales/recent?limit=40"),
        api<{ wallets: TrackedWallet[] }>("/whales/wallets"),
        api<Status>("/whales/status"),
      ]);
      setEntries(e.entries);
      setWallets(w.wallets);
      setStatus(s);
    } catch (err: any) {
      show({ kind: "error", message: err?.message || "Failed to load whales" });
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [show]);

  useEffect(() => {
    load();
  }, [load]);

  const handleMirror = async (entry: WhaleEntry) => {
    if (!entry.canonical_id) {
      Alert.alert(
        "Market unavailable",
        "This Polymarket market isn't in our live feed right now, so we can't mirror this trade. Try a more recent entry.",
      );
      return;
    }
    Alert.alert(
      `Mirror ${entry.wallet_label || shortAddr(entry.wallet_address)}?`,
      `Side: ${entry.side === "BUY" ? entry.outcome : "EXIT"}\nMarket: ${entry.title}\nWhale stake: ${formatUsd(entry.usdc_size)} @ $${entry.price.toFixed(2)}\n\nWe'll size to your per-trade cap.`,
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "MIRROR",
          style: "default",
          onPress: async () => {
            setMirroring(entry.id);
            try {
              const res = await api<{ position: any; fill: any }>("/whales/mirror", {
                method: "POST",
                body: { entry_id: entry.id },
                idempotent: true,
              });
              show({
                kind: "success",
                message: `Mirrored: ${formatUsd(res.fill?.notional ?? 0)} on ${entry.outcome}`,
              });
            } catch (err: any) {
              show({ kind: "error", message: err?.message || "Mirror failed" });
            } finally {
              setMirroring(null);
            }
          },
        },
      ],
    );
  };

  const handleAddWallet = async () => {
    const addr = addAddress.trim();
    if (!ETH_ADDR_RE.test(addr)) {
      show({ kind: "warning", message: "Address must be 0x followed by 40 hex chars" });
      return;
    }
    setAdding(true);
    try {
      await api("/whales/wallets", {
        method: "POST",
        body: { address: addr, label: addLabel.trim() || undefined },
      });
      setAddOpen(false);
      setAddAddress("");
      setAddLabel("");
      show({ kind: "success", message: "Wallet added — first poll in <90s" });
      load();
    } catch (err: any) {
      show({ kind: "error", message: err?.message || "Add failed" });
    } finally {
      setAdding(false);
    }
  };

  const handleRemoveWallet = (w: TrackedWallet) => {
    Alert.alert(
      "Remove wallet?",
      `${w.label}\n${w.address}`,
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "REMOVE",
          style: "destructive",
          onPress: async () => {
            try {
              await api(`/whales/wallets/${w.address}`, { method: "DELETE" });
              show({ kind: "success", message: "Wallet removed" });
              load();
            } catch (err: any) {
              show({ kind: "error", message: err?.message || "Remove failed" });
            }
          },
        },
      ],
    );
  };

  const renderEntry = ({ item }: { item: WhaleEntry }) => {
    const mirrorable = !!item.canonical_id;
    const isLoading = mirroring === item.id;
    return (
      <Card style={styles.entryCard}>
        <View style={styles.entryHeader}>
          <View style={styles.entryHeaderLeft}>
            <View style={[styles.dot, { backgroundColor: colors.whale }]} />
            <Text style={styles.walletLabel}>{item.wallet_label || shortAddr(item.wallet_address)}</Text>
            <Text style={styles.dotSep}>·</Text>
            <Text style={styles.timeAgo}>{timeAgo(item.ts)}</Text>
          </View>
          <Text style={styles.usdc}>{formatUsd(item.usdc_size)}</Text>
        </View>
        <Text style={styles.market} numberOfLines={2}>
          {item.title}
        </Text>
        <View style={styles.entryFooter}>
          <View style={styles.outcomeRow}>
            <Text style={styles.outcomeLabel}>BUY</Text>
            <Text style={styles.outcomeValue}>{item.outcome}</Text>
            <Text style={styles.priceLabel}>@</Text>
            <Text style={styles.priceValue}>${item.price.toFixed(3)}</Text>
          </View>
          <Pressable
            onPress={() => handleMirror(item)}
            disabled={!mirrorable || isLoading}
            style={[
              styles.mirrorBtn,
              !mirrorable && styles.mirrorBtnDisabled,
              isLoading && styles.mirrorBtnLoading,
            ]}
          >
            {isLoading ? (
              <ActivityIndicator size="small" color={colors.primaryText} />
            ) : (
              <>
                <Feather
                  name="copy"
                  size={12}
                  color={mirrorable ? colors.primaryText : colors.textTertiary}
                />
                <Text style={[styles.mirrorBtnText, !mirrorable && styles.mirrorBtnTextDisabled]}>
                  {mirrorable ? "MIRROR" : "UNAVAILABLE"}
                </Text>
              </>
            )}
          </Pressable>
        </View>
      </Card>
    );
  };

  const renderWallet = ({ item }: { item: TrackedWallet }) => (
    <Card style={styles.walletCard}>
      <View style={styles.walletRow}>
        <View style={{ flex: 1 }}>
          <View style={styles.walletTopRow}>
            <Text style={styles.walletName}>{item.label}</Text>
            {item.source === "admin" && (
              <View style={styles.badge}>
                <Text style={styles.badgeText}>CURATED</Text>
              </View>
            )}
          </View>
          <Text style={styles.walletAddr}>{item.address}</Text>
          {!!item.note && <Text style={styles.walletNote}>{item.note}</Text>}
          <Text style={styles.walletMeta}>
            {item.entries_count} entries
            {item.last_entry_at ? ` · last ${timeAgo(new Date(item.last_entry_at).getTime() / 1000)}` : " · no recent buys"}
          </Text>
        </View>
        {item.source === "user" && (
          <Pressable onPress={() => handleRemoveWallet(item)} style={styles.removeBtn}>
            <Feather name="trash-2" size={16} color={colors.danger} />
          </Pressable>
        )}
      </View>
    </Card>
  );

  return (
    <SafeAreaView style={styles.container}>
      <View style={styles.headerBar}>
        <Pressable onPress={() => router.back()} style={styles.closeBtn}>
          <Feather name="x" size={20} color={colors.textPrimary} />
        </Pressable>
        <Text style={styles.headerTitle}>WHALES</Text>
        <Pressable onPress={() => setAddOpen(true)} style={styles.addBtn}>
          <Feather name="plus" size={18} color={colors.textPrimary} />
        </Pressable>
      </View>

      <ResponsiveContent>
        <View style={styles.statusBar}>
          <View style={styles.statusItem}>
            <Text style={styles.statusVal}>{status?.total_tracked_wallets ?? "—"}</Text>
            <Text style={styles.statusLabel}>WALLETS</Text>
          </View>
          <View style={styles.statusItem}>
            <Text style={styles.statusVal}>{status?.entries_24h ?? "—"}</Text>
            <Text style={styles.statusLabel}>BUYS · 24H</Text>
          </View>
          <View style={styles.statusItem}>
            <View style={styles.statusValRow}>
              <View
                style={[
                  styles.healthDot,
                  {
                    backgroundColor:
                      status && status.last_tick_age_sec !== null && status.last_tick_age_sec < 180
                        ? colors.positive
                        : colors.negative,
                  },
                ]}
              />
              <Text style={styles.statusValSmall}>
                {status?.last_tick_age_sec != null ? `${Math.floor(status.last_tick_age_sec)}s` : "—"}
              </Text>
            </View>
            <Text style={styles.statusLabel}>LAST POLL</Text>
          </View>
        </View>

        <View style={styles.tabRow}>
          <Pressable
            onPress={() => setTab("feed")}
            style={[styles.tabBtn, tab === "feed" && styles.tabBtnActive]}
          >
            <Text style={[styles.tabText, tab === "feed" && styles.tabTextActive]}>FEED</Text>
          </Pressable>
          <Pressable
            onPress={() => setTab("wallets")}
            style={[styles.tabBtn, tab === "wallets" && styles.tabBtnActive]}
          >
            <Text style={[styles.tabText, tab === "wallets" && styles.tabTextActive]}>WALLETS</Text>
          </Pressable>
        </View>
      </ResponsiveContent>

      {loading ? (
        <View style={styles.loadingBox}>
          <ActivityIndicator size="small" color={colors.textSecondary} />
        </View>
      ) : (
        <FlatList
          data={tab === "feed" ? entries : (wallets as any)}
          keyExtractor={(it: any) => it.id}
          renderItem={tab === "feed" ? renderEntry : (renderWallet as any)}
          contentContainerStyle={styles.list}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={() => {
                setRefreshing(true);
                load();
              }}
              tintColor={colors.textSecondary}
            />
          }
          ListEmptyComponent={
            <View style={styles.emptyBox}>
              <Feather
                name={tab === "feed" ? "activity" : "users"}
                size={32}
                color={colors.textTertiary}
              />
              <Text style={styles.emptyText}>
                {tab === "feed"
                  ? "No recent whale buys ≥ $500. Tracked wallets are quiet."
                  : "No tracked wallets yet."}
              </Text>
            </View>
          }
        />
      )}

      <Modal
        visible={addOpen}
        animationType="slide"
        transparent
        onRequestClose={() => setAddOpen(false)}
      >
        <KeyboardAvoidingView
          behavior={Platform.OS === "ios" ? "padding" : "height"}
          style={styles.modalRoot}
        >
          <View style={styles.modalSheet}>
            <View style={styles.modalHandle} />
            <Text style={styles.modalTitle}>ADD WALLET</Text>
            <Text style={styles.modalSub}>
              Any Polymarket trader address. We'll poll their public on-chain activity every 90s.
            </Text>
            <View style={styles.modalField}>
              <Text style={styles.fieldLabel}>ETHEREUM ADDRESS</Text>
              <TextInput
                value={addAddress}
                onChangeText={setAddAddress}
                placeholder="0x..."
                placeholderTextColor={colors.textMuted}
                style={styles.input}
                autoCapitalize="none"
                autoCorrect={false}
              />
            </View>
            <View style={styles.modalField}>
              <Text style={styles.fieldLabel}>LABEL (OPTIONAL)</Text>
              <TextInput
                value={addLabel}
                onChangeText={setAddLabel}
                placeholder="e.g., MyWhale-01"
                placeholderTextColor={colors.textMuted}
                style={styles.input}
              />
            </View>
            <View style={styles.modalActions}>
              <Pressable
                onPress={() => setAddOpen(false)}
                style={[styles.modalBtn, styles.modalBtnSecondary]}
              >
                <Text style={styles.modalBtnTextSecondary}>CANCEL</Text>
              </Pressable>
              <Pressable
                onPress={handleAddWallet}
                disabled={adding}
                style={[styles.modalBtn, styles.modalBtnPrimary]}
              >
                {adding ? (
                  <ActivityIndicator size="small" color={colors.primaryText} />
                ) : (
                  <Text style={styles.modalBtnTextPrimary}>ADD</Text>
                )}
              </Pressable>
            </View>
          </View>
        </KeyboardAvoidingView>
      </Modal>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  headerBar: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: space.md,
    paddingTop: space.sm,
    paddingBottom: space.md,
  },
  closeBtn: {
    width: 36,
    height: 36,
    borderRadius: radius.md,
    backgroundColor: colors.surfaceHi,
    alignItems: "center",
    justifyContent: "center",
  },
  addBtn: {
    width: 36,
    height: 36,
    borderRadius: radius.md,
    backgroundColor: colors.surfaceHi,
    alignItems: "center",
    justifyContent: "center",
  },
  headerTitle: { ...typo.micro, color: colors.textSecondary, letterSpacing: 2 },
  statusBar: {
    flexDirection: "row",
    justifyContent: "space-around",
    paddingHorizontal: space.md,
    paddingVertical: space.md,
    backgroundColor: colors.surface,
    marginHorizontal: space.md,
    borderRadius: radius.md,
  },
  statusItem: { alignItems: "center" },
  statusVal: { ...typo.h3, color: colors.textPrimary },
  statusValRow: { flexDirection: "row", alignItems: "center", gap: 6 },
  statusValSmall: { ...typo.bodyLarge, color: colors.textPrimary },
  statusLabel: { ...typo.micro, color: colors.textTertiary, letterSpacing: 1.2, marginTop: 2 },
  healthDot: { width: 8, height: 8, borderRadius: 4 },
  tabRow: { flexDirection: "row", paddingHorizontal: space.md, marginTop: space.md, gap: space.sm },
  tabBtn: {
    flex: 1,
    paddingVertical: 10,
    borderRadius: radius.md,
    backgroundColor: colors.surface,
    alignItems: "center",
  },
  tabBtnActive: { backgroundColor: colors.primary },
  tabText: { ...typo.micro, color: colors.textSecondary, letterSpacing: 1.5 },
  tabTextActive: { color: colors.primaryText },
  list: {
    paddingHorizontal: space.md,
    paddingTop: space.md,
    paddingBottom: 80,
    gap: space.sm,
    width: "100%",
    maxWidth: 760,
    alignSelf: "center",
  },
  entryCard: { padding: space.md, gap: space.sm },
  entryHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  entryHeaderLeft: { flexDirection: "row", alignItems: "center", gap: 6 },
  dot: { width: 7, height: 7, borderRadius: 4 },
  walletLabel: { ...typo.body, color: colors.textPrimary, fontWeight: "600" },
  dotSep: { color: colors.textTertiary, marginHorizontal: 2 },
  timeAgo: { ...typo.caption, color: colors.textTertiary },
  usdc: { ...typo.bodyLarge, color: colors.positive, fontWeight: "700" },
  market: { ...typo.body, color: colors.textSecondary, marginTop: 2 },
  entryFooter: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    marginTop: space.xs,
  },
  outcomeRow: { flexDirection: "row", alignItems: "baseline", gap: 6 },
  outcomeLabel: { ...typo.micro, color: colors.positive, letterSpacing: 1 },
  outcomeValue: { ...typo.body, color: colors.textPrimary, fontWeight: "600" },
  priceLabel: { ...typo.caption, color: colors.textTertiary },
  priceValue: { ...typo.body, color: colors.textPrimary },
  mirrorBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingHorizontal: space.md,
    paddingVertical: 8,
    borderRadius: radius.md,
    backgroundColor: colors.primary,
    minHeight: 32,
  },
  mirrorBtnDisabled: { backgroundColor: colors.surfaceHi },
  mirrorBtnLoading: { opacity: 0.7 },
  mirrorBtnText: { ...typo.micro, color: colors.primaryText, letterSpacing: 1.2, fontWeight: "700" },
  mirrorBtnTextDisabled: { color: colors.textTertiary },
  walletCard: { padding: space.md },
  walletRow: { flexDirection: "row", alignItems: "flex-start", gap: space.sm },
  walletTopRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  walletName: { ...typo.bodyLarge, color: colors.textPrimary, fontWeight: "600" },
  badge: {
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 4,
    backgroundColor: colors.whale + "20",
  },
  badgeText: { ...typo.micro, fontSize: 9, color: colors.whale, letterSpacing: 1 },
  walletAddr: { ...typo.caption, color: colors.textSecondary, fontFamily: Platform.OS === "ios" ? "Menlo" : "monospace", marginTop: 2 },
  walletNote: { ...typo.body, color: colors.textSecondary, marginTop: 4 },
  walletMeta: { ...typo.caption, color: colors.textTertiary, marginTop: 6 },
  removeBtn: { padding: space.sm },
  loadingBox: { flex: 1, alignItems: "center", justifyContent: "center" },
  emptyBox: { alignItems: "center", justifyContent: "center", padding: space.xxl, gap: space.sm },
  emptyText: { ...typo.body, color: colors.textTertiary, textAlign: "center", maxWidth: 280 },
  modalRoot: { flex: 1, justifyContent: "flex-end", backgroundColor: "rgba(0,0,0,0.55)" },
  modalSheet: {
    backgroundColor: colors.modal,
    borderTopLeftRadius: radius.xl,
    borderTopRightRadius: radius.xl,
    padding: space.lg,
    paddingBottom: space.xxl,
    gap: space.md,
  },
  modalHandle: {
    width: 36,
    height: 4,
    borderRadius: 2,
    backgroundColor: colors.textTertiary,
    alignSelf: "center",
    marginTop: -space.sm,
    marginBottom: space.sm,
  },
  modalTitle: { ...typo.h3, color: colors.textPrimary, letterSpacing: 1 },
  modalSub: { ...typo.body, color: colors.textSecondary },
  modalField: { gap: 6 },
  fieldLabel: { ...typo.micro, color: colors.textTertiary, letterSpacing: 1.2 },
  input: {
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    paddingHorizontal: space.md,
    paddingVertical: 12,
    color: colors.textPrimary,
    fontSize: 15,
    minHeight: 48,
  },
  modalActions: { flexDirection: "row", gap: space.sm, marginTop: space.sm },
  modalBtn: {
    flex: 1,
    paddingVertical: 14,
    borderRadius: radius.md,
    alignItems: "center",
    justifyContent: "center",
    minHeight: 48,
  },
  modalBtnPrimary: { backgroundColor: colors.primary },
  modalBtnSecondary: { backgroundColor: colors.surfaceHi },
  modalBtnTextPrimary: { ...typo.micro, color: colors.primaryText, letterSpacing: 1.5, fontWeight: "700" },
  modalBtnTextSecondary: { ...typo.micro, color: colors.textPrimary, letterSpacing: 1.5 },
});
