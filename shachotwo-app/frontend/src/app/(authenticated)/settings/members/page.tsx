"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useAuth } from "@/hooks/use-auth";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Select } from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";

// ---------- Types ----------

interface MemberUser {
  id: string;
  company_id: string;
  email: string;
  name: string;
  role: string;
  department: string | null;
  is_active: boolean;
  created_at: string;
}

interface Invitation {
  id: string;
  company_id: string;
  email: string;
  role: string;
  invited_by: string;
  status: string;
  expires_at: string;
  created_at: string;
}

// ---------- Page ----------

export default function MembersPage() {
  const { user, session } = useAuth();
  const [members, setMembers] = useState<MemberUser[]>([]);
  const [invitations, setInvitations] = useState<Invitation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Cancel invitation confirmation
  const [cancelTargetId, setCancelTargetId] = useState<string | null>(null);

  // Invite form
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState("editor");
  const [inviteName, setInviteName] = useState("");
  const [inviting, setInviting] = useState(false);
  const [inviteSuccess, setInviteSuccess] = useState<string | null>(null);
  const [inviteError, setInviteError] = useState<string | null>(null);

  /** 行ごとのロール下書き（一覧と同期） */
  const [pendingRoles, setPendingRoles] = useState<Record<string, string>>(
    {}
  );
  const [savingMemberId, setSavingMemberId] = useState<string | null>(null);
  const [roleSaveErrors, setRoleSaveErrors] = useState<Record<string, string>>(
    {}
  );
  const [roleSaveSuccess, setRoleSaveSuccess] = useState<string | null>(null);

  const companyId = user?.app_metadata?.company_id;
  const token = session?.access_token;
  const currentRole = user?.app_metadata?.role;

  async function fetchData(options?: { silent?: boolean }) {
    if (!token || !companyId) return;
    const silent = options?.silent === true;
    if (!silent) {
      setLoading(true);
    }
    setError(null);
    try {
      const [membersRes, invitationsRes] = await Promise.all([
        apiFetch<{ items: MemberUser[]; total: number }>(
          `/companies/${companyId}/users`,
          { token }
        ),
        apiFetch<{ items: Invitation[]; total: number }>(
          `/companies/${companyId}/invitations`,
          { token, params: { status: "pending" } }
        ),
      ]);
      setMembers(membersRes.items);
      setInvitations(invitationsRes.items);
      const next: Record<string, string> = {};
      for (const m of membersRes.items) {
        next[m.id] = m.role;
      }
      setPendingRoles(next);
      setRoleSaveErrors({});
    } catch (err) {
      setError(err instanceof Error ? err.message : "データの取得に失敗しました");
    } finally {
      if (!silent) {
        setLoading(false);
      }
    }
  }

  useEffect(() => {
    fetchData();
  }, [token, companyId]);

  async function saveMemberRole(memberId: string) {
    if (!token || !companyId) return;
    const role = pendingRoles[memberId];
    if (!role) return;

    setSavingMemberId(memberId);
    setRoleSaveErrors((prev) => ({ ...prev, [memberId]: "" }));
    setRoleSaveSuccess(null);

    try {
      await apiFetch(`/companies/${companyId}/users/${memberId}`, {
        method: "PATCH",
        token,
        body: { role },
      });
      setRoleSaveSuccess("権限を更新しました");
      await fetchData({ silent: true });
    } catch (err) {
      const msg =
        err instanceof Error ? err.message : "権限の更新に失敗しました";
      setRoleSaveErrors((prev) => ({ ...prev, [memberId]: msg }));
    } finally {
      setSavingMemberId(null);
    }
  }

  async function handleInvite(e: FormEvent) {
    e.preventDefault();
    if (!token || !companyId) return;

    setInviting(true);
    setInviteError(null);
    setInviteSuccess(null);

    try {
      await apiFetch(`/companies/${companyId}/invitations`, {
        method: "POST",
        token,
        body: {
          email: inviteEmail,
          role: inviteRole,
          name: inviteName || undefined,
        },
      });
      setInviteSuccess(`${inviteEmail} に招待メールを送信しました`);
      setInviteEmail("");
      setInviteName("");
      setInviteRole("editor");
      // リロード
      fetchData();
    } catch (err) {
      setInviteError(
        err instanceof Error ? err.message : "招待の送信に失敗しました"
      );
    } finally {
      setInviting(false);
    }
  }

  async function handleCancelInvitation(invitationId: string) {
    if (!token || !companyId) return;
    try {
      await apiFetch(`/companies/${companyId}/invitations/${invitationId}`, {
        method: "DELETE",
        token,
      });
      fetchData();
    } catch {
      setError("招待のキャンセルに失敗しました。しばらく経ってから再度お試しください");
    } finally {
      setCancelTargetId(null);
    }
  }

  if (currentRole !== "admin") {
    return (
      <div className="mx-auto max-w-3xl py-8">
        <Card>
          <CardContent className="py-8 text-center text-sm text-muted-foreground">
            メンバー管理は管理者のみ利用できます。
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold">メンバー管理</h1>
        <p className="text-sm text-muted-foreground">
          チームメンバーの管理と招待ができます。
        </p>
      </div>

      {error && (
        <Card className="border-destructive/50 bg-destructive/5">
          <CardContent className="pt-4">
            <p className="text-sm text-destructive">{error}</p>
          </CardContent>
        </Card>
      )}

      {/* Invite form */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">メンバーを招待</CardTitle>
          <CardDescription>
            メールアドレスを入力して招待メールを送信します。
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleInvite} className="flex flex-col gap-4">
            {inviteSuccess && (
              <div className="rounded-md bg-green-50 px-3 py-2 text-sm text-green-700 dark:bg-green-950 dark:text-green-300">
                {inviteSuccess}
              </div>
            )}
            {inviteError && (
              <div className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
                {inviteError}
              </div>
            )}

            <div className="flex flex-col gap-2">
              <Label htmlFor="invite-email">メールアドレス</Label>
              <Input
                id="invite-email"
                type="email"
                placeholder="例: yamada@example.com"
                value={inviteEmail}
                onChange={(e) => setInviteEmail(e.target.value)}
                required
              />
            </div>

            <div className="flex gap-4">
              <div className="flex flex-1 flex-col gap-2">
                <Label htmlFor="invite-name">名前（任意）</Label>
                <Input
                  id="invite-name"
                  type="text"
                  placeholder="山田 太郎"
                  value={inviteName}
                  onChange={(e) => setInviteName(e.target.value)}
                />
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor="invite-role">権限</Label>
                <select
                  id="invite-role"
                  value={inviteRole}
                  onChange={(e) => setInviteRole(e.target.value)}
                  className="h-10 rounded-md border border-input bg-background px-3 text-sm"
                >
                  <option value="editor">編集者</option>
                  <option value="admin">管理者</option>
                </select>
              </div>
            </div>

            <Button type="submit" disabled={inviting} className="self-start">
              {inviting ? "送信中..." : "招待メールを送信"}
            </Button>
          </form>
        </CardContent>
      </Card>

      {/* Pending invitations */}
      {invitations.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">招待中</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="divide-y">
              {invitations.map((inv) => (
                <div
                  key={inv.id}
                  className="flex items-center justify-between py-3"
                >
                  <div className="flex flex-col gap-0.5">
                    <span className="text-sm font-medium">{inv.email}</span>
                    <div className="flex items-center gap-2">
                      <Badge variant="outline" className="text-xs">
                        {inv.role === "admin" ? "管理者" : "編集者"}
                      </Badge>
                      <span className="text-xs text-muted-foreground">
                        {new Date(inv.created_at).toLocaleDateString("ja-JP")}
                        に招待
                      </span>
                    </div>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-destructive hover:text-destructive"
                    onClick={() => setCancelTargetId(inv.id)}
                  >
                    キャンセル
                  </Button>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Cancel invitation confirmation dialog */}
      <Dialog open={cancelTargetId !== null} onOpenChange={(open) => { if (!open) setCancelTargetId(null); }}>
        <DialogContent showCloseButton={false}>
          <DialogHeader>
            <DialogTitle>招待を取り消す</DialogTitle>
            <DialogDescription>
              この招待を取り消しますか？取り消すと、招待メールのリンクが無効になります。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCancelTargetId(null)}>
              戻る
            </Button>
            <Button
              variant="destructive"
              onClick={() => { if (cancelTargetId) handleCancelInvitation(cancelTargetId); }}
            >
              取り消す
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Current members */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">
            メンバー一覧
            {!loading && (
              <span className="ml-2 text-sm font-normal text-muted-foreground">
                ({members.length}人)
              </span>
            )}
          </CardTitle>
          <CardDescription>
            権限を変更したあと、対象ユーザーは再ログイン（またはセッション更新）まで
            API の権限が古いままの場合があります。
          </CardDescription>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="space-y-3">
              {Array.from({ length: 3 }).map((_, i) => (
                <div key={i} className="flex items-center gap-3 py-3">
                  <div className="h-10 w-10 animate-pulse rounded-full bg-muted" />
                  <div className="flex-1 space-y-1.5">
                    <div className="h-4 w-32 animate-pulse rounded bg-muted" />
                    <div className="h-3 w-48 animate-pulse rounded bg-muted" />
                  </div>
                </div>
              ))}
            </div>
          ) : members.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              メンバーがいません。
            </p>
          ) : (
            <div className="space-y-2">
              {roleSaveSuccess && (
                <p className="text-sm text-green-600 dark:text-green-500">
                  {roleSaveSuccess}
                </p>
              )}
              <div className="divide-y">
                {members.map((member) => {
                  const draft = pendingRoles[member.id] ?? member.role;
                  const dirty = draft !== member.role;
                  const rowBusy = savingMemberId === member.id;
                  const rowErr = roleSaveErrors[member.id];

                  return (
                    <div
                      key={member.id}
                      className="flex flex-col gap-2 py-3 sm:flex-row sm:items-center sm:justify-between"
                    >
                      <div className="flex items-center gap-3">
                        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-primary/10 text-sm font-medium text-primary">
                          {(member.name || member.email)[0]}
                        </div>
                        <div className="flex min-w-0 flex-col gap-0.5">
                          <span className="text-sm font-medium">
                            {member.name || member.email}
                          </span>
                          <span className="truncate text-xs text-muted-foreground">
                            {member.email}
                          </span>
                        </div>
                      </div>
                      <div className="flex flex-col items-stretch gap-2 sm:flex-row sm:items-center sm:gap-3">
                        <div className="flex flex-wrap items-center gap-2">
                          <Select
                            className="w-[140px]"
                            value={draft}
                            disabled={rowBusy}
                            onChange={(e) => {
                              setPendingRoles((prev) => ({
                                ...prev,
                                [member.id]: e.target.value,
                              }));
                              setRoleSaveErrors((prev) => {
                                const next = { ...prev };
                                delete next[member.id];
                                return next;
                              });
                              setRoleSaveSuccess(null);
                            }}
                          >
                            <option value="admin">管理者</option>
                            <option value="editor">編集者</option>
                          </Select>
                          <Button
                            type="button"
                            size="sm"
                            variant="secondary"
                            disabled={!dirty || rowBusy}
                            onClick={() => saveMemberRole(member.id)}
                          >
                            {rowBusy ? "保存中…" : "保存"}
                          </Button>
                          {!member.is_active && (
                            <Badge
                              variant="outline"
                              className="text-muted-foreground"
                            >
                              無効
                            </Badge>
                          )}
                        </div>
                        {rowErr ? (
                          <p className="text-xs text-destructive">{rowErr}</p>
                        ) : null}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
