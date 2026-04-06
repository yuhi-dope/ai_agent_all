"""卸売業BPOワーカーパッケージ

コア6業界の一つ。事業所数約25万社、年商400兆円規模のB2B流通市場向け。
受発注のFAX/電話依存からの脱却と在庫・請求業務の自動化を主目的とする。

パイプライン:
  - order_processing:      受発注AI（★キラーフィーチャー、Phase A）
  - inventory_management:  在庫・倉庫管理（Tier1、Phase A）
  - accounts_receivable:   請求・売掛管理（Tier1、Phase B）
  - accounts_payable:      仕入・買掛管理（Tier1、Phase B）
  - shipping:              物流・配送管理（Tier2、Phase C）
  - sales_intelligence:    営業支援（Tier2、Phase C）
"""
from workers.bpo.wholesale.pipelines import PIPELINE_REGISTRY, get_pipeline_runner

__all__ = [
    "PIPELINE_REGISTRY",
    "get_pipeline_runner",
]
