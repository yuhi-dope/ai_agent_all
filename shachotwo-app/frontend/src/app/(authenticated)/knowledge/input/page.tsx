"use client";

import { useCallback, useRef, useState } from "react";
import { useAuth } from "@/hooks/use-auth";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DEPARTMENTS = [
  "営業",
  "経理",
  "総務",
  "製造",
  "品質管理",
  "人事",
  "情報システム",
  "経営企画",
] as const;

const CATEGORIES = [
  { value: "pricing", label: "価格・見積", placeholder: "見積金額の算出方法、値引き基準、有効期限などを入力してください..." },
  { value: "workflow", label: "業務フロー", placeholder: "業務の流れ、手順、担当者、期限などを入力してください..." },
  { value: "safety", label: "安全管理", placeholder: "安全基準、作業中止条件、事故防止ルールなどを入力してください..." },
  { value: "finance", label: "経理・財務", placeholder: "経費精算、承認フロー、支払条件などを入力してください..." },
  { value: "hr", label: "人事・労務", placeholder: "採用基準、勤怠ルール、評価制度などを入力してください..." },
  { value: "compliance", label: "コンプライアンス", placeholder: "法令遵守、社内規程、ガバナンスルールなどを入力してください..." },
  { value: "other", label: "その他", placeholder: "会社のルール・ナレッジを自由に入力してください..." },
] as const;

const EXAMPLE_INPUTS = [
  {
    tag: "見積ルール",
    category: "pricing",
    text: "見積書の有効期限は発行日から30日間。100万円以上の案件は社長承認が必要。",
  },
  {
    tag: "業務フロー",
    category: "workflow",
    text: "受注→見積作成→承認→発注→納品→請求の順で処理する。見積作成は3営業日以内。",
  },
  {
    tag: "判断基準",
    category: "workflow",
    text: "新規取引先との取引開始には、信用調査レポートと社長面談が必要。",
  },
  {
    tag: "安全規則",
    category: "safety",
    text: "雨量5mm/h以上または風速10m/s以上の場合、屋外作業を中止する。",
  },
  {
    tag: "経理ルール",
    category: "finance",
    text: "5万円以下の経費は部門長承認、5万円超は社長承認が必要。",
  },
] as const;

const ACCEPTED_FILE_TYPES = ".xlsx,.xls,.pdf,.docx,.doc,.txt";
const ACCEPTED_FILE_EXTENSIONS = ["xlsx", "xls", "pdf", "docx", "doc", "txt"];
const MAX_FILE_SIZE_MB = 10;
const MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024;

const CATEGORY_LABELS: Record<string, string> = {
  pricing: "価格・見積",
  hiring: "採用・人事",
  workflow: "業務フロー",
  policy: "社内方針",
  know_how: "ノウハウ",
  safety: "安全管理",
  finance: "経理・財務",
  hr: "人事・労務",
  compliance: "コンプライアンス",
  other: "その他",
};

const ITEM_TYPE_LABELS: Record<string, string> = {
  rule: "ルール",
  flow: "フロー",
  decision_logic: "判断基準",
  fact: "事実",
  tip: "ノウハウ",
};

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ExtractedItem {
  title: string;
  content: string;
  category: string;
  item_type: string;
  department: string;
  confidence: number;
}

interface IngestionResponse {
  session_id: string;
  items: ExtractedItem[];
  model_used: string;
  cost_yen: number;
}

interface FileUploadResponse {
  session_id: string;
  filename: string;
  items: ExtractedItem[];
  model_used: string;
  cost_yen: number;
}

interface ApplyTemplateResponse {
  template_id: string;
  company_id: string;
  items_created: number;
  departments: string[];
  message: string;
}

