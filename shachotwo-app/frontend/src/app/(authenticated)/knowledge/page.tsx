"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/hooks/use-auth";
import { apiFetch } from "@/lib/api";
import type { PaginatedResponse } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface FileInfo {
  file_name: string;
  file_size: number;
  file_content_type: string;
  download_url: string;
  created_at: string;
}

interface KnowledgeItem {
  id: string;
  department: string;
  category: string;
  item_type: string;
  title: string;
  content: string;
  conditions: string[] | null;
  examples: string[] | null;
  exceptions: string[] | null;
  source_type: string;
  confidence: number | null;
  version: number;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

// ---------------------------------------------------------------------------
// Types (theme progress)
// ---------------------------------------------------------------------------

interface ThemeProgress {
  theme: string;
  display_name: string;
  coverage_rate: number;
  question_count: number;
  status: "completed" | "active" | "not_started";
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DEPARTMENTS = [
  "経理・財務", "総務・人事・労務", "法務・コンプライアンス", "情報システム", "営業事務",
  "営業", "経理", "総務", "製造", "品質管理", "人事",
  "経営企画", "現場管理", "安全管理", "建設業法務",
  "原価・在庫管理", "生産管理", "診療", "受付", "経営",
  "衛生管理", "歯科医院管理",
] as const;

const ITEM_TYPE_LABELS: Record<string, string> = {
  rule: "ルール",
  flow: "フロー",
  decision_logic: "判断基準",
  fact: "事実",
  tip: "ノウハウ",
};

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

const SOURCE_TYPE_LABELS: Record<string, string> = {
  explicit: "手動入力",
  template: "テンプレート",
  manual: "手動入力",
  extracted: "自動抽出",
  inferred: "推論",
};

const PAGE_SIZE = 100;

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function KnowledgeListPage() {
  const { session } = useAuth();
  const router = useRouter();

  // Theme progress state
  const [themes, setThemes] = useState<ThemeProgress[]>([]);
  const [themesLoading, setThemesLoading] = useState(true);
  const [themesError, setThemesError] = useState<string | null>(null);

  const [items, setItems] = useState<KnowledgeItem[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [fileUploadError, setFileUploadError] = useState<string | null>(null);

  const [search, setSearch] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [department, setDepartment] = useState("");
  const [availableDepts, setAvailableDepts] = useState<string[]>([]);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [collapsedDepts, setCollapsedDepts] = useState<Set<string>>(new Set());
  const [listOpen, setListOpen] = useState(true);
  const [helpOpen, setHelpOpen] = useState(false);

  // Edit (inline in detail pane)
  const [isEditing, setIsEditing] = useState(false);
  const [editTitle, setEditTitle] = useState("");
  const [editContent, setEditContent] = useState("");
  const [editConditions, setEditConditions] = useState("");
  const [editExamples, setEditExamples] = useState("");
  const [editDepartment, setEditDepartment] = useState("");
  const [editCategory, setEditCategory] = useState("");
  const [editExceptions, setEditExceptions] = useState("");
  const [editSaving, setEditSaving] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);

  // Delete dialog
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);

  // 👎 rejected content confirmation dialog
  const [rejectedConfirmOpen, setRejectedConfirmOpen] = useState(false);
  const [pendingSaveItem, setPendingSaveItem] = useState<{ title: string; onConfirm: () => void } | null>(null);

