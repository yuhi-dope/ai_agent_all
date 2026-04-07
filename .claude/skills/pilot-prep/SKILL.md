---
name: pilot-prep
description: パイロット企業投入前の準備チェック。オンボーディングフロー・テンプレート・招待フロー・BPOパイプラインを確認する。パイロット準備確認・オンボーディング確認・投入前チェック時に使用。
argument-hint: "[company_name] [industry]"
allowed-tools: Read, Bash, Grep, Glob
---

# パイロット企業投入準備チェック

対象: $ARGUMENTS

## チェック項目

### 1. デプロイ前チェック完了確認
`/pre-deploy` スキルを実行して全項目クリアを確認する。

### 2. 業種テンプレート確認
```bash
cd shachotwo-app
python -c "
from brain.genome.templates import list_templates
templates = list_templates()
print('利用可能テンプレート:')
for t in templates:
    print(f'  {t.id}: {t.name} ({t.total_items}件のナレッジ)')
"
```
- 対象業種のテンプレートが存在し、ナレッジが0件でないか確認

### 3. BPOパイプライン動作確認
対象業種の #1 パイプラインが登録・動作するか確認：
```bash
python -c "
from workers.bpo.manager.task_router import PIPELINE_REGISTRY
industry_pipelines = {k: v for k, v in PIPELINE_REGISTRY.items() if k.startswith('{industry}')}
print('登録済みパイプライン:')
for k in industry_pipelines:
    print(f'  {k}')
"
```

### 4. オンボーディングフロー確認
- `/onboarding/status` エンドポイントが正常応答するか
- 業種選択 → テンプレート適用 → Q&A誘導の流れが動作するか

### 5. 招待フロー確認
```bash
grep -n "invitations\|invitation" main.py
grep -rn "POST.*invitations\|send_invitation" routers/invitations.py | head -5
```
- 招待メール送信フローが実装済みか確認

### 6. Q&Aサンプル動作確認
業種テンプレートのナレッジを使ったQ&Aが回答できるか確認（モック環境で）

## チェックリスト出力

```
## パイロット投入準備チェック: {company_name}（{industry}）

### システム準備
- [ ] デプロイ前チェック全件クリア
- [ ] 対象業種テンプレート存在（{N}件のナレッジ）
- [ ] BPOパイプライン登録済み（{pipeline_name}）
- [ ] 招待フロー動作確認

### オペレーション準備（手動確認）
- [ ] 管理者アカウント作成済み
- [ ] CORS_ORIGINS に本番ドメイン追加済み
- [ ] パイロット企業への操作説明資料準備
- [ ] サポート連絡先の案内

判定: ✅ 投入可能 / ❌ {N}件要対応
```
