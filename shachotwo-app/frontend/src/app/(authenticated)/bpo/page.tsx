"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { apiFetch } from "@/lib/api";

export default function BPODashboardPage() {
  const [stats, setStats] = useState({
    estimationCount: 0,
    activeSites: 0,
    workerCount: 0,
    expiringQualifications: 0,
  });

  useEffect(() => {
    async function loadStats() {
      try {
        const [projects, sites, workers, expiring] = await Promise.all([
          apiFetch<unknown[]>("/bpo/construction/estimation/projects").catch(() => []),
          apiFetch<unknown[]>("/bpo/construction/sites", { params: { status: "active" } }).catch(() => []),
          apiFetch<unknown[]>("/bpo/construction/workers").catch(() => []),
          apiFetch<unknown[]>("/bpo/construction/workers/expiring-qualifications", { params: { days_ahead: "90" } }).catch(() => []),
        ]);
        setStats({
          estimationCount: Array.isArray(projects) ? projects.length : 0,
          activeSites: Array.isArray(sites) ? sites.length : 0,
          workerCount: Array.isArray(workers) ? workers.length : 0,
          expiringQualifications: Array.isArray(expiring) ? expiring.length : 0,
        });
      } catch {
        // API未接続時は初期値のまま
      }
    }
    loadStats();
  }, []);

  const cards = [
    { title: "積算プロジェクト", value: stats.estimationCount, unit: "件" },
    { title: "稼働中の現場", value: stats.activeSites, unit: "現場" },
    { title: "登録作業員", value: stats.workerCount, unit: "名" },
    {
      title: "資格期限アラート",
      value: stats.expiringQualifications,
      unit: "件",
      alert: stats.expiringQualifications > 0,
    },
  ];

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">建設業BPO</h1>

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        {cards.map((card) => (
          <Card key={card.title} className={card.alert ? "border-red-300" : ""}>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                {card.title}
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-3xl font-bold">
                {card.value}
                <span className="ml-1 text-base font-normal text-muted-foreground">
                  {card.unit}
                </span>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
