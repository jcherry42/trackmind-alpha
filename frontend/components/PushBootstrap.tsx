/**
 * PushBootstrap — registers the device for Expo push notifications and POSTs the
 * Expo push token to the backend so the server can fan-out alerts.
 *
 * Mounted once near the auth provider. No-ops on web and on simulators.
 */
import { useEffect } from "react";
import * as Notifications from "expo-notifications";
import * as Device from "expo-device";
import { Platform } from "react-native";
import { useAuth } from "../lib/auth";
import { api } from "../lib/api";

// Default behaviour: show banner + play sound for foreground notifications.
Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowBanner: true,
    shouldShowList: true,
    shouldPlaySound: true,
    shouldSetBadge: true,
  }),
});

async function registerForPushNotifications(): Promise<string | null> {
  if (Platform.OS === "web") return null;
  if (!Device.isDevice) return null;

  const { status: existing } = await Notifications.getPermissionsAsync();
  let final = existing;
  if (existing !== "granted") {
    const { status } = await Notifications.requestPermissionsAsync();
    final = status;
  }
  if (final !== "granted") return null;

  if (Platform.OS === "android") {
    await Notifications.setNotificationChannelAsync("trackmind-signals", {
      name: "Master Signals",
      importance: Notifications.AndroidImportance.HIGH,
      vibrationPattern: [0, 240, 120, 240],
      lightColor: "#5BFFB1",
    });
  }

  // Avoid hard-failing if projectId isn't configured yet (e.g. dev client).
  try {
    const token = (await Notifications.getExpoPushTokenAsync()).data;
    return token;
  } catch (e) {
    // eslint-disable-next-line no-console
    console.warn("[push] getExpoPushTokenAsync failed:", e);
    return null;
  }
}

export function PushBootstrap() {
  const { user } = useAuth();

  useEffect(() => {
    let cancelled = false;
    if (!user) return;
    (async () => {
      const token = await registerForPushNotifications();
      if (!token || cancelled) return;
      try {
        await api("/me/push-token", {
          method: "POST",
          body: { token, platform: Platform.OS },
          idempotent: false,
        });
      } catch {
        /* non-critical; user can re-trigger by reopening app */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [user]);

  return null;
}
