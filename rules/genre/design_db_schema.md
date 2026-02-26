# デザイン DBスキーマテンプレート

```sql
-- デザイン案件
CREATE TABLE IF NOT EXISTS design_projects (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  title TEXT NOT NULL,
  client_or_team TEXT,
  project_type TEXT NOT NULL,     -- Web, アプリ, 印刷, ブランド, その他
  status TEXT DEFAULT '要件整理', -- 要件整理, デザイン中, レビュー中, 修正中, 承認済, 納品完了
  deadline DATE,
  description TEXT,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- 制作物
CREATE TABLE IF NOT EXISTS design_deliverables (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  project_id UUID NOT NULL REFERENCES design_projects(id),
  name TEXT NOT NULL,
  format TEXT,                    -- figma, png, pdf, svg, ai, psd
  version INT DEFAULT 1,
  file_url TEXT,
  thumbnail_url TEXT,
  status TEXT DEFAULT '作業中',   -- 作業中, レビュー待ち, 承認済
  created_by TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- レビュー依頼
CREATE TABLE IF NOT EXISTS design_reviews (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  deliverable_id UUID NOT NULL REFERENCES design_deliverables(id),
  requester TEXT NOT NULL,
  reviewers TEXT[] NOT NULL DEFAULT '{}',
  status TEXT DEFAULT '依頼中',   -- 依頼中, レビュー中, 承認, 差戻し
  deadline DATE,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- フィードバック
CREATE TABLE IF NOT EXISTS design_feedbacks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  review_id UUID NOT NULL REFERENCES design_reviews(id),
  reviewer TEXT NOT NULL,
  comment TEXT NOT NULL,
  position JSONB,                 -- {x, y, page} 画像上のピン位置
  resolved BOOLEAN DEFAULT false,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- デザイントークン
CREATE TABLE IF NOT EXISTS design_tokens (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  category TEXT NOT NULL,         -- color, typography, spacing, shadow, border-radius
  name TEXT NOT NULL,             -- primary, secondary, body-text, heading-1 等
  value TEXT NOT NULL,            -- #3B82F6, 16px, 1rem 等
  description TEXT,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(company_id, category, name)
);
```