  // File
  const [fileInfo, setFileInfo] = useState<FileInfo | null>(null);
  const [fileLoading, setFileLoading] = useState(false);
  const [fileUploading, setFileUploading] = useState(false);
  const [fileDragOver, setFileDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const selected = items.find((i) => i.id === selectedId) ?? null;

  // ---- Fetch ----
  const fetchItems = useCallback(async () => {
    if (!session?.access_token) return;
    setLoading(true);
    setError(null);
    try {
      const params: Record<string, string> = {
        limit: String(PAGE_SIZE),
        offset: String(offset),
      };
      if (search) params.search = search;
      if (department) params.department = department;
      const res = await apiFetch<PaginatedResponse<KnowledgeItem>>(
        "/knowledge/items",
        { token: session.access_token, params }
      );
      setItems(res.items);
      setTotal(res.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : "取得に失敗しました");
    } finally {
      setLoading(false);
    }
  }, [session?.access_token, offset, search, department]);

  useEffect(() => { fetchItems(); }, [fetchItems]);

  // Fetch available departments
  useEffect(() => {
    if (!session?.access_token) return;
    apiFetch<string[]>("/knowledge/departments", { token: session.access_token })
      .then(setAvailableDepts)
      .catch(() => {});
  }, [session?.access_token, items]);

  // Fetch theme progress
  useEffect(() => {
    if (!session?.access_token) return;
    setThemesLoading(true);
    setThemesError(null);
    apiFetch<ThemeProgress[]>("/knowledge/theme-progress", {
      token: session.access_token,
    })
      .then(setThemes)
      .catch(() => setThemesError("テーマ情報の取得に失敗しました"))
      .finally(() => setThemesLoading(false));
  }, [session?.access_token]);

  // Keyboard shortcuts
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      // Ignore when typing in input/textarea
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;

      const flatItems = items;
      const currentIdx = flatItems.findIndex((i) => i.id === selectedId);

      // Arrow down / j — next item
      if (e.key === "ArrowDown" || e.key === "j") {
        e.preventDefault();
        const next = currentIdx < flatItems.length - 1 ? currentIdx + 1 : 0;
        setSelectedId(flatItems[next]?.id ?? null);
        setIsEditing(false);
      }
      // Arrow up / k — previous item
      else if (e.key === "ArrowUp" || e.key === "k") {
        e.preventDefault();
        const prev = currentIdx > 0 ? currentIdx - 1 : flatItems.length - 1;
        setSelectedId(flatItems[prev]?.id ?? null);
        setIsEditing(false);
      }
      // Escape — close detail / cancel edit
      else if (e.key === "Escape") {
        if (isEditing) setIsEditing(false);
        else setSelectedId(null);
      }
      // e — edit selected
      else if (e.key === "e" && selectedId && !isEditing) {
        e.preventDefault();
        startEdit();
      }
      // [ — collapse all
      else if (e.key === "[") {
        const allDepts = [...new Set(items.map((i) => i.department))];
        setCollapsedDepts(new Set(allDepts));
      }
      // ] — expand all
      else if (e.key === "]") {
        setCollapsedDepts(new Set());
      }
      // / — focus search
      else if (e.key === "/") {
        e.preventDefault();
        document.querySelector<HTMLInputElement>('input[placeholder*="キーワード"]')?.focus();
      }
      // Cmd+B / Ctrl+B — toggle list pane
      else if (e.key === "b" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setListOpen((prev) => !prev);
      }
      // ? — help
      else if (e.key === "?") {
        setHelpOpen((prev) => !prev);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [items, selectedId, isEditing]);

  function handleSearch(e: React.FormEvent) {
    e.preventDefault();
    setOffset(0);
    setSearch(searchInput);
  }

  // ---- Select ----
  function handleSelect(item: KnowledgeItem) {
    setSelectedId(item.id);
    setIsEditing(false);
    setEditError(null);
  }

  // ---- Edit ----
  // Convert string[] | null to newline-separated text for editing
  function arrToText(arr: string[] | null): string {
    return arr?.length ? arr.join("\n") : "";
  }
  // Convert newline-separated text back to string[] (filter empty lines)
  function textToArr(text: string): string[] | null {
    const lines = text.split("\n").map((l) => l.trim()).filter(Boolean);
    return lines.length > 0 ? lines : null;
  }

  function startEdit() {
    if (!selected) return;
    setEditTitle(selected.title);
    setEditContent(selected.content);
    setEditDepartment(selected.department || "");
    setEditCategory(selected.category || "");
    setEditConditions(arrToText(selected.conditions));
    setEditExamples(arrToText(selected.examples));
    setEditExceptions(arrToText(selected.exceptions));
    setEditError(null);
    setIsEditing(true);
  }

  // ---- Rejected content check ----
  // 過去に👎がついたコンテンツを保存しようとした場合に確認ダイアログを表示する
  async function checkRejectedAndSave(title: string, onConfirm: () => void) {
    if (!session?.access_token) return;
    try {
      const result = await apiFetch<{ is_rejected: boolean }>(
        "/knowledge/check-rejected",
        {
          token: session.access_token,
          method: "POST",
          body: { title },
        }
      );
      if (result.is_rejected) {
        setPendingSaveItem({ title, onConfirm });
        setRejectedConfirmOpen(true);
        return;
      }
    } catch {
      // チェック失敗時はそのまま保存続行
    }
    onConfirm();
  }

  async function handleSave() {
    if (!selected || !session?.access_token) return;
    setEditSaving(true);
    setEditError(null);
    try {
      const updated = await apiFetch<KnowledgeItem>(
        `/knowledge/items/${selected.id}`,
        {
          method: "PATCH",
          token: session.access_token,
          body: {
            title: editTitle,
            content: editContent,
            department: editDepartment || undefined,
            category: editCategory || undefined,
            conditions: textToArr(editConditions),
            examples: textToArr(editExamples),
            exceptions: textToArr(editExceptions),
            version: selected.version,
          },
        }
      );
      setItems((prev) => prev.map((it) => (it.id === updated.id ? updated : it)));
      setIsEditing(false);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "更新に失敗しました";
      setEditError(msg.includes("VERSION_CONFLICT") ? "他のユーザーが先に更新しました。" : msg);
    } finally {
      setEditSaving(false);
    }
  }

  // ---- Delete ----
  async function handleDeactivate() {
    if (!selected || !session?.access_token) return;
    setDeleting(true);
    try {
      await apiFetch<KnowledgeItem>(
        `/knowledge/items/${selected.id}`,
        { method: "PATCH", token: session.access_token, body: { is_active: false, version: selected.version } }
      );
      setItems((prev) => prev.filter((it) => it.id !== selected.id));
      setTotal((prev) => prev - 1);
      setSelectedId(null);
      setDeleteOpen(false);
    } catch {
      setDeleteOpen(false);
    } finally {
      setDeleting(false);
    }
  }

  // ---- File helpers ----
  function formatFileSize(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  function getFileIcon(contentType: string, fileName: string): string {
    const ext = fileName.split(".").pop()?.toLowerCase() ?? "";
    if (contentType.includes("pdf") || ext === "pdf") return "📄";
    if (["xlsx", "xls"].includes(ext)) return "📊";
    if (["docx", "doc"].includes(ext)) return "📝";
    if (ext === "txt") return "📃";
    return "📎";
  }

  const fetchFileInfo = useCallback(async (itemId: string) => {
    if (!session?.access_token) return;
    setFileLoading(true);
    try {
      const res = await apiFetch<{ file: FileInfo | null }>(
        `/knowledge/items/${itemId}/file`,
        { token: session.access_token }
      );
      setFileInfo(res.file);
    } catch {
      setFileInfo(null);
    } finally {
      setFileLoading(false);
    }
  }, [session?.access_token]);

  useEffect(() => {
    if (selectedId) {
      setFileInfo(null);
      fetchFileInfo(selectedId);
    } else {
      setFileInfo(null);
    }
  }, [selectedId, fetchFileInfo]);

  async function handleFileDelete() {
    if (!selected || !session?.access_token) return;
    try {
      await apiFetch(`/knowledge/items/${selected.id}/file`, {
        method: "DELETE",
        token: session.access_token,
      });
      setFileInfo(null);
    } catch (err) {
      console.error("ファイル削除に失敗しました", err);
    }
  }

  async function handleFileUpload(file: File) {
    if (!selected || !session?.access_token) return;

    // バリデーション
    const ext = file.name.split(".").pop()?.toLowerCase() ?? "";
    const acceptedExts = ["txt", "pdf", "xlsx", "xls", "docx", "doc"];
    if (!acceptedExts.includes(ext)) {
      setFileUploadError("対応していないファイル形式です (.txt .pdf .xlsx .xls .docx .doc)");
      setTimeout(() => setFileUploadError(null), 5000);
      return;
    }
    if (file.size > 10 * 1024 * 1024) {
      setFileUploadError("ファイルサイズは10MB以下にしてください");
      setTimeout(() => setFileUploadError(null), 5000);
      return;
    }

    setFileUploading(true);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const res = await fetch(
        `${API_BASE}/knowledge/items/${selected.id}/file`,
        {
          method: "POST",
          headers: { Authorization: `Bearer ${session.access_token}` },
          body: formData,
        }
      );
      if (!res.ok) {
        const errData = await res.json().catch(() => null);
        throw new Error(errData?.detail ?? res.statusText);
      }
      const uploaded: FileInfo = await res.json();
      setFileInfo(uploaded);
    } catch (err) {
      setFileUploadError(err instanceof Error ? err.message : "アップロードに失敗しました");
      setTimeout(() => setFileUploadError(null), 5000);
    } finally {
      setFileUploading(false);
    }
  }

  function handleFileDrop(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setFileDragOver(false);
    const file = e.dataTransfer.files[0];
    if (file) handleFileUpload(file);
  }

  function handleFileInputChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) handleFileUpload(file);
    e.target.value = "";
  }