const INDUSTRY_TEMPLATES = [
  { value: "construction", label: "建設業" },
  { value: "manufacturing", label: "製造業" },
  { value: "dental", label: "歯科医院" },
] as const;

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function KnowledgeInputPage() {
  const { session } = useAuth();

  // Text input state
  const [content, setContent] = useState("");
  const [department, setDepartment] = useState("");
  const [customDepartment, setCustomDepartment] = useState("");
  const [category, setCategory] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<IngestionResponse | null>(null);

  // Template state
  const [selectedTemplate, setSelectedTemplate] = useState("construction");
  const [applyingTemplate, setApplyingTemplate] = useState(false);
  const [templateError, setTemplateError] = useState<string | null>(null);
  const [templateResult, setTemplateResult] = useState<ApplyTemplateResponse | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);

  // File upload state
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [fileDepartment, setFileDepartment] = useState("");
  const [fileCustomDepartment, setFileCustomDepartment] = useState("");
  const [fileCategory, setFileCategory] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadResult, setUploadResult] = useState<FileUploadResponse | null>(null);
  const [isDragOver, setIsDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const effectiveDepartment =
    department === "__custom__" ? customDepartment : department;
  const effectiveFileDepartment =
    fileDepartment === "__custom__" ? fileCustomDepartment : fileDepartment;

  const selectedCategoryConfig = CATEGORIES.find((c) => c.value === category);
  const placeholderText =
    selectedCategoryConfig?.placeholder ??
    "会社のルール・業務フロー・判断基準を入力してください...";

  // ---- Text submit ----
  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!content.trim()) return;

    setLoading(true);
    setError(null);
    setResult(null);

    try {
      const res = await apiFetch<IngestionResponse>("/ingestion/text", {
        method: "POST",
        token: session?.access_token,
        body: {
          content: content.trim(),
          ...(effectiveDepartment && { department: effectiveDepartment }),
          ...(category && { category }),
        },
      });
      setResult(res);
      setContent("");
    } catch {
      setError("処理に失敗しました。しばらく経ってから再度お試しください");
    } finally {
      setLoading(false);
    }
  }

  // ---- Template apply ----
  function handleApplyClick() {
    setConfirmOpen(true);
  }

  async function handleConfirmApply() {
    setConfirmOpen(false);
    if (!session?.access_token) return;

    setApplyingTemplate(true);
    setTemplateError(null);
    setTemplateResult(null);

    try {
      const res = await apiFetch<ApplyTemplateResponse>(
        "/genome/apply-template",
        {
          method: "POST",
          token: session.access_token,
          body: { template_id: selectedTemplate },
        }
      );
      setTemplateResult(res);
    } catch {
      setTemplateError("処理に失敗しました。しばらく経ってから再度お試しください");
    } finally {
      setApplyingTemplate(false);
    }
  }

  // ---- Example click ----
  function handleExampleClick(example: (typeof EXAMPLE_INPUTS)[number]) {
    setContent(example.text);
    setCategory(example.category);
  }

  // ---- Category click ----
  function handleCategoryClick(value: string) {
    setCategory((prev) => (prev === value ? "" : value));
  }

  // ---- File validation ----
  const validateFile = useCallback((file: File): string | null => {
    const ext = file.name.split(".").pop()?.toLowerCase();
    if (!ext || !ACCEPTED_FILE_EXTENSIONS.includes(ext)) {
      return `対応していないファイル形式です。対応形式: ${ACCEPTED_FILE_EXTENSIONS.join(", ")}`;
    }
    if (file.size > MAX_FILE_SIZE_BYTES) {
      return `ファイルサイズが${MAX_FILE_SIZE_MB}MBを超えています。`;
    }
    return null;
  }, []);

  // ---- File selection ----
  function handleFileSelect(file: File) {
    const validationError = validateFile(file);
    if (validationError) {
      setUploadError(validationError);
      setSelectedFile(null);
      return;
    }
    setUploadError(null);
    setSelectedFile(file);
    setUploadResult(null);
  }

  function handleFileInputChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) handleFileSelect(file);
    // Reset input so the same file can be re-selected
    e.target.value = "";
  }

  // ---- Drag & drop ----
  function handleDragOver(e: React.DragEvent) {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(true);
  }

  function handleDragLeave(e: React.DragEvent) {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(false);
  }

  function handleDrop(e: React.DragEvent) {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(false);
    const file = e.dataTransfer.files?.[0];
    if (file) handleFileSelect(file);
  }

  // ---- File upload ----
  async function handleFileUpload() {
    if (!selectedFile || !session?.access_token) return;

    setUploading(true);
    setUploadError(null);
    setUploadResult(null);
    setUploadProgress(0);

    try {
      const formData = new FormData();
      formData.append("file", selectedFile);
      if (effectiveFileDepartment) {
        formData.append("department", effectiveFileDepartment);
      }
      if (fileCategory) {
        formData.append("category", fileCategory);
      }

      // Use XMLHttpRequest for progress tracking
      const result = await new Promise<FileUploadResponse>((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open("POST", `${API_BASE}/ingestion/file`);
        xhr.setRequestHeader("Authorization", `Bearer ${session.access_token}`);

        xhr.upload.addEventListener("progress", (e) => {
          if (e.lengthComputable) {
            setUploadProgress(Math.round((e.loaded / e.total) * 100));
          }
        });

        xhr.addEventListener("load", () => {
          if (xhr.status >= 200 && xhr.status < 300) {
            try {
              resolve(JSON.parse(xhr.responseText));
            } catch {
              reject(new Error("レスポンスの解析に失敗しました"));
            }
          } else {
            try {
              const errData = JSON.parse(xhr.responseText);
              const message =
                errData?.error?.message ??
                errData?.detail?.error?.message ??
                (typeof errData?.detail === "string" ? errData.detail : null) ??
                "ファイルのアップロードに失敗しました";
              reject(new Error(message));
            } catch {
              reject(new Error("ファイルのアップロードに失敗しました"));
            }
          }
        });

        xhr.addEventListener("error", () => {
          reject(new Error("ネットワークエラーが発生しました"));
        });

        xhr.send(formData);
      });

      setUploadResult(result);
      setSelectedFile(null);
      setUploadProgress(100);
    } catch {
      setUploadError("処理に失敗しました。しばらく経ってから再度お試しください");
    } finally {
      setUploading(false);
    }
  }

  function formatFileSize(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  // ---- Shared result rendering ----
  function renderResults(items: ExtractedItem[], _modelUsed: string, _costYen: number) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            抽出結果
            <Badge variant="secondary">
              {items.length}件のナレッジを抽出しました
            </Badge>
          </CardTitle>
          <CardDescription>
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-3">
            {items.map((item, index) => (
              <div
                key={index}
                className="rounded-lg border p-3 space-y-2"
              >
                <div className="flex items-start justify-between gap-2">
                  <h3 className="font-medium">{item.title}</h3>
                  <Badge variant="outline">
                    {item.confidence >= 0.8 ? "確度：高" : item.confidence >= 0.5 ? "確度：中" : "参考情報"}
                  </Badge>
                </div>
                <p className="text-sm text-muted-foreground whitespace-pre-wrap">
                  {item.content}
                </p>
                <div className="flex gap-2">
                  {item.category && (
                    <Badge variant="secondary">{CATEGORY_LABELS[item.category] || item.category}</Badge>
                  )}
                  {item.department && (
                    <Badge variant="outline">{item.department}</Badge>
                  )}
                  {item.item_type && (
                    <Badge variant="ghost">{ITEM_TYPE_LABELS[item.item_type] || item.item_type}</Badge>
                  )}
                </div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    );
  }

  // ---- Department selector (reused for both tabs) ----
  function renderDepartmentSelect(
    id: string,
    value: string,
    onChange: (v: string) => void,
    customValue: string,
    onCustomChange: (v: string) => void
  ) {
    return (
      <div className="space-y-2">
        <Label htmlFor={id}>部署（任意）</Label>
        <select
          id={id}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="h-8 w-full rounded-lg border border-input bg-transparent px-2.5 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
        >
          <option value="">選択してください</option>
          {DEPARTMENTS.map((dep) => (
            <option key={dep} value={dep}>
              {dep}
            </option>
          ))}
          <option value="__custom__">その他（自由入力）</option>
        </select>
        {value === "__custom__" && (
          <Input
            placeholder="部署名を入力"
            value={customValue}
            onChange={(e) => onCustomChange(e.target.value)}
          />
        )}
      </div>
    );
  }

  // ---- Category selector for file tab ----
  function renderFileCategorySelect() {
    return (
      <div className="space-y-2">
        <Label htmlFor="file-category">カテゴリ（任意）</Label>
        <select
          id="file-category"
          value={fileCategory}
          onChange={(e) => setFileCategory(e.target.value)}
          className="h-8 w-full rounded-lg border border-input bg-transparent px-2.5 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
        >
          <option value="">選択してください</option>
          {CATEGORIES.map((cat) => (
            <option key={cat.value} value={cat.value}>
              {cat.label}
            </option>
          ))}
        </select>
      </div>
    );
  }

  // =======================================================================
  // Render
  // =======================================================================

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold">ナレッジ入力</h1>
        <p className="text-muted-foreground">
          会社のルール・業務フロー・判断基準を入力してください。
          AIが自動でナレッジを抽出・構造化します。
        </p>
      </div>

      {/* Industry template card */}
      <Card>
        <CardHeader>
          <CardTitle>業種テンプレートを適用</CardTitle>
          <CardDescription>
            業種に合わせたナレッジテンプレートを適用すると、初日から使える標準ルール・業務フローが登録されます。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-end gap-4">
            <div className="space-y-2 flex-1">
              <Label htmlFor="template-select">業種を選択</Label>
              <select
                id="template-select"
                value={selectedTemplate}
                onChange={(e) => setSelectedTemplate(e.target.value)}
                className="h-8 w-full rounded-lg border border-input bg-transparent px-2.5 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
              >
                {INDUSTRY_TEMPLATES.map((t) => (
                  <option key={t.value} value={t.value}>
                    {t.label}
                  </option>
                ))}
              </select>
            </div>
            <Button
              onClick={handleApplyClick}
              disabled={applyingTemplate}
              variant="secondary"
            >
              {applyingTemplate ? "適用中..." : "テンプレートを適用"}
            </Button>
          </div>

          {templateError && (
            <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
              {templateError}
            </div>
          )}

          {templateResult && (
            <div className="rounded-lg border border-green-500/50 bg-green-500/10 p-3 text-sm text-green-700 dark:text-green-400">
              {templateResult.message}
              <span className="ml-2 text-xs text-muted-foreground">
                （部署: {templateResult.departments.join("、")}）
              </span>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Main tabs */}
      <Tabs defaultValue="text">
        <TabsList>
          <TabsTrigger value="text">テキスト入力</TabsTrigger>
          <TabsTrigger value="file">ファイルアップロード</TabsTrigger>
        </TabsList>

        {/* ============================================================= */}
        {/* Text input tab                                                 */}
        {/* ============================================================= */}
        <TabsContent value="text" className="space-y-4 pt-4">
            {/* Input form */}
          <Card>
            <CardHeader>
              <CardTitle>テキスト入力</CardTitle>
              <CardDescription>
                社内ルール、業務プロセス、判断基準などのテキストを入力してください。
              </CardDescription>
            </CardHeader>
            <CardContent>
              <form onSubmit={handleSubmit} className="space-y-4">
                {/* Category quick-select */}
                <div className="space-y-2">
                  <Label>カテゴリ（任意）</Label>
                  <div className="flex flex-wrap gap-2">
                    {CATEGORIES.map((cat) => (
                      <button
                        key={cat.value}
                        type="button"
                        onClick={() => handleCategoryClick(cat.value)}
                        className={`inline-flex items-center rounded-full border px-3 py-1 text-sm font-medium transition-colors ${
                          category === cat.value
                            ? "border-primary bg-primary text-primary-foreground"
                            : "border-input bg-transparent text-foreground hover:bg-muted"
                        }`}
                      >
                        {cat.label}
                      </button>
                    ))}
                  </div>
                </div>

                {/* Textarea with example chips */}
                <div className="space-y-2">
                  <Label htmlFor="content">内容 *</Label>
                  <div className="flex flex-wrap gap-1.5 mb-2">
                    {EXAMPLE_INPUTS.map((example, i) => (
                      <button
                        key={i}
                        type="button"
                        onClick={() => handleExampleClick(example)}
                        className="inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium transition-colors hover:bg-primary hover:text-primary-foreground hover:border-primary"
                      >
                        {example.tag}
                      </button>
                    ))}
                  </div>
                  <Textarea
                    id="content"
                    placeholder={placeholderText}
                    value={content}
                    onChange={(e) => setContent(e.target.value)}
                    className="min-h-40"
                    required
                  />
                </div>

                {/* Department */}
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  {renderDepartmentSelect(
                    "department",
                    department,
                    setDepartment,
                    customDepartment,
                    setCustomDepartment
                  )}
                </div>

                {error && (
                  <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
                    {error}
                  </div>
                )}

                <Button type="submit" disabled={loading || !content.trim()}>
                  {loading ? "取り込み中..." : "ナレッジを取り込む"}
                </Button>
              </form>
            </CardContent>
          </Card>

          {/* Text results */}
          {result &&
            renderResults(result.items, result.model_used, result.cost_yen)}
        </TabsContent>

        {/* ============================================================= */}
        {/* File upload tab                                                */}
        {/* ============================================================= */}
        <TabsContent value="file" className="space-y-4 pt-4">
          <Card>
            <CardHeader>
              <CardTitle>ファイルアップロード</CardTitle>
              <CardDescription>
                Excel、PDF、Word、テキストファイルからナレッジを自動抽出します。
                対応形式: .xlsx, .xls, .pdf, .docx, .doc, .txt（最大{MAX_FILE_SIZE_MB}MB）
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* Drop zone */}
              <div
                onDragOver={handleDragOver}
                onDragLeave={handleDragLeave}
                onDrop={handleDrop}
                onClick={() => fileInputRef.current?.click()}
                className={`flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed p-8 text-center transition-colors ${
                  isDragOver
                    ? "border-primary bg-primary/5"
                    : "border-input hover:border-primary/50 hover:bg-muted/50"
                }`}
              >
                <svg
                  className="mb-3 h-10 w-10 text-muted-foreground"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                  strokeWidth={1.5}
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M12 16.5V9.75m0 0l3 3m-3-3l-3 3M6.75 19.5a4.5 4.5 0 01-1.41-8.775 5.25 5.25 0 0110.338-2.32 3 3 0 013.548 3.795A3.75 3.75 0 0118 19.5H6.75z"
                  />
                </svg>
                <p className="text-sm font-medium">
                  ここにファイルをドラッグ&ドロップ
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  またはクリックしてファイルを選択
                </p>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept={ACCEPTED_FILE_TYPES}
                  onChange={handleFileInputChange}
                  className="hidden"
                />
              </div>

              {/* Selected file info */}
              {selectedFile && (
                <div className="flex items-center justify-between rounded-lg border bg-muted/50 p-3">
                  <div className="flex items-center gap-3 min-w-0">
                    <svg
                      className="h-5 w-5 shrink-0 text-muted-foreground"
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                      strokeWidth={1.5}
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z"
                      />
                    </svg>
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium">
                        {selectedFile.name}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        {formatFileSize(selectedFile.size)}
                      </p>
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => {
                      setSelectedFile(null);
                      setUploadError(null);
                    }}
                    className="ml-2 shrink-0 rounded p-1 text-muted-foreground hover:text-foreground"
                  >
                    <svg
                      className="h-4 w-4"
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                      strokeWidth={2}
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        d="M6 18L18 6M6 6l12 12"
                      />
                    </svg>
                  </button>
                </div>
              )}

              {/* Upload progress */}
              {uploading && (
                <div className="space-y-2">
                  <div className="flex items-center justify-between text-sm">
                    <span className="text-muted-foreground">アップロード中...</span>
                    <span className="font-medium">{uploadProgress}%</span>
                  </div>
                  <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
                    <div
                      className="h-full rounded-full bg-primary transition-all duration-300"
                      style={{ width: `${uploadProgress}%` }}
                    />
                  </div>
                </div>
              )}

              {/* Department & Category for file */}
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                {renderDepartmentSelect(
                  "file-department",
                  fileDepartment,
                  setFileDepartment,
                  fileCustomDepartment,
                  setFileCustomDepartment
                )}
                {renderFileCategorySelect()}
              </div>

              {uploadError && (
                <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
                  {uploadError}
                </div>
              )}

              <Button
                onClick={handleFileUpload}
                disabled={uploading || !selectedFile}
              >
                {uploading ? "アップロード中..." : "ファイルを取り込む"}
              </Button>
            </CardContent>
          </Card>

          {/* File upload results */}
          {uploadResult &&
            renderResults(
              uploadResult.items,
              uploadResult.model_used,
              uploadResult.cost_yen
            )}
        </TabsContent>
      </Tabs>

      {/* Template confirm dialog */}
      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>テンプレートを適用しますか？</DialogTitle>
            <DialogDescription>
              「{INDUSTRY_TEMPLATES.find((t) => t.value === selectedTemplate)?.label}」テンプレートを適用します。
              既に適用済みのテンプレートがある場合、以前のテンプレートデータは削除され、新しいテンプレートに置き換わります。
              手動で入力したナレッジには影響しません。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmOpen(false)}>
              キャンセル
            </Button>
            <Button onClick={handleConfirmApply}>
              適用する
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
