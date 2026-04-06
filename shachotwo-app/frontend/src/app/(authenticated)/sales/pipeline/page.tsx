"use client";

import { useEffect, useState, useCallback } from "react";
import { useAuth } from "@/hooks/use-auth";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

// ---------------------------------------------------------------------------
// 型定義
// ---------------------------------------------------------------------------

interface Opportunity {
  id: string;
  title: string;
  target_company_name: string;
  target_industry: string | null;
  selected_modules: string[];
  monthly_amount: number;
  annual_amount: number;
  stage: "proposal" | "quotation" | "negotiation" | "contract" | "won" | "lost";
  probability: number;
  expected_close_date: string | null;
  lost_reason: string | null;
  stage_changed_at: string;
  created_at: string;
  updated_at: string;
}

// ---------------------------------------------------------------------------
// 定数
// ---------------------------------------------------------------------------

const PIPELINE_STAGES: {
  key: Opportunity["stage"];
  label: string;
  color: string;
}[] = [
  { key: "proposal", label: "提案中", color: "bg-blue-50 border-blue-200" },
  { key: "quotation", label: "見積提示", color: "bg-yellow-50 border-yellow-200" },
  { key: "negotiation", label: "交渉中", color: "bg-orange-50 border-orange-200" },
  { key: "contract", label: "契約準備", color: "bg-purple-50 border-purple-200" },
  { key: "won", label: "受注", color: "bg-green-50 border-green-200" },
];

const MODULE_LABELS: Record<string, string> = {
  brain: "ブレイン ¥30,000",
  bpo_core: "業務自動化コア ¥250,000",
  bpo_additional: "追加モジュール ¥100,000",
  backoffice: "バックオフィス ¥200,000",
};

function formatAmount(yen: number): string {
  if (yen >= 10000) {
    return `¥${Math.round(yen / 10000)}万`;
  }
  return `¥${yen.toLocaleString()}`;
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString("ja-JP", {
    month: "short",
    day: "numeric",
  });
}

// ---------------------------------------------------------------------------
// スコア（確度）バッジ
// ---------------------------------------------------------------------------

function ProbabilityBadge({ probability }: { probability: number }) {
  if (probability >= 70) {
    return (
      <Badge className="bg-green-100 text-green-800 text-[11px]">
        確度 {probability}%
      </Badge>
    );
  }
  if (probability >= 40) {
    return (
      <Badge className="bg-yellow-100 text-yellow-800 text-[11px]">
        確度 {probability}%
      </Badge>
    );
  }
  return (
    <Badge className="bg-muted text-muted-foreground text-[11px]">
      確度 {probability}%
    </Badge>
  );
}

// ---------------------------------------------------------------------------
// スケルトン
// ---------------------------------------------------------------------------

