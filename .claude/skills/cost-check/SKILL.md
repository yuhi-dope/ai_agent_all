---
name: cost-check
description: LLMコスト・テナント別使用量を確認する。月次で実行してコスト超過テナントを特定する。API利用料・トークン消費量・予算確認・料金チェック時に使用。
argument-hint: "[company_id] (省略時は全テナント)"
allowed-tools: Read, Bash, Grep, Glob
---

# LLMコスト確認

対象: $ARGUMENTS（省略時は全テナント）

## 確認手順

### 1. CostTracker の状態確認
```bash
cd shachotwo-app
python -c "
from llm.cost_tracker import get_cost_tracker
tracker = get_cost_tracker()
# 全テナントのコスト状況を表示
status = tracker.get_all_status() if hasattr(tracker, 'get_all_status') else {}
for company_id, s in status.items():
    usage_rate = s['total_cost_yen'] / s['budget_yen'] * 100
    flag = '🔴' if usage_rate > 80 else '🟡' if usage_rate > 60 else '✅'
    print(f'{flag} {company_id}: ¥{s[\"total_cost_yen\"]:.0f} / ¥{s[\"budget_yen\"]:.0f} ({usage_rate:.1f}%)')
"
```

### 2. 月次コスト集計（ダッシュボードAPI経由）
```bash
grep -n "monthly.cost\|total_cost_yen\|extraction_cost\|qa_cost" \
  shachotwo-app/routers/dashboard.py | head -20
```

### 3. コスト内訳分析
```bash
python -c "
from llm.cost_tracker import get_cost_tracker
tracker = get_cost_tracker()
# モデル別コスト内訳
print('モデル別単価:')
costs = {'gemini-2.5-flash':  {'in': 0.011, 'out': 0.044},
         'gemini-2.5-pro':    {'in': 0.184, 'out': 0.735},
         'claude-sonnet-4-6': {'in': 0.45,  'out': 2.25},
         'claude-opus-4-6':   {'in': 2.25,  'out': 11.25}}
for model, c in costs.items():
    print(f'  {model}: in=¥{c[\"in\"]}/1Ktok, out=¥{c[\"out\"]}/1Ktok')
"
```

### 4. 予算アラートテナント特定

予算の80%以上を消費しているテナントをリストアップし、対応を提示：
- 80-100%: ⚠️ 管理者に通知推奨
- 100%超: 🔴 パイプライン停止中（要確認）

## 結果出力フォーマット

```
## LLMコスト確認レポート ({年月})

### テナント別使用状況
| テナント | 使用額 | 上限 | 使用率 | 状態 |
|---|---|---|---|---|
| company_xxx | ¥12,340 | ¥50,000 | 24.7% | ✅ 正常 |
| company_yyy | ¥43,210 | ¥50,000 | 86.4% | ⚠️ 要注意 |

### 主要コスト要因
- Q&A: ¥{合計} ({N}回)
- ナレッジ抽出: ¥{合計} ({N}回)
- BPO実行: ¥{合計} ({N}回)

### 推奨アクション
{コスト超過テナントへの対応・最適化提案}
```