  // ---- Grouping ----
  const grouped = new Map<string, KnowledgeItem[]>();
  for (const item of items) {
    const dept = item.department || "未分類";
    if (!grouped.has(dept)) grouped.set(dept, []);
    grouped.get(dept)!.push(item);
  }

  const totalPages = Math.ceil(total / PAGE_SIZE);
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1;

  // ---- Helper: render array section ----
  function renderArraySection(label: string, arr: unknown) {
    if (!arr || !Array.isArray(arr) || arr.length === 0) return null;
    return (
      <div className="space-y-1.5">
        <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">{label}</h3>
        <ul className="space-y-1">
          {arr.map((item, i) => (
            <li key={i} className="text-sm flex gap-2">
              <span className="text-muted-foreground shrink-0">-</span>
              <span>{String(item)}</span>
            </li>
          ))}
        </ul>
      </div>
    );
  }

  // =====================================================================
  // Helpers
  // =====================================================================
  function getStatusBadge(status: ThemeProgress["status"]) {
    if (status === "completed") {
      return <Badge className="bg-green-100 text-green-800">完了</Badge>;
    }
    if (status === "active") {
      return <Badge className="bg-yellow-100 text-yellow-800">進行中</Badge>;
    }
    return <Badge variant="outline" className="text-muted-foreground">未着手</Badge>;
  }

