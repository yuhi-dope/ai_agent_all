/**
 * Company search API route.
 * Searches gBizINFO (primary) and National Tax Agency (fallback) APIs.
 */
import { NextRequest, NextResponse } from "next/server";

interface CompanyResult {
  corporate_number: string;
  name: string;
  location: string;
  postal_code?: string;
  status?: string;
  source: "gbiz" | "nta";
}

// --- gBizINFO ---

interface GbizCorporation {
  corporate_number?: string;
  name?: string;
  location?: string;
  postal_code?: string;
  status?: string;
}

interface GbizResponse {
  "hojin-infos"?: GbizCorporation[];
  totalCount?: number;
}

async function searchGbiz(query: string): Promise<CompanyResult[]> {
  const token = process.env.GBIZ_API_TOKEN;
  if (!token) return [];

  const url = new URL("https://info.gbiz.go.jp/hojin/v1/hojin");
  url.searchParams.set("name", query);
  url.searchParams.set("page", "1");
  url.searchParams.set("limit", "10");

  const res = await fetch(url.toString(), {
    headers: { "X-hojinInfo-api-token": token },
  });

  if (!res.ok) return [];

  const data: GbizResponse = await res.json();
  const items = data["hojin-infos"] ?? [];

  return items.map((item) => ({
    corporate_number: item.corporate_number ?? "",
    name: item.name ?? "",
    location: item.location ?? "",
    postal_code: item.postal_code,
    status: item.status,
    source: "gbiz" as const,
  }));
}

// --- National Tax Agency (XML) ---

async function searchNta(query: string): Promise<CompanyResult[]> {
  const appId = process.env.NTA_API_ID;
  if (!appId) return [];

  const url = new URL("https://api.houjin-bangou.nta.go.jp/4/name");
  url.searchParams.set("id", appId);
  url.searchParams.set("name", query);
  url.searchParams.set("type", "12"); // XML
  url.searchParams.set("mode", "2"); // partial match
  url.searchParams.set("target", "1"); // fuzzy
  url.searchParams.set("history", "0");
  url.searchParams.set("close", "0"); // exclude closed

  const res = await fetch(url.toString());
  if (!res.ok) return [];

  const xml = await res.text();
  return parseNtaXml(xml);
}

function parseNtaXml(xml: string): CompanyResult[] {
  const results: CompanyResult[] = [];
  // Extract <corporation> blocks
  const corpRegex = /<corporation>([\s\S]*?)<\/corporation>/g;
  let match;
  while ((match = corpRegex.exec(xml)) !== null) {
    const block = match[1];
    const getTag = (tag: string) => {
      const m = block.match(new RegExp(`<${tag}>([^<]*)</${tag}>`));
      return m ? m[1].trim() : "";
    };
    const number = getTag("corporateNumber");
    const name = getTag("name");
    const prefecture = getTag("prefectureName");
    const city = getTag("cityName");
    const street = getTag("streetNumber");
    if (name) {
      results.push({
        corporate_number: number,
        name,
        location: [prefecture, city, street].filter(Boolean).join(""),
        source: "nta",
      });
    }
  }
  return results.slice(0, 10);
}

// --- Route Handler ---

export async function GET(request: NextRequest) {
  const q = request.nextUrl.searchParams.get("q")?.trim();
  if (!q || q.length < 2) {
    return NextResponse.json(
      { error: "検索キーワードは2文字以上入力してください" },
      { status: 400 }
    );
  }

  // Try gBizINFO first, fall back to NTA
  let results = await searchGbiz(q);
  if (results.length === 0) {
    results = await searchNta(q);
  }

  return NextResponse.json({ results, total: results.length });
}
