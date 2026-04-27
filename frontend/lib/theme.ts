// Refined minimalist theme — more whitespace, hero typography, single-accent layouts.
export const colors = {
  bg: "#000000",
  surface: "#0A0A0C",
  surfaceElevated: "#101013",
  surfaceHi: "#16161A",
  modal: "#1A1A1E",

  textPrimary: "#FFFFFF",
  textSecondary: "#9A9AA0",
  textTertiary: "#5A5A60",
  textMuted: "#3A3A40",
  textInverse: "#000000",

  borderHairline: "#16161A",
  borderSubtle: "#1F1F24",
  borderFocus: "#33333A",

  positive: "#3DDB7E",
  positiveSoft: "rgba(61,219,126,0.10)",
  negative: "#FF4D5E",
  negativeSoft: "rgba(255,77,94,0.10)",
  neutral: "#9A9AA0",

  alpha: "#F2B544",
  quant: "#5C9DFF",
  whale: "#5BD2F5",
  micro: "#FFA547",

  primary: "#FFFFFF",
  primaryText: "#000000",
  danger: "#FF4D5E",
} as const;

export const space = {
  xs: 4,
  sm: 8,
  md: 16,
  lg: 24,
  xl: 32,
  xxl: 48,
  xxxl: 64,
} as const;

export const radius = {
  none: 0,
  sm: 4,
  md: 10,
  lg: 16,
  xl: 22,
  pill: 9999,
} as const;

export const type = {
  hero: { fontSize: 56, fontWeight: "800" as const, lineHeight: 60, letterSpacing: -2 },
  h1: { fontSize: 34, fontWeight: "800" as const, lineHeight: 41, letterSpacing: -1 },
  h2: { fontSize: 26, fontWeight: "700" as const, lineHeight: 32, letterSpacing: -0.6 },
  h3: { fontSize: 20, fontWeight: "600" as const, lineHeight: 26, letterSpacing: -0.3 },
  bodyLarge: { fontSize: 17, fontWeight: "500" as const, lineHeight: 22 },
  body: { fontSize: 14, fontWeight: "400" as const, lineHeight: 20 },
  caption: { fontSize: 12, fontWeight: "500" as const, lineHeight: 16 },
  micro: {
    fontSize: 10,
    fontWeight: "600" as const,
    lineHeight: 12,
    letterSpacing: 1.6,
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

export const hexAlpha = (hex: string, alpha: number): string => {
  if (!hex.startsWith("#") || hex.length !== 7) return hex;
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${alpha})`;
};
