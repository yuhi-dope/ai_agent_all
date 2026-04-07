---
name: review-agents
description: AIエージェント体制を定期レビューする。設計ドキュメントと現在の実装を照合し、未実装・不完全・過剰なエージェントを特定して優先度付き改善提案を出す。エージェント棚卸し・体制確認・実装状況サマリー確認時に使用。週1〜2回実行推奨。
argument-hint: "[quick|construction|manufacturing|dental|micro] (省略時=フルレビュー)"
allowed-tools: Read, Write, Edit, Bash, Grep, Glob, Agent
---

# AIエージェント体制レビュー

対象スコープ: $ARGUMENTS（省略時=フルレビュー）

## 実行内容

agent-architect サブエージェントを使って、現在のエージェント実装体制を設計ドキュメントと照合します。

以下の順でレビューを実施してください：

### 1. 現状スキャン

以下を確認する：
- `.claude/agents/` — 登録済みサブエージェント
- `.claude/skills/` — 登録済みスキル
- `shachotwo-app/workers/micro/` — 実装済みマイクロエージェント
- `shachotwo-app/workers/bpo/` — 実装済みBPOパイプライン
- `.claude/AGENT_REGISTRY.md` — 現在のレジストリ状態

### 2. 設計ドキュメント照合

以下を参照して、要求されているエージェントとのギャップを特定：
- `shachotwo/b_詳細設計/b_02_BPO編.md`
- `shachotwo/d_アーキテクチャ/d_00_BPOアーキテクチャ設計.md`
- `shachotwo/d_アーキテクチャ/d_01_BPOエージェントエンジン設計.md`
- `shachotwo/e_業界別BPO/e_01_建設業BPO設計.md`（スコープに建設業が含まれる場合）
- `shachotwo/e_業界別BPO/e_02_製造業BPO設計.md`（スコープに製造業が含まれる場合）
- `shachotwo/e_業界別BPO/e_03_歯科BPO設計.md`（スコープに歯科が含まれる場合）

### 3. 出力フォーマット

```
## エージェント体制レビュー - {今日の日付}

### 実装状況サマリー
- マイクロエージェント: X/12 実装済み
- BPOパイプライン: X/N 実装済み
- サブエージェント定義: X ファイル

### 今すぐやるべき（P0）
1. ...

### 今週中（P1）
1. ...

### 次のフェーズ（P2+）
1. ...

### 不要・重複（削除推奨）
1. ...
```

### 4. AGENT_REGISTRY.md 更新

`.claude/AGENT_REGISTRY.md` を最新状態に更新する。

### 5. 次のアクション提示

具体的なコマンド例を提示：
```
# 最優先の未実装エージェントを実装する場合
/implement workers/micro/rule_matcher

# 建設見積パイプラインをリファクタする場合
Agent bpo-pipeline: construction/estimation
```
