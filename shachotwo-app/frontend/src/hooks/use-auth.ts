"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase";
import { apiFetch } from "@/lib/api";
import type { User, Session } from "@supabase/supabase-js";

export function useAuth() {
  const [user, setUser] = useState<User | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
  const router = useRouter();
  const supabase = createClient();

  useEffect(() => {
    const { data: { subscription } } = supabase.auth.onAuthStateChange(
      (_event, session) => {
        setSession(session);
        setUser(session?.user ?? null);
        setLoading(false);
      }
    );

    // Initial session check
    supabase.auth.getSession().then(({ data: { session } }) => {
      setSession(session);
      setUser(session?.user ?? null);
      setLoading(false);
    });

    return () => subscription.unsubscribe();
  }, []);

  const signIn = useCallback(async (email: string, password: string) => {
    const { data, error } = await supabase.auth.signInWithPassword({ email, password });
    if (error) throw error;

    // 招待ユーザーの初回ログイン: app_metadata に company_id があるが users レコードがまだない場合
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
  }, [router]);

  const signUp = useCallback(async (email: string, password: string, metadata?: { full_name?: string; company_name?: string; corporate_number?: string; company_location?: string }) => {
    const { data, error } = await supabase.auth.signUp({
      email,
      password,
      options: { data: metadata },
    });
    if (error) throw error;

    // Call setup API to create company + set app_metadata
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
        // Refresh session to pick up new app_metadata
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
  }, [router, setSession, setUser]);

  const signOut = useCallback(async () => {
    await supabase.auth.signOut();
    router.push("/");
  }, [router]);

  return { user, session, loading, signIn, signUp, signOut };
}
