import { Page } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

const SCREENSHOT_DIR = path.join(__dirname, '..', 'screenshots');

// UI_RULES.md 禁止ワード（一般ユーザー画面向け）
// 注: 管理者専用画面（/bpo等）では一部ワードが許容される
const BANNED_WORDS = [
  'パイプライン', 'デジタルツイン', 'confidence',
  'model_used', 'session_id', 'token', 'execution_id',
];

// /bpo 以外のページでのみチェックする追加禁止ワード
const BANNED_WORDS_NON_ADMIN = ['BPO'];

const ENGLISH_ONLY_PATTERN = /^[A-Za-z\s\d.,!?:;'"()-]+$/;

export interface VisualCheckResult {
  pagePath: string;
  screenshotPath: string;
  violations: Violation[];
  passed: boolean;
}

export interface Violation {
  rule: string;
  severity: 'error' | 'warning';
  element?: string;
  detail: string;
}

export async function captureScreenshot(page: Page, name: string): Promise<string> {
  if (!fs.existsSync(SCREENSHOT_DIR)) {
    fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });
  }
  const filePath = path.join(SCREENSHOT_DIR, `${name}.png`);
  await page.screenshot({ path: filePath, fullPage: true });
  return filePath;
}

export async function checkUIRules(page: Page): Promise<Violation[]> {
  const violations: Violation[] = [];
  const currentPath = new URL(page.url()).pathname;

  // 1. 禁止ワードチェック
  const bodyText = await page.locator('body').innerText();
  const wordsToCheck = currentPath.startsWith('/bpo')
    ? BANNED_WORDS
    : [...BANNED_WORDS, ...BANNED_WORDS_NON_ADMIN];
  for (const word of wordsToCheck) {
    if (bodyText.includes(word)) {
      violations.push({
        rule: '禁止ワード検出',
        severity: 'error',
        detail: `"${word}" がページ内に表示されています`,
      });
    }
  }

  // 2. 英語のみのボタン・ラベル検出
  const buttons = await page.locator('button, [role="button"], a.btn').all();
  for (const btn of buttons) {
    const text = (await btn.innerText()).trim();
    if (text && ENGLISH_ONLY_PATTERN.test(text) && text.length > 1) {
      violations.push({
        rule: '英語UIテキスト検出',
        severity: 'error',
        element: `button: "${text}"`,
        detail: `ボタンテキストが英語のみです: "${text}"`,
      });
    }
  }

  // 3. placeholder英語チェック
  const inputs = await page.locator('input[placeholder], textarea[placeholder]').all();
  for (const input of inputs) {
    const ph = await input.getAttribute('placeholder');
    if (ph && ENGLISH_ONLY_PATTERN.test(ph) && ph.length > 2) {
      violations.push({
        rule: 'placeholder英語検出',
        severity: 'error',
        element: `input placeholder: "${ph}"`,
        detail: `placeholderが英語のみです: "${ph}"`,
      });
    }
  }

  // 4. ローディングスピナー裸チェック
  const spinners = await page.locator('.animate-spin').all();
  for (const spinner of spinners) {
    const parent = spinner.locator('..');
    const parentText = (await parent.innerText()).trim();
    if (!parentText || parentText.length < 2) {
      violations.push({
        rule: 'スピナー裸表示',
        severity: 'warning',
        detail: 'ローディングスピナーにテキストメッセージがありません',
      });
    }
  }

  // 5. 小さすぎるフォントサイズ検出
  const tinyTexts = await page.locator('[class*="text-["]').all();
  for (const el of tinyTexts) {
    const cls = await el.getAttribute('class') || '';
    const match = cls.match(/text-\[(\d+)px\]/);
    if (match && parseInt(match[1]) < 11) {
      violations.push({
        rule: '最小フォント違反',
        severity: 'error',
        element: `class: "${cls}"`,
        detail: `フォントサイズ ${match[1]}px は最小11px未満です`,
      });
    }
  }

  // 6. 空状態チェック
  const emptyStates = await page.locator(':text("ありません"), :text("見つかりません"), :text("登録されていません")').all();
  for (const el of emptyStates) {
    const parent = el.locator('..');
    const hasButton = await parent.locator('button, a').count();
    if (hasButton === 0) {
      violations.push({
        rule: '空状態にアクション未設置',
        severity: 'warning',
        detail: '空の状態表示に次のアクションボタンがありません',
      });
    }
  }

  return violations;
}

export async function runVisualCheck(page: Page, pageName: string): Promise<VisualCheckResult> {
  const screenshotPath = await captureScreenshot(page, pageName);
  const violations = await checkUIRules(page);
  return {
    pagePath: page.url(),
    screenshotPath,
    violations,
    passed: violations.filter(v => v.severity === 'error').length === 0,
  };
}
