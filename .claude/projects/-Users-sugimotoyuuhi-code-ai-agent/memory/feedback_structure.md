---
name: コード構造・保守運用コストに関するフィードバック
description: フォルダ構造・ファイル配置・プロジェクト整理に関するユーザーの強い意向
type: feedback
---

1. コードは shachotwo-app/ に一元化。別リポジトリを切らない
   **Why:** DB・認証・LLM抽象化の二重管理防止
   **How to apply:** 新機能は必ず shachotwo-app/ 内に作る

2. 設計書は shachotwo/ に一元化。ルートに散在させない
   **Why:** サブエージェントが参照する Source of Truth の一貫性
   **How to apply:** 新しい設計書は shachotwo/の適切なサブディレクトリに配置

3. パイプラインはポジション別（AI社員別）にフォルダ分け
   **Why:** 「誰が何をやるか」が一目でわかる構造にしたい
   **How to apply:** workers/bpo/sales/ は marketing/sfa/crm/cs/learning/ で分ける

4. マーケAI・契約AIのような独立プロジェクトは作らず吸収統合
   **Why:** 保守運用コスト削減
   **How to apply:** 使える部品は micro/connector/ に移植、元はアーカイブ

5. PROJECT_MAP.md をプロジェクト直下に置き、全ファイルの役割を一覧化
   **Why:** 新しい会話でもすぐ全体像を把握できるように
   **How to apply:** ファイル追加時はPROJECT_MAP.mdも更新
