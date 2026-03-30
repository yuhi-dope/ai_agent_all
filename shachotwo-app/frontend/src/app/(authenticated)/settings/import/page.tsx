"use client";

import { useCallback, useRef, useState } from "react";
import { useAuth } from "@/hooks/use-auth";
import { apiFetch } from "@/lib/api";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

// ---------- 型定義 ----------

interface ValidationError {
  row: number;
  column: string;
  message: string;
}

interface PreviewResponse {
  /** APIが返す取込トークン（内部管理用）*/
  importToken: string;
  columns: string[];
  rows: string[][];
  total_rows: number;
  errors: ValidationError[];
}

interface ImportResponse {
  imported: number;
  skipped: number;
  errors: number;
}

type ImportStep = "upload" | "preview" | "importing" | "done";

// ---------- テンプレート定義 ----------

const TEMPLATES = [
  {
    id: "products",
    label: "製品マスタ",
    description: "品番 / 品名 / 材質 / 単価 / リードタイム",
    columns: ["品番", "品名", "材質", "単価（円）", "リードタイム（日）"],
  },
  {
    id: "equipment",
    label: "設備台帳",
    description: "設備名 / メーカー / 導入年 / 保全周期",
    columns: ["設備名", "メーカー", "導入年", "保全周期（月）"],
  },
  {
    id: "suppliers",
    label: "仕入先マスタ",
    description: "仕入先名 / 品目 / 単価 / リードタイム / 評価",
    columns: ["仕入先名", "品目", "単価（円）", "リードタイム（日）", "評価（1-5）"],
  },
  {
    id: "quality",
    label: "品質基準",
    description: "工程名 / 検査項目 / 基準値 / 許容範囲",
    columns: ["工程名", "検査項目", "基準値", "許容範囲"],
  },
  {
    id: "costs",
    label: "原価データ",
    description: "品番 / 材料費 / 加工費 / 外注費 / 経費",
    columns: ["品番", "材料費（円）", "加工費（円）", "外注費（円）", "経費（円）"],
  },
] as const;

type TemplateId = (typeof TEMPLATES)[number]["id"];

// ---------- CSV ダウンロードユーティリティ ----------

