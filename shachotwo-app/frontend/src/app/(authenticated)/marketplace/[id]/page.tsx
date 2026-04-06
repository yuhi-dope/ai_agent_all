"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";

// ---------- 型定義 ----------

type AppCategory = "bpo" | "template" | "connector";

interface Review {
  id: string;
  rating: number;
  comment: string;
  reviewer_name: string;
  created_at: string;
}

interface MarketplaceAppDetail {
  id: string;
  name: string;
  description: string;
  partner_name: string;
  partner_description: string | null;
  category: AppCategory;
  price_yen: number | null;
  rating_avg: number | null;
  install_count: number;
  is_installed: boolean;
  icon_url: string | null;
  reviews: Review[];
}

// ---------- ユーティリティ ----------

function formatPrice(price: number | null): string {
  if (price === null || price === 0) return "無料";
  return `¥${price.toLocaleString("ja-JP")}/月`;
}

function categoryLabel(cat: AppCategory): string {
  switch (cat) {
    case "bpo": return "業務自動化";
    case "template": return "テンプレート";
    case "connector": return "コネクタ";
  }
}

function StarSelector({
  value,
  onChange,
}: {
  value: number;
  onChange: (v: number) => void;
}) {
  return (
    <div className="flex gap-1">
      {[1, 2, 3, 4, 5].map((n) => (
        <button
          key={n}
          type="button"
          onClick={() => onChange(n)}
          className={`text-2xl transition-colors ${
            n <= value ? "text-yellow-400" : "text-muted-foreground/30"
          }`}
          aria-label={`${n}点`}
        >
          ★
        </button>
      ))}
    </div>
  );
}

// ---------- スケルトン ----------

function DetailSkeleton() {
  return (
    <div className="space-y-6 animate-pulse">
      <div className="h-4 w-20 rounded bg-muted" />
      <div className="flex gap-4 items-start">
        <div className="h-16 w-16 rounded-lg bg-muted shrink-0" />
        <div className="space-y-2 flex-1">
          <div className="h-7 w-2/3 rounded bg-muted" />
          <div className="h-4 w-1/3 rounded bg-muted" />
        </div>
      </div>
      <div className="h-24 rounded bg-muted" />
      <div className="h-10 w-40 rounded bg-muted" />
    </div>
  );
}

// ---------- メインページ ----------

