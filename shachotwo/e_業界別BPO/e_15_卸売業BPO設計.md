# e_15: 卸売業BPO設計

> **業界概要**: 日本の卸売業は約25万社、市場規模約400兆円。中小卸はFAX・電話・メールでの受発注が主流で、IT化が遅れている。
> **BPO価値**: 受発注処理の自動化だけで月40-80時間の削減が可能。請求照合・在庫問い合わせ対応も大きな工数削減余地あり。
> **ゲノム系統**: 建設系（OCR→抽出→照合の共通パターン）

---

## 対象事業所プロファイル

| 項目 | 値 |
|---|---|
| 事業所数 | 約25万社 |
| 市場規模 | 約400兆円 |
| 従業員規模 | 10-300名 |
| BPO月額想定 | ¥20-40万 |
| ゲノム系統 | 建設系 |

---

## モジュール一覧（6モジュール）

| # | モジュール | 概要 | 優先度 | BPO価値 |
|---|---|---|---|---|
| ① | **受発注AI** | FAX/メール注文→OCR→自動読取→基幹システム転記 | ★#1 | 月¥10-20万 |
| ② | 請求書照合AI | 納品書と請求書の突合→差異アラート→承認WF | A | 月¥5-10万 |
| ③ | 在庫問い合わせ対応AI | 「在庫ある？」→在庫DB参照→即回答（チャット/電話） | A | 月¥3-5万 |
| ④ | 価格表・見積作成AI | 取引先別掛率×数量→自動見積書生成 | B | 月¥3-5万 |
| ⑤ | 与信管理AI | 取引先の支払遅延パターン検知→アラート | B | 月¥2-3万 |
| ⑥ | 発注最適化AI | 売上データ→需要予測→自動発注提案 | C | 月¥3-5万 |

---

## ① 受発注AI — ★キラーフィーチャー

**課題**: 中小卸の受注の60-80%がFAX・電話・メール。1件あたり10-15分の手入力作業。月500件で80-125時間。

```
業務フロー:
  FAX/メール/LINE受信
    → OCR（Google Document AI）でテキスト抽出
      → LLMで構造化（商品名・数量・単価・納期・発注元）
        → 商品マスタと照合（商品コード・在庫確認）
          → 基幹システム（販売管理）へ自動転記
            → 発注元へ受注確認メール/FAX自動返信

AI化ポイント:
  Level 1: OCR + LLM で注文内容を構造化抽出（手入力70%削減）
  Level 2: 商品マスタとのファジーマッチ（略称・型番揺れ対応）
  Level 3: 取引先別の注文パターン学習→予測入力

制約: FAXの手書き文字認識精度は80-90%。確信度低い項目はHITL（人間確認）
```

### パイプライン設計

```python
# workers/bpo/wholesale/pipelines/order_processing_pipeline.py

async def run_order_processing_pipeline(context: dict) -> PipelineResult:
    """卸売業 受発注AIパイプライン"""
    # Step 1: OCR（micro/ocr_reader）
    raw_text = await ocr_reader.extract(context["document_url"])

    # Step 2: 構造化抽出（micro/extractor）
    order_data = await extractor.extract_order(raw_text, genome="wholesale")

    # Step 3: 商品マスタ照合（micro/validator）
    validated = await validator.match_products(order_data, context["company_id"])

    # Step 4: 基幹システム転記（connector/erp）
    if validated.confidence >= 0.85:
        result = await erp_connector.create_order(validated)
    else:
        result = await hitl.request_review(validated)

    return PipelineResult(output=result)
```

---

## ② 請求書照合AI

**課題**: 仕入先からの請求書と自社の発注・納品記録の突合。月100-300件、1件15-30分。

```
業務フロー:
  請求書PDF受領
    → OCR で金額・明細抽出
      → 発注データ・納品データと突合
        → 差異があればアラート（数量違い・単価違い・未納品）
          → 承認WF → 支払処理

AI化ポイント:
  建設業の「出来高・請求書照合」パイプラインとほぼ同構造。
  ゲノムJSONの業務用語・帳票フォーマットだけ差し替えで対応可能。
```

---

## ③ 在庫問い合わせ対応AI

**課題**: 得意先からの「○○の在庫ある？」に営業が電話/チャットで対応。1日20-50件。

```
業務フロー:
  問い合わせ受信（電話/チャット/メール）
    → 商品特定（自然言語 → 商品コード）
      → 在庫DB照会（現在庫・入荷予定・代替品）
        → 回答生成（在庫あり→見積提案 / なし→入荷予定日案内）

AI化ポイント:
  チャットボット + RAG（商品マスタ + 在庫DB）で即時回答。
  電話はWhisper STT → テキスト化 → 同じフローで処理。
```

---

## 既存パイプラインとの共通性

| 卸売業モジュール | 再利用元 | 共通度 |
|---|---|---|
| 受発注AI | construction/estimation（OCR→抽出→構造化） | 80% |
| 請求書照合AI | construction/billing（帳票照合） | 90% |
| 在庫問い合わせ | brain/knowledge/qa（RAG Q&A） | 70% |
| 価格表・見積作成 | manufacturing/quoting（見積計算） | 60% |

**結論**: 既存の共通エンジンで70-90%カバー可能。卸売業固有のゲノムJSON定義が主な追加作業。
