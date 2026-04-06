-- Migration 018: bpo_hitl_requirements 全29パイプライン登録
-- Migration 015 で登録済みの7本に加え、残り22本を追加する。
-- pipeline_key は workers/bpo/{industry}/pipelines/{name}_pipeline.py のパスに対応。

-- 建設業: 追加4本
INSERT INTO bpo_hitl_requirements (pipeline_key, requires_approval, min_confidence_for_auto, description)
VALUES
    ('construction/photo_organize', FALSE, 0.85, '建設業施工写真整理: 外部送付なし・低リスク'),
    ('construction/cost_report',    TRUE,  NULL,  '建設業原価報告: 金額・経営情報'),
    ('construction/subcontractor',  TRUE,  NULL,  '建設業下請管理: 金額・法的書類'),
    ('construction/permit',         TRUE,  NULL,  '建設業許可申請: 法的効力あり')
ON CONFLICT (pipeline_key) DO UPDATE SET
    requires_approval        = EXCLUDED.requires_approval,
    min_confidence_for_auto  = EXCLUDED.min_confidence_for_auto,
    description              = EXCLUDED.description;

-- 製造業: 実際のpipeline_keyを登録（015は manufacturing/estimation だったが
--   正確なキーは manufacturing/quoting に合わせて追加）
INSERT INTO bpo_hitl_requirements (pipeline_key, requires_approval, min_confidence_for_auto, description)
VALUES
    ('manufacturing/quoting', TRUE, NULL, '製造業見積: 金額誤りリスク高（manufacturing/estimationと同義）')
ON CONFLICT (pipeline_key) DO UPDATE SET
    requires_approval        = EXCLUDED.requires_approval,
    min_confidence_for_auto  = EXCLUDED.min_confidence_for_auto,
    description              = EXCLUDED.description;

-- 共通BPO: 残り3本
INSERT INTO bpo_hitl_requirements (pipeline_key, requires_approval, min_confidence_for_auto, description)
VALUES
    ('common/attendance',      TRUE,  NULL,  '勤怠集計: 給与計算に直結・機密情報'),
    ('common/vendor',          FALSE, 0.90,  '取引先マスタ更新: 低リスク・高信頼度で自動可'),
    ('common/admin_reminder',  FALSE, 0.80,  '管理リマインダ: 通知のみ・低リスク')
ON CONFLICT (pipeline_key) DO UPDATE SET
    requires_approval        = EXCLUDED.requires_approval,
    min_confidence_for_auto  = EXCLUDED.min_confidence_for_auto,
    description              = EXCLUDED.description;

-- 飲食業
INSERT INTO bpo_hitl_requirements (pipeline_key, requires_approval, min_confidence_for_auto, description)
VALUES
    ('restaurant/fl_cost', TRUE,  NULL,  '飲食業FL原価計算: 仕入金額・経営情報'),
    ('restaurant/shift',   FALSE, 0.85,  '飲食業シフト生成: 外部送付なし・低リスク')
ON CONFLICT (pipeline_key) DO UPDATE SET
    requires_approval        = EXCLUDED.requires_approval,
    min_confidence_for_auto  = EXCLUDED.min_confidence_for_auto,
    description              = EXCLUDED.description;

-- 美容業
INSERT INTO bpo_hitl_requirements (pipeline_key, requires_approval, min_confidence_for_auto, description)
VALUES
    ('beauty/recall', FALSE, 0.80, '美容リコール送信: 顧客連絡・文面要確認のため中程度')
ON CONFLICT (pipeline_key) DO UPDATE SET
    requires_approval        = EXCLUDED.requires_approval,
    min_confidence_for_auto  = EXCLUDED.min_confidence_for_auto,
    description              = EXCLUDED.description;

-- 人材派遣業
INSERT INTO bpo_hitl_requirements (pipeline_key, requires_approval, min_confidence_for_auto, description)
VALUES
    ('staffing/dispatch_contract', TRUE, NULL, '派遣契約書作成: 法的効力・個人情報含む')
ON CONFLICT (pipeline_key) DO UPDATE SET
    requires_approval        = EXCLUDED.requires_approval,
    min_confidence_for_auto  = EXCLUDED.min_confidence_for_auto,
    description              = EXCLUDED.description;

-- 設計事務所（建築）
INSERT INTO bpo_hitl_requirements (pipeline_key, requires_approval, min_confidence_for_auto, description)
VALUES
    ('architecture/building_permit', TRUE, NULL, '建築確認申請: 法的書類・許可申請')
ON CONFLICT (pipeline_key) DO UPDATE SET
    requires_approval        = EXCLUDED.requires_approval,
    min_confidence_for_auto  = EXCLUDED.min_confidence_for_auto,
    description              = EXCLUDED.description;

-- 医療クリニック（法的文書整備後に解禁）
INSERT INTO bpo_hitl_requirements (pipeline_key, requires_approval, min_confidence_for_auto, description)
VALUES
    ('clinic/medical_receipt', TRUE, NULL, '医療レセプト: 要配慮個人情報・法的書類整備後のみ解禁')
ON CONFLICT (pipeline_key) DO UPDATE SET
    requires_approval        = EXCLUDED.requires_approval,
    min_confidence_for_auto  = EXCLUDED.min_confidence_for_auto,
    description              = EXCLUDED.description;

