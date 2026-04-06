"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/hooks/use-auth";
import { apiFetch } from "@/lib/api";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";

// ---------- 型定義 ----------

interface Connector {
  id: string;
  tool_name: string;
  tool_type: string;
  connection_method: string;
  health_status: string;
  last_health_check: string | null;
}

interface ConnectorListResponse {
  items: Connector[];
  total: number;
}

// ---------- ツール定義 ----------

const TOOL_OPTIONS = [
  {
    name: "kintone",
    label: "kintone",
    type: "saas",
    fields: [
      { key: "subdomain", label: "サブドメイン", placeholder: "例: mycompany（mycompany.cybozu.comの場合）" },
      { key: "api_token", label: "APIトークン", placeholder: "例: xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx", secret: true },
    ],
  },
  {
    name: "freee",
    label: "freee",
    type: "saas",
    fields: [
      { key: "client_id", label: "クライアントID", placeholder: "例: freee_client_id_xxxx" },
      { key: "client_secret", label: "クライアントシークレット", placeholder: "例: xxxxxxxxxx", secret: true },
    ],
  },
  {
    name: "slack",
    label: "Slack",
    type: "api",
    fields: [
      { key: "bot_token", label: "Botトークン", placeholder: "例: xoxb-xxxx-xxxx-xxxx", secret: true },
      { key: "channel_id", label: "通知先チャンネルID", placeholder: "例: C0123456789" },
    ],
  },
  {
    name: "lineworks",
    label: "LINE WORKS",
    type: "api",
    fields: [
      { key: "client_id", label: "クライアントID", placeholder: "例: xxxxxxxxxx" },
      { key: "client_secret", label: "クライアントシークレット", placeholder: "例: xxxxxxxxxx", secret: true },
      { key: "service_account", label: "サービスアカウント", placeholder: "例: xxxx@xxxx.bot.works" },
    ],
  },
  {
    name: "google_sheets",
    label: "Google スプレッドシート",
    type: "api",
    fields: [
      { key: "service_account_json", label: "サービスアカウントJSON", placeholder: "例: {\"type\": \"service_account\", ...}", secret: true },
    ],
  },
  {
    name: "cloudsign",
    label: "CloudSign",
    type: "saas",
    fields: [
      { key: "api_key", label: "APIキー", placeholder: "例: xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx", secret: true },
    ],
  },
  {
    name: "email",
    label: "メール（SMTP）",
    type: "api",
    fields: [
      { key: "smtp_host", label: "SMTPホスト", placeholder: "例: smtp.gmail.com" },
      { key: "smtp_port", label: "SMTPポート", placeholder: "例: 587" },
      { key: "smtp_user", label: "メールアドレス", placeholder: "例: yourname@example.com" },
      { key: "smtp_password", label: "パスワード", placeholder: "例: xxxxxxxx", secret: true },
    ],
  },
  {
    name: "webform",
    label: "Webフォーム",
    type: "api",
    fields: [
      { key: "webhook_url", label: "Webhook URL", placeholder: "例: https://example.com/webhook" },
      { key: "secret_token", label: "シークレットトークン（任意）", placeholder: "例: xxxxxxxx", secret: true },
    ],
  },
] as const;

type ToolName = typeof TOOL_OPTIONS[number]["name"];

// ---------- ヘルパー ----------

function healthStatusLabel(status: string): string {
  switch (status) {
    case "healthy": return "正常";
    case "degraded": return "低下";
    case "down": return "停止中";
    case "unknown": return "未確認";
    default: return status;
  }
}

function healthStatusBadge(status: string) {
  switch (status) {
    case "healthy":
      return <Badge className="bg-green-100 text-green-800">正常</Badge>;
    case "degraded":
      return <Badge className="bg-yellow-100 text-yellow-800">低下</Badge>;
    case "down":
      return <Badge variant="destructive">停止中</Badge>;
    default:
      return <Badge variant="secondary">未確認</Badge>;
  }
}

function toolTypeLabel(type: string): string {
  switch (type) {
    case "saas": return "SaaS";
    case "api": return "API";
    case "cli": return "CLI";
    default: return type;
  }
}

