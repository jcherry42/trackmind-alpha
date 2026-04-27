import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api, tokenStore } from "./api";

export type User = {
  id: string;
  email: string;
  name: string;
  role: string;
  biometrics_enabled: boolean;
  live_mode: boolean;
  bankroll: number;
  starting_bankroll: number;
  paused_engines: string[];
  kill_switch: boolean;
  created_at: string;
};

type AuthState = {
  user: User | null | undefined; // undefined = checking
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, name?: string) => Promise<void>;
  logout: () => Promise<void>;
  refreshUser: () => Promise<void>;
};

const AuthCtx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null | undefined>(undefined);

  const refreshUser = useCallback(async () => {
    try {
      const me = await api<User>("/auth/me");
      setUser(me);
    } catch {
      setUser(null);
    }
  }, []);

  useEffect(() => {
    (async () => {
      const token = await tokenStore.get();
      if (!token) {
        setUser(null);
        return;
      }
      await refreshUser();
    })();
  }, [refreshUser]);

  const login = useCallback(async (email: string, password: string) => {
    const data = await api<{ access_token: string; refresh_token: string; user: User }>(
      "/auth/login",
      { method: "POST", body: { email, password } }
    );
    await tokenStore.set(data.access_token, data.refresh_token);
    setUser(data.user);
  }, []);

  const register = useCallback(async (email: string, password: string, name?: string) => {
    const data = await api<{ access_token: string; refresh_token: string; user: User }>(
      "/auth/register",
      { method: "POST", body: { email, password, name: name || "" } }
    );
    await tokenStore.set(data.access_token, data.refresh_token);
    setUser(data.user);
  }, []);

  const logout = useCallback(async () => {
    try {
      await api("/auth/logout", { method: "POST" });
    } catch {}
    await tokenStore.clear();
    setUser(null);
  }, []);

  const value = useMemo(
    () => ({ user, login, register, logout, refreshUser }),
    [user, login, register, logout, refreshUser]
  );

  return <AuthCtx.Provider value={value}>{children}</AuthCtx.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthCtx);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
