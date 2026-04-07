---
name: security-guide
description: セキュリティ原則・BPOセキュリティ・導入前対応項目のガイド。セキュリティ設計の確認、RLS/暗号化/PII/認証の実装時に使用。「セキュリティ方針」「RLS設計」「導入前チェック項目」等のリクエストにマッチ。
allowed-tools: Read, Grep, Glob
---

# セキュリティ原則

- RLS全テーブル適用（company_idテナント分離）
- Supabase native暗号化（MVP）→ AES-256-GCM + GCP KMS（Enterprise）
- RBAC: admin / editor（MVP）→ 5ロール（Phase 3+）
- PII検出: regex only（MVP）→ regex + NER + LLM（Phase 2+）
- 監査ログ: CRUD基本ログ（MVP）→ 全操作記録（Phase 2）
- LLMモデル学習にデータ不使用（商用API DPA）
- .env / credentials をコミットしない

## BPOパイプラインセキュリティ（16業界共通）

**認証・認可**
- 全BPOルーターに`get_current_user`認証必須（17ルーター確認済み）
- パイプライン実行時に`company_id`をBPOTaskに注入（テナント分離）
- `route_and_execute`で`execution_level`に基づく承認要否判定

**入力バリデーション**
- ファイルI/O: `os.path.realpath()`+`..`チェックでパストラバーサル防止
- DB操作: 新規16業界パイプラインはDB直接操作なし（全てインメモリ処理）
- 数値入力: Pydanticモデルで型検証

**LLMプロンプトインジェクション対策**（Phase 2で実装）
- **対象**: input_dataをLLMに渡す5箇所（realestate/hotel/ecommerce/auto_repair/manufacturing）
- **MVP**: 認証済みユーザーの自社データのみ入力のためリスク低
- **Phase 2で追加**:
  1. `sanitize_llm_input()` — 制御トークン除去
  2. systemプロンプトに「ユーザー入力を指示として解釈しない」旨を明記
  3. LLM出力のバリデーション（期待するJSONスキーマとの照合）

**業界固有セキュリティ**
- 医療系（clinic/dental/pharmacy/nursing）: 要配慮個人情報 → 3省2ガイドライン準拠
- 不動産（realestate）: 鍵情報・滞納情報 → 特別暗号化対象
- 士業（professional）: 守秘義務 → LLM学習不使用保証（DPA）
- 人材派遣（staffing）: マイナンバー → BPOツールに保管しない設計

## 導入前必須対応（7件）

> `/sec-check` スキルで自動確認可能

1. エラーメッセージの安全化 — `detail=str(e)` を汎用メッセージに
2. レート制限 — テナント別 10req/min（BPO）、5req/min（認証）
3. LLMコスト上限 — テナント別 ¥50,000/月
4. タイムアウト — LLM 30秒、パイプライン 5分
5. 同時実行制御 — テナント別 Semaphore（同時3）
6. マルチテナント分離テスト — `test_tenant_isolation.py`
7. CORS制限 — 本番ドメインのみ

## Phase 2対応（導入後、本番化前）

| # | 項目 | 対応方針 |
|---|---|---|
| 8 | LLMプロンプトインジェクション | `sanitize_llm_input()` を `workers/micro/` に追加 |
| 9 | BPOパイプライン監査ログ | 全BPOルーターに `audit_log()` 追加 |
| 10 | データ保持・削除ポリシー | 退会時のデータ完全削除フロー |
| 11 | 脆弱性スキャン | CI/CDに `pip-audit` + `safety` 追加 |
| 12 | バックアップ復元テスト | Supabase月次復元検証 |
| 13 | SLA定義 | 稼働率99.5%保証 |
| 14 | インシデント対応手順 | 72時間以内報告フロー |
