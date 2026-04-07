---
name: アーキテクチャ方針・統合判断
description: プロジェクト構造の重要な判断と理由
type: project
---

**リポジトリ構造（2026-03-22確定）:**
```
ai_agent/
├── CLAUDE.md          開発ガイド
├── PROJECT_MAP.md     全体マップ
├── shachotwo/         設計書（Source of Truth）
├── shachotwo-app/     全コード（唯一のコードベース）
└── shachotwo-X運用AI/ X運用（独立小規模）
```

**統合判断:**
- shachotwo-マーケAI → shachotwo-app に吸収。元は shachotwo/z_その他/archive/ にアーカイブ
- shachotwo-契約AI → 同上
- 理由: DB/認証/LLMの二重管理防止 + コネクタ/マイクロエージェントの再利用

**AI社員フォルダ構造:**
workers/bpo/sales/ を marketing/sfa/crm/cs/learning/ の5ポジションに分割。
旧 pipelines/ は後方互換re-exportとして残存。

**Why:** ユーザーが「それぞれのポジションのAIエージェントを並べたい」と明言
**How to apply:** 新しいパイプラインは適切なポジションフォルダに配置
