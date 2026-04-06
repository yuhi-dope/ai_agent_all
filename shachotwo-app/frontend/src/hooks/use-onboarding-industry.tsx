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
import { useAuth } from "@/hooks/use-auth";
import { apiFetch } from "@/lib/api";

interface OnboardingStatusIndustry {
  industry: string | null;
}

export type OnboardingIndustryContextValue = {
  industry: string | null;
  isLoading: boolean;
  fetchFailed: boolean;
  refetch: () => Promise<void>;
};

const OnboardingIndustryContext = createContext<OnboardingIndustryContextValue | null>(null);

/** BPO 配下で `/onboarding/status` を1回だけ取得し、layout / page で共有する */
export function OnboardingIndustryProvider({ children }: { children: ReactNode }) {
  const { session } = useAuth();
  const [industry, setIndustry] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [fetchFailed, setFetchFailed] = useState(false);

  const refetch = useCallback(async () => {
    if (!session?.access_token) {
      setIndustry(null);
      setIsLoading(false);
      setFetchFailed(false);
      return;
    }
    setIsLoading(true);
    setFetchFailed(false);
    try {
      const data = await apiFetch<OnboardingStatusIndustry>("/onboarding/status", {
        token: session.access_token,
      });
      setIndustry(data.industry ?? null);
    } catch {
      setFetchFailed(true);
      setIndustry(null);
    } finally {
      setIsLoading(false);
    }
  }, [session?.access_token]);

  useEffect(() => {
    void refetch();
  }, [refetch]);

  const value = useMemo(
    () => ({ industry, isLoading, fetchFailed, refetch }),
    [industry, isLoading, fetchFailed, refetch]
  );

  return (
    <OnboardingIndustryContext.Provider value={value}>
      {children}
    </OnboardingIndustryContext.Provider>
  );
}

export function useOnboardingIndustry(): OnboardingIndustryContextValue {
  const ctx = useContext(OnboardingIndustryContext);
  if (!ctx) {
    throw new Error("useOnboardingIndustry must be used within OnboardingIndustryProvider");
  }
  return ctx;
}