export default function MarketplaceDetailPage() {
  const params = useParams();
  const router = useRouter();
  const id = params.id as string;

  const [app, setApp] = useState<MarketplaceAppDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [installing, setInstalling] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);

  // レビューフォーム
  const [reviewRating, setReviewRating] = useState(5);
  const [reviewComment, setReviewComment] = useState("");
  const [submittingReview, setSubmittingReview] = useState(false);
  const [reviewFeedback, setReviewFeedback] = useState<string | null>(null);
  const [reviewError, setReviewError] = useState<string | null>(null);

  useEffect(() => {
    async function fetchDetail() {
      setLoading(true);
      setError(null);
      try {
        const data = await apiFetch<MarketplaceAppDetail>(`/marketplace/apps/${id}`);
        setApp(data);
      } catch {
        setError("アプリ情報の取得に失敗しました。しばらく経ってから再度お試しください。");
      } finally {
        setLoading(false);
      }
    }
    fetchDetail();
  }, [id]);

  async function handleInstall() {
    if (!app) return;
    setInstalling(true);
    try {
      await apiFetch(`/marketplace/apps/${id}/install`, { method: "POST" });
      setApp((prev) => prev ? { ...prev, is_installed: true } : prev);
      setFeedback("アプリを導入しました。");
      setTimeout(() => setFeedback(null), 3000);
    } catch {
      setError("導入に失敗しました。しばらく経ってから再度お試しください。");
      setTimeout(() => setError(null), 4000);
    } finally {
      setInstalling(false);
    }
  }

  async function handleUninstall() {
    if (!app) return;
    setInstalling(true);
    try {
      await apiFetch(`/marketplace/apps/${id}/install`, { method: "DELETE" });
      setApp((prev) => prev ? { ...prev, is_installed: false } : prev);
      setFeedback("アプリの導入を解除しました。");
      setTimeout(() => setFeedback(null), 3000);
    } catch {
      setError("解除に失敗しました。しばらく経ってから再度お試しください。");
      setTimeout(() => setError(null), 4000);
    } finally {
      setInstalling(false);
    }
  }

  async function handleReviewSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!reviewComment.trim()) {
      setReviewError("コメントを入力してください。");
      return;
    }
    setSubmittingReview(true);
    setReviewError(null);
    try {
      await apiFetch(`/marketplace/apps/${id}/review`, {
        method: "POST",
        body: { rating: reviewRating, comment: reviewComment.trim() },
      });
      setReviewFeedback("レビューを投稿しました。ありがとうございます！");
      setReviewComment("");
      setReviewRating(5);
      setTimeout(() => setReviewFeedback(null), 4000);
      // レビューを再取得
      const data = await apiFetch<MarketplaceAppDetail>(`/marketplace/apps/${id}`);
      setApp(data);
    } catch {
      setReviewError("レビューの投稿に失敗しました。しばらく経ってから再度お試しください。");
    } finally {
      setSubmittingReview(false);
    }
  }

  if (loading) {
    return (
      <div className="max-w-2xl mx-auto space-y-6">
        <p className="text-sm text-muted-foreground">アプリ情報を読み込んでいます...</p>
        <DetailSkeleton />
      </div>
    );
  }

  if (error || !app) {
    return (
      <div className="max-w-2xl mx-auto space-y-4">
        <div className="rounded-md bg-destructive/10 border border-destructive/20 px-4 py-3 text-sm text-destructive">
          {error ?? "アプリが見つかりませんでした。"}
        </div>
        <Button variant="outline" onClick={() => router.push("/marketplace")}>
          マーケットプレイスに戻る
        </Button>
      </div>
    );
  }

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      {/* 戻るボタン */}
      <Button
        variant="ghost"
        size="sm"
        onClick={() => router.push("/marketplace")}
        className="text-muted-foreground -ml-2"
      >
        ← マーケットプレイスに戻る
      </Button>

      {/* フィードバックバナー */}
      {feedback && (
        <div className="rounded-md bg-green-50 border border-green-200 px-4 py-3 text-sm text-green-700">
          {feedback}
        </div>
      )}

      {/* アプリヘッダー */}
      <div className="flex items-start gap-4">
        <div className="h-16 w-16 rounded-lg bg-muted flex items-center justify-center shrink-0 text-2xl font-bold text-muted-foreground">
          {app.icon_url ? (
            <img
              src={app.icon_url}
              alt={app.name}
              className="h-16 w-16 rounded-lg object-cover"
            />
          ) : (
            app.name.charAt(0)
          )}
        </div>
        <div className="flex-1 min-w-0">
          <h1 className="text-2xl font-bold">{app.name}</h1>
          <p className="text-sm text-muted-foreground">{app.partner_name}</p>
          <div className="flex flex-wrap items-center gap-3 mt-2">
            <Badge variant="secondary">{categoryLabel(app.category)}</Badge>
            <span className="text-sm font-semibold text-primary">
              {formatPrice(app.price_yen)}
            </span>
            {app.rating_avg !== null && (
              <span className="text-sm text-muted-foreground">
                {"★".repeat(Math.round(app.rating_avg))}
                {"☆".repeat(5 - Math.round(app.rating_avg))}
              </span>
            )}
            <span className="text-xs text-muted-foreground">
              {app.install_count.toLocaleString("ja-JP")} 件導入
            </span>
          </div>
        </div>
      </div>

      {/* 説明 */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg font-semibold">アプリの説明</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm whitespace-pre-wrap">{app.description}</p>
        </CardContent>
      </Card>

      {/* パートナー情報 */}
      {app.partner_description && (
        <Card>
          <CardHeader>
            <CardTitle className="text-lg font-semibold">提供パートナー</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm font-medium">{app.partner_name}</p>
            <p className="text-sm text-muted-foreground mt-1">{app.partner_description}</p>
          </CardContent>
        </Card>
      )}

      {/* インストールボタン */}
      <div className="flex gap-3">
        {app.is_installed ? (
          <Button
            variant="secondary"
            size="lg"
            className="w-full sm:w-auto"
            onClick={handleUninstall}
            disabled={installing}
          >
            {installing ? (
              <>
                <span className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
                処理中...
              </>
            ) : (
              "導入を解除する"
            )}
          </Button>
        ) : (
          <Button
            size="lg"
            className="w-full sm:w-auto"
            onClick={handleInstall}
            disabled={installing}
          >
            {installing ? (
              <>
                <span className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
                導入中...
              </>
            ) : (
              "このアプリを導入する"
            )}
          </Button>
        )}
      </div>

      {/* エラーバナー */}
      {error && (
        <div className="rounded-md bg-destructive/10 border border-destructive/20 px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {/* レビュー一覧 */}
      <section className="space-y-4">
        <h2 className="text-lg font-semibold">レビュー</h2>
        {app.reviews.length === 0 ? (
          <p className="text-sm text-muted-foreground">まだレビューがありません。導入したら感想を投稿しましょう。</p>
        ) : (
          <div className="space-y-3">
            {app.reviews.map((review) => (
              <Card key={review.id}>
                <CardContent className="pt-4 pb-4">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm font-medium">{review.reviewer_name}</span>
                    <span className="text-xs text-muted-foreground">
                      {new Date(review.created_at).toLocaleDateString("ja-JP")}
                    </span>
                  </div>
                  <div className="text-yellow-400 text-sm mt-1">
                    {"★".repeat(review.rating)}
                    {"☆".repeat(5 - review.rating)}
                  </div>
                  <p className="text-sm text-muted-foreground mt-2">{review.comment}</p>
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </section>

      {/* レビュー投稿フォーム */}
      <section className="space-y-4">
        <h2 className="text-lg font-semibold">レビューを投稿する</h2>
        {reviewFeedback && (
          <div className="rounded-md bg-green-50 border border-green-200 px-4 py-3 text-sm text-green-700">
            {reviewFeedback}
          </div>
        )}
        {reviewError && (
          <div className="rounded-md bg-destructive/10 border border-destructive/20 px-4 py-3 text-sm text-destructive">
            {reviewError}
          </div>
        )}
        <form onSubmit={handleReviewSubmit} className="space-y-4">
          <div className="space-y-2">
            <Label>評価</Label>
            <StarSelector value={reviewRating} onChange={setReviewRating} />
          </div>
          <div className="space-y-2">
            <Label htmlFor="review-comment">コメント</Label>
            <Textarea
              id="review-comment"
              value={reviewComment}
              onChange={(e) => setReviewComment(e.target.value)}
              placeholder="例: 見積書のテンプレートが使いやすく、作業時間が半分になりました。"
              rows={4}
              className="w-full resize-none"
            />
          </div>
          <Button
            type="submit"
            disabled={submittingReview}
            className="w-full sm:w-auto"
          >
            {submittingReview ? (
              <>
                <span className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
                投稿中...
              </>
            ) : (
              "レビューを投稿する"
            )}
          </Button>
        </form>
      </section>
    </div>
  );
}
