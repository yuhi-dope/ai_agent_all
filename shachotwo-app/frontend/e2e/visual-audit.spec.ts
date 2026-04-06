import { test, expect } from '@playwright/test';
import { runVisualCheck } from './utils/visual-check';
import * as fs from 'fs';
import * as path from 'path';

const PAGES = [
  { name: 'dashboard', path: '/dashboard' },
  { name: 'knowledge', path: '/knowledge' },
  { name: 'knowledge-qa', path: '/knowledge/qa' },
  { name: 'bpo', path: '/bpo' },
  { name: 'twin', path: '/twin' },
  { name: 'proposals', path: '/proposals' },
  { name: 'settings', path: '/settings' },
];

const RESULTS_DIR = path.join(__dirname, 'screenshots');
const REPORT_PATH = path.join(RESULTS_DIR, 'visual-audit-report.json');

test.describe('UI_RULES.md 視覚監査', () => {
  test.beforeAll(() => {
    if (!fs.existsSync(RESULTS_DIR)) fs.mkdirSync(RESULTS_DIR, { recursive: true });
  });

  for (const pg of PAGES) {
    test(`${pg.name}: UI_RULES準拠チェック`, async ({ page }) => {
      await page.goto(pg.path, { waitUntil: 'networkidle', timeout: 45000 });
      const result = await runVisualCheck(page, pg.name);

      // 個別結果をファイルに書き出し（race condition回避）
      const resultPath = path.join(RESULTS_DIR, `${pg.name}.result.json`);
      fs.writeFileSync(resultPath, JSON.stringify(result, null, 2));

      const errors = result.violations.filter(v => v.severity === 'error');
      if (errors.length > 0) {
        console.log(`\n${pg.name} violations:`);
        errors.forEach(e => console.log(`  - [${e.rule}] ${e.detail}`));
      }
      expect(errors).toHaveLength(0);
    });
  }

  test.afterAll(() => {
    // 個別結果ファイルを集約してレポート作成
    const results = PAGES
      .map(pg => {
        const resultPath = path.join(RESULTS_DIR, `${pg.name}.result.json`);
        if (fs.existsSync(resultPath)) {
          const data = JSON.parse(fs.readFileSync(resultPath, 'utf-8'));
          fs.unlinkSync(resultPath); // 個別ファイルをクリーンアップ
          return data;
        }
        return null;
      })
      .filter(Boolean);

    fs.writeFileSync(REPORT_PATH, JSON.stringify(results, null, 2));
  });
});
