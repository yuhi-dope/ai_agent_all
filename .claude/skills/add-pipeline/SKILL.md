---
name: add-pipeline
description: 新しい業種・業務のBPOパイプラインを追加する。スケルトンファイル・テスト・レジストリ登録まで一括で行う。新業種対応・新業務フロー追加・パイプライン新規作成時に使用。
argument-hint: "{industry}/{pipeline_name} 例: dental/receipt_check, manufacturing/quoting, construction/photo_organize"
allowed-tools: Read, Write, Edit, Bash, Grep, Glob
---

# 新BPOパイプライン追加

追加するパイプライン: $ARGUMENTS

## 手順

### 1. 設計ドキュメント確認

引数の業種に対応する設計ドキュメントを確認：
- 建設業 → `shachotwo/e_業界別BPO/e_01_建設業BPO設計.md`
- 製造業 → `shachotwo/e_業界別BPO/e_02_製造業BPO設計.md`
- 歯科 → `shachotwo/e_業界別BPO/e_03_歯科BPO設計.md`
- 不動産 → `shachotwo/f_BPOガイド/f_04_不動産業BPO支払ロジック・業務フロー・課題一覧.md`
- 士業 → `shachotwo/f_BPOガイド/f_05_士業事務所BPO支払ロジック・業務フロー・課題一覧.md`

### 2. ディレクトリ・ファイル作成

```
shachotwo-app/workers/bpo/{industry}/
├── __init__.py（なければ作成）
└── pipelines/
    ├── __init__.py（なければ作成）
    └── {pipeline_name}_pipeline.py  ← 新規作成
```

### 3. パイプラインファイルのスケルトン

以下の構造でスケルトンを作成する：

```python
"""
{業種} - {パイプライン名}パイプライン

業務フロー:
  (設計書から転記)

ステップ:
  Step 1: {マイクロエージェント名} - {説明}
  Step 2: ...
"""
from workers.micro.models import MicroAgentInput, MicroAgentOutput
# 使用するマイクロエージェントをimport
from workers.bpo.manager.models import BPOTask, PipelineResult

async def run_{pipeline_name}_pipeline(
    company_id: str,
    input_data: dict,
    task: BPOTask | None = None,
) -> PipelineResult:
    """
    {詳細説明}

    Input: {入力データ説明}
    Output: {出力データ説明}
    """
    steps: list[MicroAgentOutput] = []
    context: dict = {}

    # TODO: Step 1 実装
    # TODO: Step 2 実装
    # ...

    raise NotImplementedError("パイプライン未実装 — bpo-pipeline エージェントで実装してください")
```

### 4. テストスケルトン作成

```
shachotwo-app/tests/workers/bpo/{industry}/
└── test_{pipeline_name}_pipeline.py
```

### 5. PIPELINE_REGISTRY に登録

`shachotwo-app/workers/bpo/manager/task_router.py` の `PIPELINE_REGISTRY` に追加：
```python
"{industry}/{pipeline_name}": "workers.bpo.{industry}.pipelines.{pipeline_name}_pipeline",
```

### 6. AGENT_REGISTRY.md 更新

`.claude/AGENT_REGISTRY.md` の該当業種セクションに追記。

### 7. 完了メッセージ

スケルトン作成完了後、以下を表示：
```
✅ パイプライン追加完了: {industry}/{pipeline_name}

実装するには:
  Agent bpo-pipeline: {industry}/{pipeline_name}

テスト実行:
  cd shachotwo-app && python -m pytest tests/workers/bpo/{industry}/ -v
```
