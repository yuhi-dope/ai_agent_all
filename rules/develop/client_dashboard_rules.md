# クライアント向けダッシュボード標準構造

## 概要

develop_agent が顧客リポジトリに生成する Next.js アプリは、以下の標準ダッシュボード構造に従うこと。
全ジャンルのエージェントアウトプットを1つのアプリで一元的に提供し、DBはSupabaseで共通管理する。

---

## レイアウト要件

### 左サイドバー（幅 240px 固定）
- 会社ロゴ（上部）
- ジャンルナビゲーション: 10 ジャンルへのリンク一覧（アイコン + ラベル）
- ユーザーアカウントメニュー（下部）

### メインコンテンツ（残幅）
- 各ジャンルのシステム画面を表示する
- レスポンシブ: モバイルでは左サイドバーをドロワーに変換

---

## ホームページ（`/` または `/dashboard`）

- 10 ジャンルのカードを 3 列グリッドで配置（最後の列は 1 カード）
- 各カードの構成:
  - ジャンルアイコン（絵文字または Lucide React アイコン）
  - タイトル: 「〇〇エージェントシステム」
  - 1〜2 行の説明文
  - 「開く」ボタン → 対応ジャンルページへ遷移

### カード定義

| genre_id | タイトル | アイコン | 説明文 |
|---------|---------|--------|-------|
| sfa | SFA/営業エージェントシステム | 📊 | 商談管理・パイプライン・見積書を一元管理 |
| crm | CRMエージェントシステム | 👥 | 顧客情報・関係履歴・フォローアップを管理 |
| accounting | 会計エージェントシステム | 💴 | 請求・仕訳・財務分析を自動化 |
| legal | 法務エージェントシステム | ⚖️ | 契約書・稟議・コンプライアンスを管理 |
| admin | 事務エージェントシステム | 📝 | 日報・経費・勤怠・申請業務を効率化 |
| it | 情シスエージェントシステム | 🖥️ | IT資産・ヘルプデスク・インフラを一元管理 |
| marketing | マーケティングエージェントシステム | 📣 | 集客・広告・施策効果を可視化 |
| design | デザインエージェントシステム | 🎨 | UI/UX・制作物・デザインシステムを管理 |
| ma | M&Aエージェントシステム | 🏢 | 買収候補・DD・企業価値分析を支援 |
| no2 | No.2/経営エージェントシステム | 🧠 | KPI・経営分析・戦略提言を提供 |

---

## 各ジャンルページ（`/[genre]`）

- ページ固有のエージェントアウトプット UI を配置
- 共通ヘッダー: ジャンルアイコン + タイトル + パンくずリスト（ホーム > ジャンル名）
- コンテンツエリア: ジャンルに応じたシステム（一覧表・フォーム・ダッシュボード等）

---

## DB 一元管理（Supabase 共通スキーマ）

### 原則

1. **すべてのジャンルテーブルに `company_id TEXT NOT NULL` を持つ**
2. **全テーブルに RLS を設定**（stack_domain_rules.md の標準パターン準拠）
3. **共通テーブル**（全ジャンル共有）と**ジャンル固有テーブル**を同一 Supabase プロジェクト内に配置

### 共通テーブル（必須）

```sql
-- エージェントアウトプット共通ログ（全ジャンルのアウトプットを一元記録）
CREATE TABLE IF NOT EXISTS agent_outputs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  genre TEXT NOT NULL,  -- sfa / crm / accounting / legal / admin / it / marketing / design / ma / no2
  output_type TEXT,     -- report / form_submission / alert 等
  title TEXT,
  content JSONB,
  created_at TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE agent_outputs ENABLE ROW LEVEL SECURITY;
CREATE POLICY "agent_outputs_select" ON agent_outputs
  FOR SELECT USING (company_id = current_setting('app.company_id', true));
CREATE POLICY "agent_outputs_insert" ON agent_outputs
  FOR INSERT WITH CHECK (company_id = current_setting('app.company_id', true));
```

### ジャンル固有テーブル命名規則

- プレフィックスにジャンルIDを使用: `sfa_deals`, `crm_customers`, `accounting_invoices` 等
- 必ず `company_id TEXT NOT NULL` を含める
- 必ず RLS を設定する

---

## ファイル構成標準

```
src/
  app/
    layout.tsx           ← 左サイドバー含む共通レイアウト
    page.tsx             ← ジャンルホーム（10 カード）
    (dashboard)/         ← オプション: Route Group でサイドバーを共通化
      [genre]/
        page.tsx         ← ジャンル別ページ
  components/
    layout/
      Sidebar.tsx        ← 左サイドバー（ナビゲーション含む）
      MainLayout.tsx     ← サイドバー + コンテンツの組み合わせ
    home/
      GenreCard.tsx      ← ジャンルカードボタン
      GenreGrid.tsx      ← 10 カードのグリッドコンテナ
    [genre]/             ← ジャンル固有コンポーネント
  lib/
    supabase.ts          ← Supabase クライアント初期化
    genres.ts            ← ジャンル定義（genre_id・タイトル・説明・アイコン）
```

---

## 技術スタック（必須）

- **フレームワーク**: Next.js 14 以上（App Router）
- **スタイリング**: Tailwind CSS + shadcn/ui
- **DB**: Supabase（PostgreSQL + RLS）
- **認証**: Supabase Auth
- **型安全**: TypeScript 必須

---

## Coder への指示

1. 新機能を追加する際は、既存の `Sidebar.tsx` のナビゲーションリストに当該ジャンルのリンクが含まれていることを確認すること
2. 新しいジャンルページを作成する際は `src/app/[genre]/page.tsx` に配置し、ジャンルのタイトルとパンくずリストを含む共通ヘッダーコンポーネントを使用すること
3. 新規テーブル作成時は `agent_outputs` テーブルへの記録も合わせて実装すること（任意だが推奨）
4. ジャンル定義は `src/lib/genres.ts` に一元管理し、カード・サイドバー・ページで使い回すこと
