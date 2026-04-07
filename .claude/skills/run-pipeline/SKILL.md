---
name: run-pipeline
description: BPOパイプラインを指定インプットで実行してテストする。実際のDBを使わずにモックデータでパイプラインの動作確認・精度検証ができる。パイプラインのテスト・動作確認・精度検証時に使用。
argument-hint: "{industry}/{pipeline_name} [--input path/to/input.json] [--dry-run]"
allowed-tools: Read, Write, Edit, Bash, Grep, Glob
---

# BPOパイプライン実行テスト

実行対象: $ARGUMENTS

## 手順

### 1. パイプライン存在確認

`shachotwo-app/workers/bpo/` 配下に対象パイプラインが実装済みか確認する。
未実装の場合: `Agent bpo-pipeline: {指定されたパイプライン}` で実装を促す。

### 2. テストスクリプト生成・実行

以下を実行：

```bash
cd shachotwo-app

# パイプラインを直接実行するテストスクリプト
python -c "
import asyncio
import json
from workers.bpo.{industry}.pipelines.{pipeline_name}_pipeline import run_{pipeline_name}_pipeline

# テスト用インプット
test_input = {
    # 引数で --input が指定された場合はそのJSONを読み込む
    # 未指定の場合はデフォルトのモックデータ
}

async def main():
    result = await run_{pipeline_name}_pipeline(
        company_id='test-company-id',
        input_data=test_input,
    )
    print('=== パイプライン実行結果 ===')
    print(f'成功: {result.success}')
    print(f'ステップ数: {len(result.steps)}')
    print(f'総コスト: ¥{result.total_cost_yen:.2f}')
    print(f'総処理時間: {result.total_duration_ms}ms')
    print()
    print('=== ステップ別結果 ===')
    for i, step in enumerate(result.steps, 1):
        status = '✅' if step.success else '❌'
        print(f'Step {i} {status} {step.agent_name}: confidence={step.confidence:.2f}, cost=¥{step.cost_yen:.2f}')
    if not result.success:
        print(f'失敗ステップ: {result.failed_step}')

asyncio.run(main())
"
```

### 3. 精度レポート出力

各ステップのconfidenceスコアを一覧表示：

```
=== 精度レポート ===
Step 1 document_ocr:        confidence=0.95 ✅
Step 2 structured_extractor: confidence=0.82 ⚠️ (0.80未満で要改善)
Step 3 rule_matcher:         confidence=0.99 ✅
...
```

confidence < 0.80 のステップには改善提案を出す。

### 4. コスト分析

```
=== コスト分析 ===
Step 1 document_ocr:        ¥2.50 (FAST)
Step 2 structured_extractor: ¥8.30 (STANDARD)
Step 3 rule_matcher:         ¥0.10 (FAST)
...
合計: ¥XX.XX / 実行
月間想定(100回/月): ¥X,XXX
```

### 5. テスト結果サマリー

```
=== テスト結果サマリー ===
パイプライン: {industry}/{pipeline_name}
実行日時: {datetime}
ステータス: ✅成功 / ❌失敗
精度スコア: X.XX/1.00
改善推奨: Step X ({agent_name}) の精度が低い → プロンプト改善またはモデル階層変更を推奨
```
