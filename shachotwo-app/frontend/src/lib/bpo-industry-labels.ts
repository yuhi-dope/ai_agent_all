/** onboarding / companies.industry のキー → 画面表示用の短い日本語名 */
const INDUSTRY_KEY_TO_LABEL: Record<string, string> = {
  construction: "建設業",
  manufacturing: "製造業",
  common: "共通",
};

export function getBpoIndustryDisplayLabel(industryKey: string | null): string | null {
  if (!industryKey) return null;
  return INDUSTRY_KEY_TO_LABEL[industryKey] ?? null;
}
