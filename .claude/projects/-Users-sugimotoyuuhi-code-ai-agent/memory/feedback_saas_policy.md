---
name: SaaS連携 vs 自社開発の方針
description: 外部SaaSをどこまで使い、どこを自社開発するかの判断基準
type: feedback
---

「薄いSaaS」は自社開発OK、「厚いSaaS」はAPI維持。

**自社開発に置き換えたもの:**
- SendGrid → Gmail API（SMTP直接、2,000件/日）
- スライド提案書 → python-pptx + LLM自動生成
- 簡易電子同意 → アプリ内同意ボタン + DB記録

**SaaS APIを維持するもの:**
- freee（請求書・入金管理・会計帳簿 — 法的要件）
- CloudSign（正式な電子署名 — 電子署名法の認定が必要）
- Google Calendar（予約管理 — 自社開発する意味がない）
- Google Sheets（営業リスト管理）

**Slack:** チーム化するまで後回し。現在は全通知がlogger.infoにフォールバック。
SLACK_WEBHOOK_URL を設定すれば即有効化される設計。

**Why:** 法的要件・認証が必要なものは自社開発不可能。それ以外はコスト削減+依存削減のため自社化。
**How to apply:** 新しいSaaS連携を検討する時、「法的要件あるか」「自社開発で十分か」で判断。
