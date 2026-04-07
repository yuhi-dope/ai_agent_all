---
name: pmf-check
description: PMFゲート指標を確認する。NPS≥30/WAU≥60%/「ないと困る」≥60%の達成状況をチェック。PMF判定・KPI確認・NPS/WAU/継続率チェック時に使用。Week 8-9に実行。
argument-hint: ""
allowed-tools: Read, Bash, Grep, Glob
---

# PMFゲートチェック

## PMF判定基準（CLAUDE.mdより）

| 指標 | 閾値 | 意味 |
|---|---|---|
| NPS | ≥ 30 | 推奨者 - 批判者 ≥ 30pt |
| WAU (週次アクティブ率) | ≥ 60% | 登録ユーザーの60%が週1回以上使用 |
| 「ないと困る」 | ≥ 60% | 「このサービスがなくなったら困る」回答率 |

## チェック手順

### 1. WAU計測クエリ確認
```bash
grep -rn "WAU\|weekly_active\|active_users" \
  shachotwo-app/routers/dashboard.py
```

WAU計測のためのダッシュボードAPIが実装されているか確認。
未実装の場合: 以下のSQLをSupabaseで手動実行する方法を提示：
```sql
-- 過去7日間のアクティブユーザー率
SELECT
  COUNT(DISTINCT user_id) FILTER (
    WHERE created_at > NOW() - INTERVAL '7 days'
  )::float / NULLIF(COUNT(DISTINCT user_id), 0) as wau_rate
FROM audit_logs
WHERE company_id = '{company_id}';
```

### 2. Q&A利用状況確認
```bash
grep -rn "qa_count\|question_count\|qa_sessions" \
  shachotwo-app/routers/dashboard.py
```

### 3. NPS・定性指標確認（手動）
以下の調査項目を提示する（自動計測不可）：

**NPSアンケート文言**
```
Q1: このサービスを友人・同業者に勧める可能性は？（0-10点）
Q2: このサービスがなくなったら困りますか？
  ① とても困る  ② 少し困る  ③ どちらでもない  ④ 困らない
Q3: 最も価値を感じている機能は何ですか？（自由記述）
```

### 4. PMF判定

収集した数値を入力し、PMF判定を出力：

```
## PMFゲートチェック結果 ({日付})

| 指標 | 目標 | 実績 | 判定 |
|---|---|---|---|
| NPS | ≥ 30 | {値} | ✅/❌ |
| WAU | ≥ 60% | {値}% | ✅/❌ |
| 「ないと困る」 | ≥ 60% | {値}% | ✅/❌ |

### PMF判定
✅ PMF達成 → Phase 2移行を推奨
❌ 未達 → {未達指標}の改善が必要。推奨アクション：
  - NPS低い → Q&Aの回答精度改善、オンボーディング簡素化
  - WAU低い → プロアクティブ提案の強化、リマインド通知
  - 「困る」低い → コア機能の絞り込み、ユースケース見直し
```
