"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";

// ---------- 型定義 ----------

type AppCategory = "bpo" | "template" | "connector";

interface MarketplaceApp {
  id: string;
  name: string;
  description: string;
  partner_name: string;
  category: AppCategory;
  price_yen: number | null;
  rating_avg: number | null;
  install_count: number;
  is_installed: boolean;
  icon_url: string | null;
}

// ---------- ユーティリティ ----------

function formatPrice(price: number | null): string {
  if (price === null || price === 0) return "無料";
  return `¥${price.toLocaleString("ja-JP")}/月`;
}

function renderStars(rating: number | null): string {
  if (rating === null) return "未評価";
  const full = Math.round(rating);
  return "★".repeat(full) + "☆".repeat(5 - full);
}

function categoryLabel(cat: AppCategory): string {
  switch (cat) {
    case "bpo":
      return "業務自動化";
    case "template":
      return "テンプレート";
    case "connector":
      return "コネクタ";
  }
}

// ---------- スケルトン ----------

function AppCardSkeleton() {
  return (
    <div className="rounded-lg border bg-card p-4 space-y-3 animate-pulse">
      <div className="flex items-center gap-3">
        <div className="h-10 w-10 rounded-md bg-muted shrink-0" />
        <div className="flex-1 space-y-2">
          <div className="h-4 w-3/4 rounded bg-muted" />
          <div className="h-3 w-1/2 rounded bg-muted" />
        </div>
      </div>
      <div className="h-3 w-full rounded bg-muted" />
      <div className="h-3 w-5/6 rounded bg-muted" />
      <div className="flex justify-between items-center pt-1">
        <div className="h-4 w-20 rounded bg-muted" />
        <div className="h-8 w-28 rounded bg-muted" />
      </div>
    </div>
  );
}

// ---------- アプリカード ----------

interface AppCardProps {
  app: MarketplaceApp;
  onInstall: (id: string) => void;
  onUninstall: (id: string) => void;
  installing: string | null;
}

