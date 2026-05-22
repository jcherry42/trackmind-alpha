/**
 * AlertPoller — Expo Go fallback for remote push notifications.
 *
 * Polls /api/me/alerts/unseen every ALERT_POLL_INTERVAL_MS while the app is open
 * (foreground OR backgrounded — both work for local notifications). For each new
 * alert the backend would have pushed via Expo's remote service, we instead fire
 * a LOCAL notification with the same title/body/sound. From the user's POV it
 * looks identical to a real push: banner + sound + tap-to-open.
 *
 * Works WITHOUT a configured projectId / EAS setup. The limitation is that
 * notifications won't wake the app when it has been swiped-away/killed by the
 * user — only the OS-level APNs/FCM channel can do that, and that requires a
 * real Expo dev build + projectId.
 *
 * Cursor persistence: we store the latest seen alert ts in AsyncStorage so the
 * device only fires for alerts created AFTER the most recent poll. On first
 * mount, the backend caps the lookback to the last 30 minutes (avoids spamming
 * on fresh installs).
 */
import { useEffect, useRef } from "react";
import { AppState, Platform } from "react-native";
import * as Notifications from "expo-notifications";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { useAuth } from "../lib/auth";
import { api } from "../lib/api";

const ALERT_POLL_INTERVAL_MS = 20_000;
const CURSOR_STORAGE_KEY = "trackmind.alert.cursor.v1";
const SEEN_IDS_STORAGE_KEY = "trackmind.alert.seenIds.v1";
// Cap how many alert IDs we remember (avoid unbounded growth).
const SEEN_IDS_MAX = 200;

type Alert = {
  id: string;
  category: string;
  title: string;
  body: string;
  data: Record<string, unknown>;
  ts: number;
  delivered_push: boolean;
};

async function loadCursor(): Promise<number | null> {
  try {
    const raw = await AsyncStorage.getItem(CURSOR_STORAGE_KEY);
    return raw ? Number(raw) : null;
  } catch {
    return null;
  }
}

async function saveCursor(ts: number): Promise<void> {
  try {
    await AsyncStorage.setItem(CURSOR_STORAGE_KEY, String(ts));
  } catch {
    /* non-critical */
  }
}

async function loadSeenIds(): Promise<Set<string>> {
  try {
    const raw = await AsyncStorage.getItem(SEEN_IDS_STORAGE_KEY);
    if (!raw) return new Set();
    const arr: string[] = JSON.parse(raw);
    return new Set(arr);
  } catch {
    return new Set();
  }
}

async function saveSeenIds(seen: Set<string>): Promise<void> {
  try {
    let arr = Array.from(seen);
    if (arr.length > SEEN_IDS_MAX) arr = arr.slice(-SEEN_IDS_MAX);
    await AsyncStorage.setItem(SEEN_IDS_STORAGE_KEY, JSON.stringify(arr));
  } catch {
    /* non-critical */
  }
}

async function fireLocalNotification(a: Alert): Promise<void> {
  // Skip on web — Notifications.scheduleNotificationAsync exists but doesn't
  // produce real banner toasts in the browser.
  if (Platform.OS === "web") return;
  try {
    await Notifications.scheduleNotificationAsync({
      content: {
        title: a.title,
        body: a.body,
        sound: "default",
        data: { ...a.data, category: a.category, alertId: a.id },
      },
      trigger: null, // fire immediately
    });
  } catch {
    /* non-critical — banner just won't show */
  }
}

export function AlertPoller() {
  const { user } = useAuth();
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const inFlightRef = useRef(false);

  useEffect(() => {
    if (!user) return;

    let cancelled = false;

    async function tick(): Promise<void> {
      if (inFlightRef.current || cancelled) return;
      inFlightRef.current = true;
      try {
        const cursor = await loadCursor();
        const seenIds = await loadSeenIds();
        const res = await api<{ alerts: Alert[]; cursor: number }>(
          `/me/alerts/unseen${cursor != null ? `?since=${cursor}` : ""}`,
        );
        const newAlerts = (res.alerts || []).filter((a) => !seenIds.has(a.id));
        if (newAlerts.length > 0) {
          // Fire in order so the most-recent banner stays on top.
          for (const a of newAlerts) {
            // If a remote push already arrived for this alert (delivered_push=true
            // AND the device has a real APNs/FCM token), skip the local copy to
            // avoid double notifications. Otherwise always fire locally.
            if (!a.delivered_push) {
              await fireLocalNotification(a);
            }
            seenIds.add(a.id);
          }
          await saveSeenIds(seenIds);
        }
        if (res.cursor) await saveCursor(res.cursor);
      } catch {
        /* network blip — retry on next tick */
      } finally {
        inFlightRef.current = false;
      }
    }

    // Fire once immediately, then every ALERT_POLL_INTERVAL_MS while active.
    tick();
    timerRef.current = setInterval(tick, ALERT_POLL_INTERVAL_MS);

    // Re-tick on app foreground (catches up faster than the steady cadence).
    const sub = AppState.addEventListener("change", (state) => {
      if (state === "active") tick();
    });

    return () => {
      cancelled = true;
      if (timerRef.current) clearInterval(timerRef.current);
      sub.remove();
    };
  }, [user]);

  return null;
}
