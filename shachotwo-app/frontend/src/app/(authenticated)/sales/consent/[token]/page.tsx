"use client";

import { useEffect, useState, useCallback } from "react";
import { useParams } from "next/navigation";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

// ---------------------------------------------------------------------------
// 型定義
// ---------------------------------------------------------------------------

interface ConsentInfo {
  token: string;
  contract_id: string;
  contract_title: string;
  contract_data: Record<string, unknown>;
  company_name: string;
  to_email: string;
  status: string;
  created_at: string;
  is_expired: boolean;
}

interface ConsentAgreeResult {
  success: boolean;
  consent_record_id: string;
  contract_id: string;
  signed_at: string;
  message: string;
}

// ---------------------------------------------------------------------------
// API呼び出し（認証不要のため apiFetch を使わず直接 fetch）
// ---------------------------------------------------------------------------

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function fetchConsentInfo(token: string): Promise<ConsentInfo> {
  const res = await fetch(`${API_BASE}/api/v1/consent/${token}`);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || "同意情報の取得に失敗しました");
  }
  return res.json();
}

async function fetchConsentDocument(token: string): Promise<string> {
  const res = await fetch(`${API_BASE}/api/v1/consent/${token}/document`);
  if (!res.ok) {
    throw new Error("書類の取得に失敗しました");
  }
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}

async function submitConsent(
  token: string,
  fullName: string
): Promise<ConsentAgreeResult> {
  const res = await fetch(`${API_BASE}/api/v1/consent/${token}/agree`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ full_name: fullName, agreed: true }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || "同意処理に失敗しました");
  }
  return res.json();
}

// ---------------------------------------------------------------------------
// 金額フォーマット
// ---------------------------------------------------------------------------

function formatCurrency(amount: unknown): string {
  if (typeof amount !== "number") return "";
  return `¥${amount.toLocaleString("ja-JP")}`;
}

function formatDate(dateStr: string | undefined): string {
  if (!dateStr) return "";
  try {
    return new Date(dateStr).toLocaleDateString("ja-JP", {
      year: "numeric",
      month: "long",
      day: "numeric",
    });
  } catch {
    return dateStr;
  }
}

// ---------------------------------------------------------------------------
// コンポーネント
// ---------------------------------------------------------------------------

type PageState = "loading" | "consent_form" | "submitting" | "completed" | "error" | "expired" | "already_agreed";

