# マーケティング DBスキーマテンプレート

```sql
-- チャネルマスタ
CREATE TABLE IF NOT EXISTS marketing_channels (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  name TEXT NOT NULL,
  channel_type TEXT NOT NULL,     -- 広告, SNS, メール, SEO, 紹介, その他
  is_active BOOLEAN DEFAULT true,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- 施策
CREATE TABLE IF NOT EXISTS marketing_campaigns (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  name TEXT NOT NULL,
  channel_id UUID REFERENCES marketing_channels(id),
  budget BIGINT DEFAULT 0,
  start_date DATE,
  end_date DATE,
  goal TEXT,                      -- リード100件獲得, CVR 3%達成 等
  status TEXT DEFAULT '企画中',   -- 企画中, 承認済, 実行中, 完了, 分析中
  created_at TIMESTAMPTZ DEFAULT now()
);

-- 施策効果（日次）
CREATE TABLE IF NOT EXISTS marketing_campaign_metrics (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  campaign_id UUID NOT NULL REFERENCES marketing_campaigns(id),
  date DATE NOT NULL,
  impressions INT DEFAULT 0,
  clicks INT DEFAULT 0,
  conversions INT DEFAULT 0,
  cost BIGINT DEFAULT 0,
  UNIQUE(company_id, campaign_id, date)
);

-- リード
CREATE TABLE IF NOT EXISTS marketing_leads (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  name TEXT NOT NULL,
  email TEXT,
  phone TEXT,
  source_campaign_id UUID REFERENCES marketing_campaigns(id),
  status TEXT DEFAULT '新規',     -- 新規, ナーチャリング中, ホットリード, 商談化, 失効
  score INT DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- リード行動イベント（スコアリング用）
CREATE TABLE IF NOT EXISTS marketing_lead_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  lead_id UUID NOT NULL REFERENCES marketing_leads(id),
  event_type TEXT NOT NULL,       -- LP閲覧, 資料DL, 問い合わせ, メール開封, リンククリック
  score_delta INT DEFAULT 1,
  event_date TIMESTAMPTZ DEFAULT now()
);

-- コンテンツ
CREATE TABLE IF NOT EXISTS marketing_contents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  title TEXT NOT NULL,
  content_type TEXT NOT NULL,     -- 記事, 動画, バナー, LP, メルマガ
  campaign_id UUID REFERENCES marketing_campaigns(id),
  url TEXT,
  status TEXT DEFAULT '制作中',   -- 制作中, 公開中, 停止, アーカイブ
  created_at TIMESTAMPTZ DEFAULT now()
);
```
