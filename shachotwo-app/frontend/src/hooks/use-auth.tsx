"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase";
import { apiFetch } from "@/lib/api";
import type { User, Session } from "@supabase/supabase-js";

export type AuthContextValue = {
  user: User | null;
  session: Session | null;
  loading: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (
    email: string,
    password: string,
    metadata?: {
      full_name?: string;
      company_name?: string;
      corporate_number?: string;
      company_location?: string;
    }
  ) => Promise<void>;
  signOut: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

/** アプリ全体で1インスタンスのセッション状態を共有する（ページごとの useState 二重化を防ぐ） */
export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
  const router = useRouter();
  const supabase = useMemo(() => createClient(), []);

  useEffect(() => {
    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, session) => {
      setSession(session);
      setUser(session?.user ?? null);
      setLoading(false);
    });

    supabase.auth.getSession().then(({ data: { session } }) => {
      setSession(session);
      setUser(session?.user ?? null);
      setLoading(false);
    });

    return () => subscription.unsubscribe();
  }, [supabase]);

  const signIn = useCallback(
    async (email: string, password: string) => {
      const { data, error } = await supabase.auth.signInWithPassword({ email, password });
      if (error) throw error;

      const appMeta = data.session?.user?.app_metadata;
      if (appMeta?.company_id) {
        try {
          await apiFetch("/auth/setup", {
            method: "POST",
            token: data.session!.access_token,
            body: {
              company_name: "",
              industry: "その他",
            },
          });
        } catch (e) {
          console.error("Setup for invited user failed:", e);
        }
      }

      router.push("/dashboard");
    },
    [router, supabase]
  );

  const signUp = useCallback(
    async (
      email: string,
      password: string,
      metadata?: {
        full_name?: string;
        company_name?: string;
        corporate_number?: string;
        company_location?: string;
      }
    ) => {
      const { data, error } = await supabase.auth.signUp({
        email,
        password,
        options: { data: metadata },
      });
      if (error) throw error;

      const token = data.session?.access_token;
      if (token && metadata?.company_name) {
        try {
          await apiFetch("/auth/setup", {
            method: "POST",
            token,
            body: {
              company_name: metadata.company_name,
              industry: "その他",
              corporate_number: metadata.corporate_number || null,
              company_location: metadata.company_location || null,
            },
          });
          const { data: refreshData } = await supabase.auth.refreshSession();
          if (refreshData.session) {
            setSession(refreshData.session);
            setUser(refreshData.session.user ?? null);
          }
        } catch (e) {
          console.error("Setup API failed:", e);
        }
      }

      router.push("/dashboard");
    },
    [router, supabase]
  );

  const signOut = useCallback(async () => {
    await supabase.auth.signOut();
    router.push("/");
  }, [router, supabase]);

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      session,
      loading,
      signIn,
      signUp,
      signOut,
    }),
    [user, session, loading, signIn, signUp, signOut]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within AuthProvider");
  }
  return ctx;
}
