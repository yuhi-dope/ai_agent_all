# 事務（Admin）専門ルール

## ドメイン概要

事務エージェントは、日報・勤怠・経費申請・スケジュール・議事録・備品管理など、バックオフィスの定型業務を管理するシステムである。社員の日常業務を効率化し、申請・承認フローを電子化する。

---

## 必須エンティティ

| エンティティ | 説明 | 必須フィールド例 |
|---|---|---|
| **DailyReport（日報）** | 業務日報 | author, report_date, tasks(JSONB), hours_worked, summary |
| **Attendance（勤怠）** | 出退勤記録 | employee_id, date, clock_in, clock_out, status(出勤/有給/欠勤/遅刻) |
| **LeaveRequest（休暇申請）** | 有給・休暇申請 | employee_id, leave_type, start_date, end_date, reason, status |
| **Equipment（備品）** | 備品管理 | name, category, serial_number, location, assigned_to, status |
| **MeetingMinutes（議事録）** | 会議議事録 | title, meeting_date, attendees, agenda, decisions, action_items |

---

## ビジネスルール（Spec Agent / Coder Agent 共通）

### 日報

1. **1日1報**: 同一著者・同一日付の日報は1件のみ（UNIQUE制約）
2. **必須項目**: 業務内容（tasks）と稼働時間（hours_worked）は必須
3. **提出期限**: 当日23:59まで。未提出者はリマインダー通知
4. **閲覧権限**: 自分の日報は常時閲覧可。他人の日報は同部署のみ閲覧可

### 勤怠

1. **打刻**: 出勤打刻・退勤打刻の2回。打刻忘れは管理者が手動修正
2. **勤務時間計算**: clock_out - clock_in - 休憩時間（デフォルト1時間）
3. **遅刻判定**: 定時（9:00）より後の出勤打刻は自動的に「遅刻」
4. **月次集計**: 出勤日数・有給取得日数・残業時間を月次サマリー表示

### 休暇申請

- 承認フロー: 申請者 → 上長承認
- 有給残日数チェック: 残日数 < 申請日数 の場合はエラー
- 申請種別: 有給休暇 / 特別休暇 / 半休（午前/午後）

### 備品管理

- ステータス: 利用可能 / 貸出中 / 修理中 / 廃棄
- 貸出時に assigned_to を設定し、返却時に NULL に戻す
- 棚卸し機能: 全備品の現状確認チェックリスト

---

## 受入条件テンプレート

- [ ] 日報を作成し、同日に2件目の作成はエラーになる
- [ ] 出勤・退勤の打刻ができ、勤務時間が自動計算される
- [ ] 有給申請で残日数を超える場合はエラーになる
- [ ] 備品の貸出・返却を記録できる
- [ ] 議事録にアクションアイテムを登録し、担当者と期限を設定できる
- [ ] 月次の勤怠サマリー（出勤日数・有給・残業）が表示される

---

## Coder 向け実装指針

- 日報の UNIQUE 制約: `(company_id, author, report_date)` の複合ユニーク
- 勤怠打刻は `TIMESTAMPTZ` で保存し、タイムゾーンを考慮
- 休暇残日数は年度初めにリセット（4月始まり）。`admin_leave_balances` テーブルで管理
- 議事録のアクションアイテムは JSONB 配列 `[{assignee, task, due_date, done}]`
- リマインダー通知は Slack Webhook または Email を想定した構造にする
