import { describe, expect, it } from "vitest";

import {
  descriptionLooksNested,
  extractJsonFromTextWithMeta,
  extractPreambleBeforeFirstFence,
  getProactiveParseDebugTrace,
  normalizeMarkdownFencesForJson,
  normalizeSmartQuotesForJsonBalance,
  parseProactiveDescription,
  shouldOfferTechnicalDetails,
} from "./parse-proposal-description";

describe("parseProactiveDescription", () => {
  it("parses preamble plus fenced JSON array", () => {
    const raw = `会社状態が未提供です。

\`\`\`json
[{"type":"risk_alert","title":"建設業許可失効リスク","description":"本文です。","impact_estimate":{"time_saved_hours":null,"cost_reduction_yen":null}}]
\`\`\`
`;
    const r = parseProactiveDescription(raw);
    expect(r.kind).toBe("structured");
    if (r.kind === "structured") {
      expect(r.preamble).toContain("未提供");
      expect(r.items).toHaveLength(1);
      expect(r.items[0].title).toBe("建設業許可失効リスク");
      expect(r.items[0].description).toBe("本文です。");
    }
  });

  it("parses trailing-comma JSON in fenced block", () => {
    const raw = `以下は提案です。

\`\`\`json
[
  {
    "type": "improvement",
    "title": "見積もりテンプレート統一",
    "description": "社内テンプレートを統一します。",
  },
]
\`\`\`
`;
    const r = parseProactiveDescription(raw);
    expect(r.kind).toBe("structured");
    if (r.kind === "structured") {
      expect(r.items).toHaveLength(1);
      expect(r.items[0].title).toBe("見積もりテンプレート統一");
    }
  });

  it("accepts description null when type and title exist", () => {
    const raw = `[{"type":"risk_alert","title":"T","description":null,"impact_estimate":{}}]`;
    const r = parseProactiveDescription(raw);
    expect(r.kind).toBe("structured");
    if (r.kind === "structured") {
      expect(r.items[0].title).toBe("T");
      expect(r.items[0].description).toBeUndefined();
    }
  });

  it("does not show technical-details path for Japanese-only backend message", () => {
    const raw =
      "分析結果の形式を自動で解釈できませんでした。ナレッジを整理したうえで、もう一度お試しください。";
    const r = parseProactiveDescription(raw);
    expect(r.kind).toBe("plain");
    expect(shouldOfferTechnicalDetails(r)).toBe(false);
  });

  it("extractPreambleBeforeFirstFence returns text before fence", () => {
    const p = extractPreambleBeforeFirstFence("前置き\n\n```json\n[]\n```");
    expect(p).toContain("前置き");
  });

  it("flattens fenced JSON array inside a single proposal item description", () => {
    const nestedDesc = `ナレッジを分析しました。

\`\`\`json
[{"type":"risk_alert","title":"建設業許可失効リスク","description":"本文です。","impact_estimate":{}}]
\`\`\`
`;
    const raw = JSON.stringify([
      {
        type: "improvement",
        title: "分析結果",
        description: nestedDesc,
      },
    ]);
    const r = parseProactiveDescription(raw);
    expect(r.kind).toBe("structured");
    if (r.kind === "structured") {
      expect(r.items.length).toBe(1);
      expect(r.items[0].type).toBe("risk_alert");
      expect(r.items[0].title).toBe("建設業許可失効リスク");
      expect(r.items[0].description).toContain("本文です");
      expect(r.items[0].description).toContain("ナレッジを分析");
    }
  });

  it("flattens to multiple items when inner JSON array has several entries", () => {
    const nestedDesc = `\`\`\`json
[
  {"type":"risk_alert","title":"A","description":"a"},
  {"type":"improvement","title":"B","description":"b"}
]
\`\`\``;
    const raw = JSON.stringify([
      {
        type: "improvement",
        title: "分析結果",
        description: nestedDesc,
      },
    ]);
    const r = parseProactiveDescription(raw);
    expect(r.kind).toBe("structured");
    if (r.kind === "structured") {
      expect(r.items.length).toBe(2);
      expect(r.items[0].title).toBe("A");
      expect(r.items[1].title).toBe("B");
    }
  });

  it("descriptionLooksNested detects fences and bracket-open text", () => {
    expect(descriptionLooksNested('x\n```json\n[]\n```')).toBe(true);
    expect(descriptionLooksNested('[{"type":"x"}]')).toBe(true);
    expect(descriptionLooksNested("plain only")).toBe(false);
  });

  it("normalizes fullwidth grave U+FF40 to ASCII backticks so fences match", () => {
    const raw = `前置き\n\n\uFF40\uFF40\uFF40json\n[\n  {"type":"risk_alert","title":"T","description":"本文"}\n]\n`;
    expect(normalizeMarkdownFencesForJson(raw)).toContain("```json");
    const r = parseProactiveDescription(raw);
    expect(r.kind).toBe("structured");
    if (r.kind === "structured") {
      expect(r.items[0].title).toBe("T");
    }
  });

  it("parses when markdown closing fence is missing (open fence only)", () => {
    const raw = `会社状態が提供されていないため、ナレッジベースのルールやフローから潜在的なリスク、改善機会、ルールの矛盾、ビジネス機会を検出します。

\`\`\`json
[
  {
    "type": "risk_alert",
    "title": "建設業許可失効リスク",
    "description": "建設業許可は5年ごとの更新が義務付けられており。",
    "impact_estimate": { "time_saved_hours": null, "cost_reduction_yen": null }
  }
]
`;
    const r = parseProactiveDescription(raw);
    expect(r.kind).toBe("structured");
    if (r.kind === "structured") {
      expect(r.items.length).toBeGreaterThanOrEqual(1);
      expect(r.items[0].title).toBe("建設業許可失効リスク");
    }
  });

  it("parses open fence JSON with trailing prose and no closing fence (json5 trailing)", () => {
    const raw = `\`\`\`json
[{"type":"risk_alert","title":"規程照合","description":"本文。","impact_estimate":{}}]
以上は参考です。`;
    const meta = extractJsonFromTextWithMeta(raw);
    expect(["open_fence_then_balanced", "open_fence_then_json5_trailing"]).toContain(meta.meta.path);
    const r = parseProactiveDescription(raw);
    expect(r.kind).toBe("structured");
    if (r.kind === "structured") {
      expect(r.items[0].title).toBe("規程照合");
    }
  });

  it("parses JSON with // line comment after open fence (no closing fence)", () => {
    const raw = `\`\`\`json
[
  // コメント
  {"type":"risk_alert","title":"T","description":"d","impact_estimate":{}}
]
`;
    const meta = extractJsonFromTextWithMeta(raw);
    expect(["open_fence_then_balanced", "open_fence_then_json5_trailing"]).toContain(meta.meta.path);
    const r = parseProactiveDescription(raw);
    expect(r.kind).toBe("structured");
    if (r.kind === "structured") {
      expect(r.items[0].title).toBe("T");
    }
  });

  it("does not use preamble [ for bracket start when fence exists (fixture)", () => {
    const raw = `参照[1]があります。

\`\`\`json
[{"type":"risk_alert","title":"正しい","description":"x","impact_estimate":{}}]
`;
    const meta = extractJsonFromTextWithMeta(raw);
    expect(meta.text.trim().startsWith("[")).toBe(true);
    const r = parseProactiveDescription(raw);
    expect(r.kind).toBe("structured");
    if (r.kind === "structured") {
      expect(r.items[0].title).toBe("正しい");
    }
  });

  it("normalizes smart quotes so balance scan matches string boundaries", () => {
    const withSmart = `{"type":"x","title":\u201c見出し\u201d,"description":"d"}`;
    const norm = normalizeSmartQuotesForJsonBalance(withSmart);
    expect(norm).toContain('"見出し"');
    const r = parseProactiveDescription(`\`\`\`json\n${withSmart}\n\`\`\``);
    expect(r.kind).toBe("structured");
  });

  it("repairs severely truncated JSON after open fence (jsonrepair fallback)", () => {
    const raw = `\`\`\`json
[{"type":"risk_alert","title":"建設","description":"`;
    const meta = extractJsonFromTextWithMeta(raw);
    expect(meta.meta.path).toBe("open_fence_then_json_repair");
    const r = parseProactiveDescription(raw);
    expect(r.kind).toBe("structured");
    if (r.kind === "structured") {
      expect(r.items[0].title).toBe("建設");
    }
  });
});