  // =====================================================================
  // Render
  // =====================================================================
  return (
    <div className="flex h-[calc(100vh-4rem)] flex-col">
      {/* ---- ナレッジ収集ガイド（テーマ選択セクション） ---- */}
      <div className="shrink-0 border-b bg-muted/30 px-4 py-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-lg font-semibold">ナレッジ収集ガイド</h2>
          <span className="text-xs text-muted-foreground">テーマを選んでAIとQ&Aを始めましょう</span>
        </div>
        {themesLoading ? (
          <div className="flex items-center gap-2 py-2">
            <div className="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" />
            <span className="text-sm text-muted-foreground">テーマ情報を読み込んでいます...</span>
          </div>
        ) : themesError ? (
          <p className="text-sm text-destructive">{themesError}</p>
        ) : themes.length === 0 ? (
          <div className="flex items-center gap-3 rounded-lg border border-dashed p-4">
            <p className="text-sm text-muted-foreground">業種設定後にテーマが表示されます</p>
            <Button size="sm" variant="outline" onClick={() => router.push("/settings")}>
              業種を設定する
            </Button>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {themes.map((t) => (
              <Card key={t.theme} className="overflow-hidden">
                <CardHeader className="pb-2 pt-3 px-4">
                  <div className="flex items-start justify-between gap-2">
                    <CardTitle className="text-base font-medium leading-snug">{t.display_name}</CardTitle>
                    {getStatusBadge(t.status)}
                  </div>
                </CardHeader>
                <CardContent className="px-4 pb-3 space-y-3">
                  {/* 進捗バー */}
                  <div className="space-y-1">
                    <div className="h-2 w-full rounded-full bg-muted overflow-hidden">
                      <div
                        className={`h-full rounded-full transition-all ${
                          t.status === "completed" ? "bg-green-500" : "bg-primary"
                        }`}
                        style={{ width: `${Math.round(t.coverage_rate * 100)}%` }}
                      />
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-xs text-muted-foreground">
                        {Math.round(t.coverage_rate * 100)}% 収集済み
                      </span>
                      <span className="text-xs text-muted-foreground">
                        {t.question_count}件のルール
                      </span>
                    </div>
                  </div>
                  <Button
                    size="sm"
                    variant={t.status === "not_started" ? "default" : "outline"}
                    className="w-full"
                    onClick={() => router.push(`/knowledge/session?theme=${encodeURIComponent(t.theme)}`)}
                  >
                    {t.status === "not_started" ? "質問を開始する" : "続きから始める"}
                  </Button>
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </div>

      {/* ---- Top bar ---- */}
      <div className="shrink-0 border-b px-4 py-3">
        <form onSubmit={handleSearch} className="flex items-center gap-3">
          <Input
            placeholder="キーワード検索..."
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            className="max-w-xs h-8 text-sm"
          />
          <select
            value={department}
            onChange={(e) => { setDepartment(e.target.value); setOffset(0); }}
            className="h-8 rounded-lg border border-input bg-transparent px-2.5 text-sm outline-none"
          >
            <option value="">全部署</option>
            {availableDepts.map((d) => <option key={d} value={d}>{d}</option>)}
          </select>
          <Button type="submit" variant="secondary" size="sm">検索</Button>
          {(search || department) && (
            <Button type="button" variant="ghost" size="sm"
              onClick={() => { setSearch(""); setSearchInput(""); setDepartment(""); setOffset(0); }}>
              クリア
            </Button>
          )}
          <span className="ml-auto text-xs text-muted-foreground">
            ナレッジ一覧 — {total}件
          </span>
          <Button type="button" variant="ghost" size="sm" onClick={() => setHelpOpen(true)}>
            ヘルプ
          </Button>
        </form>
      </div>

      {error && (
        <div className="shrink-0 mx-4 mt-2 rounded-lg border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
          {error}
        </div>
      )}
      {fileUploadError && (
        <div className="shrink-0 mx-4 mt-2 rounded-lg border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
          {fileUploadError}
        </div>
      )}

      {/* ---- Split view ---- */}
      <div className="flex flex-1 min-h-0">
        {/* ==== Left pane: grouped list ==== */}
        <div className={`shrink-0 border-r flex flex-col min-h-0 transition-all duration-200 ${listOpen ? "w-80" : "w-0 overflow-hidden border-r-0"}`}>
          {loading ? (
            <div className="flex items-center justify-center py-12">
              <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary border-t-transparent" />
            </div>
          ) : items.length === 0 ? (
            <div className="py-12 text-center space-y-4 px-4">
              <p className="text-sm text-muted-foreground">ナレッジが見つかりません</p>
              <Button variant="outline" size="sm" onClick={() => { setSearch(""); setSearchInput(""); setDepartment(""); setOffset(0); }}>
                検索条件をクリア
              </Button>
            </div>
          ) : (
            <div className="flex-1 overflow-y-auto">
              {[...grouped.entries()].map(([dept, deptItems]) => {
                const isCollapsed = collapsedDepts.has(dept);
                return (
                  <div key={dept}>
                    <button
                      type="button"
                      className="sticky top-0 z-10 flex w-full items-center justify-between bg-muted/80 backdrop-blur-sm px-3 py-1.5 text-left border-b"
                      onClick={() =>
                        setCollapsedDepts((prev) => {
                          const next = new Set(prev);
                          next.has(dept) ? next.delete(dept) : next.add(dept);
                          return next;
                        })
                      }
                    >
                      <span className="text-xs font-semibold truncate">{dept}</span>
                      <span className="text-xs text-muted-foreground shrink-0 ml-2">
                        {deptItems.length} {isCollapsed ? "▸" : "▾"}
                      </span>
                    </button>
                    {!isCollapsed && (
                      <div>
                        {deptItems.map((item) => {
                          const isActive = item.id === selectedId;
                          return (
                            <button
                              key={item.id}
                              type="button"
                              onClick={() => handleSelect(item)}
                              className={`w-full text-left px-3 py-2 text-sm border-l-2 transition-colors ${
                                isActive
                                  ? "bg-primary/10 border-l-primary"
                                  : "border-l-transparent hover:bg-muted/50"
                              }`}
                            >
                              <div className="font-medium truncate text-sm">{item.title}</div>
                              <div className="flex items-center gap-1.5 mt-0.5">
                                <span className="text-xs text-muted-foreground">
                                  {ITEM_TYPE_LABELS[item.item_type] || item.item_type}
                                </span>
                              </div>
                            </button>
                          );
                        })}
                      </div>
                    )}
                  </div>
                );
              })}

              {totalPages > 1 && (
                <div className="flex items-center justify-between border-t px-3 py-2">
                  <Button variant="ghost" size="sm" disabled={offset === 0}
                    onClick={() => setOffset((p) => Math.max(0, p - PAGE_SIZE))}>前へ</Button>
                  <span className="text-xs text-muted-foreground">{currentPage}/{totalPages}</span>
                  <Button variant="ghost" size="sm" disabled={offset + PAGE_SIZE >= total}
                    onClick={() => setOffset((p) => p + PAGE_SIZE)}>次へ</Button>
                </div>
              )}
            </div>
          )}
        </div>

        {/* ==== Right pane: detail ==== */}
        <div className="flex-1 min-h-0 overflow-y-auto">
          {!selected ? (
            <div className="flex h-full items-center justify-center text-muted-foreground text-sm">
              左のリストからナレッジを選択してください
            </div>
          ) : isEditing ? (
            /* ---- Edit mode ---- */
            <div className="p-6 space-y-4 max-w-2xl">
              <div className="flex items-center justify-between">
                <h2 className="text-lg font-bold">編集</h2>
                <div className="flex gap-2">
                  <Button variant="outline" size="sm" onClick={() => setIsEditing(false)}>キャンセル</Button>
                  <Button
                    size="sm"
                    onClick={() => checkRejectedAndSave(editTitle, handleSave)}
                    disabled={editSaving || !editTitle.trim() || !editContent.trim()}
                  >
                    {editSaving ? (
                      <>
                        <span className="mr-2 h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent" />
                        保存中...
                      </>
                    ) : "ナレッジを保存する"}
                  </Button>
                </div>
              </div>
              {editError && (
                <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
                  {editError}
                </div>
              )}
              <div className="space-y-2">
                <Label>タイトル</Label>
                <Input value={editTitle} onChange={(e) => setEditTitle(e.target.value)} />
              </div>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div className="space-y-2">
                  <Label>部署</Label>
                  <select
                    value={DEPARTMENTS.includes(editDepartment as typeof DEPARTMENTS[number]) ? editDepartment : "__custom__"}
                    onChange={(e) => {
                      if (e.target.value === "__custom__") {
                        setEditDepartment(editDepartment);
                      } else {
                        setEditDepartment(e.target.value);
                      }
                    }}
                    className="h-8 w-full rounded-lg border border-input bg-transparent px-2.5 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
                  >
                    {DEPARTMENTS.map((d) => <option key={d} value={d}>{d}</option>)}
                    <option value="__custom__">その他（自由入力）</option>
                  </select>
                  {!DEPARTMENTS.includes(editDepartment as typeof DEPARTMENTS[number]) && (
                    <Input
                      placeholder="部署名を入力"
                      value={editDepartment}
                      onChange={(e) => setEditDepartment(e.target.value)}
                    />
                  )}
                </div>
                <div className="space-y-2">
                  <Label>カテゴリ</Label>
                  <Input value={editCategory} onChange={(e) => setEditCategory(e.target.value)} placeholder="例: 価格・見積、業務フロー" />
                </div>
              </div>
              <div className="space-y-2">
                <Label>内容</Label>
                <Textarea value={editContent} onChange={(e) => setEditContent(e.target.value)} className="min-h-40" />
              </div>
              <div className="space-y-2">
                <Label>適用条件 <span className="text-muted-foreground font-normal">（1行に1つ）</span></Label>
                <Textarea
                  value={editConditions}
                  onChange={(e) => setEditConditions(e.target.value)}
                  placeholder="例: 全社員に適用"
                  className="min-h-16"
                />
              </div>
              <div className="space-y-2">
                <Label>具体例 <span className="text-muted-foreground font-normal">（1行に1つ）</span></Label>
                <Textarea
                  value={editExamples}
                  onChange={(e) => setEditExamples(e.target.value)}
                  placeholder="例: 出張旅費: 日帰り日当2,000円"
                  className="min-h-20"
                />
              </div>
              <div className="space-y-2">
                <Label>例外 <span className="text-muted-foreground font-normal">（1行に1つ）</span></Label>
                <Textarea
                  value={editExceptions}
                  onChange={(e) => setEditExceptions(e.target.value)}
                  placeholder="例: 緊急時は口頭承認で対応可"
                  className="min-h-16"
                />
              </div>

              {/* File management in edit mode */}
              <div className="space-y-2 border-t pt-4">
                <Label>ソースファイル</Label>
                {fileLoading ? (
                  <div className="text-xs text-muted-foreground">読み込み中...</div>
                ) : fileInfo ? (
                  <div className="space-y-2">
                    <div className="flex items-center gap-3 rounded-lg border bg-muted/30 px-3 py-2.5">
                      <span className="text-xl shrink-0">{getFileIcon(fileInfo.file_content_type, fileInfo.file_name)}</span>
                      <div className="flex-1 min-w-0">
                        <div className="text-sm font-medium truncate">{fileInfo.file_name}</div>
                        <div className="text-xs text-muted-foreground">{formatFileSize(fileInfo.file_size)}</div>
                      </div>
                      <Button
                        variant="outline"
                        size="sm"
                        className="text-destructive hover:text-destructive hover:bg-destructive/10 shrink-0"
                        onClick={handleFileDelete}
                      >
                        削除
                      </Button>
                    </div>
                    {/* Replace file */}
                    <div
                      className={`rounded-lg border-2 border-dashed px-4 py-3 text-center transition-colors cursor-pointer ${
                        fileDragOver ? "border-primary bg-primary/5" : "border-muted-foreground/25 hover:border-muted-foreground/50"
                      }`}
                      onDragOver={(e) => { e.preventDefault(); setFileDragOver(true); }}
                      onDragLeave={() => setFileDragOver(false)}
                      onDrop={handleFileDrop}
                      onClick={() => fileInputRef.current?.click()}
                    >
                      <p className="text-xs text-muted-foreground">
                        {fileUploading ? "アップロード中..." : "差し替えるファイルをドロップまたはクリック"}
                      </p>
                    </div>
                  </div>
                ) : (
                  <div
                    className={`rounded-lg border-2 border-dashed px-4 py-6 text-center transition-colors cursor-pointer ${
                      fileDragOver ? "border-primary bg-primary/5" : "border-muted-foreground/25 hover:border-muted-foreground/50"
                    }`}
                    onDragOver={(e) => { e.preventDefault(); setFileDragOver(true); }}
                    onDragLeave={() => setFileDragOver(false)}
                    onDrop={handleFileDrop}
                    onClick={() => fileInputRef.current?.click()}
                  >
                    <p className="text-sm text-muted-foreground">
                      {fileUploading ? "アップロード中..." : "ファイルをドロップまたはクリックしてアップロード"}
                    </p>
                    <p className="text-xs text-muted-foreground mt-1">
                      .txt .pdf .xlsx .xls .docx .doc（最大10MB）
                    </p>
                  </div>
                )}
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".txt,.pdf,.xlsx,.xls,.docx,.doc"
                  className="hidden"
                  onChange={handleFileInputChange}
                />
              </div>
            </div>
          ) : (
            /* ---- Detail view ---- */
            <div className="p-6 space-y-5 max-w-2xl">
              {/* Header + actions */}
              <div className="flex items-start justify-between gap-4">
                <h2 className="text-lg font-bold leading-tight">{selected.title}</h2>
                <div className="flex shrink-0 gap-2">
                  <Button variant="outline" size="sm" onClick={startEdit}>編集</Button>
                  <Button variant="outline" size="sm"
                    className="text-destructive hover:text-destructive hover:bg-destructive/10"
                    onClick={() => setDeleteOpen(true)}>
                    削除
                  </Button>
                </div>
              </div>

              {/* Badges */}
              <div className="flex flex-wrap gap-1.5">
                <Badge variant="outline">{ITEM_TYPE_LABELS[selected.item_type] || selected.item_type}</Badge>
                <Badge variant="secondary">{selected.department}</Badge>
                {selected.category && <Badge variant="secondary">{CATEGORY_LABELS[selected.category] || selected.category}</Badge>}
                <Badge variant="secondary">{SOURCE_TYPE_LABELS[selected.source_type] || selected.source_type}</Badge>
                {selected.confidence !== null && (
                  <Badge variant="outline">
                    {selected.confidence >= 0.8 ? "確度：高" : selected.confidence >= 0.5 ? "確度：中" : "参考情報"}
                  </Badge>
                )}
              </div>

              {/* Content */}
              <div className="space-y-1.5">
                <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">内容</h3>
                <p className="text-sm whitespace-pre-wrap leading-relaxed">{selected.content}</p>
              </div>

              {renderArraySection("適用条件", selected.conditions)}
              {renderArraySection("具体例", selected.examples)}
              {renderArraySection("例外", selected.exceptions)}

              {/* Source file */}
              {!fileLoading && fileInfo && (
                <div className="space-y-2">
                  <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">ソースファイル</h3>
                  <div className="flex items-center gap-3 rounded-lg border bg-muted/30 px-3 py-2.5">
                    <span className="text-xl shrink-0">{getFileIcon(fileInfo.file_content_type, fileInfo.file_name)}</span>
                    <div className="flex-1 min-w-0">
                      <div className="text-sm font-medium truncate">{fileInfo.file_name}</div>
                      <div className="text-xs text-muted-foreground">{formatFileSize(fileInfo.file_size)}</div>
                    </div>
                    <a
                      href={fileInfo.download_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="shrink-0"
                    >
                      <Button variant="outline" size="sm">ダウンロード</Button>
                    </a>
                  </div>
                </div>
              )}

              {/* Meta */}
              <div className="border-t pt-4 grid grid-cols-2 gap-y-2 text-xs text-muted-foreground">
                <span>作成日</span>
                <span className="text-right">{new Date(selected.created_at).toLocaleDateString("ja-JP")}</span>
                <span>更新日</span>
                <span className="text-right">{new Date(selected.updated_at).toLocaleDateString("ja-JP")}</span>
                <span>バージョン</span>
                <span className="text-right">v{selected.version}</span>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Shortcut help dialog */}
      <Dialog open={helpOpen} onOpenChange={setHelpOpen}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>キーボードショートカット</DialogTitle>
          </DialogHeader>
          <div className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
            <kbd className="rounded border bg-muted px-1.5 py-0.5 text-xs font-mono">↑</kbd><span>前のナレッジ</span>
            <kbd className="rounded border bg-muted px-1.5 py-0.5 text-xs font-mono">↓</kbd><span>次のナレッジ</span>
            <kbd className="rounded border bg-muted px-1.5 py-0.5 text-xs font-mono">e</kbd><span>編集</span>
            <kbd className="rounded border bg-muted px-1.5 py-0.5 text-xs font-mono">Esc</kbd><span>閉じる / キャンセル</span>
            <kbd className="rounded border bg-muted px-1.5 py-0.5 text-xs font-mono">Cmd+B</kbd><span>リスト表示/非表示</span>
            <kbd className="rounded border bg-muted px-1.5 py-0.5 text-xs font-mono">Cmd+\</kbd><span>サイドバー折りたたみ</span>
            <kbd className="rounded border bg-muted px-1.5 py-0.5 text-xs font-mono">[</kbd><span>すべて折りたたむ</span>
            <kbd className="rounded border bg-muted px-1.5 py-0.5 text-xs font-mono">]</kbd><span>すべて展開</span>
            <kbd className="rounded border bg-muted px-1.5 py-0.5 text-xs font-mono">/</kbd><span>検索にフォーカス</span>
            <kbd className="rounded border bg-muted px-1.5 py-0.5 text-xs font-mono">?</kbd><span>このヘルプ</span>
          </div>
        </DialogContent>
      </Dialog>

      {/* Delete dialog */}
      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent className="sm:max-w-sm w-full mx-2">
          <DialogHeader>
            <DialogTitle>ナレッジを削除しますか？</DialogTitle>
            <DialogDescription>
              「{selected?.title}」を削除します。この操作は管理者が復元できます。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteOpen(false)}>キャンセル</Button>
            <Button variant="destructive" onClick={handleDeactivate} disabled={deleting}>
              {deleting ? "削除中..." : "このナレッジを削除"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 👎 不採用コンテンツ確認ダイアログ */}
      <Dialog open={rejectedConfirmOpen} onOpenChange={setRejectedConfirmOpen}>
        <DialogContent className="sm:max-w-sm w-full mx-2">
          <DialogHeader>
            <DialogTitle>以前「不採用」と判断された内容です</DialogTitle>
            <DialogDescription>
              「{pendingSaveItem?.title}」は過去に不採用と判断されています。
              今回ナレッジに追加しますか？
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="flex-col sm:flex-row gap-2">
            <Button
              variant="outline"
              className="w-full sm:w-auto"
              onClick={() => {
                setRejectedConfirmOpen(false);
                setPendingSaveItem(null);
              }}
            >
              キャンセル
            </Button>
            <Button
              className="w-full sm:w-auto"
              onClick={() => {
                setRejectedConfirmOpen(false);
                pendingSaveItem?.onConfirm();
                setPendingSaveItem(null);
              }}
            >
              ナレッジに追加する
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
