"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useAuth } from "@/hooks/use-auth";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  MANUFACTURING_API_PIPELINE_IDS,
  MVP_PIPELINES,
  type ManufacturingApiPipelineId,
} from "@/lib/bpo-pipeline-catalog";

interface PipelineStep {
  step_no: number;
  step_name: string;
  success: boolean;
  confidence?: number;
  warning?: string | null;
}

interface ManufacturingPipelineResponse {
  success: boolean;
  failed_step?: string | null;
  total_cost_yen?: number;
  total_duration_ms?: number;
  steps: PipelineStep[];
  final_output?: unknown;
}

function isPipelineId(value: string): value is ManufacturingApiPipelineId {
  return (MANUFACTURING_API_PIPELINE_IDS as readonly string[]).includes(value);
}

function defaultInputFor(pipelineId: ManufacturingApiPipelineId): Record<string, unknown> {
  switch (pipelineId) {
    case "production_planning":
      return { order_no: "SO-2026-001", due_date: "2026-04-20", qty: 120, process: ["旋盤", "研磨", "検査"] };
    case "quality_control":
      return { lot_no: "LOT-2026-03A", ng_rate: 0.015, defect_types: ["寸法不良", "外観キズ"] };
    case "inventory_optimization":
      return { sku: "PART-A", lead_time_days: 14, monthly_demand: 320, current_stock: 150 };
    case "sop_management":
      return { process_name: "CNC加工", revision: "1.3", hazards: ["切粉", "高温"] };
    case "equipment_maintenance":
      return { machine_id: "MC-01", runtime_hours: 420, failure_logs: ["異音", "停止"] };
    case "procurement":
      return { bom_id: "BOM-2026-01", demand_qty: 120, suppliers: ["A商事", "B物産"] };
    case "iso_document":
      return { standard: "ISO9001", clause: "8.5.1", audit_date: "2026-05-01" };
    case "quoting":
      return {};
  }
}

export default function ManufacturingPipelineRunPage() {
  const params = useParams<{ pipelineId: string }>();
  const router = useRouter();
  const { session } = useAuth();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ManufacturingPipelineResponse | null>(null);

  const pipelineIdRaw = params.pipelineId ?? "";
  const pipelineId = isPipelineId(pipelineIdRaw) ? pipelineIdRaw : null;

  const pipelineMeta = useMemo(() => {
    if (!pipelineId) return null;
    return MVP_PIPELINES.find((p) => p.key === `manufacturing/${pipelineId}`) ?? null;
  }, [pipelineId]);

  const [inputText, setInputText] = useState(() =>
    pipelineId ? JSON.stringify(defaultInputFor(pipelineId), null, 2) : "{\n  \n}"
  );

  if (!pipelineId) {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-bold">製造業BPO</h1>
        <Card>
          <CardContent className="py-8 text-center text-sm text-muted-foreground">
            無効なパイプラインIDです。
            <br />
            <Link href="/bpo/manufacturing" className="underline">製造BPOに戻る</Link>
          </CardContent>
        </Card>
      </div>
    );
  }

  useEffect(() => {
    if (pipelineId === "quoting") {
      router.replace("/bpo/manufacturing");
    }
  }, [pipelineId, router]);

  if (pipelineId === "quoting") return null;

  async function handleRun() {
    if (!session?.access_token) return;
    setError(null);
    setResult(null);
    let parsed: unknown;
    try {
      parsed = JSON.parse(inputText);
    } catch (e) {
      setError(e instanceof Error ? `JSONパースエラー: ${e.message}` : "JSONパースエラー");
      return;
    }
    setLoading(true);
    try {
      const response = await apiFetch<ManufacturingPipelineResponse>(
        `/bpo/manufacturing/pipelines/${pipelineId}`,
        {
          method: "POST",
          token: session.access_token,
          body: { input_data: parsed, options: {} },
        }
      );
      setResult(response);
    } catch (e) {
      setError(e instanceof Error ? e.message : "実行に失敗しました");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">
            {pipelineMeta?.name ?? pipelineId}
          </h1>
          <p className="text-sm text-muted-foreground">{pipelineMeta?.description}</p>
        </div>
        <Badge variant="secondary">製造BPO</Badge>
      </div>

      {!result && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">実行パラメータ</CardTitle>
            <CardDescription>JSON形式で入力して実行します。</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <Label htmlFor="input-json">入力データ（JSON）</Label>
            <Textarea
              id="input-json"
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              className="min-h-56 font-mono text-sm"
              disabled={loading}
            />
            {error && <p className="text-sm text-destructive">{error}</p>}
            <div className="flex gap-2">
              <Button onClick={handleRun} disabled={loading}>
                {loading ? "実行中..." : "この自動化を実行する"}
              </Button>
              <Button variant="outline" onClick={() => router.push("/bpo/manufacturing")}>
                戻る
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {result && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">
              {result.success ? "実行結果" : "実行失敗"}
            </CardTitle>
            <CardDescription>
              所要時間: {result.total_duration_ms ?? 0}ms / コスト: ¥{Math.round(result.total_cost_yen ?? 0)}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              {result.steps?.map((step) => (
                <div key={`${step.step_no}-${step.step_name}`} className="rounded-md border p-3 text-sm">
                  <div className="flex items-center justify-between">
                    <span>{step.step_no}. {step.step_name}</span>
                    <Badge variant={step.success ? "outline" : "destructive"}>
                      {step.success ? "成功" : "失敗"}
                    </Badge>
                  </div>
                  {step.warning && <p className="mt-1 text-amber-700">{step.warning}</p>}
                </div>
              ))}
            </div>
            <pre className="overflow-auto rounded-md bg-muted p-3 text-xs">
              {JSON.stringify(result.final_output ?? {}, null, 2)}
            </pre>
            <div className="flex gap-2">
              <Button variant="outline" onClick={() => setResult(null)}>再実行する</Button>
              <Button variant="outline" onClick={() => router.push("/bpo/manufacturing")}>製造BPOに戻る</Button>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
