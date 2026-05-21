/**
 * Responsive layout helpers.
 *
 * The app is **phone-portrait** by default, but iPads are locked to landscape
 * (configured in app.json's UISupportedInterfaceOrientations~ipad). On the wider
 * iPad canvas (~1366 × 1024) we don't want the phone layout to stretch edge-to-
 * edge — that looks broken. Instead we:
 *
 *   1. Center all main content inside a max-width column (700pt).
 *   2. Allow specific screens (Dashboard) to opt into a two-column layout via
 *      `useResponsive().twoCol`.
 *
 * Keep this file framework-agnostic — just dimensions + flags. NO styling
 * decisions live here; the consuming screen owns its layout.
 */
import { useEffect, useState } from "react";
import { Dimensions, Platform, ScaledSize } from "react-native";

export type Responsive = {
  /** Current device width (pt). */
  width: number;
  /** Current device height (pt). */
  height: number;
  /** True when landscape (width > height) AND device is wide enough for two-column. */
  isLandscape: boolean;
  /** True on iPad / Android tablets — `Platform.isPad` on iOS, ≥600pt min-dim on Android. */
  isTablet: boolean;
  /**
   * True when we should use the two-column iPad-landscape layout.
   * Phones never get this even rotated, because the dimensions threshold isn't hit.
   */
  twoCol: boolean;
  /** Max content width when in two-column layout (each column). */
  colMaxWidth: number;
};

function compute(dim: ScaledSize): Responsive {
  const { width, height } = dim;
  const isLandscape = width > height;
  // Platform.isPad is available on iOS; on Android, treat ≥600pt shortest-side as tablet.
  const shortSide = Math.min(width, height);
  const isTablet =
    (Platform.OS === "ios" && (Platform as any).isPad === true) ||
    shortSide >= 600;
  // Two-column only when tablet AND landscape AND wide enough (≥900pt).
  const twoCol = isTablet && isLandscape && width >= 900;
  // When two-column, each column gets ~half the screen minus 64pt total padding.
  const colMaxWidth = twoCol ? Math.floor((width - 64) / 2) : Math.min(720, width);
  return { width, height, isLandscape, isTablet, twoCol, colMaxWidth };
}

/**
 * Subscribe to dimension changes. Returns the current responsive descriptor.
 * Use in any screen / component that needs to adapt to iPad landscape.
 */
export function useResponsive(): Responsive {
  const [r, setR] = useState<Responsive>(() => compute(Dimensions.get("window")));
  useEffect(() => {
    const sub = Dimensions.addEventListener("change", ({ window }) => {
      setR(compute(window));
    });
    return () => sub?.remove();
  }, []);
  return r;
}
