"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { apiClient } from "@/lib/api";

interface Worker {
  id: string;
  last_name: string;
  first_name: string;
  experience_years: number | null;
  health_check_date: string | null;
  status: string;
}

export default function WorkersPage() {
  const [workers, setWorkers] = useState<Worker[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [formData, setFormData] = useState({ last_name: "", first_name: "" });
  const [expiringQuals, setExpiringQuals] = useState<any[]>([]);

  useEffect(() => {
    loadData();
  }, []);

  async function loadData() {
    try {
      const [w, eq] = await Promise.all([
        apiClient("/bpo/construction/workers").catch(() => []),
        apiClient("/bpo/construction/workers/expiring-qualifications?days_ahead=90").catch(() => []),
      ]);
      setWorkers(Array.isArray(w) ? w : []);
      setExpiringQuals(Array.isArray(eq) ? eq : []);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }

  async function handleCreate() {
    try {
      await apiClient("/bpo/construction/workers", {
        method: "POST",
        body: JSON.stringify(formData),
      });
      setShowForm(false);
      setFormData({ last_name: "", first_name: "" });
      loadData();
    } catch {
      alert("作業員登録に失敗しました");
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">作業員管理</h1>
        <Button onClick={() => setShowForm(!showForm)}>
          {showForm ? "キャンセル" : "新規登録"}
        </Button>
      </div>

      {/* 資格期限アラート */}
      {expiringQuals.length > 0 && (
        <Card className="border-red-300 bg-red-50">
          <CardContent className="py-3">
            <p className="font-medium text-red-700">
              資格期限アラート（90日以内）: {expiringQuals.length}件
            </p>
            <ul className="mt-2 space-y-1 text-sm text-red-600">
              {expiringQuals.slice(0, 5).map((q, i) => (
                <li key={i}>
                  {q.worker_name} — {q.qualification_name}（残り{q.days_until_expiry}日）
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

      {showForm && (
        <Card>
          <CardContent className="space-y-3 pt-4">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label>姓</Label>
                <Input value={formData.last_name} onChange={(e) => setFormData({ ...formData, last_name: e.target.value })} />
              </div>
              <div>
                <Label>名</Label>
                <Input value={formData.first_name} onChange={(e) => setFormData({ ...formData, first_name: e.target.value })} />
              </div>
            </div>
            <Button onClick={handleCreate} disabled={!formData.last_name || !formData.first_name}>登録</Button>
          </CardContent>
        </Card>
      )}

      {loading ? (
        <p className="text-muted-foreground">読み込み中...</p>
      ) : workers.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center text-muted-foreground">
            作業員が登録されていません。
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-2">
          {workers.map((w) => (
            <Card key={w.id}>
              <CardContent className="flex items-center justify-between py-3">
                <div>
                  <p className="font-medium">{w.last_name} {w.first_name}</p>
                  <p className="text-sm text-muted-foreground">
                    {w.experience_years ? `経験${w.experience_years}年` : ""}
                    {w.health_check_date && ` / 健診: ${w.health_check_date}`}
                  </p>
                </div>
                <Badge variant="outline">稼働中</Badge>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
