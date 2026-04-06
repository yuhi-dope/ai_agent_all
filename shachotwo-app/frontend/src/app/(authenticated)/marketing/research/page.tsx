"use client";

import { useEffect, useState, useCallback } from "react";
import { useAuth } from "@/hooks/use-auth";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";

// ---------- Types ----------

interface ResearchCompany {
  id: string;
  company_name: string;
  industry: string;
  industry_label: string;
  employee_count: number | null;
  contact_name: string | null;
  contact_email: string | null;
  score: number;
  temperature: "hot" | "warm" | "cold";
  pain_points: string[];
  enriched_at: string;
  outreach_sent: boolean;
  outreach_sent_at: string | null;
}

interface ResearchListResponse {
  items: ResearchCompany[];
  total: number;
  has_more: boolean;
}

interface EnrichResult {
  message: string;
  enriched_count: number;
}

// ---------- Helpers ----------

const temperatureConfig: Record<
  ResearchCompany["temperature"],
  { label: string; badgeClass: string }
> = {
  hot: { label: "高温（要連絡）", badgeClass: "bg-red-100 text-red-800" },
  warm: { label: "中温（フォロー中）", badgeClass: "bg-yellow-100 text-yellow-800" },
  cold: { label: "低温", badgeClass: "bg-muted text-muted-foreground" },
};

