"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/hooks/use-auth";
import { apiFetch } from "@/lib/api";

interface Contract {
  id: string;
  project_name: string;
  client_name: string;
  contract_amount: number;
  contract_date: string | null;
  start_date: string | null;
  completion_date: string | null;
  billing_type: string;
  status: string;
}

const statusLabels: Record<string, string> = {
  active: "進行中",
  completed: "完了",
  cancelled: "キャンセル",
};

const statusVariant: Record<string, "default" | "secondary" | "outline" | "destructive"> = {
  active: "default",
  completed: "secondary",
  cancelled: "destructive",
};

function formatAmount(amount: number): string {
  return new Intl.NumberFormat("ja-JP").format(amount);
}

export default function ContractsPage() {
  const { session } = useAuth();
  const [contracts, setContracts] = useState<Contract[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [formData, setFormData] = useState({
    project_name: "",
    client_name: "",
    contract_amount: "",
    contract_date: "",
    start_date: "",
    completion_date: "",
    items: [{ name: "工事一式", amount: 0, unit: "式" }],
  });

  useEffect(() => {
    if (session?.access_token) loadContracts();
  }, [session?.access_token]);

  async function loadContracts() {
    const token = session?.access_token;
    if (!token) return;
    try {
      const data = await apiFetch<Contract[]>("/bpo/construction/contracts", { token });
      setContracts(Array.isArray(data) ? data : []);
    } catch {
      setContracts([]);
    } finally {
      setLoading(false);
    }
  }

  async function handleCreate() {
    const token = session?.access_token;
    if (!token) return;
    const amount = parseInt(formData.contract_amount, 10);
    if (!amount) return alert("契約金額を入力してください");
    try {
      await apiFetch("/bpo/construction/contracts", {
        method: "POST",
        token,
        body: {
          project_name: formData.project_name,
          client_name: formData.client_name,
          contract_amount: amount,
          contract_date: formData.contract_date || null,
          start_date: formData.start_date || null,
          completion_date: formData.completion_date || null,
          items: [{ name: "工事一式", amount, unit: "式" }],
        },
      });
      setShowForm(false);
      setFormData({
        project_name: "", client_name: "", contract_amount: "",
        contract_date: "", start_date: "", completion_date: "",
        items: [{ name: "工事一式", amount: 0, unit: "式" }],
      });
      loadContracts();
    } catch {
      alert("工事台帳の登録に失敗しました");
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">出来高・請求</h1>
        <Button onClick={() => setShowForm(!showForm)}>
          {showForm ? "キャンセル" : "新規工事台帳"}
        </Button>
      </div>

      {showForm && (
        <Card>
          <CardHeader><CardTitle className="text-lg">工事台帳登録</CardTitle></CardHeader>
          <CardContent className="space-y-3">
            <div><Label>工事名</Label><Input value={formData.project_name} onChange={(e) => setFormData({ ...formData, project_name: e.target.value })} /></div>
            <div><Label>発注者/元請名</Label><Input value={formData.client_name} onChange={(e) => setFormData({ ...formData, client_name: e.target.value })} /></div>
            <div><Label>契約金額（税抜・円）</Label><Input type="number" value={formData.contract_amount} onChange={(e) => setFormData({ ...formData, contract_amount: e.target.value })} /></div>
            <div className="grid grid-cols-3 gap-3">
              <div><Label>契約日</Label><Input type="date" value={formData.contract_date} onChange={(e) => setFormData({ ...formData, contract_date: e.target.value })} /></div>
              <div><Label>着工日</Label><Input type="date" value={formData.start_date} onChange={(e) => setFormData({ ...formData, start_date: e.target.value })} /></div>
              <div><Label>竣工予定日</Label><Input type="date" value={formData.completion_date} onChange={(e) => setFormData({ ...formData, completion_date: e.target.value })} /></div>
            </div>
            <Button onClick={handleCreate} disabled={!formData.project_name || !formData.client_name || !formData.contract_amount}>
              登録
            </Button>
          </CardContent>
        </Card>
      )}

      {loading ? (
        <p className="text-muted-foreground">読み込み中...</p>
      ) : contracts.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center text-muted-foreground">
            工事台帳がありません。「新規工事台帳」から登録してください。
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-3">
          {contracts.map((c) => (
            <Card key={c.id}>
              <CardContent className="flex items-center justify-between py-4">
                <div className="space-y-1">
                  <p className="font-medium">{c.project_name}</p>
                  <p className="text-sm text-muted-foreground">
                    {c.client_name}
                    {c.start_date && ` / ${c.start_date}`}
                    {c.completion_date && ` 〜 ${c.completion_date}`}
                  </p>
                </div>
                <div className="flex items-center gap-3">
                  <span className="text-sm font-medium">¥{formatAmount(c.contract_amount)}</span>
                  <Badge variant={statusVariant[c.status] || "outline"}>
                    {statusLabels[c.status] || c.status}
                  </Badge>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
