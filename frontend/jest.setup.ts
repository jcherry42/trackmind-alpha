// Jest setup — runs before each test file.
// We mock native modules that aren't reachable inside Jest's JS-only runtime.
// Note: @testing-library/react-native v12+ ships its own jest matchers, so we
// don't need jest-native/extend-expect here.

// ---- AsyncStorage (already shipped by community mock) ----
jest.mock("@react-native-async-storage/async-storage", () =>
  require("@react-native-async-storage/async-storage/jest/async-storage-mock")
);

// ---- expo-secure-store (stateful in-memory stub) ----
jest.mock("expo-secure-store", () => {
  const _mem: Record<string, string> = {};
  return {
    getItemAsync: jest.fn(async (k: string) => (k in _mem ? _mem[k] : null)),
    setItemAsync: jest.fn(async (k: string, v: string) => {
      _mem[k] = v;
    }),
    deleteItemAsync: jest.fn(async (k: string) => {
      delete _mem[k];
    }),
    WHEN_UNLOCKED: 0,
  };
});

// ---- expo-haptics ----
jest.mock("expo-haptics", () => ({
  selectionAsync: jest.fn(),
  impactAsync: jest.fn(),
  notificationAsync: jest.fn(),
  ImpactFeedbackStyle: { Light: "light", Medium: "medium", Heavy: "heavy" },
  NotificationFeedbackType: { Success: "s", Warning: "w", Error: "e" },
}));

// ---- expo-local-authentication ----
jest.mock("expo-local-authentication", () => ({
  hasHardwareAsync: jest.fn(async () => true),
  isEnrolledAsync: jest.fn(async () => true),
  authenticateAsync: jest.fn(async () => ({ success: true })),
}));

// ---- expo-router (minimal surface used by our screens) ----
jest.mock("expo-router", () => {
  const React = require("react");
  return {
    useRouter: () => ({
      push: jest.fn(),
      replace: jest.fn(),
      back: jest.fn(),
    }),
    useLocalSearchParams: () => ({}),
    Link: ({ children, ...props }: any) => React.createElement("Text", props, children),
    Stack: {
      Screen: ({ children }: any) => children ?? null,
    },
    Tabs: Object.assign(
      ({ children }: any) => children,
      { Screen: ({ children }: any) => children ?? null },
    ),
    Redirect: () => null,
  };
});

// ---- global fetch mock (tests override per-scenario) ----
(global as any).fetch = jest.fn();

// ---- silence noisy warnings ----
const realWarn = console.warn;
console.warn = (...args: any[]) => {
  const msg = String(args[0] ?? "");
  if (
    msg.includes("useNativeDriver") ||
    msg.includes("Animated:") ||
    msg.includes("shadow*")
  ) {
    return;
  }
  realWarn.apply(console, args as [any, ...any[]]);
};
