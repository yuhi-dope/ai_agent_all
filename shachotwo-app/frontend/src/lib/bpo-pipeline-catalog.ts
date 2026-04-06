export interface BpoPipelineDefinition {
  key: string;
  industry: string;
  industryKey: string;
  name: string;
  displayName?: string;
  /** サブナビ用の短いラベル（未指定時は name） */
  navLabel?: string;
  description: string;
  icon: string;
  maturity?: "stable" | "beta";
}

/** /bpo 一覧のクエリ ?tab= と連動 */
export type BpoDashboardTab = "all" | "industry" | "common";

export function bpoDashboardTabFromSearchParams(
  sp: URLSearchParams | null
): BpoDashboardTab {
  const t = sp?.get("tab");
  if (t === "common") return "common";
  if (t === "industry") return "industry";
  return "all";
}

/** カード・バッジの業種キー別スタイル（ライト/ダーク） */
export function industryPipelineBadgeClassName(industryKey: string): string {
  switch (industryKey) {
    case "common":
      return "border-slate-300 bg-slate-100 text-slate-800 dark:border-slate-600 dark:bg-slate-800/80 dark:text-slate-100";
    case "manufacturing":
      return "border-blue-300 bg-blue-50 text-blue-900 dark:border-blue-700 dark:bg-blue-950/60 dark:text-blue-100";
    case "construction":
      return "border-amber-300 bg-amber-50 text-amber-950 dark:border-amber-700 dark:bg-amber-950/50 dark:text-amber-100";
    default:
      return "border-border bg-muted text-muted-foreground";
  }
}

export function getPipelineNavLabel(p: BpoPipelineDefinition): string {
  return p.navLabel ?? p.name;
}

export const MVP_PIPELINES: BpoPipelineDefinition[] = [
  { key: "construction/estimation", industry: "建設業", industryKey: "construction", name: "積算・見積", description: "図面・仕様書から工種・数量を自動積算し見積書を作成します", icon: "🏗️", maturity: "stable" },
  { key: "construction/billing", industry: "建設業", industryKey: "construction", name: "出来高・請求", description: "出来高管理と請求書の自動作成・送付を行います", icon: "📋", maturity: "stable" },
  { key: "construction/safety_docs", industry: "建設業", industryKey: "construction", name: "安全書類", description: "グリーンファイル等の安全書類を自動生成・管理します", icon: "🦺", maturity: "stable" },
  { key: "construction/construction_plan", industry: "建設業", industryKey: "construction", name: "施工計画書AI", description: "工事情報を入力するだけで、施工方針・安全管理計画・品質管理計画などを自動生成します", icon: "📐", maturity: "stable" },
  { key: "manufacturing/quoting", industry: "製造業", industryKey: "manufacturing", name: "見積作成", navLabel: "見積", description: "図面・仕様から原価計算し見積書を自動作成します", icon: "🏭", maturity: "stable" },
  { key: "manufacturing/production_planning", industry: "製造業", industryKey: "manufacturing", name: "生産計画AI", navLabel: "生産計画", description: "受注データから山積み計算・ガントチャート・生産計画書までを支援します", icon: "📅", maturity: "beta" },
  { key: "manufacturing/quality_control", industry: "製造業", industryKey: "manufacturing", name: "品質管理", navLabel: "品質", description: "検査データからSPC計算・不良予兆検知・品質レポート作成を支援します", icon: "✅", maturity: "beta" },
  { key: "manufacturing/inventory_optimization", industry: "製造業", industryKey: "manufacturing", name: "在庫最適化", navLabel: "在庫", description: "ABC分析・安全在庫・発注点の算出を支援します", icon: "📊", maturity: "beta" },
  { key: "manufacturing/sop_management", industry: "製造業", industryKey: "manufacturing", name: "SOP管理", navLabel: "SOP", description: "手順書作成・安全衛生法チェック・改訂管理を支援します", icon: "📑", maturity: "beta" },
  { key: "manufacturing/equipment_maintenance", industry: "製造業", industryKey: "manufacturing", name: "設備保全", navLabel: "設備保全", description: "MTBF/MTTRに基づく保全カレンダー生成を支援します", icon: "🔧", maturity: "beta" },
  { key: "manufacturing/procurement", industry: "製造業", industryKey: "manufacturing", name: "仕入・MRP", navLabel: "仕入", description: "BOM展開・MRP計算・発注書生成を支援します", icon: "📦", maturity: "beta" },
  { key: "manufacturing/iso_document", industry: "製造業", industryKey: "manufacturing", name: "ISO文書管理", navLabel: "ISO", description: "条項チェック・監査チェックリスト生成を支援します", icon: "📋", maturity: "beta" },
  { key: "common/expense", industry: "共通", industryKey: "common", name: "経費精算", description: "領収書OCR・承認フロー・仕訳計上を自動化します", icon: "💴", maturity: "stable" },
  { key: "common/payroll", industry: "共通", industryKey: "common", name: "給与計算", description: "勤怠データから給与計算・明細発行を自動化します", icon: "💰", maturity: "stable" },
  { key: "common/attendance", industry: "共通", industryKey: "common", name: "勤怠管理", description: "打刻データの集計・残業管理・36協定チェックを行います", icon: "⏰", maturity: "stable" },
  { key: "common/contract", industry: "共通", industryKey: "common", name: "契約管理", description: "契約書の作成・電子署名・更新期限管理を自動化します", icon: "📝", maturity: "stable" },
];

