"use client";

import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

// ---------- 型定義 ----------

type PartnerStatus = "pending" | "approved" | "rejected";
type PartnerType = "system_integrator" | "consultant" | "developer" | "other";
type AppCategory = "bpo" | "template" | "connector";
type AppStatus = "draft" | "published" | "suspended";

interface PartnerInfo {
  id: string;
  display_name: string;
  contact_email: string;
  partner_type: PartnerType;
  status: PartnerStatus;
}

interface RevenueMonth {
  year_month: string; // "2025-10"
  total_revenue: number;
  partner_share: number;
  install_count: number;
}

interface RevenueSummary {
  months: RevenueMonth[];
  current_month_revenue: number;
  current_month_share: number;
  current_month_installs: number;
}

interface PartnerApp {
  id: string;
  name: string;
  category: AppCategory;
  status: AppStatus;
  install_count: number;
  rating_avg: number | null;
  price_yen: number | null;
}

// ---------- ユーティリティ ----------

function partnerTypeLabel(type: PartnerType): string {
  switch (type) {
    case "system_integrator": return "システムインテグレーター";
    case "consultant": return "コンサルタント";
    case "developer": return "開発者";
    case "other": return "その他";
  }
}

function appCategoryLabel(cat: AppCategory): string {
  switch (cat) {
    case "bpo": return "業務自動化";
    case "template": return "テンプレート";
    case "connector": return "コネクタ";
  }
}

function appStatusBadge(status: AppStatus) {
  switch (status) {
    case "published":
      return <Badge className="bg-green-100 text-green-800">公開中</Badge>;
    case "draft":
      return <Badge variant="secondary">下書き</Badge>;
    case "suspended":
      return <Badge className="bg-yellow-100 text-yellow-800">停止中</Badge>;
  }
}

function formatPrice(price: number | null): string {
  if (price === null || price === 0) return "無料";
  return `¥${price.toLocaleString("ja-JP")}/月`;
}

// ---------- スケルトン ----------

function PartnerSkeleton() {
  return (
    <div className="space-y-6 animate-pulse">
      <div className="h-8 w-48 rounded bg-muted" />
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        {[0, 1, 2].map((i) => (
          <div key={i} className="h-24 rounded-lg border bg-card p-4 space-y-2">
            <div className="h-3 w-20 rounded bg-muted" />
            <div className="h-6 w-16 rounded bg-muted" />
          </div>
        ))}
      </div>
      <div className="h-40 rounded-lg border bg-muted" />
    </div>
  );
}

// ---------- パートナー未登録: 登録フォーム ----------

interface RegisterFormProps {
  onSuccess: (partner: PartnerInfo) => void;
}

