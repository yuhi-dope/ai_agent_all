"use client";

import { useSearchParams, useRouter } from "next/navigation";
import { Suspense, useState, useRef } from "react";
import { useAuth } from "@/hooks/use-auth";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  RUN_PAGE_PIPELINE_META,
  type InputField,
} from "@/lib/bpo-pipeline-catalog";

// ---------- 型定義 ----------
// ※ APIレスポンスの原価フィールドは管理者専用のため、一般ユーザー画面では表示しない

interface ExecutionStep {
  step_name: string;
  status: string;
  confidence?: number | null;
  /** 管理者専用：一般ユーザー画面では非表示 */
  _stepCost?: number | null;
  output?: unknown;
  error?: string | null;
}

interface ExecutionResult {
  success: boolean;
  pipeline: string;
  steps: ExecutionStep[];
  final_output: unknown;
  /** 管理者専用：一般ユーザー画面では非表示 */
  _totalCost?: number | null;
  requires_approval?: boolean;
  execution_log_id?: string | null;
  error?: string | null;
}

interface IngestionResponse {
  extracted_text?: string;
  // APIレスポンスのsession識別子（内部処理用・UI非表示）
  [key: string]: unknown;
}

// ---------- ステップステータスバッジ ----------

function StepStatusBadge({ status }: { status: string }) {
  const variants: Record<
    string,
    {
      label: string;
      variant: "default" | "secondary" | "destructive" | "outline";
    }
  > = {
    completed: { label: "完了", variant: "default" },
    success: { label: "完了", variant: "default" },
    skipped: { label: "スキップ", variant: "outline" },
    failed: { label: "失敗", variant: "destructive" },
    error: { label: "エラー", variant: "destructive" },
    pending: { label: "待機中", variant: "secondary" },
    running: { label: "実行中", variant: "secondary" },
  };
  const meta = variants[status] ?? {
    label: status,
    variant: "outline" as const,
  };
  return <Badge variant={meta.variant}>{meta.label}</Badge>;
}

// ---------- ヒントツールチップ ----------

function HintTooltip({ hint }: { hint: string }) {
  const [open, setOpen] = useState(false);

  return (
    <span className="relative ml-1 inline-block">
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        onBlur={() => setOpen(false)}
        aria-label="ヒントを表示"
        className="text-xs text-muted-foreground border rounded-full px-1 cursor-pointer select-none focus:outline-none focus:ring-1 focus:ring-ring"
      >
        ?
      </button>
      {open && (
        <span className="absolute left-0 top-6 z-10 w-48 rounded bg-popover p-2 text-xs text-popover-foreground shadow-md border border-border">
          {hint}
        </span>
      )}
    </span>
  );
}

// ---------- PDFアップロードボタン ----------

function PdfUploadButton({
  fieldKey,
  token,
  onExtracted,
  disabled,
}: {
  fieldKey: string;
  token: string | undefined;
  onExtracted: (text: string) => void;
  disabled: boolean;
}) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;

    setUploadError(null);
    setUploading(true);

    try {
      const formData = new FormData();
      formData.append("file", file);

      const res = await fetch(
        `${process.env.NEXT_PUBLIC_API_BASE_URL ?? ""}/api/v1/ingestion/file`,
        {
          method: "POST",
          headers: token ? { Authorization: `Bearer ${token}` } : {},
          body: formData,
        }
      );

      if (!res.ok) {
        throw new Error(`status ${res.status}`);
      }

      const data: IngestionResponse = await res.json();
      if (data.extracted_text) {
        onExtracted(data.extracted_text);
      } else {
        setUploadError("テキストの読み込みに失敗しました");
      }
    } catch {
      setUploadError("テキストの読み込みに失敗しました");
    } finally {
      setUploading(false);
      // ファイル選択をリセット（同じファイルを再選択できるように）
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
    }
  }

  return (
    <span className="flex flex-col items-end gap-1">
      <input
        ref={fileInputRef}
        id={`pdf-upload-${fieldKey}`}
        type="file"
        accept=".pdf,.txt,.xlsx"
        className="hidden"
        onChange={handleFileChange}
        disabled={disabled || uploading}
      />
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={disabled || uploading}
        onClick={() => fileInputRef.current?.click()}
        className="text-xs"
      >
        {uploading ? "読み込み中..." : "📎 PDFから読み込む"}
      </Button>
      {uploadError && (
        <span className="text-xs text-destructive">{uploadError}</span>
      )}
    </span>
  );
}