function PipelineSkeleton() {
  return (
    <div className="flex gap-3 overflow-x-auto pb-4">
      {PIPELINE_STAGES.map((col) => (
        <div
          key={col.key}
          className="min-w-[200px] flex-shrink-0 rounded-lg border p-3 space-y-2"
        >
          <div className="h-5 w-20 animate-pulse rounded bg-muted" />
          {[1, 2].map((i) => (
            <div key={i} className="rounded-md border bg-card p-3 space-y-2">
              <div className="h-4 w-28 animate-pulse rounded bg-muted" />
              <div className="h-3 w-20 animate-pulse rounded bg-muted" />
              <div className="h-3 w-16 animate-pulse rounded-full bg-muted" />
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 商談カード
// ---------------------------------------------------------------------------

function OpportunityCard({
  opp,
  onMoveStage,
}: {
  opp: Opportunity;
  onMoveStage: (opp: Opportunity) => void;
}) {
  return (
    <Card className="cursor-pointer hover:shadow-md transition-shadow">
      <CardContent className="p-3 space-y-1.5">
        <p className="text-sm font-medium leading-snug">{opp.target_company_name}</p>
        <p className="text-xs text-muted-foreground leading-snug">{opp.title}</p>
        <p className="text-sm font-semibold text-primary">
          {formatAmount(opp.monthly_amount)}/月
        </p>
        <div className="flex flex-wrap gap-1">
          <ProbabilityBadge probability={opp.probability} />
        </div>
        {opp.expected_close_date && (
          <p className="text-xs text-muted-foreground">
            受注予定: {formatDate(opp.expected_close_date)}
          </p>
        )}
        {opp.stage !== "won" && (
          <Button
            size="sm"
            variant="outline"
            className="w-full mt-1 h-7 text-xs"
            onClick={(e) => {
              e.stopPropagation();
              onMoveStage(opp);
            }}
          >
            次のステージへ進める
          </Button>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// ステージ移動ダイアログ
// ---------------------------------------------------------------------------

const NEXT_STAGE: Partial<Record<Opportunity["stage"], Opportunity["stage"]>> =
  {
    proposal: "quotation",
    quotation: "negotiation",
    negotiation: "contract",
    contract: "won",
  };

const STAGE_LABELS: Record<string, string> = {
  proposal: "提案中",
  quotation: "見積提示",
  negotiation: "交渉中",
  contract: "契約準備",
  won: "受注",
  lost: "失注",
};

// ---------------------------------------------------------------------------
// ページ
// ---------------------------------------------------------------------------

export default function PipelinePage() {
  const { session } = useAuth();
  const [opportunities, setOpportunities] = useState<Opportunity[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [movingOpp, setMovingOpp] = useState<Opportunity | null>(null);
  const [moving, setMoving] = useState(false);

  const fetchOpportunities = useCallback(async () => {
    if (!session?.access_token) return;
    setLoading(true);
    setError(null);
    try {
      const res = await apiFetch<{ items: Opportunity[]; total: number }>(
        "/sales/opportunities",
        {
          token: session.access_token,
          params: { limit: "100", exclude_lost: "true" },
        }
      );
      setOpportunities(res.items);
    } catch {
      setError(
        "商談情報の取得に失敗しました。しばらく経ってから再度お試しください。"
      );
    } finally {
      setLoading(false);
    }
  }, [session?.access_token]);

  useEffect(() => {
    fetchOpportunities();
  }, [fetchOpportunities]);

  async function handleMoveStage() {
    if (!movingOpp || !session?.access_token) return;
    const nextStage = NEXT_STAGE[movingOpp.stage];
    if (!nextStage) return;
    setMoving(true);
    try {
      await apiFetch(`/sales/opportunities/${movingOpp.id}`, {
        token: session.access_token,
        method: "PATCH",
        body: { stage: nextStage },
      });
      setOpportunities((prev) =>
        prev.map((o) =>
          o.id === movingOpp.id ? { ...o, stage: nextStage } : o
        )
      );
      setMovingOpp(null);
    } catch {
      setError(
        "ステージの更新に失敗しました。しばらく経ってから再度お試しください。"
      );
    } finally {
      setMoving(false);
    }
  }

  // 合計パイプライン金額
  const totalPipeline = opportunities
    .filter((o) => o.stage !== "won")
    .reduce((sum, o) => sum + o.monthly_amount * (o.probability / 100), 0);

  const wonThisMonth = opportunities
    .filter((o) => o.stage === "won")
    .reduce((sum, o) => sum + o.monthly_amount, 0);

  return (
    <div className="mx-auto max-w-7xl space-y-6">
      {/* ヘッダー */}
      <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold">商談パイプライン</h1>
          <p className="text-sm text-muted-foreground">
            進行中の商談をステージ別に管理します。確度をもとに加重予測を計算します。
          </p>
        </div>
        <div className="flex flex-col items-end gap-0.5">
          <p className="text-xs text-muted-foreground">加重予測合計（月額）</p>
          <p className="text-xl font-bold text-primary">
            {formatAmount(Math.round(totalPipeline))}/月
          </p>
        </div>
      </div>

      {/* サマリーカード */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {PIPELINE_STAGES.map((col) => {
          const items = opportunities.filter((o) => o.stage === col.key);
          const total = items.reduce((s, o) => s + o.monthly_amount, 0);
          return (
            <div
              key={col.key}
              className={`rounded-lg border p-3 ${col.color}`}
            >
              <p className="text-xs text-muted-foreground">{col.label}</p>
              <p className="text-lg font-bold">{items.length}件</p>
              <p className="text-xs text-muted-foreground">
                {formatAmount(total)}/月
              </p>
            </div>
          );
        })}
      </div>

      {/* エラー */}
      {error && (
        <div className="rounded-md border border-destructive/50 bg-destructive/5 p-3">
          <p className="text-sm text-destructive">{error}</p>
        </div>
      )}

      {/* カンバンボード（横スクロール） */}
      {loading ? (
        <PipelineSkeleton />
      ) : opportunities.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center gap-4 py-12 text-center">
            <p className="text-sm text-muted-foreground">
              まだ商談が登録されていません。
            </p>
            <p className="text-xs text-muted-foreground">
              リード管理でスコアの高いリードから提案書を生成すると、商談が自動登録されます。
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="overflow-x-auto pb-4">
          <div className="flex gap-3 min-w-max">
            {PIPELINE_STAGES.map((col) => {
              const colOpps = opportunities.filter(
                (o) => o.stage === col.key
              );
              return (
                <div
                  key={col.key}
                  className={`w-52 flex-shrink-0 rounded-lg border p-3 space-y-2 ${col.color}`}
                >
                  <div className="flex items-center justify-between">
                    <h2 className="text-sm font-semibold">{col.label}</h2>
                    <Badge variant="secondary" className="text-[11px]">
                      {colOpps.length}
                    </Badge>
                  </div>
                  {colOpps.length === 0 ? (
                    <p className="text-xs text-muted-foreground py-4 text-center">
                      該当なし
                    </p>
                  ) : (
                    <div className="space-y-2">
                      {colOpps.map((opp) => (
                        <OpportunityCard
                          key={opp.id}
                          opp={opp}
                          onMoveStage={setMovingOpp}
                        />
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* ステージ移動確認ダイアログ */}
      <Dialog
        open={movingOpp !== null}
        onOpenChange={(open) => {
          if (!open && !moving) setMovingOpp(null);
        }}
      >
        <DialogContent className="sm:max-w-md w-full mx-2">
          <DialogHeader>
            <DialogTitle>ステージを進めますか？</DialogTitle>
            <DialogDescription>
              {movingOpp && (
                <>
                  「{movingOpp.target_company_name}」の商談を
                  <strong>
                    {STAGE_LABELS[movingOpp.stage]}
                  </strong>
                  から
                  <strong>
                    {STAGE_LABELS[NEXT_STAGE[movingOpp.stage] ?? ""] ?? ""}
                  </strong>
                  へ進めます。
                </>
              )}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="flex gap-2 sm:gap-0">
            <Button
              variant="outline"
              onClick={() => setMovingOpp(null)}
              disabled={moving}
            >
              キャンセル
            </Button>
            <Button onClick={handleMoveStage} disabled={moving}>
              {moving ? (
                <>
                  <span className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
                  更新中...
                </>
              ) : (
                "ステージを進める"
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
