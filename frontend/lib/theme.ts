// Centralized theme — sourced from /app/design_guidelines.json
export const colors = {
  bg: "#000000",
  surface: "#0C0C0E",
  surfaceElevated: "#141417",
  modal: "#1C1C1E",

  textPrimary: "#FFFFFF",
  textSecondary: "#8A8A8E",
  textTertiary: "#505055",
  textInverse: "#000000",

  borderSubtle: "#1C1C1E",
  borderFocus: "#303036",

  positive: "#00D84A",
  positiveBg: "rgba(0,216,74,0.10)",
  negative: "#FF3344",
  negativeBg: "rgba(255,51,68,0.10)",
  neutral: "#8A8A8E",

  alpha: "#E5A823",
  quant: "#007AFF",
  whale: "#32ADE6",
  micro: "#FF9F0A",

  primary: "#FFFFFF",
  primaryText: "#000000",
  danger: "#FF3344",
} as const;

export const space = {
  xs: 4,
  sm: 8,
  md: 16,
  lg: 24,
  xl: 32,
  xxl: 48,
} as const;

export const radius = {
  none: 0,
  sm: 4,
  md: 8,
  lg: 12,
  pill: 9999,
} as const;

export const type = {
  h1: { fontSize: 34, fontWeight: "800" as const, lineHeight: 41, letterSpacing: -1 },
  h2: { fontSize: 28, fontWeight: "700" as const, lineHeight: 34, letterSpacing: -0.7 },
  h3: { fontSize: 22, fontWeight: "600" as const, lineHeight: 28, letterSpacing: -0.4 },
  bodyLarge: { fontSize: 17, fontWeight: "500" as const, lineHeight: 22 },
  body: { fontSize: 15, fontWeight: "400" as const, lineHeight: 20 },
  caption: { fontSize: 12, fontWeight: "500" as const, lineHeight: 16 },
  micro: {
    fontSize: 10,
    fontWeight: "600" as const,
    lineHeight: 12,
    letterSpacing: 1.5,
    textTransform: "uppercase" as const,
  },
  metric: {
    fontVariant: ["tabular-nums" as const],
    letterSpacing: -0.5,
    fontWeight: "700" as const,
  },
};

export const engineColor = (engine: string): string => {
  switch (engine) {
    case "alpha":
      return colors.alpha;
    case "quant":
      return colors.quant;
    case "whale":
      return colors.whale;
    case "micro":
      return colors.micro;
    default:
      return colors.textSecondary;
  }
};
