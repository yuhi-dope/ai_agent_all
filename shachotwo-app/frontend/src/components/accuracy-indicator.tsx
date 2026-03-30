"use client";

import { Progress } from "@/components/ui/progress";

// ---------- 型定義 ----------

export interface AccuracyIndicatorProps {
  confidence: number;        // 0.0 - 1.0
  dataCompleteness: number;  // 0.0 - 1.0
  pipelineName: string;
  showDetail?: boolean;
}

// ---------- 精度レベルの判定 ----------

type AccuracyLevel = "high" | "medium" | "low";

function getAccuracyLevel(confidence: number): AccuracyLevel {
  if (confidence >= 0.8) return "high";
  if (confidence >= 0.6) return "medium";
  return "low";
}

const LEVEL_CONFIG: Record<
  AccuracyLevel,
  { label: string; badgeClass: string; progressClass: string }
> = {
  high: {
    label: "高精度",
    badgeClass: "bg-green-100 text-green-800",
    progressClass: "bg-green-500",
  },
  medium: {
    label: "参考値",
    badgeClass: "bg-yellow-100 text-yellow-800",
    progressClass: "bg-yellow-500",
  },
  low: {
    label: "要確認",
    badgeClass: "bg-orange-100 text-orange-800",
    progressClass: "bg-orange-500",
  },
};

// ---------- データ充足度のアドバイス ----------

function DataAdvice({ dataCompleteness }: { dataCompleteness: number }) {
  if (dataCompleteness >= 0.8) {
    return (
      <p className="text-xs text-green-700">
        十分なデータがあります
      </p>
    );
  }
  if (dataCompleteness >= 0.5) {
    // 精度80%到達に必要な残件数（概算: completeness 0.8相当 - 現在値 を件数換算）
    const remaining = Math.ceil((0.8 - dataCompleteness) * 50);
    return (
      <p className="text-xs text-yellow-700">
        あと約{remaining}件のデータで精度80%に到達します
      </p>
    );
  }
  return (
    <p className="text-xs text-orange-700">
      CSVでデータを追加してください
    </p>
  );
}

// ---------- 精度バッジ（インライン表示用） ----------

export function AccuracyBadge({
  confidence,
  className = "",
}: {
  confidence: number;
  className?: string;
}) {
  const level = getAccuracyLevel(confidence);
  const config = LEVEL_CONFIG[level];

  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium transition-colors duration-300 ${config.badgeClass} ${className}`}
      title={level === "low" ? "データを追加すると精度が向上します" : undefined}
    >
      {config.label}
    </span>
  );
}

// ---------- メインコンポーネント ----------

export function AccuracyIndicator({
  confidence,
  dataCompleteness,
  pipelineName,
  showDetail = false,
}: AccuracyIndicatorProps) {
  const level = getAccuracyLevel(confidence);
  const config = LEVEL_CONFIG[level];

  // バッジのみ（showDetail=false）
  if (!showDetail) {
    return (
      <AccuracyBadge confidence={confidence} />
    );
  }

  // 詳細パネル（showDetail=true）
  return (
    <div className="rounded-lg border bg-background p-4 space-y-4">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm font-medium">{pipelineName}の精度</p>
        <AccuracyBadge confidence={confidence} />
      </div>

      {/* 精度プログレスバー */}
      <div className="space-y-1">
        <div className="flex justify-between text-xs text-muted-foreground">
          <span>AIの精度</span>
          <span>
            {level === "high"
              ? "高精度"
              : level === "medium"
              ? "参考値（データ追加で向上）"
              : "要確認（データが不足しています）"}
          </span>
        </div>
        <div className="relative h-2 w-full overflow-hidden rounded-full bg-muted">
          <div
            className={`h-full transition-all duration-500 ease-in-out ${config.progressClass}`}
            style={{ width: `${Math.round(confidence * 100)}%` }}
            role="progressbar"
            aria-valuenow={Math.round(confidence * 100)}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label="AIの精度"
          />
        </div>
      </div>

      {/* データ充足度プログレスバー */}
      <div className="space-y-1">
        <div className="flex justify-between text-xs text-muted-foreground">
          <span>データの充足度</span>
          <span>{Math.round(dataCompleteness * 100)}%</span>
        </div>
        <Progress
          value={Math.round(dataCompleteness * 100)}
          className="h-2"
        />
      </div>

      {/* 精度を上げるには */}
      <div className="rounded-md bg-muted/50 px-3 py-2 space-y-1">
        <p className="text-xs font-medium text-muted-foreground">精度を上げるには</p>
        <DataAdvice dataCompleteness={dataCompleteness} />
      </div>
    </div>
  );
}
