import JSON5 from "json5";
import { jsonrepair } from "jsonrepair";

/**
 * 能動提案の description に埋め込まれた JSON（フェイルオーバー全文や ```json ブロック）を
 * 画面表示用にパースする。brain/proactive/parsing.py の extract_json と同等の抽出を行う。
 */

export type ParsedProposalItem = {
  type?: string;
  title?: string;
  description?: string;
  priority?: string;
  impact_estimate?: Record<string, unknown>;
};

export type ParsedDescription =
  | { kind: "structured"; items: ParsedProposalItem[]; preamble?: string }
  | { kind: "plain"; text: string; parseFailed?: boolean };

function isRecord(x: unknown): x is Record<string, unknown> {
  return typeof x === "object" && x !== null && !Array.isArray(x);
}

/**
 * LLM が全角バッククォート U+FF40（｀）を ```json に使うと ASCII のフェンス正規表現にマッチしない。
 * 開きフェンス検出・括弧抽出の前に必ず通す。
 */
export function normalizeMarkdownFencesForJson(text: string): string {
  return text.replace(/\uFEFF/g, "").replace(/\uFF40/g, "`");
}

/** 先頭の ```json フェンスより前の説明文（LLMが前置きを付ける場合） */
export function extractPreambleBeforeFirstFence(text: string): string {
  const normalized = normalizeMarkdownFencesForJson(text);
  const idx = normalized.search(/```(?:json)?/i);
  if (idx <= 0) return "";
  return normalized.slice(0, idx).trim();
}

/**
 * LLM が JSON 区切りに使うべき ASCII `"` の代わりにスマートクォートを出すと、
 * 括弧バランス走査が文字列境界を誤認する。走査前に ASCII へ寄せる。
 */
export function normalizeSmartQuotesForJsonBalance(text: string): string {
  return text
    .replace(/\u201c/g, '"')
    .replace(/\u201d/g, '"')
    .replace(/\u201e/g, '"')
    .replace(/\u201f/g, '"')
    .replace(/\u2033/g, '"')
    .replace(/\u2036/g, '"');
}

/**
 * `[` / `{` から、文字列内（ダブル／シングル JSON5）と行コメント・ブロックコメントを無視して
 * 最初のバランスした JSON 断片を取り出す。
 */
function extractFirstBalancedJsonValue(text: string): string | null {
  const t = normalizeSmartQuotesForJsonBalance(text).trim();
  const br = t.indexOf("[");
  const ob = t.indexOf("{");
  if (br === -1 && ob === -1) return null;
  const candidates = [br, ob].filter((i) => i >= 0);
  const start = Math.min(...candidates);

  const stack: string[] = [];
  let inDouble = false;
  let inSingle = false;
  let escape = false;
  let inLineComment = false;
  let inBlockComment = false;

  for (let i = start; i < t.length; i++) {
    const c = t[i];
    if (inLineComment) {
      if (c === "\n" || c === "\r") inLineComment = false;
      continue;
    }
    if (inBlockComment) {
      if (c === "*" && t[i + 1] === "/") {
        inBlockComment = false;
        i++;
      }
      continue;
    }
    if (escape) {
      escape = false;
      continue;
    }
    if (inDouble) {
      if (c === "\\") {
        escape = true;
        continue;
      }
      if (c === '"') {
        inDouble = false;
        continue;
      }
      continue;
    }
    if (inSingle) {
      if (c === "\\") {
        escape = true;
        continue;
      }
      if (c === "'") {
        inSingle = false;
        continue;
      }
      continue;
    }
    if (c === "/" && t[i + 1] === "/") {
      inLineComment = true;
      i++;
      continue;
    }
    if (c === "/" && t[i + 1] === "*") {
      inBlockComment = true;
      i++;
      continue;
    }
    if (c === '"') {
      inDouble = true;
      continue;
    }
    if (c === "'") {
      inSingle = true;
      continue;
    }
    if (c === "[") {
      stack.push("]");
    } else if (c === "{") {
      stack.push("}");
    } else if (c === "]" || c === "}") {
      if (stack.length > 0 && stack[stack.length - 1] === c) {
        stack.pop();
        if (stack.length === 0) {
          return t.slice(start, i + 1);
        }
      }
    }
  }
  return null;
}

/**
 * 閉じ括弧が欠ける／バランス走査が壊れるケース向けに、末尾の `]` / `}` 候補ごとに JSON5.parse を試す。
 */
function tryExtractJsonByTrailingJson5Parse(text: string): string | null {
  const t = normalizeSmartQuotesForJsonBalance(text).trim();
  const br = t.indexOf("[");
  const ob = t.indexOf("{");
  if (br === -1 && ob === -1) return null;
  const start = Math.min(...[br, ob].filter((i) => i >= 0));
  const open = t[start];
  const close = open === "[" ? "]" : "}";
  for (let i = t.length - 1; i > start; i--) {
    if (t[i] !== close) continue;
    const slice = t.slice(start, i + 1);
    try {
      JSON5.parse(slice);
      return slice;
    } catch {
      continue;
    }
  }
  return null;
}

/** 先頭の `[` または `{` から末尾まで（LLM が途中で切れた JSON 修復用） */
function sliceFromFirstJsonBracket(text: string): string | null {
  const t = normalizeSmartQuotesForJsonBalance(text).trim();
  const br = t.indexOf("[");
  const ob = t.indexOf("{");
  if (br === -1 && ob === -1) return null;
  const start = Math.min(...[br, ob].filter((i) => i >= 0));
  return t.slice(start);
}

/**
 * jsonrepair で欠けた括弧・不正なカンマ等を直し、JSON5 で検証できた文字列だけ返す。
 */
function tryExtractJsonByJsonRepair(text: string): string | null {
  const candidate = sliceFromFirstJsonBracket(text);
  if (!candidate) return null;
  try {
    const repaired = jsonrepair(candidate);
    const data: unknown = JSON5.parse(repaired);
    if (data === null || typeof data !== "object") return null;
    if (Array.isArray(data) && data.length === 0) return null;
    if (isRecord(data) && Object.keys(data).length === 0) return null;
    return repaired;
  } catch {
    return null;
  }
}

/** 最初の ``` / ```json の直後（閉じフェンスなしでも可） */
function sliceAfterFenceStart(trimmed: string): string | null {
  const m = /```(?:json)?\s*\n?/i.exec(trimmed);
  if (!m) return null;
  return trimmed.slice(m.index + m[0].length);
}

/** `extractJsonFromText` がどの分岐で切り出したか（デバッグ用） */
export type JsonExtractionPath =
  | "closed_fence"
  | "open_fence_then_balanced"
  | "open_fence_then_json5_trailing"
  | "open_fence_then_json_repair"
  | "full_text_balanced"
  | "full_text_json5_trailing"
  | "full_text_json_repair"
  | "naive_bracket_depth"
  | "fallback_full_trimmed";

export type JsonExtractionMeta = {
  path: JsonExtractionPath;
  closedFenceMatched: boolean;
  openFenceSliceLength: number | null;
  balancedAfterOpenOk: boolean | null;
  balancedFullTextOk: boolean | null;
  naiveDepthUsed: boolean;
};

function countCodeUnits(s: string, code: number): number {
  let n = 0;
  for (let i = 0; i < s.length; i++) {
    if (s.charCodeAt(i) === code) n++;
  }
  return n;
}

/** 正規化後テキストで最初の ``` 付近のコードポイント列（フェンスが別文字に見える問題の切り分け用） */
export function fenceRegionCodepointsHex(trimmed: string, span = 14): string {
  const idx = trimmed.search(/```(?:json)?/i);
  if (idx === -1) return "(no ``` in normalized text)";
  const part = trimmed.slice(idx, Math.min(trimmed.length, idx + span));
  return [...part]
    .map((ch) => "U+" + ch.charCodeAt(0).toString(16).toUpperCase().padStart(4, "0"))
    .join(" ");
}

/** LLM 応答から JSON 部分を抽出（閉じフェンス優先・開きフェンス＋括弧・全文フォールバック） */
export function extractJsonFromTextWithMeta(text: string): { text: string; meta: JsonExtractionMeta } {
  const trimmed = normalizeMarkdownFencesForJson(text).trim();
  const closedFence = /```(?:json)?\s*\n?([\s\S]*?)\n?```/.exec(trimmed);
  if (closedFence) {
    return {
      text: closedFence[1].trim(),
      meta: {
        path: "closed_fence",
        closedFenceMatched: true,
        openFenceSliceLength: null,
        balancedAfterOpenOk: null,
        balancedFullTextOk: null,
        naiveDepthUsed: false,
      },
    };
  }

  const afterOpen = sliceAfterFenceStart(trimmed);
  let balancedAfterOpenOk: boolean | null = null;

  if (afterOpen !== null) {
    const fromFence = extractFirstBalancedJsonValue(afterOpen);
    balancedAfterOpenOk = fromFence !== null;
    if (fromFence) {
      return {
        text: fromFence,
        meta: {
          path: "open_fence_then_balanced",
          closedFenceMatched: false,
          openFenceSliceLength: afterOpen.length,
          balancedAfterOpenOk: true,
          balancedFullTextOk: null,
          naiveDepthUsed: false,
        },
      };
    }
    const trailFence = tryExtractJsonByTrailingJson5Parse(afterOpen);
    if (trailFence) {
      return {
        text: trailFence,
        meta: {
          path: "open_fence_then_json5_trailing",
          closedFenceMatched: false,
          openFenceSliceLength: afterOpen.length,
          balancedAfterOpenOk: false,
          balancedFullTextOk: null,
          naiveDepthUsed: false,
        },
      };
    }
    const repairFence = tryExtractJsonByJsonRepair(afterOpen);
    if (repairFence) {
      return {
        text: repairFence,
        meta: {
          path: "open_fence_then_json_repair",
          closedFenceMatched: false,
          openFenceSliceLength: afterOpen.length,
          balancedAfterOpenOk: false,
          balancedFullTextOk: null,
          naiveDepthUsed: false,
        },
      };
    }
  }

  if (afterOpen === null) {
    const balancedBody = extractFirstBalancedJsonValue(trimmed);
    if (balancedBody) {
      return {
        text: balancedBody,
        meta: {
          path: "full_text_balanced",
          closedFenceMatched: false,
          openFenceSliceLength: null,
          balancedAfterOpenOk: null,
          balancedFullTextOk: true,
          naiveDepthUsed: false,
        },
      };
    }
    const trailFull = tryExtractJsonByTrailingJson5Parse(trimmed);
    if (trailFull) {
      return {
        text: trailFull,
        meta: {
          path: "full_text_json5_trailing",
          closedFenceMatched: false,
          openFenceSliceLength: null,
          balancedAfterOpenOk: null,
          balancedFullTextOk: false,
          naiveDepthUsed: false,
        },
      };
    }
    const repairFull = tryExtractJsonByJsonRepair(trimmed);
    if (repairFull) {
      return {
        text: repairFull,
        meta: {
          path: "full_text_json_repair",
          closedFenceMatched: false,
          openFenceSliceLength: null,
          balancedAfterOpenOk: null,
          balancedFullTextOk: false,
          naiveDepthUsed: false,
        },
      };
    }
  }

  const naiveSource = afterOpen !== null ? afterOpen : trimmed;
  for (const [startChar, endChar] of [
    ["[", "]"],
    ["{", "}"],
  ] as const) {
    const start = naiveSource.indexOf(startChar);
    if (start === -1) continue;
    let depth = 0;
    for (let i = start; i < naiveSource.length; i++) {
      const c = naiveSource[i];
      if (c === startChar) depth++;
      else if (c === endChar) {
        depth--;
        if (depth === 0) {
          return {
            text: naiveSource.slice(start, i + 1),
            meta: {
              path: "naive_bracket_depth",
              closedFenceMatched: false,
              openFenceSliceLength: afterOpen?.length ?? null,
              balancedAfterOpenOk,
              balancedFullTextOk: false,
              naiveDepthUsed: true,
            },
          };
        }
      }
    }
  }

  return {
    text: trimmed,
    meta: {
      path: "fallback_full_trimmed",
      closedFenceMatched: false,
      openFenceSliceLength: afterOpen?.length ?? null,
      balancedAfterOpenOk,
      balancedFullTextOk: false,
      naiveDepthUsed: false,
    },
  };
}

export function extractJsonFromText(text: string): string {
  return extractJsonFromTextWithMeta(text).text;
}

function optStr(v: unknown): string | undefined {
  if (typeof v === "string") return v;
  if (typeof v === "number" && Number.isFinite(v)) return String(v);
  return undefined;
}

function parseLenientJson(raw: string): unknown {
  try {
    return JSON.parse(raw);
  } catch {
    return JSON5.parse(raw);
  }
}

function normalizeItem(raw: Record<string, unknown>): ParsedProposalItem {
  const desc = raw.description;
  let description: string | undefined;
  if (typeof desc === "string") description = desc;
  else if (desc === null || desc === undefined) description = undefined;

  return {
    type: optStr(raw.type),
    title: optStr(raw.title),
    description,
    priority: optStr(raw.priority),
    impact_estimate: isRecord(raw.impact_estimate) ? raw.impact_estimate : undefined,
  };
}

/** LLM のゆらぎを許容し、表示に使える行かどうか */
function couldBeProposalItem(raw: Record<string, unknown>): boolean {
  const n = normalizeItem(raw);
  const hasImpact =
    n.impact_estimate != null && Object.keys(n.impact_estimate).length > 0;
  return !!(n.type || n.title || n.description || hasImpact);
}

const DEFAULT_PARSE_DEPTH = 2;

/** description にネストした ```json や生の配列が含まれるか（フラット化の候補） */
export function descriptionLooksNested(desc: string | undefined): boolean {
  if (!desc) return false;
  const t = normalizeMarkdownFencesForJson(desc).trim();
  if (!t) return false;
  if (t.includes("```")) return true;
  if (t.startsWith("[") || t.startsWith("{")) return true;
  return false;
}

function mergePreambleIntoFirstItem(
  items: ParsedProposalItem[],
  preamble: string | undefined
): ParsedProposalItem[] {
  if (!preamble?.trim() || items.length === 0) return items;
  const first = items[0];
  const mergedDesc = [preamble.trim(), first.description?.trim()].filter(Boolean).join("\n\n");
  return [{ ...first, description: mergedDesc || first.description }, ...items.slice(1)];
}

/**
 * 各 item の description に埋め込まれた JSON 配列を再パースし、1件を複数件に展開する。
 */
function flattenNestedItems(items: ParsedProposalItem[], depth: number): ParsedProposalItem[] {
  if (depth <= 0) return items;
  const out: ParsedProposalItem[] = [];
  for (const item of items) {
    const desc = item.description;
    if (!descriptionLooksNested(desc)) {
      out.push(item);
      continue;
    }
    const inner = parseProactiveDescriptionWithDepth(desc!, depth - 1);
    if (inner.kind === "structured" && inner.items.length > 0) {
      const merged = mergePreambleIntoFirstItem(inner.items, inner.preamble);
      out.push(...merged);
    } else {
      out.push(item);
    }
  }
  return out;
}

function parseProactiveDescriptionWithDepth(raw: string, depth: number): ParsedDescription {
  const trimmed = normalizeMarkdownFencesForJson(raw).trim();
  if (!trimmed) return { kind: "plain", text: "" };

  const preamble = extractPreambleBeforeFirstFence(trimmed);
  const jsonCandidate = normalizeSmartQuotesForJsonBalance(extractJsonFromText(trimmed));
  let data: unknown;
  try {
    data = parseLenientJson(jsonCandidate);
  } catch {
    try {
      data = parseLenientJson(trimmed);
    } catch {
      return { kind: "plain", text: raw, parseFailed: true };
    }
  }

  if (Array.isArray(data)) {
    const items = data.filter(isRecord).filter(couldBeProposalItem).map(normalizeItem);
    if (items.length > 0) {
      return {
        kind: "structured",
        items: flattenNestedItems(items, depth),
        ...(preamble ? { preamble } : {}),
      };
    }
    return { kind: "plain", text: raw, parseFailed: true };
  }

  if (isRecord(data) && couldBeProposalItem(data)) {
    return {
      kind: "structured",
      items: flattenNestedItems([normalizeItem(data)], depth),
      ...(preamble ? { preamble } : {}),
    };
  }

  return { kind: "plain", text: raw, parseFailed: true };
}

/**
 * description 文字列を解釈し、構造化できれば items（任意で preamble）、できなければプレーンテキスト。
 */
export function parseProactiveDescription(raw: string): ParsedDescription {
  return parseProactiveDescriptionWithDepth(raw, DEFAULT_PARSE_DEPTH);
}

/** プレーン表示で「技術的な詳細」折りたたみを出すか（先頭が JSON のみのとき） */
export function shouldOfferTechnicalDetails(parsed: ParsedDescription): boolean {
  if (parsed.kind === "structured") return false;
  const t = parsed.text.trim();
  if (!t) return false;
  if (parsed.parseFailed && (t.startsWith("[") || t.startsWith("{"))) return true;
  return false;
}

const TRACE_PREVIEW_MAX = 200;

function clipTracePreview(s: string, max = TRACE_PREVIEW_MAX): string {
  if (s.length <= max) return s;
  return `${s.slice(0, max)}…`;
}

/** 閉じフェンスあり、または ``` / ```json が含まれるか（デバッグ用） */
function fenceMatchedInText(trimmed: string): boolean {
  if (/```(?:json)?\s*\n?([\s\S]*?)\n?```/.exec(trimmed) !== null) return true;
  return /```(?:json)?/i.test(trimmed);
}

function firstCharCodepointHex(s: string): string {
  if (s.length === 0) return "(empty)";
  const ch = s[0];
  return "U+" + ch.charCodeAt(0).toString(16).toUpperCase().padStart(4, "0");
}

/**
 * 開発用: `parseProactiveDescription` がどこで落ちたか追うためのメタデータ（副作用なし）。
 */
export type ProactiveParseDebugTrace = {
  rawInputLength: number;
  /** 正規化前の生文字列に含まれる全角バッククォート U+FF40 の個数 */
  fullwidthGraveCountInRaw: number;
  trimmedLength: number;
  trimmedPreview: string;
  preambleLength: number;
  fenceMatched: boolean;
  /** 正規化後テキストで最初の ``` 付近のコードポイント（フェンス文字の切り分け） */
  fenceRegionHex: string;
  jsonExtraction: JsonExtractionMeta;
  jsonCandidateLength: number;
  jsonCandidatePreview: string;
  /** jsonCandidate の先頭1文字のコードポイント（「会」なら日本語のまま切り出せていない） */
  jsonCandidateFirstCharHex: string;
  /** 抽出に失敗して全文がそのまま JSON 候補になっているか */
  jsonCandidateEqualsFullTrimmed: boolean;
  jsonParseStage: "ok" | "fail_candidate_then_ok" | "fail_both";
  jsonParseError?: string;
  parsedDataKind: "array" | "object" | "other" | "parse_failed";
  rawArrayLength?: number;
  recordCountAfterIsRecord?: number;
  afterFilterCount?: number;
  singleObjectAccepted?: boolean;
  finalParsedKind: "structured" | "plain";
  finalItemsCount: number;
  finalParseFailed?: boolean;
};

export function getProactiveParseDebugTrace(raw: string): ProactiveParseDebugTrace {
  const fullwidthGraveCountInRaw = countCodeUnits(raw, 0xff40);
  const trimmed = normalizeMarkdownFencesForJson(raw).trim();
  const preamble = extractPreambleBeforeFirstFence(trimmed);
  const base = {
    rawInputLength: raw.length,
    fullwidthGraveCountInRaw,
    trimmedLength: trimmed.length,
    trimmedPreview: clipTracePreview(trimmed),
    preambleLength: preamble.length,
    fenceMatched: fenceMatchedInText(trimmed),
    fenceRegionHex: fenceRegionCodepointsHex(trimmed),
  };

  if (!trimmed) {
    const final = parseProactiveDescription(raw);
    return {
      ...base,
      jsonExtraction: {
        path: "fallback_full_trimmed",
        closedFenceMatched: false,
        openFenceSliceLength: null,
        balancedAfterOpenOk: null,
        balancedFullTextOk: null,
        naiveDepthUsed: false,
      },
      jsonCandidateLength: 0,
      jsonCandidatePreview: "",
      jsonCandidateFirstCharHex: "(empty)",
      jsonCandidateEqualsFullTrimmed: true,
      jsonParseStage: "fail_both",
      parsedDataKind: "parse_failed",
      finalParsedKind: final.kind,
      finalItemsCount: final.kind === "structured" ? final.items.length : 0,
      finalParseFailed: final.kind === "plain" ? final.parseFailed : undefined,
    };
  }

  const { text: jsonCandidate, meta: jsonExtraction } = extractJsonFromTextWithMeta(trimmed);
  let data: unknown;
  let jsonParseStage: ProactiveParseDebugTrace["jsonParseStage"] = "ok";
  let jsonParseError: string | undefined;

  try {
    data = parseLenientJson(jsonCandidate);
  } catch (e1) {
    const msg1 = e1 instanceof Error ? e1.message : String(e1);
    try {
      data = parseLenientJson(trimmed);
      jsonParseStage = "fail_candidate_then_ok";
      jsonParseError = msg1;
    } catch (e2) {
      const msg2 = e2 instanceof Error ? e2.message : String(e2);
      const final = parseProactiveDescription(raw);
      return {
        ...base,
        jsonExtraction,
        jsonCandidateLength: jsonCandidate.length,
        jsonCandidatePreview: clipTracePreview(jsonCandidate),
        jsonCandidateFirstCharHex: firstCharCodepointHex(jsonCandidate),
        jsonCandidateEqualsFullTrimmed: jsonCandidate === trimmed,
        jsonParseStage: "fail_both",
        jsonParseError: `${msg1} | fallback: ${msg2}`,
        parsedDataKind: "parse_failed",
        finalParsedKind: final.kind,
        finalItemsCount: final.kind === "structured" ? final.items.length : 0,
        finalParseFailed: final.kind === "plain" ? final.parseFailed : undefined,
      };
    }
  }

  let parsedDataKind: ProactiveParseDebugTrace["parsedDataKind"] = "other";
  let rawArrayLength: number | undefined;
  let recordCountAfterIsRecord: number | undefined;
  let afterFilterCount: number | undefined;
  let singleObjectAccepted: boolean | undefined;

  if (Array.isArray(data)) {
    parsedDataKind = "array";
    rawArrayLength = data.length;
    const records = data.filter(isRecord);
    recordCountAfterIsRecord = records.length;
    afterFilterCount = records.filter(couldBeProposalItem).length;
  } else if (isRecord(data)) {
    parsedDataKind = "object";
    singleObjectAccepted = couldBeProposalItem(data);
  }

  const final = parseProactiveDescription(raw);
  return {
    ...base,
    jsonExtraction,
    jsonCandidateLength: jsonCandidate.length,
    jsonCandidatePreview: clipTracePreview(jsonCandidate),
    jsonCandidateFirstCharHex: firstCharCodepointHex(jsonCandidate),
    jsonCandidateEqualsFullTrimmed: jsonCandidate === trimmed,
    jsonParseStage,
    jsonParseError,
    parsedDataKind,
    rawArrayLength,
    recordCountAfterIsRecord,
    afterFilterCount,
    singleObjectAccepted,
    finalParsedKind: final.kind,
    finalItemsCount: final.kind === "structured" ? final.items.length : 0,
    finalParseFailed: final.kind === "plain" ? final.parseFailed : undefined,
  };
}