function downloadCsvTemplate(templateId: TemplateId) {
  const template = TEMPLATES.find((t) => t.id === templateId);
  if (!template) return;

  // BOM付きUTF-8でExcelが文字化けしないようにする
  const bom = "\uFEFF";
  const header = template.columns.join(",");
  const blob = new Blob([bom + header + "\n"], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `template_${templateId}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

// ---------- ページ ----------

export default function CsvImportPage() {
  const { session } = useAuth();
  const token = session?.access_token;

  // ステップ管理
  const [step, setStep] = useState<ImportStep>("upload");

  // ファイル選択
  const [isDragging, setIsDragging] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // プレビュー
  const [previewing, setPreviewing] = useState(false);
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);

  // カラムマッピング
  const [selectedCategory, setSelectedCategory] = useState<TemplateId>("products");

  // 取込実行
  const [progress, setProgress] = useState(0);
  const [importResult, setImportResult] = useState<ImportResponse | null>(null);
  const [importError, setImportError] = useState<string | null>(null);

  // ---------- ファイルバリデーション ----------

  function validateFile(file: File): string | null {
    const allowedTypes = [
      "text/csv",
      "application/vnd.ms-excel",
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ];
    const allowedExtensions = [".csv", ".xlsx"];
    const ext = "." + file.name.split(".").pop()?.toLowerCase();

    if (!allowedExtensions.includes(ext) && !allowedTypes.includes(file.type)) {
      return "CSVまたはExcel（.xlsx）ファイルのみ取り込めます";
    }
    if (file.size > 10 * 1024 * 1024) {
      return "ファイルサイズは10MB以下にしてください";
    }
    return null;
  }

  function handleFileSelect(file: File) {
    const error = validateFile(file);
    if (error) {
      setFileError(error);
      setSelectedFile(null);
      return;
    }
    setFileError(null);
    setSelectedFile(file);
    setPreview(null);
    setPreviewError(null);
  }

  // ---------- ドラッグ&ドロップ ----------

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  }, []);

  const handleDragLeave = useCallback(() => {
    setIsDragging(false);
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    const files = Array.from(e.dataTransfer.files);
    if (files[0]) handleFileSelect(files[0]);
  }, []);

  // ---------- プレビュー取得 ----------

  async function handlePreview() {
    if (!selectedFile || !token) return;

    setPreviewing(true);
    setPreviewError(null);
    setPreview(null);

    try {
      const formData = new FormData();
      formData.append("file", selectedFile);

      const res = await apiFetch<PreviewResponse>("/ingestion/csv/preview", {
        token,
        method: "POST",
        body: formData,
      });
      setPreview(res);
      setStep("preview");
    } catch (err) {
      setPreviewError(
        err instanceof Error
          ? "ファイルの読み込みに失敗しました。ファイル形式と内容を確認してください"
          : "ファイルの読み込みに失敗しました。しばらく経ってから再度お試しください"
      );
    } finally {
      setPreviewing(false);
    }
  }

  // ---------- 取込実行 ----------

  async function handleImport() {
    if (!preview || !token) return;

    setStep("importing");
    setImportError(null);
    setProgress(0);

    // プログレス演出
    const timer = setInterval(() => {
      setProgress((p) => Math.min(p + 8, 90));
    }, 200);

    try {
      const res = await apiFetch<ImportResponse>("/ingestion/csv/import", {
        token,
        method: "POST",
        body: {
          import_token: preview.importToken,
          column_mapping: {},
          category: selectedCategory,
        },
      });
      clearInterval(timer);
      setProgress(100);
      setImportResult(res);
      setStep("done");
    } catch (err) {
      clearInterval(timer);
      setImportError(
        err instanceof Error
          ? "取り込み処理に失敗しました。内容を確認してから再度お試しください"
          : "取り込み処理に失敗しました。しばらく経ってから再度お試しください"
      );
      setStep("preview");
    }
  }

  // ---------- リセット ----------

  function handleReset() {
    setStep("upload");
    setSelectedFile(null);
    setFileError(null);
    setPreview(null);
    setPreviewError(null);
    setImportResult(null);
    setImportError(null);
    setProgress(0);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  // ---------- レンダリング ----------

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      {/* ページタイトル */}
      <div>
        <h1 className="text-2xl font-bold">データを一括取り込む</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          CSVまたはExcelファイルから製造業のマスタデータを一括で登録できます。
        </p>
      </div>

      {/* STEP 1: テンプレートダウンロード */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">ステップ1：テンプレートをダウンロード</CardTitle>
          <CardDescription>
            取り込みたいデータの種類に合わせてテンプレートを入手してください。
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {TEMPLATES.map((t) => (
              <div
                key={t.id}
                className="flex items-start justify-between rounded-lg border p-3"
              >
                <div className="flex-1 pr-3">
                  <p className="text-sm font-medium">{t.label}</p>
                  <p className="mt-0.5 text-xs text-muted-foreground">{t.description}</p>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  className="shrink-0 text-xs"
                  onClick={() => downloadCsvTemplate(t.id)}
                >
                  ダウンロード
                </Button>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* STEP 2: ファイルアップロード */}
      {(step === "upload" || step === "preview") && (
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">ステップ2：ファイルをアップロード</CardTitle>
            <CardDescription>
              CSVまたはExcel（.xlsx）ファイルをドラッグ&ドロップ、またはファイルを選択してください。最大10MBまで対応。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {/* データ種類の選択 */}
            <div className="flex flex-col gap-1.5">
              <label className="text-sm font-medium" htmlFor="category-select">
                データの種類
              </label>
              <select
                id="category-select"
                value={selectedCategory}
                onChange={(e) => setSelectedCategory(e.target.value as TemplateId)}
                className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm sm:w-64"
              >
                {TEMPLATES.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.label}
                  </option>
                ))}
              </select>
            </div>

            {/* ドラッグ&ドロップエリア */}
            <div
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              onDrop={handleDrop}
              onClick={() => fileInputRef.current?.click()}
              className={[
                "flex cursor-pointer flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed px-6 py-10 text-center transition-colors",
                isDragging
                  ? "border-primary bg-primary/5"
                  : "border-muted-foreground/25 hover:border-primary/50 hover:bg-muted/30",
              ].join(" ")}
            >
              <div className="flex h-12 w-12 items-center justify-center rounded-full bg-muted">
                <svg
                  className="h-6 w-6 text-muted-foreground"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                  aria-hidden="true"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={1.5}
                    d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5"
                  />
                </svg>
              </div>
              <div>
                <p className="text-sm font-medium">
                  {selectedFile
                    ? selectedFile.name
                    : "ここにファイルをドラッグ&ドロップ"}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {selectedFile
                    ? `${(selectedFile.size / 1024).toFixed(0)} KB`
                    : "または下のボタンからファイルを選択"}
                </p>
              </div>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={(e) => {
                  e.stopPropagation();
                  fileInputRef.current?.click();
                }}
              >
                ファイルを選択する
              </Button>
              <input
                ref={fileInputRef}
                type="file"
                accept=".csv,.xlsx"
                className="hidden"
                onChange={(e) => {
                  if (e.target.files?.[0]) handleFileSelect(e.target.files[0]);
                }}
              />
            </div>

            {/* ファイルエラー */}
            {fileError && (
              <div className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
                {fileError}
              </div>
            )}

            {/* プレビューエラー */}
            {previewError && (
              <div className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
                {previewError}
              </div>
            )}

            {/* プレビューボタン */}
            <Button
              className="w-full sm:w-auto"
              disabled={!selectedFile || previewing}
              onClick={handlePreview}
            >
              {previewing ? (
                <>
                  <span className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
                  ファイルを確認しています...
                </>
              ) : (
                "内容を確認する"
              )}
            </Button>
          </CardContent>
        </Card>
      )}

      {/* STEP 3: プレビュー & バリデーション */}
      {step === "preview" && preview && (
        <Card>
          <CardHeader>
            <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <CardTitle className="text-lg">ステップ3：内容を確認して取り込む</CardTitle>
                <CardDescription className="mt-1">
                  データの先頭5行を表示しています。内容に問題がなければ「取り込みを開始する」を押してください。
                </CardDescription>
              </div>
              {/* 件数サマリー */}
              <div className="shrink-0">
                {preview.errors.length === 0 ? (
                  <Badge className="bg-green-100 text-green-800">
                    全{preview.total_rows}件が正常です
                  </Badge>
                ) : (
                  <Badge className="bg-yellow-100 text-yellow-800">
                    {preview.total_rows}件中{" "}
                    {preview.total_rows - preview.errors.length}件が正常 /
                    {preview.errors.length}件にエラー
                  </Badge>
                )}
              </div>
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            {/* バリデーションエラー一覧 */}
            {preview.errors.length > 0 && (
              <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-3">
                <p className="mb-2 text-sm font-medium text-destructive">
                  以下の行にエラーがあります。修正後に再アップロードするか、エラー行をスキップして取り込みを続けることができます。
                </p>
                <ul className="space-y-1">
                  {preview.errors.slice(0, 5).map((err, i) => (
                    <li key={i} className="text-xs text-destructive">
                      {err.row}行目 / {err.column}：{err.message}
                    </li>
                  ))}
                  {preview.errors.length > 5 && (
                    <li className="text-xs text-muted-foreground">
                      他{preview.errors.length - 5}件のエラー
                    </li>
                  )}
                </ul>
              </div>
            )}

            {/* データプレビューテーブル（スマホはカード表示） */}
            {/* PC: テーブル */}
            <div className="hidden overflow-x-auto rounded-lg border sm:block">
              <Table>
                <TableHeader>
                  <TableRow>
                    {preview.columns.map((col) => (
                      <TableHead key={col} className="whitespace-nowrap text-xs">
                        {col}
                      </TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {preview.rows.slice(0, 5).map((row, rowIdx) => {
                    const hasRowError = preview.errors.some(
                      (e) => e.row === rowIdx + 2
                    );
                    return (
                      <TableRow
                        key={rowIdx}
                        className={hasRowError ? "bg-destructive/5" : undefined}
                      >
                        {row.map((cell, colIdx) => (
                          <TableCell key={colIdx} className="text-xs">
                            {cell || <span className="text-muted-foreground">—</span>}
                          </TableCell>
                        ))}
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>

            {/* スマホ: カードリスト */}
            <div className="space-y-2 sm:hidden">
              {preview.rows.slice(0, 5).map((row, rowIdx) => {
                const hasRowError = preview.errors.some(
                  (e) => e.row === rowIdx + 2
                );
                return (
                  <div
                    key={rowIdx}
                    className={[
                      "rounded-lg border p-3 text-xs",
                      hasRowError ? "border-destructive/40 bg-destructive/5" : "",
                    ].join(" ")}
                  >
                    <div className="mb-1.5 flex items-center gap-1.5">
                      <span className="text-muted-foreground">{rowIdx + 1}行目</span>
                      {hasRowError && (
                        <Badge variant="destructive" className="text-[11px]">
                          エラーあり
                        </Badge>
                      )}
                    </div>
                    <dl className="grid grid-cols-2 gap-1">
                      {preview.columns.map((col, colIdx) => (
                        <div key={col}>
                          <dt className="text-muted-foreground">{col}</dt>
                          <dd className="font-medium">
                            {row[colIdx] || <span className="text-muted-foreground">—</span>}
                          </dd>
                        </div>
                      ))}
                    </dl>
                  </div>
                );
              })}
            </div>

            {/* 取込ボタン */}
            <div className="flex flex-col gap-2 sm:flex-row">
              <Button className="w-full sm:w-auto" onClick={handleImport}>
                取り込みを開始する
              </Button>
              <Button
                variant="outline"
                className="w-full sm:w-auto"
                onClick={handleReset}
              >
                別のファイルを選び直す
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* STEP 4: 取込中 */}
      {step === "importing" && (
        <Card>
          <CardContent className="flex flex-col items-center gap-4 py-12">
            <div className="h-10 w-10 animate-spin rounded-full border-4 border-primary border-t-transparent" />
            <p className="text-sm font-medium">データを取り込んでいます。しばらくお待ちください...</p>
            <div className="w-full max-w-xs space-y-1.5">
              <Progress value={progress} />
              <p className="text-center text-xs text-muted-foreground">{progress}%</p>
            </div>
          </CardContent>
        </Card>
      )}

      {/* STEP 5: 完了 */}
      {step === "done" && importResult && (
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">取り込みが完了しました</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {/* 結果サマリー */}
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <div className="rounded-lg border bg-green-50 p-4 text-center">
                <p className="text-2xl font-bold text-green-700">
                  {importResult.imported.toLocaleString()}
                </p>
                <p className="mt-1 text-xs text-green-600">取り込み完了</p>
              </div>
              <div className="rounded-lg border bg-muted p-4 text-center">
                <p className="text-2xl font-bold text-muted-foreground">
                  {importResult.skipped.toLocaleString()}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">スキップ</p>
              </div>
              <div
                className={[
                  "rounded-lg border p-4 text-center",
                  importResult.errors > 0 ? "bg-destructive/5" : "bg-muted",
                ].join(" ")}
              >
                <p
                  className={[
                    "text-2xl font-bold",
                    importResult.errors > 0 ? "text-destructive" : "text-muted-foreground",
                  ].join(" ")}
                >
                  {importResult.errors.toLocaleString()}
                </p>
                <p
                  className={[
                    "mt-1 text-xs",
                    importResult.errors > 0 ? "text-destructive" : "text-muted-foreground",
                  ].join(" ")}
                >
                  エラー
                </p>
              </div>
            </div>

            {importResult.errors > 0 && (
              <div className="rounded-md bg-yellow-50 px-3 py-2 text-sm text-yellow-800">
                エラーになった行は取り込まれていません。エラー内容を確認してデータを修正し、再度取り込んでください。
              </div>
            )}

            <div className="flex flex-col gap-2 sm:flex-row">
              <Button className="w-full sm:w-auto" onClick={handleReset}>
                続けて別のファイルを取り込む
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* 取込エラー（preview画面に戻った場合） */}
      {importError && step === "preview" && (
        <div className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {importError}
        </div>
      )}
    </div>
  );
}
