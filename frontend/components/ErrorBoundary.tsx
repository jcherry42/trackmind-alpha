import React from "react";
import { StyleSheet, Text, View, Pressable } from "react-native";
import { colors, space, type as typo } from "../lib/theme";

type Props = { children: React.ReactNode };
type State = { error: Error | null };

/**
 * Screen-level error boundary.
 * Catches runtime errors thrown from any child component and shows a minimal
 * "Something went wrong" screen with a Reload button. Prevents the whole app
 * from white-screening when one screen throws.
 *
 * Wrap the root layout (or any risky subtree) with <ErrorBoundary>...</ErrorBoundary>.
 */
export class ErrorBoundary extends React.Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo): void {
    // Local log. Swap in Sentry/Crashlytics when integrated.
    // eslint-disable-next-line no-console
    console.error("[TRACKMIND:ErrorBoundary]", error, info?.componentStack);
  }

  private reset = () => this.setState({ error: null });

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <View style={styles.root}>
        <View style={styles.card}>
          <Text style={styles.tag}>SYSTEM</Text>
          <Text style={styles.title}>Something went wrong</Text>
          <Text style={styles.desc} numberOfLines={6}>
            {this.state.error.message || "Unexpected error."}
          </Text>
          <Pressable onPress={this.reset} style={styles.btn} accessibilityRole="button">
            <Text style={styles.btnLabel}>Reload</Text>
          </Pressable>
          <Text style={styles.hint}>
            If this keeps happening, sign out from Settings and back in.
          </Text>
        </View>
      </View>
    );
  }
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: colors.bg,
    alignItems: "center",
    justifyContent: "center",
    padding: space.lg,
  },
  card: {
    backgroundColor: colors.surface,
    borderRadius: 20,
    padding: space.lg,
    borderWidth: 1,
    borderColor: colors.borderSubtle,
    width: "100%",
    maxWidth: 460,
  },
  tag: {
    color: colors.danger,
    fontSize: 11,
    letterSpacing: 1.4,
    fontWeight: "700",
    marginBottom: space.sm,
  },
  title: { ...(typo.h2 as any), color: colors.textPrimary, marginBottom: space.sm },
  desc: { ...(typo.body as any), color: colors.textSecondary, marginBottom: space.md },
  btn: {
    backgroundColor: colors.primary,
    paddingVertical: 12,
    borderRadius: 12,
    alignItems: "center",
  },
  btnLabel: { ...(typo.body as any), color: colors.primaryText, fontWeight: "700" },
  hint: {
    ...(typo.caption as any),
    color: colors.textSecondary,
    marginTop: space.md,
    textAlign: "center",
  },
});
