"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase";
import { apiFetch } from "@/lib/api";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
} from "@/components/ui/card";

/**
 * 招待リンクからのコールバックページ。
 *
 * Supabase の invite メールリンクを踏むと、
 * /invite#access_token=...&type=invite のように遷移してくる。
 * ここで:
 * 1. Supabase がハッシュフラグメントからセッションを確立
 * 2. セッションの app_metadata に company_id があれば招待経由
 * 3. /auth/setup を呼んで users レコードを作成
 * 4. ダッシュボードへリダイレクト
 */
export default function InviteCallbackPage() {
  const router = useRouter();
  const [status, setStatus] = useState<"processing" | "error">("processing");
  const [errorMessage, setErrorMessage] = useState("");

  useEffect(() => {
    async function handleInviteCallback() {
      const supabase = createClient();

      // Supabase がハッシュフラグメントからセッションを自動復元するのを待つ
      const {
        data: { session },
        error: sessionError,
      } = await supabase.auth.getSession();

      if (sessionError || !session) {
        setStatus("error");
        setErrorMessage(
          sessionError?.message || "セッションの取得に失敗しました。再度招待リンクをクリックしてください。"
        );
        return;
      }

      const appMetadata = session.user.app_metadata || {};
      const companyId = appMetadata.company_id;

      if (!companyId) {
        // 招待ではない場合（通常の新規登録）→ ダッシュボードへ
        router.push("/dashboard");
        return;
      }

      // /auth/setup を呼んで users レコード作成
      try {
        await apiFetch("/auth/setup", {
          method: "POST",
          token: session.access_token,
          body: {
            company_name: "", // 招待経由では使われない（既に company_id が設定済み）
            industry: "その他",
          },
        });
      } catch (e) {
        console.error("Setup failed:", e);
        // 失敗してもダッシュボードに進む（次回ログイン時にリトライ可能）
      }

      // セッションをリフレッシュして最新の metadata を反映
      await supabase.auth.refreshSession();

      router.push("/dashboard");
    }

    handleInviteCallback();
  }, [router]);

  if (status === "error") {
    return (
      <div className="flex min-h-screen items-center justify-center px-4">
        <Card className="w-full max-w-md">
          <CardHeader>
            <CardTitle>招待の処理に失敗しました</CardTitle>
            <CardDescription>{errorMessage}</CardDescription>
          </CardHeader>
          <CardContent>
            <a
              href="/login"
              className="text-sm text-primary underline underline-offset-4"
            >
              ログインページへ
            </a>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <Card className="w-full max-w-md">
        <CardContent className="flex flex-col items-center gap-3 py-8">
          <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
          <p className="text-sm text-muted-foreground">
            招待を処理しています...
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