function PartnerRegisterForm({ onSuccess }: RegisterFormProps) {
  const [displayName, setDisplayName] = useState("");
  const [contactEmail, setContactEmail] = useState("");
  const [partnerType, setPartnerType] = useState<PartnerType>("consultant");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!displayName.trim()) {
      setError("パートナー名を入力してください。");
      return;
    }
    if (!contactEmail.trim() || !contactEmail.includes("@")) {
      setError("メールアドレスの形式で入力してください。");
      return;
    }
    setSubmitting(true);
    try {
      const result = await apiFetch<PartnerInfo>("/partner/me", {
        method: "POST",
        body: {
          display_name: displayName.trim(),
          contact_email: contactEmail.trim(),
          partner_type: partnerType,
        },
      });
      onSuccess(result);
    } catch {
      setError("登録申請に失敗しました。入力内容を確認してもう一度お試しください。");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="max-w-lg mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold">パートナー登録</h1>
        <p className="text-sm text-muted-foreground mt-1">
          パートナーとしてご参加いただくと、マーケットプレイスにアプリを公開し、収益を得ることができます。
        </p>
      </div>

      {error && (
        <div className="rounded-md bg-destructive/10 border border-destructive/20 px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      )}

      <Card>
        <CardContent className="pt-6">
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="partner-type">パートナー種別</Label>
              <Select
                value={partnerType}
                onValueChange={(v) => setPartnerType(v as PartnerType)}
              >
                <SelectTrigger id="partner-type" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="consultant">コンサルタント</SelectItem>
                  <SelectItem value="system_integrator">システムインテグレーター</SelectItem>
                  <SelectItem value="developer">開発者</SelectItem>
                  <SelectItem value="other">その他</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-2">
              <Label htmlFor="display-name">パートナー名（表示名）</Label>
              <Input
                id="display-name"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="例: 山田コンサルティング株式会社"
                className="w-full"
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="contact-email">連絡先メールアドレス</Label>
              <Input
                id="contact-email"
                type="email"
                value={contactEmail}
                onChange={(e) => setContactEmail(e.target.value)}
                placeholder="例: partner@yamada-consulting.co.jp"
                className="w-full"
              />
            </div>

            <Button
              type="submit"
              disabled={submitting}
              className="w-full sm:w-auto"
            >
              {submitting ? (
                <>
                  <span className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
                  申請中...
                </>
              ) : (
                "パートナー登録を申請する"
              )}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}

// ---------- 新規アプリ作成ダイアログ ----------

interface CreateAppDialogProps {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}

function CreateAppDialog({ open, onClose, onCreated }: CreateAppDialogProps) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [category, setCategory] = useState<AppCategory>("template");
  const [priceYen, setPriceYen] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function reset() {
    setName("");
    setDescription("");
    setCategory("template");
    setPriceYen("");
    setError(null);
  }

  function handleClose() {
    reset();
    onClose();
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!name.trim()) {
      setError("アプリ名を入力してください。");
      return;
    }
    if (!description.trim()) {
      setError("説明を入力してください。");
      return;
    }
    const price = priceYen.trim() === "" ? null : parseInt(priceYen, 10);
    if (priceYen.trim() !== "" && (isNaN(price!) || price! < 0)) {
      setError("価格は0以上の整数を入力してください。");
      return;
    }
    setSubmitting(true);
    try {
      await apiFetch("/partner/apps", {
        method: "POST",
        body: {
          name: name.trim(),
          description: description.trim(),
          category,
          price_yen: price,
        },
      });
      reset();
      onCreated();
    } catch {
      setError("アプリの作成に失敗しました。入力内容を確認してもう一度お試しください。");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !o && handleClose()}>
      <DialogContent className="sm:max-w-md w-full mx-2">
        <DialogHeader>
          <DialogTitle>新規アプリを作成する</DialogTitle>
          <DialogDescription>
            作成したアプリは審査後、マーケットプレイスに公開されます。
          </DialogDescription>
        </DialogHeader>

        {error && (
          <div className="rounded-md bg-destructive/10 border border-destructive/20 px-4 py-3 text-sm text-destructive">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="app-name">アプリ名</Label>
            <Input
              id="app-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="例: 建設業向け見積書テンプレート"
              className="w-full"
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="app-desc">説明</Label>
            <Textarea
              id="app-desc"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="例: 建設業に特化した見積書テンプレートです。材料費・人件費・外注費を自動集計します。"
              rows={3}
              className="w-full resize-none"
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="app-category">カテゴリ</Label>
            <Select
              value={category}
              onValueChange={(v) => setCategory(v as AppCategory)}
            >
              <SelectTrigger id="app-category" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="template">テンプレート</SelectItem>
                <SelectItem value="bpo">業務自動化</SelectItem>
                <SelectItem value="connector">コネクタ</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2">
            <Label htmlFor="app-price">月額料金（円）</Label>
            <Input
              id="app-price"
              type="number"
              min="0"
              value={priceYen}
              onChange={(e) => setPriceYen(e.target.value)}
              placeholder="例: 30000（空欄の場合は無料）"
              className="w-full"
            />
          </div>

          <div className="flex gap-3 pt-2 justify-end">
            <Button type="button" variant="outline" onClick={handleClose}>
              キャンセル
            </Button>
            <Button type="submit" disabled={submitting}>
              {submitting ? (
                <>
                  <span className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
                  作成中...
                </>
              ) : (
                "アプリを作成する"
              )}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// ---------- 承認済みパートナー画面 ----------

interface PartnerDashboardProps {
  partner: PartnerInfo;
}

function PartnerDashboard({ partner }: PartnerDashboardProps) {
  const [revenue, setRevenue] = useState<RevenueSummary | null>(null);
  const [apps, setApps] = useState<PartnerApp[]>([]);
  const [loadingRevenue, setLoadingRevenue] = useState(true);
  const [loadingApps, setLoadingApps] = useState(true);
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const [appsFeedback, setAppsFeedback] = useState<string | null>(null);

  async function fetchRevenue() {
    try {
      const data = await apiFetch<RevenueSummary>("/partner/revenue");
      setRevenue(data);
    } catch {
      // 収益データは任意表示。エラーは無視（次の描画でリトライ可）
    } finally {
      setLoadingRevenue(false);
    }
  }

  async function fetchApps() {
    setLoadingApps(true);
    try {
      const data = await apiFetch<PartnerApp[]>("/partner/apps");
      setApps(data);
    } catch {
      // アプリ一覧取得失敗
    } finally {
      setLoadingApps(false);
    }
  }

  useEffect(() => {
    fetchRevenue();
    fetchApps();
  }, []);

  function handleAppCreated() {
    setCreateDialogOpen(false);
    setAppsFeedback("アプリを作成しました。審査をお待ちください。");
    setTimeout(() => setAppsFeedback(null), 4000);
    fetchApps();
  }

  return (
    <div className="space-y-6">
      {/* ページタイトル */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">パートナーポータル</h1>
          <p className="text-sm text-muted-foreground mt-1">
            {partner.display_name} ·{" "}
            {partnerTypeLabel(partner.partner_type)}
          </p>
        </div>
        <Badge className="bg-green-100 text-green-800 w-fit">承認済み</Badge>
      </div>

      {/* 収益サマリー */}
      <section className="space-y-3">
        <h2 className="text-lg font-semibold">今月の実績</h2>
        {loadingRevenue ? (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            {[0, 1, 2].map((i) => (
              <div key={i} className="h-24 rounded-lg border bg-card p-4 animate-pulse space-y-2">
                <div className="h-3 w-20 rounded bg-muted" />
                <div className="h-6 w-16 rounded bg-muted" />
              </div>
            ))}
          </div>
        ) : revenue ? (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <Card>
              <CardHeader className="pb-2">
                <CardDescription>今月の売上合計</CardDescription>
              </CardHeader>
              <CardContent>
                <p className="text-2xl font-bold">
                  ¥{revenue.current_month_revenue.toLocaleString("ja-JP")}
                </p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-2">
                <CardDescription>パートナー取り分</CardDescription>
              </CardHeader>
              <CardContent>
                <p className="text-2xl font-bold">
                  ¥{revenue.current_month_share.toLocaleString("ja-JP")}
                </p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-2">
                <CardDescription>今月の導入数</CardDescription>
              </CardHeader>
              <CardContent>
                <p className="text-2xl font-bold">
                  {revenue.current_month_installs.toLocaleString("ja-JP")} 件
                </p>
              </CardContent>
            </Card>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            収益データを取得できませんでした。画面を更新してお試しください。
          </p>
        )}
      </section>

      {/* 直近6ヶ月の収益 */}
      {revenue && revenue.months.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-lg font-semibold">直近6ヶ月の収益</h2>
          <Card>
            <CardContent className="pt-4">
              <div className="space-y-2">
                {revenue.months.map((m) => {
                  const maxShare = Math.max(...revenue.months.map((x) => x.partner_share), 1);
                  const barWidth = Math.round((m.partner_share / maxShare) * 100);
                  return (
                    <div key={m.year_month} className="flex items-center gap-3">
                      <span className="text-xs text-muted-foreground w-14 shrink-0">
                        {m.year_month}
                      </span>
                      <div className="flex-1 bg-muted rounded-full h-4 overflow-hidden">
                        <div
                          className="bg-primary h-4 rounded-full transition-all"
                          style={{ width: `${barWidth}%` }}
                        />
                      </div>
                      <span className="text-xs font-medium w-24 text-right shrink-0">
                        ¥{m.partner_share.toLocaleString("ja-JP")}
                      </span>
                    </div>
                  );
                })}
              </div>
            </CardContent>
          </Card>
        </section>
      )}

      {/* 自社アプリ一覧 */}
      <section className="space-y-3">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
          <h2 className="text-lg font-semibold">公開中のアプリ</h2>
          <Button size="sm" onClick={() => setCreateDialogOpen(true)}>
            新規アプリを作成する
          </Button>
        </div>

        {appsFeedback && (
          <div className="rounded-md bg-green-50 border border-green-200 px-4 py-3 text-sm text-green-700">
            {appsFeedback}
          </div>
        )}

        {loadingApps ? (
          <div className="space-y-2">
            {[0, 1, 2].map((i) => (
              <div key={i} className="h-14 rounded-lg border bg-card animate-pulse" />
            ))}
            <p className="text-xs text-muted-foreground">アプリ一覧を読み込んでいます...</p>
          </div>
        ) : apps.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-10 gap-3 text-center">
            <p className="text-sm text-muted-foreground">
              まだアプリがありません。最初のアプリを作成しましょう。
            </p>
            <Button onClick={() => setCreateDialogOpen(true)}>
              最初のアプリを作成する
            </Button>
          </div>
        ) : (
          <>
            {/* スマホ: カードリスト */}
            <div className="sm:hidden space-y-3">
              {apps.map((app) => (
                <Card key={app.id}>
                  <CardContent className="pt-4 pb-4 space-y-2">
                    <div className="flex items-start justify-between gap-2">
                      <p className="text-sm font-medium">{app.name}</p>
                      {appStatusBadge(app.status)}
                    </div>
                    <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
                      <span>{appCategoryLabel(app.category)}</span>
                      <span>·</span>
                      <span>{app.install_count} 件導入</span>
                      <span>·</span>
                      <span>
                        {app.rating_avg !== null
                          ? `★ ${app.rating_avg.toFixed(1)}`
                          : "未評価"}
                      </span>
                      <span>·</span>
                      <span>{formatPrice(app.price_yen)}</span>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>

            {/* PC: テーブル */}
            <div className="hidden sm:block rounded-lg border overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-muted/50">
                  <tr>
                    <th className="text-left px-4 py-3 font-medium">アプリ名</th>
                    <th className="text-left px-4 py-3 font-medium">カテゴリ</th>
                    <th className="text-left px-4 py-3 font-medium">ステータス</th>
                    <th className="text-right px-4 py-3 font-medium">導入数</th>
                    <th className="text-right px-4 py-3 font-medium">評価</th>
                    <th className="text-right px-4 py-3 font-medium">価格</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {apps.map((app) => (
                    <tr key={app.id} className="bg-card">
                      <td className="px-4 py-3 font-medium">{app.name}</td>
                      <td className="px-4 py-3 text-muted-foreground">
                        {appCategoryLabel(app.category)}
                      </td>
                      <td className="px-4 py-3">{appStatusBadge(app.status)}</td>
                      <td className="px-4 py-3 text-right text-muted-foreground">
                        {app.install_count.toLocaleString("ja-JP")}
                      </td>
                      <td className="px-4 py-3 text-right text-muted-foreground">
                        {app.rating_avg !== null
                          ? `★ ${app.rating_avg.toFixed(1)}`
                          : "未評価"}
                      </td>
                      <td className="px-4 py-3 text-right">
                        {formatPrice(app.price_yen)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>

      <CreateAppDialog
        open={createDialogOpen}
        onClose={() => setCreateDialogOpen(false)}
        onCreated={handleAppCreated}
      />
    </div>
  );
}

// ---------- メインページ ----------

export default function PartnerPage() {
  const [partner, setPartner] = useState<PartnerInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [notRegistered, setNotRegistered] = useState(false);

  useEffect(() => {
    async function fetchPartner() {
      setLoading(false);
      try {
        const data = await apiFetch<PartnerInfo>("/partner/me");
        setPartner(data);
      } catch (err: unknown) {
        // 404 は未登録
        const e = err as Error;
        if (e?.message?.includes("404") || e?.message?.toLowerCase().includes("not found")) {
          setNotRegistered(true);
        }
      } finally {
        setLoading(false);
      }
    }
    fetchPartner();
  }, []);

  if (loading) {
    return (
      <div className="max-w-2xl mx-auto">
        <p className="text-sm text-muted-foreground mb-4">パートナー情報を読み込んでいます...</p>
        <PartnerSkeleton />
      </div>
    );
  }

  // 未登録
  if (notRegistered || (!loading && !partner)) {
    return (
      <PartnerRegisterForm
        onSuccess={(p) => {
          setPartner(p);
          setNotRegistered(false);
        }}
      />
    );
  }

  if (!partner) return null;

  // 審査中
  if (partner.status === "pending") {
    return (
      <div className="max-w-lg mx-auto space-y-6">
        <h1 className="text-2xl font-bold">パートナーポータル</h1>
        <div className="rounded-md bg-yellow-50 border border-yellow-200 px-4 py-4 space-y-1">
          <p className="text-sm font-semibold text-yellow-800">審査中です</p>
          <p className="text-sm text-yellow-700">
            パートナー登録の申請を受け付けました。審査には通常3〜5営業日かかります。
            承認されましたらメールでご連絡します。
          </p>
        </div>
        <Card>
          <CardContent className="pt-4 space-y-2">
            <p className="text-sm font-medium">申請内容</p>
            <div className="text-sm text-muted-foreground space-y-1">
              <p>パートナー名: {partner.display_name}</p>
              <p>種別: {partnerTypeLabel(partner.partner_type)}</p>
              <p>連絡先: {partner.contact_email}</p>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  // 却下
  if (partner.status === "rejected") {
    return (
      <div className="max-w-lg mx-auto space-y-6">
        <h1 className="text-2xl font-bold">パートナーポータル</h1>
        <div className="rounded-md bg-destructive/10 border border-destructive/20 px-4 py-4 space-y-1">
          <p className="text-sm font-semibold text-destructive">申請が受理されませんでした</p>
          <p className="text-sm text-destructive/80">
            詳細はメールでお送りしています。内容を確認の上、再申請をご検討ください。
          </p>
        </div>
        <Button
          onClick={() => {
            setPartner(null);
            setNotRegistered(true);
          }}
        >
          再度申請する
        </Button>
      </div>
    );
  }

  // 承認済み
  return <PartnerDashboard partner={partner} />;
}
