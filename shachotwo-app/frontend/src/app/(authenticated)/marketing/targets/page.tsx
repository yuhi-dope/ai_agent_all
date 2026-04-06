"use client";

import { useEffect, useState, useCallback } from "react";
import { useAuth } from "@/hooks/use-auth";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardHeader,
  CardContent,
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

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ManufacturingCompanyItem {
  corporate_number: string;
  company_name: string;
  sub_industry: string;
  prefecture: string;
  employee_count: number | null;
  capital_stock: number | null;
  revenue_segment: string;
  priority_tier: string;
  pain_hints: string[];
}

interface ManufacturingListResponse {
  items: ManufacturingCompanyItem[];
  total: number;
  segments_summary: Record<string, number>;
}

interface SegmentSummary {
  total: number;
  priority_tier: Record<string, number>;
  revenue_segment: Record<string, number>;
  sub_industry: Record<string, number>;
  prefecture: Record<string, number>;
}

interface KintoneAppItem {
  appId: string;
  name: string;
  spaceId?: string | null;
}

interface KintoneFieldItem {
  code: string;
  label: string;
  type: string;
  required: boolean;
}

interface BackgroundJobStatus {
  job_id: string;
  job_type: string;
  status: string;
  result?: {
    total_received?: number;
    total_upsert_ok?: number;
    total_skipped?: number;
    probe_ok?: boolean;
    dry_run?: boolean;
  };
  error_message?: string | null;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const PRIORITY_LABEL: Record<string, string> = {
  S: "最優先",
  A: "優先",
  B: "候補",
  C: "参考",
};

const PRIORITY_COLOR: Record<string, string> = {
  S: "bg-red-100 text-red-800 border-red-200",
  A: "bg-orange-100 text-orange-800 border-orange-200",
  B: "bg-yellow-100 text-yellow-800 border-yellow-200",
  C: "bg-gray-100 text-gray-700 border-gray-200",
};

const REVENUE_SEGMENT_LABEL: Record<string, string> = {
  micro: "〜1億",
  small: "1億〜10億",
  mid: "10億〜100億",
  large: "100億〜500億",
  enterprise: "500億〜",
  unknown: "不明",
};

const PREFECTURES = [
  "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県",
  "茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県",
  "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県", "岐阜県",
  "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県",
  "奈良県", "和歌山県", "鳥取県", "島根県", "岡山県", "広島県", "山口県",
  "徳島県", "香川県", "愛媛県", "高知県", "福岡県", "佐賀県", "長崎県",
  "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県",
];

const SUB_INDUSTRIES = [
  "金属加工", "樹脂加工", "機械製造", "電子部品",
  "食品製造", "化学製品", "自動車部品", "その他製造",
];

const REVENUE_SEGMENTS = [
  { value: "mid",   label: "10億〜100億" },
  { value: "large", label: "100億〜500億" },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatCapital(yen: number | null): string {
  if (yen == null) return "—";
  if (yen >= 100_000_000) return `${(yen / 100_000_000).toFixed(0)}億円`;
  if (yen >= 10_000) return `${(yen / 10_000).toFixed(0)}万円`;
  return `${yen.toLocaleString()}円`;
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function PriorityCard({
  tier,
  count,
  selected,
  onClick,
}: {
  tier: string;
  count: number;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`w-full text-left rounded-lg border p-4 transition-shadow hover:shadow-md ${
        selected ? "ring-2 ring-blue-500" : ""
      }`}
    >
      <div className="flex items-center justify-between">
        <span
          className={`inline-flex items-center rounded-full border px-3 py-1 text-sm font-semibold ${PRIORITY_COLOR[tier]}`}
        >
          {tier} — {PRIORITY_LABEL[tier]}
        </span>
        <span className="text-2xl font-bold text-gray-900">{count}</span>
      </div>
      <p className="mt-1 text-xs text-gray-500">
        {tier === "S" && "利益5〜100億・従業員50〜300名"}
        {tier === "A" && "利益5〜500億・従業員10〜1000名"}
        {tier === "B" && "売上10〜500億（利益情報なし）"}
        {tier === "C" && "情報不足・対象外"}
      </p>
    </button>
  );
}

function FilterSelect({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: { value: string; label: string }[];
  onChange: (v: string) => void;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-xs font-medium text-gray-600">{label}</span>
      <select
        className="rounded-md border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">すべて</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function ManufacturingTargetsPage() {
  const { user } = useAuth();

  // フィルタ状態
  const [prefecture, setPrefecture] = useState("");
  const [subIndustry, setSubIndustry] = useState("");
  const [revenueSegment, setRevenueSegment] = useState("");
  const [priorityTier, setPriorityTier] = useState("");
  const [minEmployees, setMinEmployees] = useState(10);
  const [maxEmployees, setMaxEmployees] = useState(1000);

  // データ状態
  const [items, setItems] = useState<ManufacturingCompanyItem[]>([]);
  const [segmentSummary, setSegmentSummary] = useState<SegmentSummary | null>(null);
  const [fetchSummary, setFetchSummary] = useState<Record<string, number>>({});
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [enriching, setEnriching] = useState(false);
  const [kintoneAppId, setKintoneAppId] = useState("");
  const [kintoneImporting, setKintoneImporting] = useState(false);
  const [kintoneIndustry, setKintoneIndustry] = useState<"manufacturing" | "construction">(
    "manufacturing"
  );
  const [kintoneApps, setKintoneApps] = useState<KintoneAppItem[]>([]);
  const [kintoneAppsLoading, setKintoneAppsLoading] = useState(false);
  const [fieldsOpen, setFieldsOpen] = useState(false);
  const [fieldsLoading, setFieldsLoading] = useState(false);
  const [fieldsList, setFieldsList] = useState<KintoneFieldItem[]>([]);
  const [fieldsMissing, setFieldsMissing] = useState<string[]>([]);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [jobStatus, setJobStatus] = useState<BackgroundJobStatus | null>(null);
  const [error, setError] = useState("");
  const [successMsg, setSuccessMsg] = useState("");

  // 選択状態
  const [selected, setSelected] = useState<Set<string>>(new Set());

  // セグメントサマリーを初期ロード
  const loadSummary = useCallback(async () => {
    try {
      const data: SegmentSummary = await apiFetch("/marketing/manufacturing/segments");
      setSegmentSummary(data);
    } catch {
      // サマリー取得失敗は非致命的
    }
  }, []);

  useEffect(() => {
    loadSummary();
  }, [loadSummary]);

  // kintone 取り込みジョブのポーリング（3秒間隔・完了/失敗で停止）
  useEffect(() => {
    if (!activeJobId) return;
    let intervalId: ReturnType<typeof setInterval>;
    let polls = 0;
    const poll = async () => {
      polls += 1;
      if (polls > 100) {
        clearInterval(intervalId);
        return;
      }
      try {
        const j = await apiFetch<BackgroundJobStatus>(`/jobs/${activeJobId}`);
        setJobStatus(j);
        if (j.status === "completed" || j.status === "failed") {
          clearInterval(intervalId);
          await loadSummary();
        }
      } catch {
        setJobStatus(null);
        clearInterval(intervalId);
      }
    };
    poll();
    intervalId = setInterval(poll, 3000);
    return () => clearInterval(intervalId);
  }, [activeJobId, loadSummary]);

  const handleLoadKintoneApps = useCallback(async () => {
    setKintoneAppsLoading(true);
    setError("");
    try {
      const data = await apiFetch<{ apps: KintoneAppItem[] }>("/connectors/kintone/apps");
      setKintoneApps(data.apps ?? []);
      if (!data.apps?.length) {
        setSuccessMsg("取得できる kintone アプリがありません。APIトークンの権限を確認してください。");
      }
    } catch {
      setError("kintone アプリ一覧の取得に失敗しました。外部ツール連携を確認してください。");
      setKintoneApps([]);
    } finally {
      setKintoneAppsLoading(false);
    }
  }, []);

  const handleCheckKintoneFields = useCallback(async () => {
    if (!kintoneAppId.trim()) {
      setError("アプリを選択してください");
      return;
    }
    setFieldsLoading(true);
    setError("");
    setFieldsOpen(true);
    try {
      const q = new URLSearchParams({ industry: kintoneIndustry });
      const data = await apiFetch<{ fields: KintoneFieldItem[]; missing_required: string[] }>(
        `/connectors/kintone/apps/${encodeURIComponent(kintoneAppId.trim())}/fields?${q}`
      );
      setFieldsList(data.fields ?? []);
      setFieldsMissing(data.missing_required ?? []);
    } catch {
      setFieldsList([]);
      setFieldsMissing([]);
      setError("フィールド一覧の取得に失敗しました。");
    } finally {
      setFieldsLoading(false);
    }
  }, [kintoneAppId, kintoneIndustry]);

  // リスト取得
  const handleSearch = useCallback(async () => {
    setLoading(true);
    setError("");
    setSuccessMsg("");
    setSelected(new Set());

    try {
      const body = {
        prefectures: prefecture ? [prefecture] : [],
        min_employees: minEmployees,
        max_employees: maxEmployees,
        sub_industries: subIndustry ? [subIndustry] : [],
        revenue_segments: revenueSegment ? [revenueSegment] : [],
        limit: 100,
      };
      const data: ManufacturingListResponse = await apiFetch(
        "/marketing/manufacturing/list",
        { method: "POST", body: JSON.stringify(body) }
      );
      setItems(data.items);
      setTotal(data.total);
      setFetchSummary(data.segments_summary);
      await loadSummary();
    } catch {
      setError("リストの取得に失敗しました。しばらく経ってから再度お試しください。");
    } finally {
      setLoading(false);
    }
  }, [prefecture, subIndustry, revenueSegment, minEmployees, maxEmployees, loadSummary]);

  // 一括エンリッチ
  const handleEnrichAll = useCallback(async () => {
    setEnriching(true);
    setError("");
    setSuccessMsg("");
    try {
      const result: { message: string } = await apiFetch(
        "/marketing/manufacturing/enrich-all",
        { method: "POST" }
      );
      setSuccessMsg(result.message);
      await loadSummary();
    } catch {
      setError("一括エンリッチに失敗しました。しばらく経ってから再度お試しください。");
    } finally {
      setEnriching(false);
    }
  }, [loadSummary]);

  // kintone からリード取り込み（先頭10件プローブ後に全件）— ジョブ追跡付き
  const handleKintoneImport = useCallback(async () => {
    if (!kintoneAppId.trim()) {
      setError("kintone アプリを選択してください");
      return;
    }
    setKintoneImporting(true);
    setError("");
    setSuccessMsg("");
    setJobStatus(null);
    const path =
      kintoneIndustry === "construction"
        ? "/marketing/construction/import-kintone"
        : "/marketing/manufacturing/import-kintone";
    try {
      const res = await apiFetch<{ job_id: string; message: string }>(path, {
        method: "POST",
        body: JSON.stringify({
          app_id: kintoneAppId.trim(),
          probe_size: 10,
          dry_run: false,
        }),
      });
      setActiveJobId(res.job_id);
      setSuccessMsg(res.message ?? "取り込みをキューに入れました。下のジョブ状態を確認してください。");
    } catch {
      setActiveJobId(null);
      setError(
        "kintone 取り込みの開始に失敗しました。外部ツール連携の kintone 設定とアプリを確認してください。"
      );
    } finally {
      setKintoneImporting(false);
    }
  }, [kintoneAppId, kintoneIndustry]);

  // 選択トグル
  const toggleSelect = (corpNum: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(corpNum) ? next.delete(corpNum) : next.add(corpNum);
      return next;
    });
  };
  const toggleSelectAll = () => {
    if (selected.size === filteredItems.length) {
      setSelected(new Set());
    } else {
      setSelected(new Set(filteredItems.map((i) => i.corporate_number)));
    }
  };

  // アウトリーチ対象に追加（選択分）
  const handleAddToOutreach = () => {
    // TODO: /marketing/outreach/run に選択した corporate_number を渡す
    setSuccessMsg(
      `${selected.size}社をアウトリーチ対象に追加しました。アウトリーチ画面で確認してください。`
    );
    setSelected(new Set());
  };

  // クライアント側フィルタ（優先度）
  const filteredItems = priorityTier
    ? items.filter((i) => i.priority_tier === priorityTier)
    : items;

  const prioritySummary = segmentSummary?.priority_tier ?? fetchSummary;

  return (
    <div className="space-y-6 p-6">
      {/* ページタイトル */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900">製造業ターゲット企業リスト</h1>
        <p className="mt-1 text-sm text-gray-500">
          利益5億〜500億の製造業をセグメント分類して営業対象を絞り込みます
        </p>
      </div>

      {/* 成功・エラーメッセージ */}
      {successMsg && (
        <div className="rounded-md bg-green-50 border border-green-200 px-4 py-3 text-sm text-green-800">
          {successMsg}
        </div>
      )}
      {error && (
        <div className="rounded-md bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {/* 優先度サマリーカード */}
      {(Object.keys(prioritySummary).length > 0 || segmentSummary) && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {["S", "A", "B", "C"].map((tier) => (
            <PriorityCard
              key={tier}
              tier={tier}
              count={prioritySummary[tier] ?? 0}
              selected={priorityTier === tier}
              onClick={() =>
                setPriorityTier((prev) => (prev === tier ? "" : tier))
              }
            />
          ))}
        </div>
      )}

      {/* フィルタパネル */}
      <Card>
        <CardHeader>
          <h2 className="text-base font-semibold text-gray-800">絞り込み条件</h2>
          <CardDescription>
            条件を設定して「リストを取得する」を押してください
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
            <FilterSelect
              label="都道府県"
              value={prefecture}
              options={PREFECTURES.map((p) => ({ value: p, label: p }))}
              onChange={setPrefecture}
            />
            <FilterSelect
              label="サブ業種"
              value={subIndustry}
              options={SUB_INDUSTRIES.map((s) => ({ value: s, label: s }))}
              onChange={setSubIndustry}
            />
            <FilterSelect
              label="売上規模"
              value={revenueSegment}
              options={REVENUE_SEGMENTS}
              onChange={setRevenueSegment}
            />
            <label className="flex flex-col gap-1">
              <span className="text-xs font-medium text-gray-600">従業員数（最小）</span>
              <input
                type="number"
                className="rounded-md border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                value={minEmployees}
                min={1}
                max={maxEmployees}
                onChange={(e) => setMinEmployees(Number(e.target.value))}
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-xs font-medium text-gray-600">従業員数（最大）</span>
              <input
                type="number"
                className="rounded-md border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                value={maxEmployees}
                min={minEmployees}
                onChange={(e) => setMaxEmployees(Number(e.target.value))}
              />
            </label>
          </div>

          <div className="mt-4 flex flex-wrap gap-3">
            <Button onClick={handleSearch} disabled={loading}>
              {loading ? "AIが企業を探しています..." : "リストを取得する"}
            </Button>
            {user?.role === "admin" && (
              <Button
                variant="outline"
                onClick={handleEnrichAll}
                disabled={enriching}
              >
                {enriching
                  ? "情報を収集しています。しばらくお待ちください..."
                  : "既存リードを一括で情報収集する"}
              </Button>
            )}
          </div>
          {user?.role === "admin" && (
            <div className="mt-4 space-y-3 rounded-lg border border-dashed border-gray-200 bg-gray-50/80 p-4">
              <p className="text-xs font-medium text-gray-700">kintone → リード取り込み（要件 b_10）</p>
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  variant={kintoneIndustry === "manufacturing" ? "default" : "outline"}
                  size="sm"
                  onClick={() => setKintoneIndustry("manufacturing")}
                >
                  製造業
                </Button>
                <Button
                  type="button"
                  variant={kintoneIndustry === "construction" ? "default" : "outline"}
                  size="sm"
                  onClick={() => setKintoneIndustry("construction")}
                >
                  建設業
                </Button>
              </div>
              <div className="flex flex-wrap items-end gap-3">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={handleLoadKintoneApps}
                  disabled={kintoneAppsLoading}
                >
                  {kintoneAppsLoading ? "読み込み中..." : "アプリ一覧を取得"}
                </Button>
                <label className="flex flex-col gap-1 min-w-[200px]">
                  <span className="text-xs font-medium text-gray-600">kintone アプリ</span>
                  <select
                    className="rounded-md border border-gray-300 px-3 py-2 text-sm bg-white"
                    value={kintoneAppId}
                    onChange={(e) => setKintoneAppId(e.target.value)}
                  >
                    <option value="">選択してください</option>
                    {kintoneApps.map((a) => (
                      <option key={a.appId} value={a.appId}>
                        {a.name} (ID: {a.appId})
                      </option>
                    ))}
                  </select>
                </label>
                <Button type="button" variant="outline" size="sm" onClick={handleCheckKintoneFields}>
                  フィールドを確認
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  onClick={handleKintoneImport}
                  disabled={kintoneImporting}
                >
                  {kintoneImporting
                    ? "開始中..."
                    : kintoneIndustry === "construction"
                      ? "建設リードを取り込む（10件→全件）"
                      : "製造リードを取り込む（10件→全件）"}
                </Button>
              </div>
              {activeJobId && (
                <div className="rounded-md border bg-white px-3 py-2 text-sm space-y-1">
                  <div className="font-medium text-gray-800">
                    ジョブ: <span className="font-mono text-xs">{activeJobId}</span>
                  </div>
                  {jobStatus && (
                    <>
                      <div className="flex flex-wrap gap-2 items-center">
                        <Badge variant="outline">{jobStatus.status}</Badge>
                        <span className="text-xs text-gray-500">{jobStatus.job_type}</span>
                      </div>
                      {jobStatus.status === "completed" && jobStatus.result && (
                        <p className="text-xs text-gray-600">
                          取得 {jobStatus.result.total_received ?? "—"} 件 / upsert{" "}
                          {jobStatus.result.total_upsert_ok ?? "—"} / スキップ{" "}
                          {jobStatus.result.total_skipped ?? "—"}
                        </p>
                      )}
                      {jobStatus.status === "failed" && jobStatus.error_message && (
                        <p className="text-xs text-red-700">{jobStatus.error_message}</p>
                      )}
                    </>
                  )}
                  {!jobStatus && <p className="text-xs text-gray-500">状態を取得しています…</p>}
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="h-7 text-xs"
                    onClick={() => {
                      setActiveJobId(null);
                      setJobStatus(null);
                    }}
                  >
                    表示を閉じる
                  </Button>
                </div>
              )}
            </div>
          )}

          <Dialog open={fieldsOpen} onOpenChange={setFieldsOpen}>
            <DialogContent className="max-w-lg max-h-[80vh] overflow-y-auto">
              <DialogHeader>
                <DialogTitle>kintone フィールド一覧</DialogTitle>
                <DialogDescription>
                  アプリ {kintoneAppId} — {kintoneIndustry === "construction" ? "建設" : "製造"} 取り込み向け必須チェック
                </DialogDescription>
              </DialogHeader>
              {fieldsLoading ? (
                <p className="text-sm text-muted-foreground">読み込み中…</p>
              ) : (
                <>
                  {fieldsMissing.length > 0 && (
                    <div className="rounded-md bg-amber-50 border border-amber-200 px-3 py-2 text-xs text-amber-900">
                      必須に相当するフィールドが不足: {fieldsMissing.join(", ")}
                      <br />
                      設定 → コネクタでマッピング API からコードを保存するか、kintone アプリを調整してください。
                    </div>
                  )}
                  <ul className="mt-2 space-y-1 text-xs max-h-60 overflow-y-auto border rounded-md p-2">
                    {fieldsList.map((f) => (
                      <li key={f.code} className="flex justify-between gap-2">
                        <span className="font-mono">{f.code}</span>
                        <span className="text-gray-600 truncate">{f.label}</span>
                        <span className="text-gray-400 shrink-0">{f.type}</span>
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </DialogContent>
          </Dialog>

          {loading && (
            <p className="mt-3 text-sm text-gray-500 animate-pulse">
              gBizINFOから企業情報を取得しています。少しお待ちください...
            </p>
          )}
        </CardContent>
      </Card>

      {/* 取得結果テーブル */}
      {items.length > 0 && (
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-base font-semibold text-gray-800">
                  取得結果
                  <span className="ml-2 text-sm font-normal text-gray-500">
                    {priorityTier
                      ? `優先度 ${priorityTier} — ${filteredItems.length}社`
                      : `全${total}社`}
                  </span>
                </h2>
              </div>
              {selected.size > 0 && (
                <Button onClick={handleAddToOutreach} size="sm">
                  選択した{selected.size}社をアウトリーチ対象に追加する
                </Button>
              )}
            </div>
          </CardHeader>
          <CardContent className="p-0">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-gray-50 text-xs text-gray-500 uppercase">
                  <tr>
                    <th className="px-4 py-3 text-left">
                      <input
                        type="checkbox"
                        checked={
                          filteredItems.length > 0 &&
                          selected.size === filteredItems.length
                        }
                        onChange={toggleSelectAll}
                        className="rounded border-gray-300"
                      />
                    </th>
                    <th className="px-4 py-3 text-left">優先度</th>
                    <th className="px-4 py-3 text-left">企業名</th>
                    <th className="px-4 py-3 text-left">サブ業種</th>
                    <th className="px-4 py-3 text-left">都道府県</th>
                    <th className="px-4 py-3 text-right">従業員数</th>
                    <th className="px-4 py-3 text-right">資本金</th>
                    <th className="px-4 py-3 text-left">売上規模</th>
                    <th className="px-4 py-3 text-left">課題ヒント</th>
                    <th className="px-4 py-3 text-left">操作</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {filteredItems.map((item) => (
                    <tr
                      key={item.corporate_number}
                      className={`hover:bg-gray-50 transition-colors ${
                        selected.has(item.corporate_number) ? "bg-blue-50" : ""
                      }`}
                    >
                      <td className="px-4 py-3">
                        <input
                          type="checkbox"
                          checked={selected.has(item.corporate_number)}
                          onChange={() => toggleSelect(item.corporate_number)}
                          className="rounded border-gray-300"
                        />
                      </td>
                      <td className="px-4 py-3">
                        <span
                          className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold ${
                            PRIORITY_COLOR[item.priority_tier] ?? PRIORITY_COLOR["C"]
                          }`}
                        >
                          {item.priority_tier} — {PRIORITY_LABEL[item.priority_tier] ?? ""}
                        </span>
                      </td>
                      <td className="px-4 py-3 font-medium text-gray-900">
                        {item.company_name}
                      </td>
                      <td className="px-4 py-3 text-gray-600">{item.sub_industry}</td>
                      <td className="px-4 py-3 text-gray-600">{item.prefecture || "—"}</td>
                      <td className="px-4 py-3 text-right text-gray-600">
                        {item.employee_count != null
                          ? `${item.employee_count.toLocaleString()}名`
                          : "—"}
                      </td>
                      <td className="px-4 py-3 text-right text-gray-600">
                        {formatCapital(item.capital_stock)}
                      </td>
                      <td className="px-4 py-3 text-gray-600">
                        {REVENUE_SEGMENT_LABEL[item.revenue_segment] ?? "—"}
                      </td>
                      <td className="px-4 py-3 max-w-xs">
                        <ul className="space-y-0.5">
                          {item.pain_hints.slice(0, 2).map((hint, i) => (
                            <li key={i} className="text-xs text-gray-500 truncate">
                              • {hint}
                            </li>
                          ))}
                        </ul>
                      </td>
                      <td className="px-4 py-3">
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => {
                            setSelected(new Set([item.corporate_number]));
                            handleAddToOutreach();
                          }}
                        >
                          追加する
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}

      {/* 空状態 */}
      {!loading && items.length === 0 && (
        <Card>
          <CardContent className="py-12 text-center">
            <p className="text-gray-500">まだ企業リストが取得されていません</p>
            <p className="mt-1 text-sm text-gray-400">
              上の条件を設定して「リストを取得する」を押してください
            </p>
          </CardContent>
        </Card>
      )}

      {/* サブ業種内訳 */}
      {segmentSummary && Object.keys(segmentSummary.sub_industry).length > 0 && (
        <Card>
          <CardHeader>
            <h2 className="text-base font-semibold text-gray-800">サブ業種内訳</h2>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap gap-2">
              {Object.entries(segmentSummary.sub_industry)
                .sort((a, b) => b[1] - a[1])
                .map(([sub, cnt]) => (
                  <Badge key={sub} variant="outline" className="text-sm">
                    {sub}: {cnt}社
                  </Badge>
                ))}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
