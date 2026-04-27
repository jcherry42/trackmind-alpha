import React, { useEffect } from "react";
import { ViewStyle } from "react-native";
import Animated, {
  useSharedValue,
  useAnimatedStyle,
  withTiming,
  withDelay,
  Easing,
  withRepeat,
  withSequence,
} from "react-native-reanimated";
import { colors } from "../lib/theme";

/**
 * FadeSlideIn — entrance animation for any view.
 * Plays once on mount. Subtle 400ms fade + 12px upward translate.
 */
export function FadeSlideIn({
  delay = 0,
  distance = 12,
  duration = 420,
  style,
  children,
}: {
  delay?: number;
  distance?: number;
  duration?: number;
  style?: ViewStyle | ViewStyle[];
  children: React.ReactNode;
}) {
  const opacity = useSharedValue(0);
  const translate = useSharedValue(distance);

  useEffect(() => {
    opacity.value = withDelay(
      delay,
      withTiming(1, { duration, easing: Easing.out(Easing.cubic) })
    );
    translate.value = withDelay(
      delay,
      withTiming(0, { duration, easing: Easing.out(Easing.cubic) })
    );
  }, [delay, duration, opacity, translate]);

  const animated = useAnimatedStyle(() => ({
    opacity: opacity.value,
    transform: [{ translateY: translate.value }],
  }));

  return <Animated.View style={[style, animated]}>{children}</Animated.View>;
}

/**
 * Skeleton — pulsing placeholder block.
 */
export function Skeleton({
  width,
  height = 14,
  radius = 6,
  style,
}: {
  width: number | `${number}%`;
  height?: number;
  radius?: number;
  style?: ViewStyle;
}) {
  const opacity = useSharedValue(0.5);

  useEffect(() => {
    opacity.value = withRepeat(
      withSequence(
        withTiming(1, { duration: 800, easing: Easing.inOut(Easing.ease) }),
        withTiming(0.5, { duration: 800, easing: Easing.inOut(Easing.ease) })
      ),
      -1,
      false
    );
  }, [opacity]);

  const animated = useAnimatedStyle(() => ({ opacity: opacity.value }));

  return (
    <Animated.View
      style={[
        {
          width: width as any,
          height,
          borderRadius: radius,
          backgroundColor: colors.surfaceHi,
        },
        animated,
        style,
      ]}
    />
  );
}

/**
 * PressScale — wraps children to scale-down briefly when pressed for tactile feel.
 * Use as a Pressable child wrapper.
 */
export function PressScale({
  pressed,
  scale = 0.97,
  children,
  style,
}: {
  pressed: boolean;
  scale?: number;
  children: React.ReactNode;
  style?: ViewStyle | ViewStyle[];
}) {
  const s = useSharedValue(1);
  useEffect(() => {
    s.value = withTiming(pressed ? scale : 1, {
      duration: 120,
      easing: Easing.out(Easing.quad),
    });
  }, [pressed, s, scale]);
  const animated = useAnimatedStyle(() => ({ transform: [{ scale: s.value }] }));
  return <Animated.View style={[style, animated]}>{children}</Animated.View>;
}
