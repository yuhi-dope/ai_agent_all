"""
実データ検証テスト — 中部地方整備局の設計書PDFから数量を抽出

実行: pytest tests/test_real_data_pipeline.py -v -s --run-integration
※ LLM API呼び出しが必要なためintegrationマーク
"""
import json
import os
import pytest
import fitz  # pymupdf

# ─────────────────────────────────────
# PDF→テキスト抽出ユーティリティ
# ─────────────────────────────────────

PDF_DIR = os.path.join(os.path.dirname(__file__), "..", "建設実データ")


def extract_design_text(pdf_path: str, max_pages: int = 30) -> str:
    """設計書PDFから設計内訳書のテキストを抽出（内訳書部分を最大15ページ）"""
    doc = fitz.open(pdf_path)
    texts = []
    found_start = False

    for i in range(min(max_pages, doc.page_count)):
        text = doc[i].get_text()
        if "設計内訳書" in text or "数量総括" in text:
            found_start = True
        if found_start:
            texts.append(text)
            if len(texts) >= 15:
                break

    doc.close()
    return "\n".join(texts)


def list_pdf_files() -> list[str]:
    """建設実データディレクトリのPDFファイル一覧"""
    if not os.path.exists(PDF_DIR):
        return []
    return [
        os.path.join(PDF_DIR, f)
        for f in sorted(os.listdir(PDF_DIR))
        if f.endswith(".pdf")
    ]


# ─────────────────────────────────────
# テスト: PDF読み込み確認
# ─────────────────────────────────────

class TestPDFExtraction:
    """PDFからテキストが正しく抽出できるか"""

    def test_pdf_files_exist(self):
        """建設実データディレクトリにPDFが存在する"""
        pdfs = list_pdf_files()
        assert len(pdfs) >= 1, f"建設実データディレクトリにPDFがありません: {PDF_DIR}"
        print(f"\n{len(pdfs)}件のPDFを検出:")
        for p in pdfs:
            print(f"  {os.path.basename(p)}")

    def test_text_extraction(self):
        """PDFからテキストが抽出できる"""
        pdfs = list_pdf_files()
        if not pdfs:
            pytest.skip("PDFファイルなし")

        text = extract_design_text(pdfs[0])
        assert len(text) > 100, "テキスト抽出量が少なすぎる"
        print(f"\n抽出テキスト（最初の500文字）:\n{text[:500]}")

    def test_contains_quantity_data(self):
        """抽出テキストに数量データが含まれている"""
        pdfs = list_pdf_files()
        if not pdfs:
            pytest.skip("PDFファイルなし")

        text = extract_design_text(pdfs[0])
        # 設計内訳書の特徴的なキーワード
        has_keywords = any(kw in text for kw in ["m3", "m2", "本", "式", "kg", "工種", "種別"])
        assert has_keywords, "数量データのキーワードが見つからない"


# ─────────────────────────────────────
# テスト: 全レベル通し検証（integrationマーク）
# ─────────────────────────────────────

@pytest.mark.integration
class TestRealDataFullPipeline:
    """実データで積算AIパイプラインを全レベル検証"""

    @pytest.mark.asyncio
    async def test_level1_extraction_real_data(self):
        """Level 1: 実PDFからの数量抽出"""
        from workers.bpo.construction.estimator import EstimationPipeline
        from unittest.mock import MagicMock, patch

        pdfs = list_pdf_files()
        if not pdfs:
            pytest.skip("PDFファイルなし")

        # 最初のPDFからテキスト抽出
        text = extract_design_text(pdfs[0])
        print(f"\n入力テキスト長: {len(text)}文字")

        pipeline = EstimationPipeline()

        # DBはモック（LLMは実際に呼ぶ）
        mock_db = MagicMock()
        mock_db.table.return_value.insert.return_value.execute.return_value = MagicMock()
        mock_db.table.return_value.select.return_value.eq.return_value.or_.return_value.execute.return_value = MagicMock(data=[])

        with patch("workers.bpo.construction.estimator.get_client", return_value=mock_db):
            items = await pipeline.extract_quantities(
                project_id="test-real",
                company_id="test-company",
                raw_text=text,
            )

        print(f"\n抽出結果: {len(items)}件")
        for item in items:
            print(f"  {item.category} / {item.subcategory or '-'} / {item.detail or '-'} : {item.quantity} {item.unit}")

        # 最低限のアサーション
        assert len(items) >= 3, f"抽出件数が少なすぎる: {len(items)}件"

        # 建設工事の典型的な工種が含まれているか
        categories = {item.category for item in items}
        print(f"\n抽出された工種: {categories}")

    @pytest.mark.asyncio
    async def test_all_pdfs_extraction(self):
        """全PDFで数量抽出を試行し、精度レポートを出力"""
        from workers.bpo.construction.estimator import EstimationPipeline
        from unittest.mock import MagicMock, patch

        pdfs = list_pdf_files()
        if not pdfs:
            pytest.skip("PDFファイルなし")

        pipeline = EstimationPipeline()

        results = []
        for pdf_path in pdfs:
            fname = os.path.basename(pdf_path)
            text = extract_design_text(pdf_path)

            mock_db = MagicMock()
            mock_db.table.return_value.insert.return_value.execute.return_value = MagicMock()
            mock_db.table.return_value.select.return_value.eq.return_value.or_.return_value.execute.return_value = MagicMock(data=[])

            try:
                with patch("workers.bpo.construction.estimator.get_client", return_value=mock_db):
                    items = await pipeline.extract_quantities(
                        project_id=f"test-{fname}",
                        company_id="test-company",
                        raw_text=text,
                    )
                results.append({
                    "file": fname,
                    "text_length": len(text),
                    "items_extracted": len(items),
                    "categories": list({item.category for item in items}),
                    "status": "OK",
                })
            except Exception as e:
                results.append({
                    "file": fname,
                    "text_length": len(text),
                    "items_extracted": 0,
                    "categories": [],
                    "status": f"ERROR: {str(e)[:100]}",
                })

        # レポート出力
        print("\n" + "=" * 80)
        print("実データ検証レポート")
        print("=" * 80)
        for r in results:
            print(f"\n{r['file']}")
            print(f"  テキスト長: {r['text_length']}文字")
            print(f"  抽出件数: {r['items_extracted']}件")
            print(f"  工種: {r['categories']}")
            print(f"  状態: {r['status']}")

        # 全体サマリー
        ok_count = sum(1 for r in results if r["status"] == "OK")
        total_items = sum(r["items_extracted"] for r in results)
        print(f"\n--- サマリー ---")
        print(f"PDF数: {len(results)}")
        print(f"成功: {ok_count}/{len(results)}")
        print(f"合計抽出件数: {total_items}")

        assert ok_count >= 1, "全PDFで抽出に失敗"
