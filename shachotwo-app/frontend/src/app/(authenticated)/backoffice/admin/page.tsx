"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useAuth } from "@/hooks/use-auth";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardHeader,
  CardTitle,
  CardContent,
  CardDescription,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";

// ---------- Types ----------

interface Permit {
  id: string;
  name: string;
  expiry_date: string;
  authority: string;
  note: string | null;
  days_remaining: number;
}

interface Vendor {
  id: string;
  name: string;
  contact_person: string | null;
  last_screened_at: string | null;
  screening_status: "safe" | "caution" | "danger" | "unchecked";
}

interface AccountUser {
  id: string;
  name: string;
  email: string;
  role: string;
  last_login_at: string | null;
  is_active: boolean;
}

type ActiveTab = "permits" | "vendors" | "accounts" | "compliance";

// ---------- Helpers ----------

function daysUntil(dateStr: string): number {
  const now = new Date();
  const target = new Date(dateStr);
  return Math.ceil((target.getTime() - now.getTime()) / (1000 * 60 * 60 * 24));
}

function daysSince(dateStr: string | null): number {
  if (!dateStr) return Infinity;
  const now = new Date();
  const target = new Date(dateStr);
  return Math.floor((now.getTime() - target.getTime()) / (1000 * 60 * 60 * 24));
}

// ---------- Sub-components ----------

function PermitBadge({ days }: { days: number }) {
  if (days <= 30) {
    return (
      <span className="inline-flex items-center rounded px-2 py-0.5 text-xs font-medium bg-red-100 text-red-800">
        要更新
      </span>
    );
  }
  if (days <= 60) {
    return (
      <span className="inline-flex items-center rounded px-2 py-0.5 text-xs font-medium bg-yellow-100 text-yellow-800">
        期限接近
      </span>
    );
  }
  return (
    <span className="inline-flex items-center rounded px-2 py-0.5 text-xs font-medium bg-green-100 text-green-800">
      有効
    </span>
  );
}

function VendorStatusBadge({
  status,
}: {
  status: Vendor["screening_status"];
}) {
  const map: Record<
    Vendor["screening_status"],
    { label: string; className: string }
  > = {
    safe: { label: "問題なし", className: "bg-green-100 text-green-800" },
    caution: { label: "要注意", className: "bg-yellow-100 text-yellow-800" },
    danger: { label: "取引停止", className: "bg-red-100 text-red-800" },
    unchecked: {
      label: "未チェック",
      className: "bg-gray-100 text-gray-700",
    },
  };
  const { label, className } = map[status];
  return (
    <span
      className={`inline-flex items-center rounded px-2 py-0.5 text-xs font-medium ${className}`}
    >
      {label}
    </span>
  );
}

// ---------- Tab: Permits ----------

