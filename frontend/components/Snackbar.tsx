/**
 * Snackbar — global toast system with optional action button.
 *
 * Usage:
 *   const { show } = useSnackbar();
 *   show({ message: "Position opened", kind: "success" });
 *   show({ message: "Trade failed", kind: "error", action: { label: "Retry", onPress: retry } });
 *
 * Wrap the root layout with <SnackbarProvider>...</SnackbarProvider>.
 */
import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import Animated, {
  Easing,
  runOnJS,
  useAnimatedStyle,
  useSharedValue,
  withTiming,
} from "react-native-reanimated";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Feather } from "@expo/vector-icons";
import { colors, space, type as typo } from "../lib/theme";
import { haptics } from "../lib/haptics";

export type SnackbarKind = "success" | "error" | "info" | "warning";

export type SnackbarAction = {
  label: string;
  onPress: () => void | Promise<void>;
};

export type SnackbarOptions = {
  message: string;
  kind?: SnackbarKind;
  duration?: number; // ms, default 3500. Set 0 for sticky.
  action?: SnackbarAction;
};

type Ctx = {
  show: (o: SnackbarOptions) => void;
  hide: () => void;
};

const SnackbarContext = createContext<Ctx | null>(null);

export function useSnackbar(): Ctx {
  const ctx = useContext(SnackbarContext);
  if (!ctx) throw new Error("useSnackbar() outside <SnackbarProvider>");
  return ctx;
}

const KIND_META: Record<SnackbarKind, { icon: keyof typeof Feather.glyphMap; tint: string }> = {
  success: { icon: "check-circle", tint: colors.positive },
  error: { icon: "alert-octagon", tint: colors.negative },
  info: { icon: "info", tint: colors.primary },
  warning: { icon: "alert-triangle", tint: colors.alpha },
};

export function SnackbarProvider({ children }: { children: React.ReactNode }) {
  const insets = useSafeAreaInsets();
  const [opts, setOpts] = useState<SnackbarOptions | null>(null);
  const translateY = useSharedValue(120);
  const opacity = useSharedValue(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const hide = useCallback(() => {
    translateY.value = withTiming(120, { duration: 220, easing: Easing.in(Easing.cubic) });
    opacity.value = withTiming(
      0,
      { duration: 220 },
      (finished) => {
        if (finished) {
          runOnJS(setOpts)(null);
        }
      }
    );
    if (timer.current) {
      clearTimeout(timer.current);
      timer.current = null;
    }
  }, [translateY, opacity]);

  const show = useCallback(
    (o: SnackbarOptions) => {
      if (timer.current) clearTimeout(timer.current);
      setOpts(o);
      translateY.value = withTiming(0, { duration: 240, easing: Easing.out(Easing.cubic) });
      opacity.value = withTiming(1, { duration: 220 });
      // Subtle haptic per kind.
      try {
        if (o.kind === "success") haptics.success?.();
        else if (o.kind === "error") haptics.error?.();
        else haptics.select?.();
      } catch {
        /* haptics may not be available on web */
      }
      const dur = o.duration ?? 3500;
      if (dur > 0) {
        timer.current = setTimeout(hide, dur);
      }
    },
    [translateY, opacity, hide]
  );

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  const aStyle = useAnimatedStyle(() => ({
    transform: [{ translateY: translateY.value }],
    opacity: opacity.value,
  }));

  const value = useMemo(() => ({ show, hide }), [show, hide]);

  const meta = opts ? KIND_META[opts.kind ?? "info"] : null;

  return (
    <SnackbarContext.Provider value={value}>
      {children}
      {opts && meta && (
        <Animated.View
          pointerEvents="box-none"
          style={[
            styles.host,
            { paddingBottom: Math.max(insets.bottom, 12) + 12 },
            aStyle,
          ]}
        >
          <View
            style={[
              styles.bar,
              { borderColor: meta.tint + "55" },
            ]}
            accessibilityLiveRegion="polite"
            accessibilityRole="alert"
          >
            <View style={[styles.iconWrap, { backgroundColor: meta.tint + "1f" }]}>
              <Feather name={meta.icon} size={18} color={meta.tint} />
            </View>
            <Text style={styles.msg} numberOfLines={2}>
              {opts.message}
            </Text>
            {opts.action ? (
              <Pressable
                accessibilityRole="button"
                accessibilityLabel={opts.action.label}
                onPress={async () => {
                  try {
                    await opts.action!.onPress();
                  } finally {
                    hide();
                  }
                }}
                style={({ pressed }) => [
                  styles.action,
                  { borderColor: meta.tint, opacity: pressed ? 0.6 : 1 },
                ]}
              >
                <Text style={[styles.actionLabel, { color: meta.tint }]}>
                  {opts.action.label.toUpperCase()}
                </Text>
              </Pressable>
            ) : (
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="Dismiss"
                onPress={hide}
                style={styles.dismissBtn}
              >
                <Feather name="x" size={16} color={colors.textSecondary} />
              </Pressable>
            )}
          </View>
        </Animated.View>
      )}
    </SnackbarContext.Provider>
  );
}

const styles = StyleSheet.create({
  host: {
    position: "absolute",
    left: 0,
    right: 0,
    bottom: 0,
    paddingHorizontal: space.md,
    alignItems: "center",
  },
  bar: {
    width: "100%",
    maxWidth: 520,
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderRadius: 14,
    paddingVertical: 12,
    paddingHorizontal: 14,
    gap: 12,
    shadowColor: "#000",
    shadowOpacity: 0.45,
    shadowRadius: 18,
    shadowOffset: { width: 0, height: 8 },
    elevation: 12,
  },
  iconWrap: {
    width: 32,
    height: 32,
    borderRadius: 10,
    alignItems: "center",
    justifyContent: "center",
  },
  msg: {
    ...(typo.body as any),
    color: colors.textPrimary,
    flex: 1,
  },
  action: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 8,
    borderWidth: 1,
  },
  actionLabel: {
    fontSize: 12,
    letterSpacing: 1,
    fontWeight: "700",
  },
  dismissBtn: {
    width: 28,
    height: 28,
    alignItems: "center",
    justifyContent: "center",
  },
});
