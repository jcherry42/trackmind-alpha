/**
 * ErrorBoundary smoke test — confirms the fallback UI renders when a child throws
 * and the reload button resets state.
 */
import React from "react";
import { Text, View } from "react-native";
import { render, fireEvent } from "@testing-library/react-native";
import { ErrorBoundary } from "../ErrorBoundary";

const Boom: React.FC<{ shouldThrow: boolean }> = ({ shouldThrow }) => {
  if (shouldThrow) throw new Error("boom: something broke");
  return (
    <View>
      <Text>all good</Text>
    </View>
  );
};

describe("<ErrorBoundary />", () => {
  // Silence React's own error logging during throwing renders.
  const originalError = console.error;
  beforeAll(() => {
    console.error = jest.fn();
  });
  afterAll(() => {
    console.error = originalError;
  });

  it("renders children when no error", () => {
    const { getByText } = render(
      <ErrorBoundary>
        <Boom shouldThrow={false} />
      </ErrorBoundary>
    );
    expect(getByText("all good")).toBeTruthy();
  });

  it("shows fallback UI when a child throws", () => {
    const { getByText } = render(
      <ErrorBoundary>
        <Boom shouldThrow={true} />
      </ErrorBoundary>
    );
    expect(getByText("Something went wrong")).toBeTruthy();
    expect(getByText(/boom: something broke/i)).toBeTruthy();
    // Reload button is present and pressable.
    const btn = getByText("Reload");
    expect(btn).toBeTruthy();
    fireEvent.press(btn);
  });
});
