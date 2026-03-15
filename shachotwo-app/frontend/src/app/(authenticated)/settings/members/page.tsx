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

  // Invite form
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState("editor");
  const [inviteName, setInviteName] = useState("");
  const [inviting, setInviting] = useState(false);
  const [inviteSuccess, setInviteSuccess] = useState<string | null>(null);
  const [inviteError, setInviteError] = useState<string | null>(null);

  const companyId = user?.app_metadata?.company_id;
  const token = session?.access_token;
  const currentRole = user?.app_metadata?.role;

  async function fetchData() {
    if (!token || !companyId) return;
    setLoading(true);
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
    } catch (err) {
      setError(err instanceof Error ? err.message : "データの取得に失敗しました");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    fetchData();
  }, [token, companyId]);

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
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "招待のキャンセルに失敗しました"
      );
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
                placeholder="member@example.com"
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
                    onClick={() => handleCancelInvitation(inv.id)}
                  >
                    キャンセル
                  </Button>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

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
            <div className="divide-y">
              {members.map((member) => (
                <div
                  key={member.id}
                  className="flex items-center justify-between py-3"
                >
                  <div className="flex items-center gap-3">
                    <div className="flex h-10 w-10 items-center justify-center rounded-full bg-primary/10 text-sm font-medium text-primary">
                      {(member.name || member.email)[0]}
                    </div>
                    <div className="flex flex-col gap-0.5">
                      <span className="text-sm font-medium">
                        {member.name || member.email}
                      </span>
                      <span className="text-xs text-muted-foreground">
                        {member.email}
                      </span>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge
                      variant={
                        member.role === "admin" ? "default" : "secondary"
                      }
                    >
                      {member.role === "admin" ? "管理者" : "編集者"}
                    </Badge>
                    {!member.is_active && (
                      <Badge variant="outline" className="text-muted-foreground">
                        無効
                      </Badge>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
