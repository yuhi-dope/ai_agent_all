"use client";

import { useState, useEffect, useRef, type FormEvent } from "react";
import Link from "next/link";
import { useAuth } from "@/hooks/use-auth";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
  CardFooter,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";

interface CompanyResult {
  corporate_number: string;
  name: string;
  location: string;
  postal_code?: string;
  source: "gbiz" | "nta";
}

export default function RegisterPage() {
  const { signUp } = useAuth();
  const [companyQuery, setCompanyQuery] = useState("");
  const [companyResults, setCompanyResults] = useState<CompanyResult[]>([]);
  const [selectedCompany, setSelectedCompany] = useState<CompanyResult | null>(null);
  const [isSearching, setIsSearching] = useState(false);
  const [showDropdown, setShowDropdown] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  const [lastName, setLastName] = useState("");
  const [firstName, setFirstName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [passwordConfirm, setPasswordConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  // Debounced company search
  useEffect(() => {
    if (companyQuery.length < 2 || selectedCompany) {
      setCompanyResults([]);
      setShowDropdown(false);
      return;
    }

    const timer = setTimeout(async () => {
      setIsSearching(true);
      try {
        const res = await fetch(
          `/api/company/search?q=${encodeURIComponent(companyQuery)}`
        );
        if (res.ok) {
          const data = await res.json();
          setCompanyResults(data.results ?? []);
          setShowDropdown(true);
        }
      } catch {
        // Silently fail — user can still type manually
      } finally {
        setIsSearching(false);
      }
    }, 400);

    return () => clearTimeout(timer);
  }, [companyQuery, selectedCompany]);

  // Close dropdown on outside click
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setShowDropdown(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  function handleSelectCompany(company: CompanyResult) {
    setSelectedCompany(company);
    setCompanyQuery(company.name);
    setShowDropdown(false);
  }

  function handleClearCompany() {
    setSelectedCompany(null);
    setCompanyQuery("");
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);

    if (password !== passwordConfirm) {
      setError("パスワードが一致しません");
      return;
    }

    if (password.length < 8) {
      setError("パスワードは8文字以上で入力してください");
      return;
    }

    const companyName = selectedCompany?.name || companyQuery;
    if (!companyName.trim()) {
      setError("会社名を入力してください");
      return;
    }

    setIsLoading(true);

    try {
      await signUp(email, password, {
        full_name: `${lastName} ${firstName}`,
        company_name: companyName,
        corporate_number: selectedCompany?.corporate_number,
        company_location: selectedCompany?.location,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "登録に失敗しました");
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle className="text-xl">新規登録</CardTitle>
          <CardDescription>
            会社情報とアカウント情報を入力してください
          </CardDescription>
        </CardHeader>
        <form onSubmit={handleSubmit}>
          <CardContent className="flex flex-col gap-4">
            {error && (
              <div className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
                {error}
              </div>
            )}

            {/* Company search */}
            <div className="flex flex-col gap-2">
              <Label htmlFor="company-search">会社名</Label>
              <div className="relative" ref={dropdownRef}>
                <Input
                  id="company-search"
                  type="text"
                  placeholder="会社名を入力して検索..."
                  value={companyQuery}
                  onChange={(e) => {
                    setCompanyQuery(e.target.value);
                    if (selectedCompany) setSelectedCompany(null);
                  }}
                  required
                  autoComplete="off"
                />
                {isSearching && (
                  <div className="absolute right-3 top-1/2 -translate-y-1/2">
                    <div className="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                  </div>
                )}

                {/* Search results dropdown */}
                {showDropdown && companyResults.length > 0 && (
                  <div className="absolute z-50 mt-1 max-h-60 w-full overflow-auto rounded-md border bg-background shadow-lg">
                    {companyResults.map((company, i) => (
                      <button
                        key={`${company.corporate_number}-${i}`}
                        type="button"
                        className="flex w-full flex-col gap-0.5 px-3 py-2 text-left hover:bg-muted/50 transition-colors"
                        onClick={() => handleSelectCompany(company)}
                      >
                        <span className="text-sm font-medium">
                          {company.name}
                        </span>
                        <span className="text-xs text-muted-foreground">
                          {company.location}
                          {company.corporate_number && (
                            <> / 法人番号: {company.corporate_number}</>
                          )}
                        </span>
                      </button>
                    ))}
                  </div>
                )}

                {showDropdown && companyResults.length === 0 && !isSearching && companyQuery.length >= 2 && (
                  <div className="absolute z-50 mt-1 w-full rounded-md border bg-background p-3 text-center text-sm text-muted-foreground shadow-lg">
                    該当する企業が見つかりません。そのまま手入力できます。
                  </div>
                )}
              </div>

              {/* Selected company info */}
              {selectedCompany && (
                <div className="flex items-start justify-between rounded-md border bg-muted/30 p-3">
                  <div className="flex flex-col gap-1">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium">{selectedCompany.name}</span>
                      <Badge variant="outline" className="text-xs">
                        {selectedCompany.source === "gbiz" ? "gBizINFO" : "国税庁"}
                      </Badge>
                    </div>
                    {selectedCompany.location && (
                      <span className="text-xs text-muted-foreground">
                        {selectedCompany.location}
                      </span>
                    )}
                    {selectedCompany.corporate_number && (
                      <span className="text-xs text-muted-foreground">
                        法人番号: {selectedCompany.corporate_number}
                      </span>
                    )}
                  </div>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={handleClearCompany}
                    className="h-6 px-2 text-xs"
                  >
                    変更
                  </Button>
                </div>
              )}
            </div>

            <div className="flex gap-3">
              <div className="flex flex-1 flex-col gap-2">
                <Label htmlFor="last-name">姓</Label>
                <Input
                  id="last-name"
                  type="text"
                  placeholder="山田"
                  value={lastName}
                  onChange={(e) => setLastName(e.target.value)}
                  required
                />
              </div>
              <div className="flex flex-1 flex-col gap-2">
                <Label htmlFor="first-name">名</Label>
                <Input
                  id="first-name"
                  type="text"
                  placeholder="太郎"
                  value={firstName}
                  onChange={(e) => setFirstName(e.target.value)}
                  required
                />
              </div>
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="email">メールアドレス</Label>
              <Input
                id="email"
                type="email"
                placeholder="mail@example.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                autoComplete="email"
              />
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="password">パスワード</Label>
              <Input
                id="password"
                type="password"
                placeholder="8文字以上"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                autoComplete="new-password"
                minLength={8}
              />
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="password-confirm">パスワード（確認）</Label>
              <Input
                id="password-confirm"
                type="password"
                placeholder="もう一度入力してください"
                value={passwordConfirm}
                onChange={(e) => setPasswordConfirm(e.target.value)}
                required
                autoComplete="new-password"
                minLength={8}
              />
            </div>
          </CardContent>
          <CardFooter className="flex flex-col gap-3">
            <Button
              type="submit"
              className="w-full"
              disabled={isLoading}
            >
              {isLoading ? "登録中..." : "アカウントを作成"}
            </Button>
            <p className="text-center text-sm text-muted-foreground">
              既にアカウントをお持ちの方は{" "}
              <Link
                href="/login"
                className="text-primary underline underline-offset-4 hover:text-primary/80"
              >
                ログイン
              </Link>
            </p>
          </CardFooter>
        </form>
      </Card>
    </div>
  );
}