const industryLabels: Record<string, string> = {
  construction: "建設業",
  manufacturing: "製造業",
  dental: "歯科医院",
  restaurant: "飲食業",
  realestate: "不動産",
  professional: "士業",
  nursing: "介護",
  logistics: "物流",
  clinic: "医療クリニック",
  pharmacy: "調剤薬局",
  beauty: "美容・エステ",
  auto_repair: "自動車整備",
  hotel: "ホテル・旅館",
  ecommerce: "EC・小売",
  staffing: "人材派遣",
  architecture: "建築設計",
};

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString("ja-JP", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function scoreToLabel(score: number): string {
  if (score >= 80) return "高";
  if (score >= 50) return "中";
  return "低";
}

function scoreClass(score: number): string {
  if (score >= 80) return "text-green-600 font-bold";
  if (score >= 50) return "text-yellow-600";
  return "text-muted-foreground";
}

// ---------- Skeletons ----------

function CompanyCardSkeleton() {
  return (
    <Card>
      <CardContent className="flex flex-col gap-3 pt-4">
        <div className="flex items-start justify-between gap-2">
          <div className="flex flex-col gap-2">
            <div className="h-5 w-40 animate-pulse rounded bg-muted" />
            <div className="flex gap-2">
              <div className="h-4 w-16 animate-pulse rounded-full bg-muted" />
              <div className="h-4 w-16 animate-pulse rounded-full bg-muted" />
            </div>
          </div>
          <div className="h-8 w-12 animate-pulse rounded bg-muted" />
        </div>
        <div className="flex gap-1.5">
          <div className="h-5 w-20 animate-pulse rounded-full bg-muted" />
          <div className="h-5 w-24 animate-pulse rounded-full bg-muted" />
        </div>
      </CardContent>
    </Card>
  );
}

// ---------- Page ----------

export default function ResearchPage() {
  const { session } = useAuth();
  const [companies, setCompanies] = useState<ResearchCompany[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [industryFilter, setIndustryFilter] = useState<string>("");
  const [temperatureFilter, setTemperatureFilter] = useState<string>("");
  const [enriching, setEnriching] = useState(false);
  const [enrichResult, setEnrichResult] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const PAGE_SIZE = 20;

  const fetchData = useCallback(async () => {
    if (!session?.access_token) return;
    setLoading(true);
    setError(null);
    try {
      const params: Record<string, string> = {
        limit: String(PAGE_SIZE),
        offset: String(page * PAGE_SIZE),
      };
      if (search) params.search = search;
      if (industryFilter) params.industry = industryFilter;
      if (temperatureFilter) params.temperature = temperatureFilter;

      const result = await apiFetch<ResearchListResponse>(
        "/marketing/research/companies",
        { token: session.access_token, params }
      );
      setCompanies(result.items);
      setTotal(result.total);
    } catch {
      setError(
        "企業データの取得に失敗しました。しばらく経ってから再度お試しください。"
      );
    } finally {
      setLoading(false);
    }
  }, [session?.access_token, search, industryFilter, temperatureFilter, page]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  async function handleEnrich() {
    if (!session?.access_token) return;
    setEnriching(true);
    setEnrichResult(null);
    try {
      const result = await apiFetch<EnrichResult>(
        "/marketing/research/enrich",
        {
          token: session.access_token,
          method: "POST",
          body: { target_count: 50 },
        }
      );
      setEnrichResult(
        `企業情報を更新しました。${result.enriched_count}社のデータをエンリッチしました。`
      );
      await fetchData();
    } catch {
      setEnrichResult(
        "データのエンリッチに失敗しました。しばらく経ってから再度お試しください。"
      );
    } finally {
      setEnriching(false);
    }
  }

  const totalPages = Math.ceil(total / PAGE_SIZE);

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      {/* Header */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold">リサーチ企業一覧</h1>
          <p className="text-sm text-muted-foreground">
            AIが調査した企業リストです。ペインポイントと温度感を確認してアウトリーチに活用してください。
          </p>
        </div>
        <Button
          size="lg"
          className="w-full sm:w-auto"
          variant="outline"
          onClick={handleEnrich}
          disabled={enriching}
        >
          {enriching ? (
            <>
              <span className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
              更新中...
            </>
          ) : (
            "企業情報を最新化する"
          )}
        </Button>
      </div>

      {/* Enrich result feedback */}
      {enrichResult && (
        <Card
          className={
            enrichResult.includes("失敗")
              ? "border-destructive/50 bg-destructive/5"
              : "border-green-200 bg-green-50 dark:border-green-900 dark:bg-green-950/30"
          }
        >
          <CardContent className="pt-4">
            <p
              className={`text-sm ${
                enrichResult.includes("失敗")
                  ? "text-destructive"
                  : "text-green-700 dark:text-green-400"
              }`}
            >
              {enrichResult}
            </p>
          </CardContent>
        </Card>
      )}

      {/* Error */}
      {error && (
        <Card className="border-destructive/50 bg-destructive/5">
          <CardContent className="pt-4">
            <p className="text-sm text-destructive">{error}</p>
          </CardContent>
        </Card>
      )}

      {/* Filters */}
      <div className="flex flex-col gap-3 sm:flex-row">
        <Input
          className="w-full sm:max-w-xs"
          placeholder="例: 株式会社山田工務店"
          value={search}
          onChange={(e) => {
            setPage(0);
            setSearch(e.target.value);
          }}
        />
        <select
          className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm sm:w-40"
          value={industryFilter}
          onChange={(e) => {
            setPage(0);
            setIndustryFilter(e.target.value);
          }}
        >
          <option value="">全業種</option>
          {Object.entries(industryLabels).map(([key, label]) => (
            <option key={key} value={key}>
              {label}
            </option>
          ))}
        </select>
        <select
          className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm sm:w-40"
          value={temperatureFilter}
          onChange={(e) => {
            setPage(0);
            setTemperatureFilter(e.target.value);
          }}
        >
          <option value="">全ての温度感</option>
          <option value="hot">高温（要連絡）</option>
          <option value="warm">中温（フォロー中）</option>
          <option value="cold">低温</option>
        </select>
      </div>

      {/* Summary count */}
      {!loading && (
        <p className="text-sm text-muted-foreground">
          {total.toLocaleString()}社が見つかりました
        </p>
      )}

      {/* Company list */}
      {loading ? (
        <div className="space-y-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <CompanyCardSkeleton key={i} />
          ))}
        </div>
      ) : companies.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center gap-4 py-10 text-center">
            <p className="text-sm text-muted-foreground">
              条件に一致する企業がありません。
            </p>
            <p className="text-xs text-muted-foreground">
              フィルタを変更するか、「企業情報を最新化する」でデータを追加してください。
            </p>
            <Button variant="outline" onClick={() => { setSearch(""); setIndustryFilter(""); setTemperatureFilter(""); }}>
              フィルタをリセットする
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-3">
          {companies.map((company) => {
            const tempConf = temperatureConfig[company.temperature];
            return (
              <Card key={company.id}>
                <CardContent className="flex flex-col gap-3 pt-4">
                  <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                    <div className="flex flex-col gap-1.5">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-base font-medium">
                          {company.company_name}
                        </span>
                        {company.contact_name && (
                          <span className="text-sm text-muted-foreground">
                            {company.contact_name} 様
                          </span>
                        )}
                      </div>
                      <div className="flex flex-wrap gap-2">
                        <Badge variant="secondary">
                          {company.industry_label ||
                            industryLabels[company.industry] ||
                            company.industry}
                        </Badge>
                        <Badge className={tempConf.badgeClass}>
                          {tempConf.label}
                        </Badge>
                        {company.employee_count && (
                          <span className="text-xs text-muted-foreground">
                            従業員 {company.employee_count}名
                          </span>
                        )}
                        {company.outreach_sent && (
                          <Badge variant="outline">
                            送信済み
                            {company.outreach_sent_at
                              ? ` (${formatDate(company.outreach_sent_at)})`
                              : ""}
                          </Badge>
                        )}
                      </div>
                    </div>
                    <div className="flex items-center gap-2 sm:flex-col sm:items-end sm:gap-1">
                      <p className="text-xs text-muted-foreground">見込みスコア</p>
                      <p className={`text-2xl ${scoreClass(company.score)}`}>
                        {scoreToLabel(company.score)}
                      </p>
                    </div>
                  </div>

                  {/* Pain points */}
                  {company.pain_points.length > 0 && (
                    <div>
                      <p className="mb-1.5 text-xs font-medium text-muted-foreground">
                        推定ペインポイント
                      </p>
                      <div className="flex flex-wrap gap-1.5">
                        {company.pain_points.map((pain, idx) => (
                          <span
                            key={idx}
                            className="rounded-full border border-destructive/30 bg-destructive/5 px-2.5 py-0.5 text-xs text-destructive"
                          >
                            {pain}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}

                  <p className="text-xs text-muted-foreground">
                    情報更新: {formatDate(company.enriched_at)}
                  </p>
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}

      {/* Pagination */}
      {!loading && totalPages > 1 && (
        <div className="flex items-center justify-center gap-3">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setPage((p) => Math.max(0, p - 1))}
            disabled={page === 0}
          >
            前のページ
          </Button>
          <span className="text-sm text-muted-foreground">
            {page + 1} / {totalPages}
          </span>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
            disabled={page >= totalPages - 1}
          >
            次のページ
          </Button>
        </div>
      )}
    </div>
  );
}
