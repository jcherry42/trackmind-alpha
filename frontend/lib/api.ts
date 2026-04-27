import AsyncStorage from "@react-native-async-storage/async-storage";
import * as SecureStore from "expo-secure-store";
import { Platform } from "react-native";

const BASE = process.env.EXPO_PUBLIC_BACKEND_URL || "";

export type ApiOpts = {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  body?: unknown;
  signal?: AbortSignal;
  /** When true (default for trade endpoints), an Idempotency-Key header is auto-attached. */
  idempotent?: boolean;
};

const TOKEN_KEY = "tm_access_token";
const REFRESH_KEY = "tm_refresh_token";

/**
 * Cross-platform secure key-value store.
 * - Native (iOS/Android): expo-secure-store (Keychain / Keystore).
 * - Web: AsyncStorage (best available in browser context).
 */
const kv = {
  async get(key: string): Promise<string | null> {
    if (Platform.OS === "web") return AsyncStorage.getItem(key);
    try {
      return await SecureStore.getItemAsync(key);
    } catch {
      return AsyncStorage.getItem(key);
    }
  },
  async set(key: string, value: string): Promise<void> {
    if (Platform.OS === "web") {
      await AsyncStorage.setItem(key, value);
      return;
    }
    try {
      await SecureStore.setItemAsync(key, value, {
        keychainAccessible: SecureStore.WHEN_UNLOCKED,
      });
    } catch {
      await AsyncStorage.setItem(key, value);
    }
  },
  async remove(key: string): Promise<void> {
    if (Platform.OS === "web") {
      await AsyncStorage.removeItem(key);
      return;
    }
    try {
      await SecureStore.deleteItemAsync(key);
    } catch {
      await AsyncStorage.removeItem(key);
    }
  },
};

export const tokenStore = {
  async get(): Promise<string | null> {
    return kv.get(TOKEN_KEY);
  },
  async getRefresh(): Promise<string | null> {
    return kv.get(REFRESH_KEY);
  },
  async set(access: string, refresh: string): Promise<void> {
    await kv.set(TOKEN_KEY, access);
    await kv.set(REFRESH_KEY, refresh);
  },
  async setAccess(access: string): Promise<void> {
    await kv.set(TOKEN_KEY, access);
  },
  async clear(): Promise<void> {
    await kv.remove(TOKEN_KEY);
    await kv.remove(REFRESH_KEY);
    // Also wipe any legacy AsyncStorage tokens from older builds.
    try {
      await AsyncStorage.multiRemove([TOKEN_KEY, REFRESH_KEY]);
    } catch {}
  },
};

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

function formatDetail(detail: unknown): string {
  if (detail == null) return "Something went wrong.";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail))
    return detail
      .map((e: any) => (e && typeof e.msg === "string" ? e.msg : JSON.stringify(e)))
      .join(" ");
  if (typeof detail === "object" && (detail as any).msg) return (detail as any).msg;
  return String(detail);
}

// Tiny UUIDv4 for idempotency keys (no runtime dep).
function uuid4(): string {
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

const IDEMPOTENT_DEFAULT_PATHS = ["/trade/approve", "/trade/auto-deploy"];

export async function api<T = any>(path: string, opts: ApiOpts = {}): Promise<T> {
  const url = `${BASE}/api${path}`;
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = await tokenStore.get();
  if (token) headers.Authorization = `Bearer ${token}`;

  // Auto-attach Idempotency-Key on POSTs to trade endpoints (or when explicitly requested).
  const shouldIdempotent =
    opts.idempotent ??
    (opts.method === "POST" && IDEMPOTENT_DEFAULT_PATHS.some((p) => path.startsWith(p)));
  if (shouldIdempotent) headers["Idempotency-Key"] = uuid4();

  let res = await fetch(url, {
    method: opts.method || "GET",
    headers,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
    signal: opts.signal,
  });

  if (res.status === 401 && path !== "/auth/refresh" && path !== "/auth/login") {
    // try refresh
    const refresh = await tokenStore.getRefresh();
    if (refresh) {
      const r = await fetch(`${BASE}/api/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${refresh}` },
      });
      if (r.ok) {
        const data = await r.json();
        if (data.access_token) {
          // Also rotate refresh if the server issued a new one (rotation).
          if (data.refresh_token) {
            await tokenStore.set(data.access_token, data.refresh_token);
          } else {
            await tokenStore.setAccess(data.access_token);
          }
          headers.Authorization = `Bearer ${data.access_token}`;
          res = await fetch(url, {
            method: opts.method || "GET",
            headers,
            body: opts.body ? JSON.stringify(opts.body) : undefined,
            signal: opts.signal,
          });
        }
      }
    }
  }

  const text = await res.text();
  let data: any = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { detail: text };
  }
  if (!res.ok) {
    throw new ApiError(formatDetail(data?.detail), res.status);
  }
  return data as T;
}