function formatDateTime(iso: string | null): string {
  if (!iso) return "未確認";
  const d = new Date(iso);
  return d.toLocaleString("ja-JP", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function toolLabel(toolName: string): string {
  const found = TOOL_OPTIONS.find((t) => t.name === toolName);
  return found ? found.label : toolName;
}

// ---------- コネクタ行コンポーネント ----------

interface ConnectorRowProps {
  connector: Connector;
  onHealthCheck: (id: string) => Promise<void>;
  onDelete: (connector: Connector) => void;
  onEdit: (connector: Connector) => void;
  healthChecking: boolean;
}

function ConnectorRow({ connector, onHealthCheck, onDelete, onEdit, healthChecking }: ConnectorRowProps) {
  return (
    <Card>
      <CardContent className="py-4 px-4">
        {/* スマホ：縦並び、PC：横並び */}
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="space-y-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-base font-medium">{toolLabel(connector.tool_name)}</span>
              <Badge variant="outline">{toolTypeLabel(connector.tool_type)}</Badge>
              {healthStatusBadge(connector.health_status)}
            </div>
            <p className="text-xs text-muted-foreground">
              最終確認: {formatDateTime(connector.last_health_check)}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => onHealthCheck(connector.id)}
              disabled={healthChecking}
            >
              {healthChecking ? (
                <span className="flex items-center gap-1.5">
                  <span className="h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent" />
                  確認中...
                </span>
              ) : "接続確認"}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => onEdit(connector)}
            >
              設定変更
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="text-destructive hover:bg-destructive/10"
              onClick={() => onDelete(connector)}
            >
              削除
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// ---------- 新規追加ダイアログ ----------

interface AddConnectorDialogProps {
  open: boolean;
  onClose: () => void;
  onSuccess: () => void;
  token: string | undefined;
}

function AddConnectorDialog({ open, onClose, onSuccess, token }: AddConnectorDialogProps) {
  const [selectedTool, setSelectedTool] = useState<ToolName | "">("");
  const [fieldValues, setFieldValues] = useState<Record<string, string>>({});
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const toolDef = TOOL_OPTIONS.find((t) => t.name === selectedTool);

  function handleToolChange(name: ToolName | "") {
    setSelectedTool(name);
    setFieldValues({});
    setTestResult(null);
    setError(null);
  }

  function handleClose() {
    setSelectedTool("");
    setFieldValues({});
    setTestResult(null);
    setError(null);
    onClose();
  }

  async function handleTest() {
    if (!toolDef) return;
    setTesting(true);
    setError(null);
    setTestResult(null);
    try {
      // まず仮登録してからテスト
      const created = await apiFetch<{ id: string }>("/connectors", {
        token,
        method: "POST",
        body: {
          tool_name: toolDef.name,
          tool_type: toolDef.type,
          connection_config: fieldValues,
        },
      });
      const result = await apiFetch<{ health_status: string; message: string }>(
        `/connectors/${created.id}/test`,
        { token, method: "POST" }
      );
      setTestResult({
        success: result.health_status === "healthy",
        message: result.message ?? "接続テストが完了しました",
      });
      // テスト後にそのまま保存完了扱い
      onSuccess();
      handleClose();
    } catch {
      setError("接続テストに失敗しました。設定内容を確認してもう一度お試しください");
    } finally {
      setTesting(false);
    }
  }

  async function handleSave() {
    if (!toolDef) return;
    setSaving(true);
    setError(null);
    try {
      await apiFetch("/connectors", {
        token,
        method: "POST",
        body: {
          tool_name: toolDef.name,
          tool_type: toolDef.type,
          connection_config: fieldValues,
        },
      });
      onSuccess();
      handleClose();
    } catch {
      setError("ツールの登録に失敗しました。入力内容を確認してもう一度お試しください");
    } finally {
      setSaving(false);
    }
  }

  const hasRequiredFields = toolDef
    ? toolDef.fields.every((f) => {
        if (f.label.includes("任意")) return true;
        return (fieldValues[f.key] ?? "").trim() !== "";
      })
    : false;

  return (
    <Dialog open={open} onOpenChange={(o) => !o && handleClose()}>
      <DialogContent className="w-full mx-2 sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>外部ツールを追加する</DialogTitle>
          <DialogDescription>
            連携するツールを選択して接続情報を入力してください
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 pt-2">
          {/* ツール選択 */}
          <div className="space-y-1.5">
            <Label>ツールを選択</Label>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {TOOL_OPTIONS.map((tool) => (
                <button
                  key={tool.name}
                  type="button"
                  onClick={() => handleToolChange(tool.name)}
                  className={`rounded-lg border p-2 text-xs font-medium transition-colors text-center ${
                    selectedTool === tool.name
                      ? "border-primary bg-primary/10 text-primary"
                      : "border-border bg-card hover:bg-muted"
                  }`}
                >
                  {tool.label}
                </button>
              ))}
            </div>
          </div>

          {/* 設定フォーム */}
          {toolDef && (
            <div className="space-y-3 rounded-lg border bg-muted/30 p-4">
              <p className="text-sm font-medium">{toolDef.label} の接続設定</p>
              {toolDef.fields.map((f) => (
                <div key={f.key} className="space-y-1.5">
                  <Label htmlFor={`field-${f.key}`}>{f.label}</Label>
                  <Input
                    id={`field-${f.key}`}
                    type={"secret" in f && f.secret ? "password" : "text"}
                    placeholder={f.placeholder}
                    value={fieldValues[f.key] ?? ""}
                    onChange={(e) =>
                      setFieldValues((prev) => ({ ...prev, [f.key]: e.target.value }))
                    }
                  />
                </div>
              ))}
            </div>
          )}

          {/* エラー */}
          {error && (
            <p className="text-sm text-destructive">{error}</p>
          )}

          {/* テスト結果 */}
          {testResult && (
            <p className={`text-sm ${testResult.success ? "text-green-600" : "text-destructive"}`}>
              {testResult.message}
            </p>
          )}

          {/* アクションボタン */}
          {toolDef && (
            <div className="flex flex-col gap-2 sm:flex-row sm:justify-end">
              <Button variant="outline" onClick={handleClose} className="w-full sm:w-auto">
                キャンセル
              </Button>
              <Button
                variant="outline"
                onClick={handleTest}
                disabled={testing || saving || !hasRequiredFields}
                className="w-full sm:w-auto"
              >
                {testing ? (
                  <span className="flex items-center gap-1.5">
                    <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />
                    接続テスト中...
                  </span>
                ) : "接続テストして追加"}
              </Button>
              <Button
                onClick={handleSave}
                disabled={saving || testing || !hasRequiredFields}
                className="w-full sm:w-auto"
              >
                {saving ? (
                  <span className="flex items-center gap-1.5">
                    <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-primary-foreground border-t-transparent" />
                    登録中...
                  </span>
                ) : "テストせずに追加する"}
              </Button>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

// ---------- 削除確認ダイアログ ----------

interface DeleteDialogProps {
  connector: Connector | null;
  onClose: () => void;
  onConfirm: () => Promise<void>;
  deleting: boolean;
}

function DeleteDialog({ connector, onClose, onConfirm, deleting }: DeleteDialogProps) {
  return (
    <Dialog open={!!connector} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="w-full mx-2 sm:max-w-md">
        <DialogHeader>
          <DialogTitle>ツール連携を削除しますか？</DialogTitle>
          <DialogDescription>
            「{connector ? toolLabel(connector.tool_name) : ""}」の連携設定を削除します。
            削除後は自動取込が停止されます。この操作は取り消せません。
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-2 pt-2 sm:flex-row sm:justify-end">
          <Button variant="outline" onClick={onClose} disabled={deleting} className="w-full sm:w-auto">
            キャンセル
          </Button>
          <Button
            variant="destructive"
            onClick={onConfirm}
            disabled={deleting}
            className="w-full sm:w-auto"
          >
            {deleting ? (
              <span className="flex items-center gap-1.5">
                <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-destructive-foreground border-t-transparent" />
                削除中...
              </span>
            ) : "連携を解除する"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

// ---------- メインページ ----------

export default function ConnectorsPage() {
  const { session } = useAuth();
  const router = useRouter();
  const token = session?.access_token;

  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  const [addDialogOpen, setAddDialogOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<Connector | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [healthCheckingIds, setHealthCheckingIds] = useState<Set<string>>(new Set());

  const fetchConnectors = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const data = await apiFetch<ConnectorListResponse>("/connectors", { token });
      setConnectors(data.items ?? []);
    } catch {
      setError("ツール一覧の取得に失敗しました。しばらく経ってから再度お試しください");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    fetchConnectors();
  }, [fetchConnectors]);

  async function handleHealthCheck(id: string) {
    setHealthCheckingIds((prev) => new Set(prev).add(id));
    try {
      await apiFetch(`/connectors/${id}/test`, { token, method: "POST" });
      showSuccess("接続確認が完了しました");
      await fetchConnectors();
    } catch {
      setError("接続確認に失敗しました。設定内容を確認してください");
    } finally {
      setHealthCheckingIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await apiFetch(`/connectors/${deleteTarget.id}`, { token, method: "DELETE" });
      showSuccess(`「${toolLabel(deleteTarget.tool_name)}」の連携を削除しました`);
      setDeleteTarget(null);
      await fetchConnectors();
    } catch {
      setError("削除に失敗しました。しばらく経ってから再度お試しください");
      setDeleteTarget(null);
    } finally {
      setDeleting(false);
    }
  }

  function showSuccess(msg: string) {
    setSuccessMessage(msg);
    setTimeout(() => setSuccessMessage(null), 3000);
  }

  // スケルトンローディング
  if (loading) {
    return (
      <div className="mx-auto max-w-3xl space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <div className="h-7 w-48 animate-pulse rounded bg-muted" />
            <div className="mt-1 h-4 w-72 animate-pulse rounded bg-muted" />
          </div>
          <div className="h-10 w-36 animate-pulse rounded bg-muted" />
        </div>
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-20 animate-pulse rounded-lg bg-muted" />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      {/* ページヘッダー */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold">外部ツール連携</h1>
          <p className="text-sm text-muted-foreground">
            kintone・freee・Slackなど外部ツールと連携して、データの自動取込・通知を設定します
          </p>
        </div>
        <Button
          onClick={() => setAddDialogOpen(true)}
          className="w-full sm:w-auto shrink-0"
          size="lg"
        >
          ツールを追加する
        </Button>
      </div>

      {/* 成功通知 */}
      {successMessage && (
        <div className="rounded-lg border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-700">
          {successMessage}
        </div>
      )}

      {/* エラー */}
      {error && (
        <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {/* 一覧 */}
      {connectors.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center gap-4 py-16">
            <div className="flex h-16 w-16 items-center justify-center rounded-full bg-muted">
              <svg
                className="h-8 w-8 text-muted-foreground"
                xmlns="http://www.w3.org/2000/svg"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={1.5}
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M13.19 8.688a4.5 4.5 0 0 1 1.242 7.244l-4.5 4.5a4.5 4.5 0 0 1-6.364-6.364l1.757-1.757m13.35-.622 1.757-1.757a4.5 4.5 0 0 0-6.364-6.364l-4.5 4.5a4.5 4.5 0 0 0 1.242 7.244"
                />
              </svg>
            </div>
            <div className="text-center">
              <p className="text-base font-medium">まだ外部ツールが連携されていません</p>
              <p className="mt-1 text-sm text-muted-foreground">
                外部ツールを接続すると、データの自動取込が可能になります
              </p>
            </div>
            <Button
              onClick={() => setAddDialogOpen(true)}
              size="lg"
              className="mt-2"
            >
              はじめてのツールを追加する
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-3">
          <p className="text-sm text-muted-foreground">
            {connectors.length}件のツールが連携されています
          </p>
          {connectors.map((connector) => (
            <ConnectorRow
              key={connector.id}
              connector={connector}
              onHealthCheck={handleHealthCheck}
              onDelete={(c) => setDeleteTarget(c)}
              onEdit={(c) => {
                // 設定変更: 一覧リフレッシュ程度（MVP: 詳細編集はPhase 2+）
                setAddDialogOpen(true);
              }}
              healthChecking={healthCheckingIds.has(connector.id)}
            />
          ))}
        </div>
      )}

      {/* 新規追加ダイアログ */}
      <AddConnectorDialog
        open={addDialogOpen}
        onClose={() => setAddDialogOpen(false)}
        onSuccess={() => {
          showSuccess("ツールを登録しました");
          fetchConnectors();
        }}
        token={token}
      />

      {/* 削除確認ダイアログ */}
      <DeleteDialog
        connector={deleteTarget}
        onClose={() => setDeleteTarget(null)}
        onConfirm={handleDelete}
        deleting={deleting}
      />
    </div>
  );
}
