"use client";

import Link from "next/link";
import {
  Users,
  Calculator,
  FileText,
  Scale,
  ArrowRight,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

// ---------- カード定義 ----------

const PROFESSIONAL_CARDS = [
  {
    key: "labor",
    title: "社会保険労務士",
    description: "社会保険・雇用保険の手続き書類を自動生成します",
    firstPipeline: "手続き書類の自動生成",
    icon: Users,
    href: "/bpo/professional/labor",
    color: "text-blue-600",
    bgColor: "bg-blue-50",
    available: true,
  },
  {
    key: "tax",
    title: "税理士",
    description: "仕訳データのミスや不整合を自動でチェックします",
    firstPipeline: "記帳の自動チェック",
    icon: Calculator,
    href: "/bpo/professional/tax",
    color: "text-green-600",
    bgColor: "bg-green-50",
    available: true,
  },
  {
    key: "legal",
    title: "行政書士",
    description: "建設業許可・一般貨物など各種申請書を自動生成します",
    firstPipeline: "許可申請書の自動生成",
    icon: FileText,
    href: "/bpo/professional/legal",
    color: "text-amber-600",
    bgColor: "bg-amber-50",
    available: true,
  },
  {
    key: "attorney",
    title: "弁護士",
    description: "契約書のリスクを自動分析し、修正提案を提示します",
    firstPipeline: "契約書のリスクレビュー",
    icon: Scale,
    href: "/bpo/professional/attorney",
    color: "text-purple-600",
    bgColor: "bg-purple-50",
    available: true,
  },
];

// ---------- Page ----------

export default function ProfessionalBPOPage() {
  return (
    <div className="space-y-6">
      {/* ヘッダー */}
      <div>
        <h1 className="text-2xl font-bold">士業サポート</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          社労士・税理士・行政書士・弁護士の専門業務をAIが補助します。
          書類作成・チェック・レビューを自動化し、専門家への依頼コストを削減します。
        </p>
      </div>

      {/* 4業種カード */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-2">
        {PROFESSIONAL_CARDS.map((card) => {
          const Icon = card.icon;
          return (
            <Card
              key={card.key}
              className="transition-shadow hover:shadow-md"
            >
              <CardHeader className="pb-3">
                <div className="flex items-start justify-between gap-3">
                  <div
                    className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg ${card.bgColor}`}
                  >
                    <Icon className={`h-5 w-5 ${card.color}`} />
                  </div>
                  {card.available ? (
                    <Badge className="bg-green-100 text-green-800 shrink-0">
                      利用可能
                    </Badge>
                  ) : (
                    <Badge className="bg-yellow-100 text-yellow-800 shrink-0">
                      準備中
                    </Badge>
                  )}
                </div>
                <div className="mt-2">
                  <CardTitle className="text-base">{card.title}</CardTitle>
                  <CardDescription className="mt-1 text-sm">
                    {card.description}
                  </CardDescription>
                </div>
              </CardHeader>
              <CardContent className="pt-0">
                <div className="mb-4 rounded-md bg-muted px-3 py-2">
                  <p className="text-xs text-muted-foreground">主な業務</p>
                  <p className="text-sm font-medium">{card.firstPipeline}</p>
                </div>
                <Link href={card.href}>
                  <Button
                    className="w-full"
                    disabled={!card.available}
                    size="lg"
                  >
                    開始する
                    <ArrowRight className="ml-2 h-4 w-4" />
                  </Button>
                </Link>
              </CardContent>
            </Card>
          );
        })}
      </div>

      {/* 注意事項 */}
      <Card className="border-amber-200 bg-amber-50">
        <CardContent className="py-4">
          <p className="text-sm text-amber-800">
            <span className="font-semibold">ご注意：</span>
            AIが生成した書類・チェック結果は参考情報です。
            実際の申請・提出の際は、必ず担当の専門家（社労士・税理士・行政書士・弁護士）に最終確認を依頼してください。
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