-- ホテル
INSERT INTO bpo_hitl_requirements (pipeline_key, requires_approval, min_confidence_for_auto, description)
VALUES
    ('hotel/revenue_mgmt', TRUE, NULL, 'ホテル収益管理: 料金設定・外部OTA連携')
ON CONFLICT (pipeline_key) DO UPDATE SET
    requires_approval        = EXCLUDED.requires_approval,
    min_confidence_for_auto  = EXCLUDED.min_confidence_for_auto,
    description              = EXCLUDED.description;

-- 調剤薬局（法的文書整備後に解禁）
INSERT INTO bpo_hitl_requirements (pipeline_key, requires_approval, min_confidence_for_auto, description)
VALUES
    ('pharmacy/dispensing_billing', TRUE, NULL, '薬局調剤報酬請求: 要配慮個人情報・法的書類整備後のみ解禁')
ON CONFLICT (pipeline_key) DO UPDATE SET
    requires_approval        = EXCLUDED.requires_approval,
    min_confidence_for_auto  = EXCLUDED.min_confidence_for_auto,
    description              = EXCLUDED.description;

-- 歯科（パイロット解禁条件クリア後のみ使用可）
INSERT INTO bpo_hitl_requirements (pipeline_key, requires_approval, min_confidence_for_auto, description)
VALUES
    ('dental/receipt_check', TRUE, NULL, '歯科レセプト点検: 要配慮個人情報・3省2ガイドライン準拠後のみ解禁')
ON CONFLICT (pipeline_key) DO UPDATE SET
    requires_approval        = EXCLUDED.requires_approval,
    min_confidence_for_auto  = EXCLUDED.min_confidence_for_auto,
    description              = EXCLUDED.description;

-- 不動産
INSERT INTO bpo_hitl_requirements (pipeline_key, requires_approval, min_confidence_for_auto, description)
VALUES
    ('realestate/rent_collection', TRUE, NULL, '不動産家賃管理: 金額・入金確認・外部連絡')
ON CONFLICT (pipeline_key) DO UPDATE SET
    requires_approval        = EXCLUDED.requires_approval,
    min_confidence_for_auto  = EXCLUDED.min_confidence_for_auto,
    description              = EXCLUDED.description;

-- 士業（守秘義務あり）
INSERT INTO bpo_hitl_requirements (pipeline_key, requires_approval, min_confidence_for_auto, description)
VALUES
    ('professional/deadline_mgmt', TRUE, NULL, '士業期限管理: 法的期限・重大なミスリスク')
ON CONFLICT (pipeline_key) DO UPDATE SET
    requires_approval        = EXCLUDED.requires_approval,
    min_confidence_for_auto  = EXCLUDED.min_confidence_for_auto,
    description              = EXCLUDED.description;

-- 介護
INSERT INTO bpo_hitl_requirements (pipeline_key, requires_approval, min_confidence_for_auto, description)
VALUES
    ('nursing/care_billing', TRUE, NULL, '介護報酬請求: 要配慮個人情報・請求金額')
ON CONFLICT (pipeline_key) DO UPDATE SET
    requires_approval        = EXCLUDED.requires_approval,
    min_confidence_for_auto  = EXCLUDED.min_confidence_for_auto,
    description              = EXCLUDED.description;

-- 物流
INSERT INTO bpo_hitl_requirements (pipeline_key, requires_approval, min_confidence_for_auto, description)
VALUES
    ('logistics/dispatch', FALSE, 0.88, '物流配車最適化: 外部送付あり・中程度リスク')
ON CONFLICT (pipeline_key) DO UPDATE SET
    requires_approval        = EXCLUDED.requires_approval,
    min_confidence_for_auto  = EXCLUDED.min_confidence_for_auto,
    description              = EXCLUDED.description;

-- EC（電子商取引）
INSERT INTO bpo_hitl_requirements (pipeline_key, requires_approval, min_confidence_for_auto, description)
VALUES
    ('ecommerce/listing', FALSE, 0.85, 'EC商品登録: 外部公開前に確認推奨・中程度リスク')
ON CONFLICT (pipeline_key) DO UPDATE SET
    requires_approval        = EXCLUDED.requires_approval,
    min_confidence_for_auto  = EXCLUDED.min_confidence_for_auto,
    description              = EXCLUDED.description;

-- 自動車整備
INSERT INTO bpo_hitl_requirements (pipeline_key, requires_approval, min_confidence_for_auto, description)
VALUES
    ('auto_repair/repair_quoting', TRUE, NULL, '自動車整備見積: 金額・顧客への説明責任')
ON CONFLICT (pipeline_key) DO UPDATE SET
    requires_approval        = EXCLUDED.requires_approval,
    min_confidence_for_auto  = EXCLUDED.min_confidence_for_auto,
    description              = EXCLUDED.description;

-- 登録件数確認用コメント
-- 015 登録済み: 7本（construction×3, manufacturing/estimation, common×3）
-- 018 追加: 22本
-- 合計: 29本（全パイプライン対応完了）

COMMENT ON TABLE bpo_hitl_requirements IS
    'BPOパイプライン別のHuman-in-the-Loop承認要否設定。全29パイプライン登録済み（018完了）。';
