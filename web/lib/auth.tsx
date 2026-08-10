"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";

import { getMe, grantPrivacyConsent, loginUser, logoutUser, registerUser } from "@/lib/api";

export interface User {
  id: string;
  email: string;
  name: string | null;
  provider: string;
  privacy_consent_at: string | null;
  is_admin: boolean; // FR-13: email terdaftar di ADMIN_EMAILS
  created_at: string;
}

type Status = "loading" | "authenticated" | "unauthenticated";

interface AuthContextValue {
  status: Status;
  user: User | null;
  login: (email: string, password: string) => Promise<void>;
  register: (name: string, email: string, password: string) => Promise<void>;
  grantConsent: () => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<Status>("loading");
  const [user, setUser] = useState<User | null>(null);

  useEffect(() => {
    getMe()
      .then((u) => {
        setUser(u);
        setStatus("authenticated");
      })
      .catch(() => {
        setUser(null);
        setStatus("unauthenticated");
      });
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const u = await loginUser({ email, password });
    setUser(u);
    setStatus("authenticated");
  }, []);

  const register = useCallback(
    async (name: string, email: string, password: string) => {
      const u = await registerUser({
        name,
        email,
        password,
        privacyConsent: true,
      });
      setUser(u);
      setStatus("authenticated");
    },
    [],
  );

  // FR-07: user Google OAuth menegaskan consent lewat banner dashboard.
  const grantConsent = useCallback(async () => {
    const u = await grantPrivacyConsent();
    setUser(u);
  }, []);

  const logout = useCallback(async () => {
    try {
      await logoutUser();
    } finally {
      setUser(null);
      setStatus("unauthenticated");
    }
  }, []);

  return (
    <AuthContext.Provider value={{ status, user, login, register, grantConsent, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth harus dipakai di dalam <AuthProvider>");
  return ctx;
}