function AppCard({ app, onInstall, onUninstall, installing }: AppCardProps) {
  const isLoading = installing === app.id;

  return (
    <Link href={`/marketplace/${app.id}`} className="block group">
      <Card className="h-full transition-shadow group-hover:shadow-md">
        <CardContent className="p-4 flex flex-col gap-3 h-full">
          {/* ヘッダー */}
          <div className="flex items-start gap-3">
            <div className="h-10 w-10 rounded-md bg-muted flex items-center justify-center shrink-0 text-lg font-bold text-muted-foreground">
              {app.icon_url ? (
                <img
                  src={app.icon_url}
                  alt={app.name}
                  className="h-10 w-10 rounded-md object-cover"
                />
              ) : (
                app.name.charAt(0)
              )}
            </div>
            <div className="min-w-0 flex-1">
              <p className="text-base font-medium truncate">{app.name}</p>
              <p className="text-xs text-muted-foreground truncate">{app.partner_name}</p>
            </div>
          </div>

          {/* カテゴリバッジ */}
          <Badge variant="secondary" className="w-fit text-xs">
            {categoryLabel(app.category)}
          </Badge>

          {/* 説明 */}
          <p className="text-sm text-muted-foreground line-clamp-2 flex-1">
            {app.description}
          </p>

          {/* 評価・インストール数 */}
          <div className="flex items-center gap-3 text-xs text-muted-foreground">
            <span>{renderStars(app.rating_avg)}</span>
            <span>{app.install_count.toLocaleString("ja-JP")} 件導入</span>
          </div>

          {/* 価格・ボタン */}
          <div className="flex items-center justify-between gap-2 pt-1">
            <span className="text-sm font-semibold text-primary">
              {formatPrice(app.price_yen)}
            </span>
            <Button
              size="sm"
              variant={app.is_installed ? "secondary" : "default"}
              disabled={app.is_installed || isLoading}
              onClick={(e) => {
                e.preventDefault();
                if (app.is_installed) {
                  onUninstall(app.id);
                } else {
                  onInstall(app.id);
                }
              }}
              className="shrink-0"
            >
              {isLoading ? (
                <>
                  <span className="mr-1.5 h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />
                  処理中...
                </>
              ) : app.is_installed ? (
                "導入済み"
              ) : (
                "導入する"
              )}
            </Button>
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}

// ---------- メインページ ----------

export default function MarketplacePage() {
  const [apps, setApps] = useState<MarketplaceApp[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"all" | AppCategory>("all");
  const [installing, setInstalling] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);

  async function fetchApps(category?: AppCategory) {
    setLoading(true);
    setError(null);
    try {
      const params = category ? { category } : undefined;
      const data = await apiFetch<MarketplaceApp[]>("/marketplace/apps", {
        params,
      });
      setApps(data);
    } catch {
      setError("アプリ一覧の取得に失敗しました。しばらく経ってから再度お試しください。");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    fetchApps(activeTab === "all" ? undefined : activeTab);
  }, [activeTab]);

  async function handleInstall(id: string) {
    setInstalling(id);
    try {
      await apiFetch(`/marketplace/apps/${id}/install`, { method: "POST" });
      setApps((prev) =>
        prev.map((a) => (a.id === id ? { ...a, is_installed: true } : a))
      );
      setFeedback("アプリを導入しました。");
      setTimeout(() => setFeedback(null), 3000);
    } catch {
      setError("導入に失敗しました。しばらく経ってから再度お試しください。");
      setTimeout(() => setError(null), 4000);
    } finally {
      setInstalling(null);
    }
  }

  async function handleUninstall(id: string) {
    setInstalling(id);
    try {
      await apiFetch(`/marketplace/apps/${id}/install`, { method: "DELETE" });
      setApps((prev) =>
        prev.map((a) => (a.id === id ? { ...a, is_installed: false } : a))
      );
      setFeedback("アプリの導入を解除しました。");
      setTimeout(() => setFeedback(null), 3000);
    } catch {
      setError("解除に失敗しました。しばらく経ってから再度お試しください。");
      setTimeout(() => setError(null), 4000);
    } finally {
      setInstalling(null);
    }
  }

  return (
    <div className="space-y-6">
      {/* ページタイトル */}
      <div>
        <h1 className="text-2xl font-bold">マーケットプレイス</h1>
        <p className="text-sm text-muted-foreground mt-1">
          パートナーが提供するアプリを導入して、業務を拡張できます。
        </p>
      </div>

      {/* フィードバックバナー */}
      {feedback && (
        <div className="rounded-md bg-green-50 border border-green-200 px-4 py-3 text-sm text-green-700">
          {feedback}
        </div>
      )}

      {/* エラーバナー */}
      {error && (
        <div className="rounded-md bg-destructive/10 border border-destructive/20 px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {/* カテゴリタブ */}
      <Tabs value={activeTab} onValueChange={(v) => setActiveTab(v as typeof activeTab)}>
        <TabsList className="w-full sm:w-auto">
          <TabsTrigger value="all" className="flex-1 sm:flex-none">すべて</TabsTrigger>
          <TabsTrigger value="bpo" className="flex-1 sm:flex-none">業務自動化</TabsTrigger>
          <TabsTrigger value="template" className="flex-1 sm:flex-none">テンプレート</TabsTrigger>
          <TabsTrigger value="connector" className="flex-1 sm:flex-none">コネクタ</TabsTrigger>
        </TabsList>
      </Tabs>

      {/* アプリグリッド */}
      {loading ? (
        <div>
          <p className="text-sm text-muted-foreground mb-4">アプリを読み込んでいます...</p>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {Array.from({ length: 6 }).map((_, i) => (
              <AppCardSkeleton key={i} />
            ))}
          </div>
        </div>
      ) : apps.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 gap-4 text-center">
          <p className="text-muted-foreground">
            {activeTab === "all"
              ? "まだアプリが登録されていません。"
              : `「${categoryLabel(activeTab as AppCategory)}」のアプリはまだありません。`}
          </p>
          <Button variant="outline" onClick={() => setActiveTab("all")}>
            すべてのアプリを見る
          </Button>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {apps.map((app) => (
            <AppCard
              key={app.id}
              app={app}
              onInstall={handleInstall}
              onUninstall={handleUninstall}
              installing={installing}
            />
          ))}
        </div>
      )}
    </div>
  );
}
