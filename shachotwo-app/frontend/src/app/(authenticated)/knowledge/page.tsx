"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/hooks/use-auth";
import { apiFetch } from "@/lib/api";
import type { PaginatedResponse } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface KnowledgeItem {
  id: string;
  department: string;
  category: string;
  item_type: string;
  title: string;
  content: string;
  conditions: unknown;
  examples: unknown;
  exceptions: unknown;
  source_type: string;
  confidence: number | null;
  version: number;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DEPARTMENTS = [
  "営業", "経理", "総務", "製造", "品質管理", "人事",
  "情報システム", "経営企画", "現場管理", "安全管理",
] as const;

const ITEM_TYPE_LABELS: Record<string, string> = {
  rule: "ルール",
  flow: "フロー",
  decision_logic: "判断基準",
  fact: "事実",
  tip: "ノウハウ",
};

const SOURCE_TYPE_LABELS: Record<string, string> = {
  template: "テンプレート",
  manual: "手動入力",
  extracted: "自動抽出",
  inferred: "推論",
};

const PAGE_SIZE = 20;

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function KnowledgeListPage() {
  const { session } = useAuth();

  // List state
  const [items, setItems] = useState<KnowledgeItem[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Filter state
  const [search, setSearch] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [department, setDepartment] = useState("");

  // Edit dialog state
  const [editItem, setEditItem] = useState<KnowledgeItem | null>(null);
  const [editOpen, setEditOpen] = useState(false);
  const [editTitle, setEditTitle] = useState("");
  const [editContent, setEditContent] = useState("");
  const [editSaving, setEditSaving] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);

  // Delete (deactivate) state
  const [deleteItem, setDeleteItem] = useState<KnowledgeItem | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);

  // Fetch items
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

  useEffect(() => {
    fetchItems();
  }, [fetchItems]);

  // Search handler
  function handleSearch(e: React.FormEvent) {
    e.preventDefault();
    setOffset(0);
    setSearch(searchInput);
  }

  // Open edit dialog
  function openEdit(item: KnowledgeItem) {
    setEditItem(item);
    setEditTitle(item.title);
    setEditContent(item.content);
    setEditError(null);
    setEditOpen(true);
  }

  // Save edit
  async function handleSave() {
    if (!editItem || !session?.access_token) return;
    setEditSaving(true);
    setEditError(null);

    try {
      const updated = await apiFetch<KnowledgeItem>(
        `/knowledge/items/${editItem.id}`,
        {
          method: "PATCH",
          token: session.access_token,
          body: {
            title: editTitle,
            content: editContent,
            version: editItem.version,
          },
        }
      );
      // Update local list
      setItems((prev) =>
        prev.map((it) => (it.id === updated.id ? updated : it))
      );
      setEditOpen(false);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "更新に失敗しました";
      if (msg.includes("VERSION_CONFLICT")) {
        setEditError("他のユーザーが先に更新しました。ページを再読み込みしてください。");
      } else {
        setEditError(msg);
      }
    } finally {
      setEditSaving(false);
    }
  }

  // Deactivate (soft delete)
  function openDelete(item: KnowledgeItem) {
    setDeleteItem(item);
    setDeleteOpen(true);
  }

  async function handleDeactivate() {
    if (!deleteItem || !session?.access_token) return;
    setDeleting(true);

    try {
      await apiFetch<KnowledgeItem>(
        `/knowledge/items/${deleteItem.id}`,
        {
          method: "PATCH",
          token: session.access_token,
          body: {
            is_active: false,
            version: deleteItem.version,
          },
        }
      );
      // Remove from local list
      setItems((prev) => prev.filter((it) => it.id !== deleteItem.id));
      setTotal((prev) => prev - 1);
      setDeleteOpen(false);
    } catch {
      // Silently close — item may already be gone
      setDeleteOpen(false);
    } finally {
      setDeleting(false);
    }
  }

  const totalPages = Math.ceil(total / PAGE_SIZE);
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1;

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">ナレッジ一覧</h1>
          <p className="text-muted-foreground">
            登録済みのナレッジを検索・編集できます。（{total}件）
          </p>
        </div>
      </div>

      {/* Filters */}
      <Card>
        <CardContent className="pt-6">
          <form onSubmit={handleSearch} className="flex flex-wrap items-end gap-3">
            <div className="flex-1 min-w-[200px] space-y-1">
              <Label htmlFor="search">キーワード検索</Label>
              <Input
                id="search"
                placeholder="タイトルまたは内容で検索..."
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
              />
            </div>
            <div className="w-40 space-y-1">
              <Label htmlFor="dept-filter">部署</Label>
              <select
                id="dept-filter"
                value={department}
                onChange={(e) => {
                  setDepartment(e.target.value);
                  setOffset(0);
                }}
                className="h-8 w-full rounded-lg border border-input bg-transparent px-2.5 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
              >
                <option value="">全部署</option>
                {DEPARTMENTS.map((d) => (
                  <option key={d} value={d}>{d}</option>
                ))}
              </select>
            </div>
            <Button type="submit" variant="secondary" size="sm">
              検索
            </Button>
            {(search || department) && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => {
                  setSearch("");
                  setSearchInput("");
                  setDepartment("");
                  setOffset(0);
                }}
              >
                クリア
              </Button>
            )}
          </form>
        </CardContent>
      </Card>

      {/* Error */}
      {error && (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="flex items-center justify-center py-12">
          <div className="h-6 w-6 animate-spin rounded-full border-2 border-primary border-t-transparent" />
        </div>
      )}

      {/* Items */}
      {!loading && items.length === 0 && (
        <Card>
          <CardContent className="py-12 text-center text-muted-foreground">
            ナレッジが見つかりませんでした。
          </CardContent>
        </Card>
      )}

      {!loading && items.length > 0 && (
        <div className="space-y-3">
          {items.map((item) => (
            <Card key={item.id} className="group">
              <CardContent className="pt-5 pb-4">
                <div className="flex items-start justify-between gap-4">
                  <div className="flex-1 min-w-0 space-y-2">
                    <div className="flex items-center gap-2 flex-wrap">
                      <h3 className="font-medium">{item.title}</h3>
                      <Badge variant="outline" className="text-xs">
                        {ITEM_TYPE_LABELS[item.item_type] || item.item_type}
                      </Badge>
                      <Badge variant="secondary" className="text-xs">
                        {item.department}
                      </Badge>
                      {item.category && (
                        <Badge variant="secondary" className="text-xs">
                          {item.category}
                        </Badge>
                      )}
                      <span className="text-xs text-muted-foreground">
                        {SOURCE_TYPE_LABELS[item.source_type] || item.source_type}
                      </span>
                    </div>
                    <p className="text-sm text-muted-foreground line-clamp-2 whitespace-pre-wrap">
                      {item.content}
                    </p>
                    {item.confidence !== null && (
                      <span className="text-xs text-muted-foreground">
                        信頼度: {Math.round(item.confidence * 100)}%
                      </span>
                    )}
                  </div>
                  <div className="flex shrink-0 gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => openEdit(item)}
                    >
                      編集
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="text-destructive hover:text-destructive"
                      onClick={() => openDelete(item)}
                    >
                      削除
                    </Button>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between">
          <p className="text-sm text-muted-foreground">
            {offset + 1}-{Math.min(offset + PAGE_SIZE, total)} / {total}件
          </p>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={offset === 0}
              onClick={() => setOffset((prev) => Math.max(0, prev - PAGE_SIZE))}
            >
              前へ
            </Button>
            <span className="flex items-center text-sm text-muted-foreground px-2">
              {currentPage} / {totalPages}
            </span>
            <Button
              variant="outline"
              size="sm"
              disabled={offset + PAGE_SIZE >= total}
              onClick={() => setOffset((prev) => prev + PAGE_SIZE)}
            >
              次へ
            </Button>
          </div>
        </div>
      )}

      {/* Edit Dialog */}
      <Dialog open={editOpen} onOpenChange={setEditOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>ナレッジを編集</DialogTitle>
            <DialogDescription>
              タイトルと内容を変更できます。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <Label htmlFor="edit-title">タイトル</Label>
              <Input
                id="edit-title"
                value={editTitle}
                onChange={(e) => setEditTitle(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="edit-content">内容</Label>
              <Textarea
                id="edit-content"
                value={editContent}
                onChange={(e) => setEditContent(e.target.value)}
                className="min-h-40"
              />
            </div>
            {editError && (
              <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
                {editError}
              </div>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditOpen(false)}>
              キャンセル
            </Button>
            <Button
              onClick={handleSave}
              disabled={editSaving || !editTitle.trim() || !editContent.trim()}
            >
              {editSaving ? "保存中..." : "保存"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation Dialog */}
      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>ナレッジを削除</DialogTitle>
            <DialogDescription>
              「{deleteItem?.title}」を削除しますか？この操作は元に戻せます（管理者が復元可能）。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteOpen(false)}>
              キャンセル
            </Button>
            <Button
              variant="destructive"
              onClick={handleDeactivate}
              disabled={deleting}
            >
              {deleting ? "削除中..." : "削除する"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