/** サブナビ用: 指定業種の MVP パイプライン（MVP_PIPELINES の出現順） */
export function getBpoSubNavPipelinesForIndustry(
  industryKey: string
): BpoPipelineDefinition[] {
  return MVP_PIPELINES.filter((p) => p.industryKey === industryKey);
}

export const PIPELINE_CONFIDENCE: Record<string, number> = {
  "construction/estimation": 0.85,
  "construction/billing": 0.82,
  "construction/safety_docs": 0.78,
  "construction/construction_plan": 0.76,
  "manufacturing/quoting": 0.8,
  "manufacturing/production_planning": 0.65,
  "manufacturing/quality_control": 0.65,
  "manufacturing/inventory_optimization": 0.65,
  "manufacturing/sop_management": 0.65,
  "manufacturing/equipment_maintenance": 0.65,
  "manufacturing/procurement": 0.65,
  "manufacturing/iso_document": 0.65,
  "common/expense": 0.83,
  "common/payroll": 0.8,
  "common/attendance": 0.88,
  "common/contract": 0.75,
};

export const PIPELINE_CARD_ROUTES: Record<string, string> = {
  "construction/estimation": "/bpo/estimation",
  "construction/construction_plan": "/bpo/construction-plan",
  "manufacturing/quoting": "/bpo/manufacturing",
};

export const MANUFACTURING_API_PIPELINE_IDS = [
  "quoting",
  "production_planning",
  "quality_control",
  "inventory_optimization",
  "sop_management",
  "equipment_maintenance",
  "procurement",
  "iso_document",
] as const;

export type ManufacturingApiPipelineId = (typeof MANUFACTURING_API_PIPELINE_IDS)[number];

export function manufacturingKeyToApiId(key: string): ManufacturingApiPipelineId | null {
  if (!key.startsWith("manufacturing/")) return null;
  const id = key.slice("manufacturing/".length);
  return (MANUFACTURING_API_PIPELINE_IDS as readonly string[]).includes(id)
    ? (id as ManufacturingApiPipelineId)
    : null;
}

export function getPipelineCardHref(pipelineKey: string): string {
  const dedicated = PIPELINE_CARD_ROUTES[pipelineKey];
  if (dedicated) return dedicated;
  const apiId = manufacturingKeyToApiId(pipelineKey);
  if (apiId && apiId !== "quoting") {
    return `/bpo/manufacturing/pipelines/${apiId}/run`;
  }
  return `/bpo/run?pipeline=${encodeURIComponent(pipelineKey)}`;
}

// ---------- 構造化フォーム用型定義 ----------

export type InputFieldType = "text" | "textarea" | "number" | "select" | "month";

export interface InputField {
  key: string;
  label: string;
  type: InputFieldType;
  placeholder: string;
  hint?: string;
  required: boolean;
  options?: { value: string; label: string }[];
}

export interface RunPagePipelineMeta {
  name: string;
  industry: string;
  description: string;
  icon: string;
  sampleInput: Record<string, unknown>;
  inputSchema?: InputField[];
}