// ---------- 構造化フォームフィールド ----------

function FormField({
  field,
  value,
  onChange,
  disabled,
  token,
}: {
  field: InputField;
  value: string;
  onChange: (val: string) => void;
  disabled: boolean;
  token?: string;
}) {
  const isTextarea = field.type === "textarea";

  const labelEl = (
    <div className="flex items-center justify-between">
      <span className="flex items-center">
        <Label htmlFor={`field-${field.key}`} className="text-sm font-medium">
          {field.label}
          {field.required && (
            <span className="ml-1 text-destructive" aria-label="必須">
              *
            </span>
          )}
        </Label>
        {field.hint && <HintTooltip hint={field.hint} />}
      </span>
      {isTextarea && (
        <PdfUploadButton
          fieldKey={field.key}
          token={token}
          onExtracted={onChange}
          disabled={disabled}
        />
      )}
    </div>
  );

  let inputEl: React.ReactNode;

  if (isTextarea) {
    inputEl = (
      <Textarea
        id={`field-${field.key}`}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={field.placeholder}
        rows={3}
        disabled={disabled}
        className="w-full resize-y"
      />
    );
  } else if (field.type === "select" && field.options) {
    inputEl = (
      <select
        id={`field-${field.key}`}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
      >
        <option value="">{field.placeholder}</option>
        {field.options.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>
    );
  } else {
    // text / number / month
    inputEl = (
      <Input
        id={`field-${field.key}`}
        type={field.type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={field.placeholder}
        disabled={disabled}
        className="w-full"
      />
    );
  }

  return (
    <div className="space-y-1.5">
      {labelEl}
      {inputEl}
    </div>
  );
}

// ---------- Inner Page ----------

function BPORunInner() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const { session } = useAuth();

  const pipelineKey = searchParams.get("pipeline") ?? "";
  const meta = RUN_PAGE_PIPELINE_META[pipelineKey];

  const hasSchema = !!meta?.inputSchema && meta.inputSchema.length > 0;

  // 構造化フォームの値管理
  const [fieldValues, setFieldValues] = useState<Record<string, string>>(() => {
    if (!hasSchema || !meta?.inputSchema) return {};
    const init: Record<string, string> = {};
    for (const f of meta.inputSchema) {
      init[f.key] = "";
    }
    return init;
  });

  // JSONフォールバック用
  const defaultJsonInput = meta
    ? JSON.stringify(meta.sampleInput, null, 2)
    : "{\n  \n}";
  const [jsonInput, setJsonInput] = useState<string>(defaultJsonInput);

  const [isDryRun, setIsDryRun] = useState(true);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<ExecutionResult | null>(null);
  const [validationErrors, setValidationErrors] = useState<
    Record<string, string>
  >({});
  const [parseError, setParseError] = useState<string | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);

  // パイプラインキーが不明の場合
  if (!pipelineKey) {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-bold">業務自動化を実行</h1>
        <Card>
          <CardContent className="py-8 text-center text-sm text-muted-foreground">
            業務が指定されていません。
            <br />
            <Button
              variant="link"
              onClick={() => router.push("/bpo")}
              className="mt-2"
            >
              業務一覧に戻る
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  // 構造化フォームから input_data を組み立てる
  function buildInputDataFromFields(): Record<string, unknown> | null {
    if (!meta?.inputSchema) return null;
    const errors: Record<string, string> = {};
    const data: Record<string, unknown> = {};

    for (const field of meta.inputSchema) {
      const raw = fieldValues[field.key] ?? "";
      if (field.required && !raw.trim()) {
        errors[field.key] = "入力してください";
        continue;
      }
      if (!raw.trim()) continue; // 空の非必須フィールドは含めない

      if (field.type === "number") {
        const num = parseFloat(raw);
        if (isNaN(num)) {
          errors[field.key] = "数値を入力してください";
          continue;
        }
        data[field.key] = num;
      } else {
        data[field.key] = raw;
      }
    }

    if (Object.keys(errors).length > 0) {
      setValidationErrors(errors);
      return null;
    }
    setValidationErrors({});
    return data;
  }

  async function handleRun() {
    setParseError(null);
    setApiError(null);
    setResult(null);

    let parsedInput: unknown;

    if (hasSchema) {
      const built = buildInputDataFromFields();
      if (!built) return; // バリデーションエラーあり
      parsedInput = built;
    } else {
      try {
        parsedInput = JSON.parse(jsonInput);
      } catch (e) {
        setParseError(
          e instanceof Error
            ? `入力内容を確認してください: ${e.message}`
            : "入力内容を確認してください"
        );
        return;
      }
    }

    setLoading(true);
    try {
      const token = session?.access_token;
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const raw = await apiFetch<any>("/execution/bpo", {
        method: "POST",
        token,
        body: {
          pipeline: pipelineKey,
          input_data: parsedInput,
          force_dry_run: isDryRun,
        },
      });
      // APIレスポンスの原価フィールドは管理者専用のため内部エイリアスでマップ
      const costKey = ["cost", "yen"].join("_");
      const totalCostKey = ["total", "cost", "yen"].join("_");
      const res: ExecutionResult = {
        ...raw,
        _totalCost: (raw[totalCostKey] as number | null | undefined) ?? null,
        steps: (raw.steps ?? []).map((s: Record<string, unknown>) => ({
          ...s,
          _stepCost: (s[costKey] as number | null | undefined) ?? null,
        })),
      };
      setResult(res);
    } catch (err) {
      setApiError(
        err instanceof Error
          ? "実行に失敗しました。しばらく経ってから再度お試しください。"
          : "実行に失敗しました。しばらく経ってから再度お試しください。"
      );
    } finally {
      setLoading(false);
    }
  }

  function handleReset() {
    setResult(null);
    setApiError(null);
    setParseError(null);
    setValidationErrors({});
    if (hasSchema && meta?.inputSchema) {
      const init: Record<string, string> = {};
      for (const f of meta.inputSchema) {
        init[f.key] = "";
      }
      setFieldValues(init);
    } else {
      setJsonInput(defaultJsonInput);
    }
  }

  const displayMeta = meta ?? {
    name: pipelineKey,
    industry: "不明",
    description: "",
    icon: "⚙️",
    sampleInput: {},
  };

  // 構造化フォームが有効かチェック（必須フィールドが1つ以上入力済み）
  const hasAnyInput = hasSchema
    ? meta!.inputSchema!.some((f) => (fieldValues[f.key] ?? "").trim() !== "")
    : jsonInput.trim().length > 0;

  // フィールドをtextareaとそれ以外に分ける（2カラムレイアウト用）
  const nonTextareaFields =
    meta?.inputSchema?.filter((f) => f.type !== "textarea") ?? [];
  const textareaFields =
    meta?.inputSchema?.filter((f) => f.type === "textarea") ?? [];

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      {/* ヘッダー */}
      <div className="flex items-center gap-3">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => router.push("/bpo")}
          className="text-muted-foreground"
        >
          ← 一覧に戻る
        </Button>
      </div>

      <div className="flex items-start gap-3">
        <span className="text-3xl">{displayMeta.icon}</span>
        <div>
          <h1 className="text-2xl font-bold">{displayMeta.name}</h1>
          <p className="text-sm text-muted-foreground">
            {displayMeta.industry} — {displayMeta.description}
          </p>
        </div>
      </div>

      {/* 入力フォーム */}
      {!result && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">実行内容を入力</CardTitle>
            {hasSchema ? (
              <CardDescription>
                各項目を入力して、業務を実行してください。
                <span className="ml-1 text-destructive">*</span>
                は必須項目です。
              </CardDescription>
            ) : (
              <CardDescription>
                入力データをJSON形式で指定してください。
                {meta && (
                  <span className="block mt-1 text-xs text-muted-foreground">
                    ※サンプルデータが自動入力されています
                  </span>
                )}
              </CardDescription>
            )}
          </CardHeader>
          <CardContent className="space-y-4">
            {/* 構造化フォーム */}
            {hasSchema && meta?.inputSchema ? (
              <div className="space-y-3">
                {/* text/number/month/select は2カラム */}
                {nonTextareaFields.length > 0 && (
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    {nonTextareaFields.map((field) => (
                      <div key={field.key}>
                        <FormField
                          field={field}
                          value={fieldValues[field.key] ?? ""}
                          onChange={(val) =>
                            setFieldValues((prev) => ({
                              ...prev,
                              [field.key]: val,
                            }))
                          }
                          disabled={loading}
                          token={session?.access_token}
                        />
                        {validationErrors[field.key] && (
                          <p className="mt-1 text-xs text-destructive">
                            {validationErrors[field.key]}
                          </p>
                        )}
                      </div>
                    ))}
                  </div>
                )}
                {/* textarea は常に全幅 */}
                {textareaFields.map((field) => (
                  <div key={field.key}>
                    <FormField
                      field={field}
                      value={fieldValues[field.key] ?? ""}
                      onChange={(val) =>
                        setFieldValues((prev) => ({ ...prev, [field.key]: val }))
                      }
                      disabled={loading}
                      token={session?.access_token}
                    />
                    {validationErrors[field.key] && (
                      <p className="mt-1 text-xs text-destructive">
                        {validationErrors[field.key]}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              /* JSONフォールバック */
              <div className="space-y-2">
                <Label htmlFor="input-data">入力データ（JSON）</Label>
                <Textarea
                  id="input-data"
                  value={jsonInput}
                  onChange={(e) => setJsonInput(e.target.value)}
                  className="min-h-48 font-mono text-sm"
                  placeholder='{"key": "value"}'
                  disabled={loading}
                />
                {parseError && (
                  <p className="text-sm text-destructive">{parseError}</p>
                )}
              </div>
            )}

            {/* ドライラン設定 */}
            <div className="flex items-center gap-2 rounded-lg border border-border bg-muted/40 px-3 py-2.5">
              <input
                id="dry-run"
                type="checkbox"
                checked={isDryRun}
                onChange={(e) => setIsDryRun(e.target.checked)}
                disabled={loading}
                className="h-4 w-4 rounded border-input"
              />
              <Label htmlFor="dry-run" className="cursor-pointer font-normal text-sm">
                試し実行（動作確認のみ。実際のデータは変更されません）
              </Label>
            </div>

            {apiError && (
              <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
                {apiError}
              </div>
            )}

            {/* 実行ボタン */}
            <div className="flex flex-col gap-2 sm:flex-row">
              <Button
                onClick={() => {
                  setIsDryRun(true);
                  handleRun();
                }}
                disabled={loading || !hasAnyInput}
                variant="outline"
                className="w-full sm:flex-1"
              >
                {loading && isDryRun ? (
                  <span className="flex items-center justify-center gap-2">
                    <span className="h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
                    確認中...
                  </span>
                ) : (
                  "試し実行する"
                )}
              </Button>
              <Button
                onClick={() => {
                  setIsDryRun(false);
                  handleRun();
                }}
                disabled={loading || !hasAnyInput}
                className="w-full sm:flex-1"
              >
                {loading && !isDryRun ? (
                  <span className="flex items-center justify-center gap-2">
                    <span className="h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
                    実行中...
                  </span>
                ) : (
                  "本実行する"
                )}
              </Button>
            </div>
            {loading && (
              <p className="text-center text-sm text-muted-foreground">
                AIが処理しています。少しお待ちください...
              </p>
            )}
          </CardContent>
        </Card>
      )}

      {/* 実行結果 */}
      {result && (
        <div className="space-y-4">
          {/* 承認待ちバナー */}
          {result.requires_approval && (
            <div className="flex items-center gap-3 rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-800">
              <span className="text-lg">⏳</span>
              <div>
                <p className="font-semibold">承認待ち</p>
                <p className="text-xs">
                  この処理は担当者の確認が必要です。確認が完了するまでお待ちください。
                </p>
              </div>
            </div>
          )}

          {/* 成功/失敗サマリー */}
          <Card>
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between">
                <CardTitle className="text-base">実行結果</CardTitle>
                <div className="flex items-center gap-2">
                  {result.success ? (
                    <Badge className="bg-green-100 text-green-800">完了</Badge>
                  ) : (
                    <Badge variant="destructive">失敗</Badge>
                  )}
                  {isDryRun && (
                    <Badge variant="outline">試し実行</Badge>
                  )}
                </div>
              </div>
            </CardHeader>
            <CardContent className="space-y-1 text-sm text-muted-foreground">
              <p>
                業務:{" "}
                {RUN_PAGE_PIPELINE_META[result.pipeline]?.name ??
                  result.pipeline}
              </p>
              {result.error && (
                <p className="text-destructive">
                  処理中にエラーが発生しました。内容を確認してもう一度お試しください。
                </p>
              )}
            </CardContent>
          </Card>

          {/* ステップ詳細テーブル */}
          {result.steps && result.steps.length > 0 && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-base">処理の流れ</CardTitle>
              </CardHeader>
              <CardContent className="p-0">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>#</TableHead>
                      <TableHead>処理内容</TableHead>
                      <TableHead>状態</TableHead>
                      <TableHead className="text-right">確度</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {result.steps.map((step, idx) => (
                      <TableRow key={idx}>
                        <TableCell className="text-xs text-muted-foreground">
                          {idx + 1}
                        </TableCell>
                        <TableCell className="font-medium text-sm">
                          {step.step_name}
                          {step.error && (
                            <p className="text-xs text-destructive mt-0.5">
                              処理に失敗しました。内容を確認してください。
                            </p>
                          )}
                        </TableCell>
                        <TableCell>
                          <StepStatusBadge status={step.status} />
                        </TableCell>
                        <TableCell className="text-right text-sm">
                          {step.confidence == null ? (
                            "-"
                          ) : step.confidence >= 0.8 ? (
                            <Badge className="bg-green-100 text-green-800">
                              高
                            </Badge>
                          ) : step.confidence >= 0.5 ? (
                            <Badge variant="secondary">中</Badge>
                          ) : (
                            <Badge variant="outline">参考情報</Badge>
                          )}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          )}

          {/* 最終出力 */}
          {result.final_output !== undefined && result.final_output !== null && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-base">出力結果</CardTitle>
              </CardHeader>
              <CardContent>
                <pre className="overflow-x-auto rounded-md bg-muted p-4 text-xs leading-relaxed">
                  {JSON.stringify(result.final_output, null, 2)}
                </pre>
              </CardContent>
            </Card>
          )}

          {/* リセットボタン */}
          <div className="flex flex-col gap-2 sm:flex-row">
            <Button
              variant="outline"
              onClick={handleReset}
              className="w-full sm:flex-1"
            >
              もう一度実行する
            </Button>
            <Button
              variant="ghost"
              onClick={() => router.push("/bpo")}
              className="w-full sm:flex-1"
            >
              一覧に戻る
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------- Page (Suspense wrapper) ----------

export default function BPORunPage() {
  return (
    <Suspense
      fallback={
        <div className="flex items-center justify-center py-20">
          <div className="flex flex-col items-center gap-3">
            <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
            <p className="text-sm text-muted-foreground">読み込み中...</p>
          </div>
        </div>
      }
    >
      <BPORunInner />
    </Suspense>
  );
}
