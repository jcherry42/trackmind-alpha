import AsyncStorage from "@react-native-async-storage/async-storage";

const BASE = process.env.EXPO_PUBLIC_BACKEND_URL || "";

export type ApiOpts = {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  body?: unknown;
  signal?: AbortSignal;
};

const TOKEN_KEY = "tm_access_token";
const REFRESH_KEY = "tm_refresh_token";

export const tokenStore = {
  async get(): Promise<string | null> {
    return AsyncStorage.getItem(TOKEN_KEY);
  },
  async getRefresh(): Promise<string | null> {
    return AsyncStorage.getItem(REFRESH_KEY);
  },
  async set(access: string, refresh: string): Promise<void> {
    await AsyncStorage.multiSet([
      [TOKEN_KEY, access],
      [REFRESH_KEY, refresh],
    ]);
  },
  async setAccess(access: string): Promise<void> {
    await AsyncStorage.setItem(TOKEN_KEY, access);
  },
  async clear(): Promise<void> {
    await AsyncStorage.multiRemove([TOKEN_KEY, REFRESH_KEY]);
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

export async function api<T = any>(path: string, opts: ApiOpts = {}): Promise<T> {
  const url = `${BASE}/api${path}`;
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = await tokenStore.get();
  if (token) headers.Authorization = `Bearer ${token}`;

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
          await tokenStore.setAccess(data.access_token);
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
