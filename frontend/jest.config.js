/**
 * Jest config for TRACKMIND frontend.
 *
 * We deliberately avoid the `jest-expo` preset because it clashes with the
 * Expo SDK 54+ winter runtime when modules import import.meta. Instead we
 * use a thin RN preset plus targeted transforms for Expo packages.
 */
module.exports = {
  preset: "react-native",
  rootDir: ".",
  setupFiles: ["./jest.setup.ts"],
  transform: {
    "^.+\\.(js|jsx|ts|tsx)$": [
      "babel-jest",
      { presets: ["babel-preset-expo"] },
    ],
  },
  transformIgnorePatterns: [
    "node_modules/(?!(?:jest-)?react-native|@react-native(?:-community)?|@react-navigation|expo(?:nent)?|@expo(?:nent)?/.*|@expo-google-fonts/.*|react-clone-referenced-element|@sentry/react-native|native-base|react-native-svg)",
  ],
  testPathIgnorePatterns: ["/node_modules/", "/\\.expo/", "/dist/"],
  moduleNameMapper: {
    "\\.(png|jpg|jpeg|gif|webp|svg)$": "<rootDir>/__mocks__/fileMock.js",
    // Stub Expo winter runtime (import.meta) to avoid its own sandbox check in jest.
    "expo/src/winter/(.+)": "<rootDir>/__mocks__/expoWinter.js",
  },
  testMatch: ["**/__tests__/**/*.test.(ts|tsx)"],
  collectCoverageFrom: [
    "app/**/*.{ts,tsx}",
    "components/**/*.{ts,tsx}",
    "lib/**/*.{ts,tsx}",
    "!**/*.d.ts",
  ],
  testEnvironment: "node",
};