describe("shouldOfferTechnicalDetails", () => {
  it("is true only for plain parse-failed JSON-looking text", () => {
    const r = parseProactiveDescription(`[{"type":`);
    expect(r.kind).toBe("plain");
    expect(shouldOfferTechnicalDetails(r)).toBe(true);
  });
});

describe("getProactiveParseDebugTrace", () => {
  it("reports fence match and filter count for fenced JSON array", () => {
    const raw = `会社状態が未提供です。

\`\`\`json
[{"type":"risk_alert","title":"建設業許可失効リスク","description":"本文です。","impact_estimate":{"time_saved_hours":null,"cost_reduction_yen":null}}]
\`\`\`
`;
    const t = getProactiveParseDebugTrace(raw);
    expect(t.fenceMatched).toBe(true);
    expect(t.jsonParseStage).toBe("ok");
    expect(t.parsedDataKind).toBe("array");
    expect(t.afterFilterCount).toBeGreaterThanOrEqual(1);
    expect(t.finalParsedKind).toBe("structured");
    expect(t.finalItemsCount).toBeGreaterThanOrEqual(1);
    expect(t.jsonExtraction.path).toBe("closed_fence");
    expect(t.jsonCandidateFirstCharHex).toBe("U+005B"); // '['
    expect(t.jsonCandidateEqualsFullTrimmed).toBe(false);
  });
});
