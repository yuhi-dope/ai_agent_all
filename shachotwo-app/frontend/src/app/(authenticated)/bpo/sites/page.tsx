"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/hooks/use-auth";
import { apiFetch } from "@/lib/api";

interface Site {
  id: string;
  name: string;
  address: string | null;
  client_name: string | null;
  status: string;
}

const statusLabels: Record<string, string> = {
  planning: "計画中",
  active: "稼働中",
  completed: "完了",
};

export default function SitesPage() {
  const { session } = useAuth();
  const [sites, setSites] = useState<Site[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [formData, setFormData] = useState({ name: "", address: "", client_name: "" });

  useEffect(() => {
    if (session?.access_token) loadSites();
  }, [session?.access_token]);

  async function loadSites() {
    const token = session?.access_token;
    if (!token) return;
    try {
      const data = await apiFetch<Site[]>("/bpo/construction/sites", { token });
      setSites(Array.isArray(data) ? data : []);
    } catch {
      setSites([]);
    } finally {
      setLoading(false);
    }
  }

  async function handleCreate() {
    const token = session?.access_token;
    if (!token) return;
    try {
      await apiFetch("/bpo/construction/sites", { method: "POST", body: formData, token });
      setShowForm(false);
      setFormData({ name: "", address: "", client_name: "" });
      loadSites();
    } catch {
      alert("現場登録に失敗しました");
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">現場管理</h1>
        <Button onClick={() => setShowForm(!showForm)}>{showForm ? "キャンセル" : "新規現場登録"}</Button>
      </div>

      {showForm && (
        <Card>
          <CardContent className="space-y-3 pt-4">
            <div><Label>現場名</Label><Input value={formData.name} onChange={(e) => setFormData({ ...formData, name: e.target.value })} /></div>
            <div><Label>住所</Label><Input value={formData.address} onChange={(e) => setFormData({ ...formData, address: e.target.value })} /></div>
            <div><Label>元請/発注者</Label><Input value={formData.client_name} onChange={(e) => setFormData({ ...formData, client_name: e.target.value })} /></div>
            <Button onClick={handleCreate} disabled={!formData.name}>登録</Button>
          </CardContent>
        </Card>
      )}

      {loading ? (
        <p className="text-muted-foreground">読み込み中...</p>
      ) : sites.length === 0 ? (
        <Card><CardContent className="py-12 text-center text-muted-foreground">現場が登録されていません。</CardContent></Card>
      ) : (
        <div className="space-y-3">
          {sites.map((site) => (
            <Card key={site.id}>
              <CardContent className="flex items-center justify-between py-4">
                <div>
                  <p className="font-medium">{site.name}</p>
                  <p className="text-sm text-muted-foreground">{site.address || "住所未設定"}{site.client_name && ` / ${site.client_name}`}</p>
                </div>
                <Badge variant="outline">{statusLabels[site.status] || site.status}</Badge>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