function PermitsTab() {
  const { session } = useAuth();
  const token = session?.access_token;

  const [permits, setPermits] = useState<Permit[]>([]);
  const [loading, setLoading] = useState(true);
  const [addOpen, setAddOpen] = useState(false);

  // Add form state
  const [formName, setFormName] = useState("");
  const [formExpiry, setFormExpiry] = useState("");
  const [formAuthority, setFormAuthority] = useState("");
  const [formNote, setFormNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    setLoading(true);
    apiFetch<{ items: Permit[] }>("/backoffice/permits", { token })
      .then((res) => setPermits(res.items ?? []))
      .catch(() => setPermits([]))
      .finally(() => setLoading(false));
  }, [token]);

  async function handleAdd(e: FormEvent) {
    e.preventDefault();
    if (!token) return;
    setSaving(true);
    setSaveError(null);
    try {
      await apiFetch("/backoffice/permits", {
        method: "POST",
        token,
        body: {
          name: formName,
          expiry_date: formExpiry,
          authority: formAuthority,
          note: formNote || null,
        },
      });
      const res = await apiFetch<{ items: Permit[] }>("/backoffice/permits", {
        token,
      });
      setPermits(res.items ?? []);
      setAddOpen(false);
      setFormName("");
      setFormExpiry("");
      setFormAuthority("");
      setFormNote("");
    } catch (err) {
      setSaveError(
        err instanceof Error
          ? err.message
          : "追加に失敗しました。入力内容を確認してもう一度お試しください"
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          許認可の有効期限を一覧で管理します。期限が近いものは早めに更新手続きを行ってください。
        </p>
        <Button size="sm" onClick={() => setAddOpen(true)}>
          許認可を追加する
        </Button>
      </div>

      {loading ? (
        <div className="space-y-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <div
              key={i}
              className="h-20 animate-pulse rounded-lg bg-muted"
            />
          ))}
          <p className="text-center text-sm text-muted-foreground">
            許認可情報を読み込んでいます...
          </p>
        </div>
      ) : permits.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center gap-4 py-10 text-center">
            <p className="text-sm text-muted-foreground">
              まだ許認可が登録されていません。最初の許認可を追加して期限管理を始めましょう。
            </p>
            <Button onClick={() => setAddOpen(true)}>
              最初の許認可を追加する
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {permits.map((permit) => {
            const days =
              permit.days_remaining ?? daysUntil(permit.expiry_date);
            return (
              <Card key={permit.id}>
                <CardHeader className="pb-2">
                  <div className="flex items-start justify-between gap-2">
                    <CardTitle className="text-base font-medium">
                      {permit.name}
                    </CardTitle>
                    <PermitBadge days={days} />
                  </div>
                </CardHeader>
                <CardContent className="space-y-1">
                  <p className="text-sm">
                    <span className="text-muted-foreground">有効期限：</span>
                    {new Date(permit.expiry_date).toLocaleDateString("ja-JP")}
                  </p>
                  <p className="text-sm">
                    <span className="text-muted-foreground">残り：</span>
                    {days > 0 ? `${days}日` : "期限切れ"}
                  </p>
                  <p className="text-sm">
                    <span className="text-muted-foreground">管轄官庁：</span>
                    {permit.authority}
                  </p>
                  {permit.note && (
                    <p className="text-xs text-muted-foreground">
                      {permit.note}
                    </p>
                  )}
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}

      {/* Add permit dialog */}
      <Dialog open={addOpen} onOpenChange={setAddOpen}>
        <DialogContent className="sm:max-w-md w-full mx-2">
          <DialogHeader>
            <DialogTitle>許認可を追加する</DialogTitle>
            <DialogDescription>
              許認可の名称・有効期限・管轄官庁を入力してください。
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handleAdd} className="space-y-4">
            {saveError && (
              <div className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
                {saveError}
              </div>
            )}
            <div className="flex flex-col gap-2">
              <Label htmlFor="permit-name">許認可名</Label>
              <Input
                id="permit-name"
                placeholder="例: 建設業許可（一般・土木工事業）"
                value={formName}
                onChange={(e) => setFormName(e.target.value)}
                required
              />
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="permit-expiry">有効期限</Label>
              <Input
                id="permit-expiry"
                type="date"
                value={formExpiry}
                onChange={(e) => setFormExpiry(e.target.value)}
                required
              />
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="permit-authority">管轄官庁</Label>
              <Input
                id="permit-authority"
                placeholder="例: 国土交通省 関東地方整備局"
                value={formAuthority}
                onChange={(e) => setFormAuthority(e.target.value)}
                required
              />
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="permit-note">備考（任意）</Label>
              <Input
                id="permit-note"
                placeholder="例: 更新申請は期限の30日前までに提出"
                value={formNote}
                onChange={(e) => setFormNote(e.target.value)}
              />
            </div>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setAddOpen(false)}
              >
                キャンセル
              </Button>
              <Button type="submit" disabled={saving}>
                {saving ? (
                  <>
                    <span className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
                    追加中...
                  </>
                ) : (
                  "許認可を追加する"
                )}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ---------- Tab: Vendors ----------

function VendorsTab() {
  const { session } = useAuth();
  const token = session?.access_token;

  const [vendors, setVendors] = useState<Vendor[]>([]);
  const [loading, setLoading] = useState(true);
  const [checking, setChecking] = useState(false);
  const [checkResult, setCheckResult] = useState<string | null>(null);
  const [checkError, setCheckError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    setLoading(true);
    apiFetch<{ items: Vendor[] }>("/backoffice/vendors", { token })
      .then((res) => setVendors(res.items ?? []))
      .catch(() => setVendors([]))
      .finally(() => setLoading(false));
  }, [token]);

  async function handleScreening() {
    if (!token) return;
    setChecking(true);
    setCheckResult(null);
    setCheckError(null);
    try {
      const res = await apiFetch<{ message?: string }>(
        "/bpo/backoffice/antisocial-screening",
        { method: "POST", token }
      );
      setCheckResult(res.message ?? "反社チェックが完了しました。結果を確認してください。");
      // Reload vendors
      const updated = await apiFetch<{ items: Vendor[] }>(
        "/backoffice/vendors",
        { token }
      );
      setVendors(updated.items ?? []);
    } catch (err) {
      setCheckError(
        err instanceof Error
          ? err.message
          : "チェックに失敗しました。しばらく経ってから再度お試しください"
      );
    } finally {
      setChecking(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-sm text-muted-foreground">
          取引先の反社会的勢力チェック結果を管理します。定期的にチェックを実施してください。
        </p>
        <Button
          size="sm"
          onClick={handleScreening}
          disabled={checking}
          className="shrink-0"
        >
          {checking ? (
            <>
              <span className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
              チェック中...
            </>
          ) : (
            "反社チェックを実行する"
          )}
        </Button>
      </div>

      {checkResult && (
        <div className="rounded-md bg-green-50 px-3 py-2 text-sm text-green-700">
          {checkResult}
        </div>
      )}
      {checkError && (
        <div className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {checkError}
        </div>
      )}

      {loading ? (
        <div className="space-y-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="h-12 animate-pulse rounded bg-muted" />
          ))}
          <p className="text-center text-sm text-muted-foreground">
            取引先情報を読み込んでいます...
          </p>
        </div>
      ) : vendors.length === 0 ? (
        <Card>
          <CardContent className="py-8 text-center text-sm text-muted-foreground">
            取引先データがありません。
          </CardContent>
        </Card>
      ) : (
        <>
          {/* Mobile: card list */}
          <div className="flex flex-col gap-3 sm:hidden">
            {vendors.map((vendor) => (
              <Card key={vendor.id}>
                <CardContent className="py-3 space-y-1">
                  <div className="flex items-center justify-between">
                    <p className="text-sm font-medium">{vendor.name}</p>
                    <VendorStatusBadge status={vendor.screening_status} />
                  </div>
                  {vendor.contact_person && (
                    <p className="text-xs text-muted-foreground">
                      担当: {vendor.contact_person}
                    </p>
                  )}
                  <p className="text-xs text-muted-foreground">
                    最終チェック:{" "}
                    {vendor.last_screened_at
                      ? new Date(vendor.last_screened_at).toLocaleDateString(
                          "ja-JP"
                        )
                      : "未実施"}
                  </p>
                </CardContent>
              </Card>
            ))}
          </div>

          {/* Desktop: table */}
          <div className="hidden sm:block overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-xs text-muted-foreground">
                  <th className="py-2 pr-4 font-medium">会社名</th>
                  <th className="py-2 pr-4 font-medium">担当者</th>
                  <th className="py-2 pr-4 font-medium">最終チェック日</th>
                  <th className="py-2 font-medium">チェック結果</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {vendors.map((vendor) => (
                  <tr key={vendor.id} className="hover:bg-muted/30">
                    <td className="py-3 pr-4 font-medium">{vendor.name}</td>
                    <td className="py-3 pr-4 text-muted-foreground">
                      {vendor.contact_person ?? "—"}
                    </td>
                    <td className="py-3 pr-4 text-muted-foreground">
                      {vendor.last_screened_at
                        ? new Date(
                            vendor.last_screened_at
                          ).toLocaleDateString("ja-JP")
                        : "未実施"}
                    </td>
                    <td className="py-3">
                      <VendorStatusBadge status={vendor.screening_status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

// ---------- Tab: Accounts ----------

function AccountsTab() {
  const { session } = useAuth();
  const token = session?.access_token;

  const [accounts, setAccounts] = useState<AccountUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [reviewing, setReviewing] = useState(false);
  const [reviewResult, setReviewResult] = useState<string | null>(null);
  const [reviewError, setReviewError] = useState<string | null>(null);

  // Per-row role state
  const [pendingRoles, setPendingRoles] = useState<Record<string, string>>({});
  const [savingId, setSavingId] = useState<string | null>(null);
  const [deactivateTargetId, setDeactivateTargetId] = useState<string | null>(
    null
  );

  useEffect(() => {
    if (!token) return;
    setLoading(true);
    apiFetch<{ items: AccountUser[] }>("/backoffice/accounts", { token })
      .then((res) => {
        const items = res.items ?? [];
        setAccounts(items);
        const roles: Record<string, string> = {};
        for (const u of items) {
          roles[u.id] = u.role;
        }
        setPendingRoles(roles);
      })
      .catch(() => setAccounts([]))
      .finally(() => setLoading(false));
  }, [token]);

  async function handleReview() {
    if (!token) return;
    setReviewing(true);
    setReviewResult(null);
    setReviewError(null);
    try {
      const res = await apiFetch<{ message?: string }>(
        "/bpo/backoffice/account-lifecycle",
        { method: "POST", token, body: { mode: "review" } }
      );
      setReviewResult(
        res.message ?? "月次棚卸しが完了しました。結果を確認してください。"
      );
    } catch (err) {
      setReviewError(
        err instanceof Error
          ? err.message
          : "棚卸しに失敗しました。しばらく経ってから再度お試しください"
      );
    } finally {
      setReviewing(false);
    }
  }

  async function saveRole(userId: string) {
    if (!token) return;
    const role = pendingRoles[userId];
    if (!role) return;
    setSavingId(userId);
    try {
      await apiFetch(`/backoffice/accounts/${userId}`, {
        method: "PATCH",
        token,
        body: { role },
      });
      setAccounts((prev) =>
        prev.map((u) => (u.id === userId ? { ...u, role } : u))
      );
    } catch {
      // silently revert draft
      setPendingRoles((prev) => ({
        ...prev,
        [userId]:
          accounts.find((u) => u.id === userId)?.role ?? prev[userId],
      }));
    } finally {
      setSavingId(null);
    }
  }

  async function handleDeactivate(userId: string) {
    if (!token) return;
    try {
      await apiFetch(`/backoffice/accounts/${userId}`, {
        method: "PATCH",
        token,
        body: { is_active: false },
      });
      setAccounts((prev) =>
        prev.map((u) => (u.id === userId ? { ...u, is_active: false } : u))
      );
    } catch {
      // noop
    } finally {
      setDeactivateTargetId(null);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-sm text-muted-foreground">
          社内アカウントの権限と状態を管理します。長期未ログインのアカウントは定期的に確認してください。
        </p>
        <Button
          size="sm"
          onClick={handleReview}
          disabled={reviewing}
          className="shrink-0"
        >
          {reviewing ? (
            <>
              <span className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
              棚卸し中...
            </>
          ) : (
            "月次棚卸しを実行する"
          )}
        </Button>
      </div>

      {reviewResult && (
        <div className="rounded-md bg-green-50 px-3 py-2 text-sm text-green-700">
          {reviewResult}
        </div>
      )}
      {reviewError && (
        <div className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {reviewError}
        </div>
      )}

      {loading ? (
        <div className="space-y-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="h-14 animate-pulse rounded-lg bg-muted" />
          ))}
          <p className="text-center text-sm text-muted-foreground">
            アカウント情報を読み込んでいます...
          </p>
        </div>
      ) : accounts.length === 0 ? (
        <Card>
          <CardContent className="py-8 text-center text-sm text-muted-foreground">
            アカウントデータがありません。
          </CardContent>
        </Card>
      ) : (
        <>
          {/* Mobile: cards */}
          <div className="flex flex-col gap-3 sm:hidden">
            {accounts.map((account) => {
              const inactive = daysSince(account.last_login_at) >= 90;
              const draft = pendingRoles[account.id] ?? account.role;
              const dirty = draft !== account.role;
              const busy = savingId === account.id;
              return (
                <Card
                  key={account.id}
                  className={inactive ? "bg-yellow-50 border-yellow-200" : ""}
                >
                  <CardContent className="py-3 space-y-2">
                    <div className="flex items-center justify-between">
                      <p className="text-sm font-medium">{account.name}</p>
                      <div className="flex gap-1">
                        {inactive && (
                          <span className="inline-flex items-center rounded px-2 py-0.5 text-xs font-medium bg-yellow-100 text-yellow-800">
                            要確認
                          </span>
                        )}
                        {!account.is_active && (
                          <Badge
                            variant="outline"
                            className="text-muted-foreground text-xs"
                          >
                            停止済
                          </Badge>
                        )}
                      </div>
                    </div>
                    <p className="text-xs text-muted-foreground">
                      {account.email}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      最終ログイン:{" "}
                      {account.last_login_at
                        ? new Date(account.last_login_at).toLocaleDateString(
                            "ja-JP"
                          )
                        : "なし"}
                    </p>
                    <div className="flex items-center gap-2">
                      <select
                        className="h-8 rounded border border-input bg-background px-2 text-xs"
                        value={draft}
                        disabled={busy || !account.is_active}
                        onChange={(e) =>
                          setPendingRoles((prev) => ({
                            ...prev,
                            [account.id]: e.target.value,
                          }))
                        }
                      >
                        <option value="admin">管理者</option>
                        <option value="editor">編集者</option>
                      </select>
                      {dirty && (
                        <Button
                          size="sm"
                          variant="secondary"
                          disabled={busy}
                          onClick={() => saveRole(account.id)}
                        >
                          {busy ? "保存中..." : "権限を保存する"}
                        </Button>
                      )}
                      {account.is_active && (
                        <Button
                          size="sm"
                          variant="outline"
                          className="text-destructive border-destructive/40 hover:bg-destructive/10"
                          onClick={() => setDeactivateTargetId(account.id)}
                        >
                          停止する
                        </Button>
                      )}
                    </div>
                  </CardContent>
                </Card>
              );
            })}
          </div>

          {/* Desktop: table */}
          <div className="hidden sm:block overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-xs text-muted-foreground">
                  <th className="py-2 pr-4 font-medium">名前</th>
                  <th className="py-2 pr-4 font-medium">メールアドレス</th>
                  <th className="py-2 pr-4 font-medium">権限</th>
                  <th className="py-2 pr-4 font-medium">最終ログイン</th>
                  <th className="py-2 font-medium">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {accounts.map((account) => {
                  const inactive = daysSince(account.last_login_at) >= 90;
                  const draft = pendingRoles[account.id] ?? account.role;
                  const dirty = draft !== account.role;
                  const busy = savingId === account.id;
                  return (
                    <tr
                      key={account.id}
                      className={inactive ? "bg-yellow-50" : "hover:bg-muted/30"}
                    >
                      <td className="py-3 pr-4">
                        <div className="flex items-center gap-2">
                          <span className="font-medium">{account.name}</span>
                          {inactive && (
                            <span className="inline-flex items-center rounded px-2 py-0.5 text-xs font-medium bg-yellow-100 text-yellow-800">
                              要確認
                            </span>
                          )}
                          {!account.is_active && (
                            <Badge
                              variant="outline"
                              className="text-muted-foreground text-xs"
                            >
                              停止済
                            </Badge>
                          )}
                        </div>
                      </td>
                      <td className="py-3 pr-4 text-muted-foreground">
                        {account.email}
                      </td>
                      <td className="py-3 pr-4">
                        <div className="flex items-center gap-2">
                          <select
                            className="h-8 rounded border border-input bg-background px-2 text-xs"
                            value={draft}
                            disabled={busy || !account.is_active}
                            onChange={(e) =>
                              setPendingRoles((prev) => ({
                                ...prev,
                                [account.id]: e.target.value,
                              }))
                            }
                          >
                            <option value="admin">管理者</option>
                            <option value="editor">編集者</option>
                          </select>
                          {dirty && (
                            <Button
                              size="sm"
                              variant="secondary"
                              disabled={busy}
                              onClick={() => saveRole(account.id)}
                            >
                              {busy ? "保存中..." : "権限を保存する"}
                            </Button>
                          )}
                        </div>
                      </td>
                      <td className="py-3 pr-4 text-muted-foreground">
                        {account.last_login_at
                          ? new Date(
                              account.last_login_at
                            ).toLocaleDateString("ja-JP")
                          : "なし"}
                      </td>
                      <td className="py-3">
                        {account.is_active && (
                          <Button
                            size="sm"
                            variant="outline"
                            className="text-destructive border-destructive/40 hover:bg-destructive/10"
                            onClick={() => setDeactivateTargetId(account.id)}
                          >
                            停止する
                          </Button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}

      {/* Deactivate confirmation dialog */}
      <Dialog
        open={deactivateTargetId !== null}
        onOpenChange={(open) => {
          if (!open) setDeactivateTargetId(null);
        }}
      >
        <DialogContent showCloseButton={false} className="sm:max-w-sm w-full mx-2">
          <DialogHeader>
            <DialogTitle>アカウントを停止しますか？</DialogTitle>
            <DialogDescription>
              このアカウントを停止すると、対象のユーザーはログインできなくなります。後から再有効化は管理者が行えます。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setDeactivateTargetId(null)}
            >
              キャンセル
            </Button>
            <Button
              variant="destructive"
              onClick={() => {
                if (deactivateTargetId)
                  handleDeactivate(deactivateTargetId);
              }}
            >
              停止する
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ---------- Tab: Compliance ----------

const COMPLIANCE_ITEMS = [
  {
    key: "appi",
    label: "個人情報保護法（APPI）対応",
    description: "プライバシーポリシーの整備・個人情報取扱規定の策定",
  },
  {
    key: "harassment",
    label: "ハラスメント防止措置",
    description: "相談窓口の設置・研修の実施・規定の整備",
  },
  {
    key: "work_rules",
    label: "就業規則",
    description: "最新の法令に準拠した就業規則の整備・周知",
  },
  {
    key: "stress_check",
    label: "ストレスチェック",
    description: "年1回の実施と結果に基づく職場環境改善",
  },
];

function ComplianceTab() {
  const { session } = useAuth();
  const token = session?.access_token;

  const [checked, setChecked] = useState<Record<string, boolean>>({});
  const [checking, setChecking] = useState(false);
  const [checkResult, setCheckResult] = useState<string | null>(null);
  const [checkError, setCheckError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    apiFetch<{ items: Array<{ key: string; is_completed: boolean }> }>(
      "/backoffice/compliance",
      { token }
    )
      .then((res) => {
        const map: Record<string, boolean> = {};
        for (const item of res.items ?? []) {
          map[item.key] = item.is_completed;
        }
        setChecked(map);
      })
      .catch(() => {});
  }, [token]);

  async function handleCheck() {
    if (!token) return;
    setChecking(true);
    setCheckResult(null);
    setCheckError(null);
    try {
      const res = await apiFetch<{ message?: string }>(
        "/bpo/backoffice/compliance-check",
        { method: "POST", token, body: { items: checked } }
      );
      setCheckResult(
        res.message ?? "コンプライアンスチェックが完了しました。"
      );
    } catch (err) {
      setCheckError(
        err instanceof Error
          ? err.message
          : "チェックに失敗しました。しばらく経ってから再度お試しください"
      );
    } finally {
      setChecking(false);
    }
  }

  const completedCount = Object.values(checked).filter(Boolean).length;
  const totalCount = COMPLIANCE_ITEMS.length;

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-sm text-muted-foreground">
          法令遵守の達成状況を確認します。未対応の項目は早めに整備してください。
        </p>
        <Button
          size="sm"
          onClick={handleCheck}
          disabled={checking}
          className="shrink-0"
        >
          {checking ? (
            <>
              <span className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
              チェック中...
            </>
          ) : (
            "コンプライアンスチェックを実行する"
          )}
        </Button>
      </div>

      {checkResult && (
        <div className="rounded-md bg-green-50 px-3 py-2 text-sm text-green-700">
          {checkResult}
        </div>
      )}
      {checkError && (
        <div className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {checkError}
        </div>
      )}

      {/* Progress gauge */}
      <Card>
        <CardContent className="pt-4 pb-3">
          <div className="flex items-center justify-between mb-2">
            <p className="text-sm font-medium">達成状況</p>
            <p className="text-sm font-semibold">
              {completedCount} / {totalCount} 項目
            </p>
          </div>
          <div className="h-2 w-full rounded-full bg-muted">
            <div
              className="h-2 rounded-full bg-green-500 transition-all"
              style={{
                width:
                  totalCount > 0
                    ? `${Math.round((completedCount / totalCount) * 100)}%`
                    : "0%",
              }}
            />
          </div>
          <p className="mt-1 text-xs text-muted-foreground text-right">
            {totalCount > 0
              ? `${Math.round((completedCount / totalCount) * 100)}% 達成`
              : ""}
          </p>
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {COMPLIANCE_ITEMS.map((item) => {
          const done = checked[item.key] ?? false;
          return (
            <Card
              key={item.key}
              className={`cursor-pointer transition-colors ${
                done ? "border-green-200 bg-green-50" : ""
              }`}
              onClick={() =>
                setChecked((prev) => ({ ...prev, [item.key]: !prev[item.key] }))
              }
            >
              <CardContent className="flex items-start gap-3 py-4">
                <div className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded border-2 border-muted-foreground/40 bg-background">
                  {done && (
                    <svg
                      className="h-3 w-3 text-green-600"
                      fill="none"
                      stroke="currentColor"
                      viewBox="0 0 24 24"
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        strokeWidth={3}
                        d="M5 13l4 4L19 7"
                      />
                    </svg>
                  )}
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium">{item.label}</p>
                  <p className="text-xs text-muted-foreground">
                    {item.description}
                  </p>
                </div>
                {done ? (
                  <span className="inline-flex items-center rounded px-2 py-0.5 text-xs font-medium bg-green-100 text-green-800 shrink-0">
                    対応済
                  </span>
                ) : (
                  <span className="inline-flex items-center rounded px-2 py-0.5 text-xs font-medium bg-yellow-100 text-yellow-800 shrink-0">
                    未対応
                  </span>
                )}
              </CardContent>
            </Card>
          );
        })}
      </div>
    </div>
  );
}

// ---------- Page ----------

const TABS: { key: ActiveTab; label: string }[] = [
  { key: "permits", label: "許認可・期限管理" },
  { key: "vendors", label: "取引先・反社チェック" },
  { key: "accounts", label: "アカウント管理" },
  { key: "compliance", label: "コンプライアンス" },
];

export default function BackofficeAdminPage() {
  const [activeTab, setActiveTab] = useState<ActiveTab>("permits");

  return (
    <div className="mx-auto max-w-5xl space-y-6 px-4 py-6 sm:px-6">
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900">総務・管理</h1>
        <p className="text-sm text-gray-500 mt-1">
          許認可管理・取引先審査・アカウント管理を一元化します
        </p>
      </div>

      {/* Tab navigation */}
      <div className="border-b">
        <nav className="-mb-px flex gap-0 overflow-x-auto">
          {TABS.map((tab) => (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={`shrink-0 border-b-2 px-4 py-2.5 text-sm font-medium transition-colors ${
                activeTab === tab.key
                  ? "border-primary text-primary"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </nav>
      </div>

      {/* Tab content */}
      {activeTab === "permits" && <PermitsTab />}
      {activeTab === "vendors" && <VendorsTab />}
      {activeTab === "accounts" && <AccountsTab />}
      {activeTab === "compliance" && <ComplianceTab />}
    </div>
  );
}
