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
  status: string;
}

interface CostRecord {
  id: string;
  contract_id: string;
  cost_type: string;
  description: string;
  amount: number;
  vendor_name: string | null;
  record_date: string;
}

const costTypeLabels: Record<string, string> = {
  material: "材料費",
  labor: "労務費",
  subcontract: "外注費",
  equipment: "機械経費",
  overhead: "現場経費",
};

function formatAmount(amount: number): string {
  return new Intl.NumberFormat("ja-JP").format(amount);
}

function profitRateColor(rate: number): string {
  if (rate >= 15) return "text-green-600";
  if (rate >= 5) return "text-yellow-600";
  return "text-red-600";
}

export default function CostsPage() {
  const { session } = useAuth();
  const [contracts, setContracts] = useState<Contract[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedContract, setSelectedContract] = useState<string | null>(null);
  const [costRecords, setCostRecords] = useState<Record<string, CostRecord[]>>({});

  // 原価登録フォーム
  const [showForm, setShowForm] = useState(false);
  const [formError, setFormError] = useState("");
  const [formData, setFormData] = useState({
    contract_id: "",
    cost_type: "material",
    record_date: new Date().toISOString().slice(0, 10),
    description: "",
    amount: "",
    vendor_name: "",
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

  async function loadCostRecords(contractId: string) {
    const token = session?.access_token;
    if (!token) return;
    try {
      const report = await apiFetch<{ costs: CostRecord[] }>(
        `/bpo/construction/costs/report/${contractId}`,
        { token }
      );
      setCostRecords((prev) => ({ ...prev, [contractId]: report.costs || [] }));
    } catch {
      setCostRecords((prev) => ({ ...prev, [contractId]: [] }));
    }
  }

  function handleSelectContract(contractId: string) {
    if (selectedContract === contractId) {
      setSelectedContract(null);
    } else {
      setSelectedContract(contractId);
      if (!costRecords[contractId]) {
        loadCostRecords(contractId);
      }
    }
  }

  function getTotalCost(contractId: string): number {
    return (costRecords[contractId] || []).reduce((sum, r) => sum + r.amount, 0);
  }

  function getCostByType(contractId: string): Record<string, number> {
    const result: Record<string, number> = {};
    for (const r of costRecords[contractId] || []) {
      result[r.cost_type] = (result[r.cost_type] || 0) + r.amount;
    }
    return result;
  }

  async function handleCreateCost() {
    const token = session?.access_token;
    if (!token || !formData.contract_id) return;
    const amount = parseInt(formData.amount, 10);
    if (!amount) {
      setFormError("金額を入力してください");
      return;
    }
    setFormError("");
    try {
      await apiFetch("/bpo/construction/costs", {
        method: "POST",
        token,
        body: {
          contract_id: formData.contract_id,
          cost_type: formData.cost_type,
          record_date: formData.record_date,
          description: formData.description,
          amount,
          vendor_name: formData.vendor_name || null,
        },
      });
      setShowForm(false);
      setFormData({
        contract_id: "", cost_type: "material",
        record_date: new Date().toISOString().slice(0, 10),
        description: "", amount: "", vendor_name: "",
      });
      if (formData.contract_id) loadCostRecords(formData.contract_id);
    } catch {
      setFormError("原価登録に失敗しました。しばらく経ってから再度お試しください");
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">原価管理</h1>
        <Button onClick={() => setShowForm(!showForm)}>
          {showForm ? "キャンセル" : "原価を登録"}
        </Button>
      </div>

      {showForm && (
        <Card>
          <CardHeader><CardTitle className="text-lg">原価登録</CardTitle></CardHeader>
          <CardContent className="space-y-3">
            <div>
              <Label>工事</Label>
              <select
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
                value={formData.contract_id}
                onChange={(e) => setFormData({ ...formData, contract_id: e.target.value })}
              >
                <option value="">選択してください</option>
                {contracts.map((c) => (
                  <option key={c.id} value={c.id}>{c.project_name}</option>
                ))}
              </select>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label>原価区分</Label>
                <select
                  className="flex h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
                  value={formData.cost_type}
                  onChange={(e) => setFormData({ ...formData, cost_type: e.target.value })}
                >
                  {Object.entries(costTypeLabels).map(([key, label]) => (
                    <option key={key} value={key}>{label}</option>
                  ))}
                </select>
              </div>
              <div><Label>発生日</Label><Input type="date" value={formData.record_date} onChange={(e) => setFormData({ ...formData, record_date: e.target.value })} /></div>
            </div>
            <div><Label>内容</Label><Input value={formData.description} onChange={(e) => setFormData({ ...formData, description: e.target.value })} placeholder="例: 鉄筋材料費" /></div>
            <div className="grid grid-cols-2 gap-3">
              <div><Label>金額（円）</Label><Input type="number" value={formData.amount} onChange={(e) => setFormData({ ...formData, amount: e.target.value })} /></div>
              <div><Label>仕入先</Label><Input value={formData.vendor_name} onChange={(e) => setFormData({ ...formData, vendor_name: e.target.value })} /></div>
            </div>
            {formError && <p className="text-sm text-destructive">{formError}</p>}
            <Button onClick={handleCreateCost} disabled={!formData.contract_id || !formData.description || !formData.amount}>
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
            工事台帳がありません。「出来高・請求」タブから工事台帳を登録してください。
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-3">
          {contracts.map((c) => {
            const totalCost = getTotalCost(c.id);
            const profit = c.contract_amount - totalCost;
            const profitRate = c.contract_amount > 0 ? (profit / c.contract_amount) * 100 : 0;
            const isSelected = selectedContract === c.id;
            const byType = getCostByType(c.id);

            return (
              <Card key={c.id} className="cursor-pointer" onClick={() => handleSelectContract(c.id)}>
                <CardContent className="py-4">
                  <div className="flex items-center justify-between">
                    <div className="space-y-1">
                      <p className="font-medium">{c.project_name}</p>
                      <p className="text-sm text-muted-foreground">{c.client_name}</p>
                    </div>
                    <div className="text-right space-y-1">
                      <p className="text-sm">契約: ¥{formatAmount(c.contract_amount)}</p>
                      {costRecords[c.id] && (
                        <>
                          <p className="text-sm">原価: ¥{formatAmount(totalCost)}</p>
                          <p className={`text-sm font-medium ${profitRateColor(profitRate)}`}>
                            利益率: {profitRate.toFixed(1)}%
                          </p>
                        </>
                      )}
                    </div>
                  </div>

                  {isSelected && costRecords[c.id] && (
                    <div className="mt-4 border-t pt-4">
                      <p className="text-sm font-medium mb-2">原価内訳</p>
                      {Object.keys(byType).length === 0 ? (
                        <p className="text-sm text-muted-foreground">原価データがありません。</p>
                      ) : (
                        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                          {Object.entries(byType).map(([type, amount]) => (
                            <div key={type} className="rounded-md border p-2">
                              <p className="text-xs text-muted-foreground">{costTypeLabels[type] || type}</p>
                              <p className="text-sm font-medium">¥{formatAmount(amount)}</p>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