export const RUN_PAGE_PIPELINE_META: Record<string, RunPagePipelineMeta> = {
  "construction/estimation": {
    name: "積算・見積",
    industry: "建設業",
    description: "図面・仕様書から工種・数量を自動積算し見積書を作成します",
    icon: "🏗️",
    sampleInput: { project_name: "〇〇ビル新築工事", document_text: "RC造3階建て、延床面積500m2。基礎工事、躯体工事、仕上げ工事を含む。" },
    inputSchema: [
      { key: "project_name", label: "工事名", type: "text", placeholder: "〇〇ビル新築工事", required: true },
      { key: "document_text", label: "仕様・数量内容", type: "textarea", placeholder: "RC造3階建て、延床面積500m2。基礎工事（コンクリート打設300m3）、鉄筋工事（25t）...", hint: "仕様書・設計図の内容をそのまま貼り付けてください。手書きメモでもOK。", required: true },
      {
        key: "region", label: "施工地域", type: "select", placeholder: "選択してください", required: false,
        options: [
          { value: "東京都", label: "東京都" }, { value: "神奈川県", label: "神奈川県" },
          { value: "埼玉県", label: "埼玉県" }, { value: "千葉県", label: "千葉県" },
          { value: "大阪府", label: "大阪府" }, { value: "愛知県", label: "愛知県" },
          { value: "福岡県", label: "福岡県" }, { value: "その他", label: "その他" },
        ],
      },
    ],
  },
  "construction/billing": {
    name: "出来高・請求",
    industry: "建設業",
    description: "出来高管理と請求書の自動作成・送付を行います",
    icon: "📋",
    sampleInput: { project_id: "PRJ-001", billing_month: "2026-03", progress_rate: 0.6 },
    inputSchema: [
      { key: "project_id", label: "案件ID", type: "text", placeholder: "PRJ-001", hint: "案件管理画面のIDを入力", required: true },
      { key: "billing_month", label: "請求月", type: "month", placeholder: "2026-03", required: true },
      { key: "progress_rate", label: "出来高進捗率", type: "number", placeholder: "0.6", hint: "0〜1の範囲で入力（例: 60%完了なら 0.6）", required: true },
    ],
  },
  "construction/safety_docs": {
    name: "安全書類",
    industry: "建設業",
    description: "グリーンファイル等の安全書類を自動生成・管理します",
    icon: "🦺",
    sampleInput: { site_name: "〇〇現場", company_name: "株式会社サンプル建設", workers: ["山田太郎", "鈴木一郎"] },
    inputSchema: [
      { key: "site_name", label: "現場名", type: "text", placeholder: "〇〇ビル改修現場", required: true },
      { key: "company_name", label: "会社名", type: "text", placeholder: "株式会社サンプル建設", required: true },
      { key: "workers_text", label: "作業員名簿", type: "textarea", placeholder: "山田太郎（職長）\n鈴木一郎\n田中花子", hint: "1行1名で入力。（職長）など役職を括弧で付けてください。", required: true },
      {
        key: "doc_type", label: "書類種別", type: "select", placeholder: "選択してください", required: true,
        options: [
          { value: "作業員名簿", label: "作業員名簿" },
          { value: "安全衛生計画書", label: "安全衛生計画書" },
          { value: "持込機械等使用届", label: "持込機械等使用届" },
          { value: "新規入場者教育実施報告書", label: "新規入場者教育実施報告書" },
          { value: "施工体制台帳", label: "施工体制台帳" },
        ],
      },
    ],
  },
  "construction/construction_plan": {
    name: "施工計画書AI",
    industry: "建設業",
    description: "工事情報を入力するだけで、施工方針・安全管理計画・品質管理計画などを自動生成します",
    icon: "📐",
    sampleInput: { project_name: "サンプル工事", site_address: "東京都港区", work_description: "内装改修工事" },
    inputSchema: [
      { key: "project_name", label: "工事名", type: "text", placeholder: "〇〇ビル新築工事", required: true },
      { key: "site_address", label: "工事場所", type: "text", placeholder: "東京都港区〇〇1-2-3", required: true },
      { key: "work_description", label: "工事内容", type: "textarea", placeholder: "鉄骨造4階建て事務所ビルの新築工事。基礎・躯体・外装・内装・設備を含む。", required: true },
      { key: "duration_months", label: "工期（ヶ月）", type: "number", placeholder: "12", required: false },
    ],
  },
  "common/expense": {
    name: "経費精算",
    industry: "共通",
    description: "領収書OCR・承認フロー・仕訳計上を自動化します",
    icon: "💴",
    sampleInput: { applicant: "山田太郎", application_date: "2026-03-20", items: [{ description: "交通費（東京→大阪）", amount: 14000 }, { description: "接待費", amount: 25000 }] },
    inputSchema: [
      { key: "applicant", label: "申請者名", type: "text", placeholder: "山田太郎", required: true },
      { key: "application_date", label: "申請日", type: "text", placeholder: "2026-04-01", required: true },
      { key: "items_text", label: "経費明細", type: "textarea", placeholder: "交通費（東京→大阪）: 14,000円\n接待費（〇〇社）: 25,000円\n宿泊費: 12,000円", hint: "1行1件、「内容: 金額」の形式で入力してください。", required: true },
    ],
  },
  "common/payroll": {
    name: "給与計算",
    industry: "共通",
    description: "勤怠データから給与計算・明細発行を自動化します",
    icon: "💰",
    sampleInput: { target_month: "2026-03", employees_count: 20 },
    inputSchema: [
      { key: "target_month", label: "対象月", type: "month", placeholder: "2026-03", required: true },
      { key: "employees_count", label: "対象社員数", type: "number", placeholder: "20", hint: "空欄の場合は全社員が対象", required: false },
    ],
  },
  "common/attendance": {
    name: "勤怠管理",
    industry: "共通",
    description: "打刻データの集計・残業管理・36協定チェックを行います",
    icon: "⏰",
    sampleInput: { target_month: "2026-03", department: "営業部" },
    inputSchema: [
      { key: "target_month", label: "集計月", type: "month", placeholder: "2026-03", required: true },
      { key: "department", label: "部門名", type: "text", placeholder: "営業部", hint: "空欄の場合は全部門を集計します", required: false },
    ],
  },
  "common/contract": {
    name: "契約管理",
    industry: "共通",
    description: "契約書の作成・電子署名・更新期限管理を自動化します",
    icon: "📝",
    sampleInput: { contract_type: "業務委託契約", party_a: "株式会社ABC", party_b: "株式会社XYZ", start_date: "2026-04-01", amount: 500000 },
    inputSchema: [
      {
        key: "contract_type", label: "契約種別", type: "select", placeholder: "選択してください", required: true,
        options: [
          { value: "業務委託契約", label: "業務委託契約" },
          { value: "売買契約", label: "売買契約" },
          { value: "賃貸借契約", label: "賃貸借契約" },
          { value: "秘密保持契約（NDA）", label: "秘密保持契約（NDA）" },
          { value: "雇用契約", label: "雇用契約" },
        ],
      },
      { key: "party_a", label: "甲（発注側）", type: "text", placeholder: "株式会社ABC", required: true },
      { key: "party_b", label: "乙（受注側）", type: "text", placeholder: "株式会社XYZ", required: true },
      { key: "start_date", label: "契約開始日", type: "text", placeholder: "2026-04-01", required: true },
      { key: "amount", label: "契約金額（円）", type: "number", placeholder: "500000", required: false },
    ],
  },
  "manufacturing/quoting": {
    name: "見積作成",
    industry: "製造業",
    description: "図面・仕様から原価計算し見積書を自動作成します",
    icon: "🏭",
    sampleInput: { product_name: "精密部品A", quantity: 100, spec_text: "材質: SUS304\n寸法: 100×50×10mm" },
    inputSchema: [
      { key: "product_name", label: "製品・部品名", type: "text", placeholder: "精密部品A", required: true },
      { key: "quantity", label: "見積数量", type: "number", placeholder: "100", required: true },
      { key: "spec_text", label: "図面・仕様", type: "textarea", placeholder: "材質: SUS304\n寸法: 100×50×10mm\n表面処理: バフ研磨\n公差: ±0.1mm", hint: "図面の内容をテキストで貼り付けてください。CADデータは後日対応予定。", required: true },
      { key: "delivery_days", label: "希望納期（日数）", type: "number", placeholder: "30", required: false },
    ],
  },
};