export default function ConsentPage() {
  const params = useParams();
  const token = params.token as string;

  const [pageState, setPageState] = useState<PageState>("loading");
  const [consentInfo, setConsentInfo] = useState<ConsentInfo | null>(null);
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [fullName, setFullName] = useState("");
  const [agreed, setAgreed] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  const [agreeResult, setAgreeResult] = useState<ConsentAgreeResult | null>(null);

  // 同意情報を取得
  const loadConsentInfo = useCallback(async () => {
    try {
      const info = await fetchConsentInfo(token);
      setConsentInfo(info);

      if (info.is_expired) {
        setPageState("expired");
        return;
      }

      if (info.status === "agreed") {
        setPageState("already_agreed");
        return;
      }

      setPageState("consent_form");

      // PDFも並行してロード
      try {
        const url = await fetchConsentDocument(token);
        setPdfUrl(url);
      } catch {
        // PDFの取得に失敗しても同意フォームは表示する
      }
    } catch (err) {
      setErrorMessage(
        err instanceof Error ? err.message : "情報の取得に失敗しました"
      );
      setPageState("error");
    }
  }, [token]);

  useEffect(() => {
    loadConsentInfo();
  }, [loadConsentInfo]);

  // 同意を送信
  const handleSubmit = async () => {
    if (!agreed || !fullName.trim()) return;

    setPageState("submitting");
    try {
      const result = await submitConsent(token, fullName.trim());
      setAgreeResult(result);
      setPageState("completed");
    } catch (err) {
      setErrorMessage(
        err instanceof Error ? err.message : "同意処理に失敗しました"
      );
      setPageState("error");
    }
  };

  // ----- ローディング -----
  if (pageState === "loading") {
    return (
      <div className="flex min-h-screen items-center justify-center bg-gray-50">
        <div className="flex flex-col items-center gap-3 py-8">
          <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
          <p className="text-sm text-muted-foreground">
            同意内容を読み込んでいます...
          </p>
        </div>
      </div>
    );
  }

  // ----- エラー -----
  if (pageState === "error") {
    return (
      <div className="flex min-h-screen items-center justify-center bg-gray-50 p-4">
        <Card className="w-full max-w-md">
          <CardHeader>
            <CardTitle className="text-2xl font-bold">
              エラーが発生しました
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-destructive">{errorMessage}</p>
            <p className="mt-4 text-sm text-muted-foreground">
              URLをご確認の上、もう一度お試しください。
              問題が解決しない場合は、送信元にお問い合わせください。
            </p>
          </CardContent>
          <CardFooter>
            <Button
              size="lg"
              className="w-full sm:w-auto"
              onClick={() => {
                setPageState("loading");
                loadConsentInfo();
              }}
            >
              再読み込みする
            </Button>
          </CardFooter>
        </Card>
      </div>
    );
  }

  // ----- 期限切れ -----
  if (pageState === "expired") {
    return (
      <div className="flex min-h-screen items-center justify-center bg-gray-50 p-4">
        <Card className="w-full max-w-md">
          <CardHeader>
            <CardTitle className="text-2xl font-bold">
              有効期限が切れています
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-muted-foreground">
              この同意リンクの有効期限が切れています。
              お手数ですが、送信元に再発行をご依頼ください。
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  // ----- 既に同意済み -----
  if (pageState === "already_agreed") {
    return (
      <div className="flex min-h-screen items-center justify-center bg-gray-50 p-4">
        <Card className="w-full max-w-md">
          <CardHeader>
            <div className="flex items-center gap-2">
              <CardTitle className="text-2xl font-bold">同意済み</CardTitle>
              <Badge className="bg-green-100 text-green-800">完了</Badge>
            </div>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-muted-foreground">
              この契約は既に同意済みです。
              同意済みの書類はメールでお送りしています。
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  // ----- 同意完了 -----
  if (pageState === "completed" && agreeResult) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-gray-50 p-4">
        <Card className="w-full max-w-lg">
          <CardHeader>
            <div className="flex items-center gap-2">
              <CardTitle className="text-2xl font-bold">
                同意が完了しました
              </CardTitle>
              <Badge className="bg-green-100 text-green-800">完了</Badge>
            </div>
            <CardDescription>
              ご対応ありがとうございます
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="rounded-lg bg-green-50 p-4">
              <p className="text-sm text-green-800">
                {agreeResult.message}
              </p>
            </div>
            <div className="space-y-2 text-sm">
              <div className="flex justify-between">
                <span className="text-muted-foreground">契約書</span>
                <span className="font-medium">
                  {consentInfo?.contract_title || "契約書"}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">同意日時</span>
                <span className="font-medium">
                  {formatDate(agreeResult.signed_at)}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">同意者</span>
                <span className="font-medium">{fullName}</span>
              </div>
            </div>
            <p className="text-xs text-muted-foreground">
              同意済みの書類はメールでもお送りします。
              このページを閉じていただいて問題ありません。
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  // ----- 同意フォーム（メイン画面） -----
  const contractData = consentInfo?.contract_data || {};

  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50 p-4">
      <div className="w-full max-w-2xl space-y-6">
        {/* ヘッダー */}
        <Card>
          <CardHeader>
            <CardTitle className="text-2xl font-bold">
              契約内容の確認と同意
            </CardTitle>
            <CardDescription>
              {consentInfo?.company_name
                ? `${consentInfo.company_name} より`
                : ""}
              以下の契約内容をご確認の上、同意をお願いいたします。
            </CardDescription>
          </CardHeader>
        </Card>

        {/* 契約内容表示 */}
        <Card>
          <CardHeader>
            <CardTitle className="text-lg font-semibold">
              {consentInfo?.contract_title || "契約書"}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {/* 契約データの主要フィールドを表示 */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              {Boolean(contractData.party_a) && (
                <div>
                  <p className="text-xs text-muted-foreground">甲（発注者）</p>
                  <p className="text-sm font-medium">
                    {String(contractData.party_a)}
                  </p>
                </div>
              )}
              {Boolean(contractData.party_b) && (
                <div>
                  <p className="text-xs text-muted-foreground">乙（受注者）</p>
                  <p className="text-sm font-medium">
                    {String(contractData.party_b)}
                  </p>
                </div>
              )}
              {contractData.contract_amount != null && (
                <div>
                  <p className="text-xs text-muted-foreground">契約金額</p>
                  <p className="text-sm font-medium">
                    {formatCurrency(contractData.contract_amount)}
                  </p>
                </div>
              )}
              {Boolean(contractData.start_date) && (
                <div>
                  <p className="text-xs text-muted-foreground">契約開始日</p>
                  <p className="text-sm font-medium">
                    {formatDate(String(contractData.start_date))}
                  </p>
                </div>
              )}
              {Boolean(contractData.end_date) && (
                <div>
                  <p className="text-xs text-muted-foreground">契約終了日</p>
                  <p className="text-sm font-medium">
                    {formatDate(String(contractData.end_date))}
                  </p>
                </div>
              )}
              {contractData.monthly_amount != null && (
                <div>
                  <p className="text-xs text-muted-foreground">月額</p>
                  <p className="text-sm font-medium">
                    {formatCurrency(contractData.monthly_amount)}
                  </p>
                </div>
              )}
            </div>

            {/* 契約書PDFプレビュー */}
            {pdfUrl && (
              <div className="mt-4">
                <p className="mb-2 text-sm font-medium">契約書の全文</p>
                <div className="rounded-lg border bg-white">
                  <iframe
                    src={pdfUrl}
                    className="h-96 w-full rounded-lg"
                    title="契約書プレビュー"
                  />
                </div>
                <a
                  href={pdfUrl}
                  download="contract.pdf"
                  className="mt-2 inline-block text-sm text-primary underline"
                >
                  PDFをダウンロードする
                </a>
              </div>
            )}

            {!pdfUrl && (
              <div className="mt-4 rounded-lg border bg-muted/50 p-4">
                <p className="text-sm text-muted-foreground">
                  契約書PDFの読み込み中...
                </p>
              </div>
            )}
          </CardContent>
        </Card>

        {/* 同意フォーム */}
        <Card>
          <CardHeader>
            <CardTitle className="text-lg font-semibold">
              同意の入力
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {/* 氏名入力 */}
            <div>
              <label
                htmlFor="fullName"
                className="mb-1 block text-sm font-medium"
              >
                同意者氏名
              </label>
              <input
                id="fullName"
                type="text"
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                placeholder="例: 山田 太郎"
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
              />
            </div>

            {/* 同意チェックボックス */}
            <div className="flex items-start gap-3">
              <input
                id="agree-checkbox"
                type="checkbox"
                checked={agreed}
                onChange={(e) => setAgreed(e.target.checked)}
                className="mt-1 h-5 w-5 rounded border-gray-300 text-primary focus:ring-primary"
              />
              <label htmlFor="agree-checkbox" className="text-sm leading-relaxed">
                上記の契約内容を確認しました。記載されている全ての条件に同意します。
              </label>
            </div>

            {/* バリデーションメッセージ */}
            {!fullName.trim() && agreed && (
              <p className="text-sm text-destructive">
                氏名を入力してください
              </p>
            )}
          </CardContent>
          <CardFooter className="flex flex-col gap-3 sm:flex-row sm:justify-end">
            <Button
              size="lg"
              className="w-full sm:w-auto"
              disabled={
                !agreed ||
                !fullName.trim() ||
                pageState === "submitting"
              }
              onClick={handleSubmit}
            >
              {pageState === "submitting" ? (
                <>
                  <span className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
                  同意処理中...
                </>
              ) : (
                "上記内容に同意する"
              )}
            </Button>
          </CardFooter>
        </Card>

        {/* フッター注意書き */}
        <div className="pb-8 text-center">
          <p className="text-xs text-muted-foreground">
            同意の記録として、IPアドレス・同意日時が保存されます。
          </p>
          <p className="text-xs text-muted-foreground">
            ご不明な点がございましたら、送信元の担当者にお問い合わせください。
          </p>
        </div>
      </div>
    </div>
  );
}
