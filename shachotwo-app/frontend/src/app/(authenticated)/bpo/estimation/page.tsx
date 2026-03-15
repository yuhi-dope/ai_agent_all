"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { apiFetch } from "@/lib/api";

interface EstimationProject {
  id: string;
  name: string;
  project_type: string;
  region: string;
  client_name: string | null;
  estimated_amount: number | null;
  status: string;
  created_at: string;
}

const statusLabels: Record<string, { label: string; variant: "default" | "secondary" | "outline" | "destructive" }> = {
  draft: { label: "下書き", variant: "secondary" },
  in_progress: { label: "作業中", variant: "default" },
  review: { label: "レビュー", variant: "outline" },
  submitted: { label: "提出済", variant: "default" },
  won: { label: "受注", variant: "default" },
  lost: { label: "失注", variant: "destructive" },
};

const typeLabels: Record<string, string> = {
  public_civil: "公共土木",
  public_building: "公共建築",
  private_civil: "民間土木",
  private_building: "民間建築",
};

export default function EstimationListPage() {
  const [projects, setProjects] = useState<EstimationProject[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function load() {
      try {
        const data = await apiFetch<EstimationProject[]>("/bpo/construction/estimation/projects");
        setProjects(Array.isArray(data) ? data : []);
      } catch {
        setProjects([]);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">積算プロジェクト</h1>
        <Link href="/bpo/estimation/new">
          <Button>新規積算</Button>
        </Link>
      </div>

      {loading ? (
        <p className="text-muted-foreground">読み込み中...</p>
      ) : projects.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center">
            <p className="text-muted-foreground">
              積算プロジェクトがありません。「新規積算」から始めてください。
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-3">
          {projects.map((p) => {
            const st = statusLabels[p.status] || { label: p.status, variant: "secondary" as const };
            return (
              <Card key={p.id} className="cursor-pointer transition-colors hover:bg-accent/50">
                <CardContent className="flex items-center justify-between py-4">
                  <div className="space-y-1">
                    <p className="font-medium">{p.name}</p>
                    <p className="text-sm text-muted-foreground">
                      {typeLabels[p.project_type] || p.project_type} / {p.region}
                      {p.client_name && ` / ${p.client_name}`}
                    </p>
                  </div>
                  <div className="flex items-center gap-3">
                    {p.estimated_amount && (
                      <span className="text-lg font-semibold">
                        ¥{p.estimated_amount.toLocaleString()}
                      </span>
                    )}
                    <Badge variant={st.variant}>{st.label}</Badge>
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
