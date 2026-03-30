"""GBizInfoConnector — gBizINFO API コネクタ。法人番号・業種・従業員数・代表者名を取得。"""
import logging
import httpx

from workers.connector.base import BaseConnector, ConnectorConfig

BASE_URL = "https://info.gbiz.go.jp/hojin/v1/hojin"

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 製造業フィルタ定数
# ---------------------------------------------------------------------------

# 日本標準産業分類 大分類E: 製造業
MANUFACTURING_INDUSTRY_CODES: list[str] = ["E"]

# 製造業の識別キーワード（業務概要・業種名に含まれる場合に製造業と判定）
MANUFACTURING_KEYWORDS: list[str] = [
    "製造", "加工", "機械", "金属", "食品", "化学", "プラスチック",
    "電子", "部品", "組立", "鍛造", "鋳造", "プレス", "切削",
    "溶接", "塗装", "表面処理", "検査", "品質",
    # 金属加工（コアターゲット）
    "めっき", "メッキ", "熱処理", "焼入れ", "ダイカスト", "板金",
    "旋盤", "マシニング", "放電加工", "研磨", "NC加工",
    # 樹脂加工（コアターゲット）
    "樹脂", "射出成形", "ブロー成形", "押出成形", "金型", "モールド", "ゴム",
]


class GBizInfoConnector(BaseConnector):
    """gBizINFO REST API コネクタ。

    credentials:
        api_token (str): gBizINFO API トークン

    resource:
        - "search": 企業名検索（filters に name 必須）
        - "{corporate_number}": 法人番号で詳細取得
    """

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)

    @property
    def headers(self) -> dict[str, str]:
        token = self.config.credentials.get("api_token", "")
        return {"X-hojinInfo-api-token": token}

    async def read_records(self, resource: str, filters: dict = {}) -> list[dict]:
        """gBizINFO からレコードを読み取る。

        Args:
            resource:
                "search" — 企業名検索。filters["name"] 必須, filters["limit"] 任意
                その他   — 法人番号として扱い企業詳細を取得

            filters: 絞り込み条件

        Returns:
            企業情報の list
        """
        async with httpx.AsyncClient(timeout=10.0) as client:
            if resource == "search":
                name = filters.get("name", "")
                limit = filters.get("limit", 5)
                resp = await client.get(
                    BASE_URL,
                    params={"name": name, "limit": limit},
                    headers=self.headers,
                )
                resp.raise_for_status()
                return resp.json().get("hojin-infos", [])
            else:
                # resource を法人番号として扱う
                resp = await client.get(
                    f"{BASE_URL}/{resource}",
                    headers=self.headers,
                )
                resp.raise_for_status()
                return resp.json().get("hojin-infos", [])

    async def search_manufacturing_companies(
        self,
        prefecture: str = "",
        min_employees: int = 10,
        max_employees: int = 300,
        limit: int = 50,
    ) -> list[dict]:
        """製造業に特化した企業検索。従業員10〜300名の中小製造業をターゲット。

        gBizINFO の業種検索 + キーワードフィルタで製造業企業を絞り込む。
        取得後にクライアント側で従業員規模フィルタを適用する。

        Args:
            prefecture:    都道府県名（例: "愛知県"）。空文字の場合は全国対象。
            min_employees: 従業員数下限（デフォルト: 10）。
            max_employees: 従業員数上限（デフォルト: 300）。
            limit:         最大取得件数（デフォルト: 50）。

        Returns:
            製造業フィルタ適用済みの企業情報 list（map_to_company_data 形式）。
        """
        params: dict[str, str | int] = {
            "limit": min(limit * 3, 200),  # フィルタ後に limit 件残るよう多めに取得
        }
        if prefecture:
            params["prefecture"] = prefecture

        # 製造業キーワードで複数回検索してマージ（金属加工・樹脂加工を厚めに）
        search_keywords = ["製造", "加工", "機械", "金属", "樹脂", "成形"]
        raw_records: list[dict] = []
        seen_numbers: set[str] = set()

        async with httpx.AsyncClient(timeout=15.0) as client:
            for keyword in search_keywords:
                try:
                    resp = await client.get(
                        BASE_URL,
                        params={**params, "name": keyword},
                        headers=self.headers,
                    )
                    resp.raise_for_status()
                    for record in resp.json().get("hojin-infos", []):
                        corp_num = record.get("corporate_number", "")
                        if corp_num not in seen_numbers:
                            seen_numbers.add(corp_num)
                            raw_records.append(record)
                except Exception as e:
                    logger.warning(f"製造業検索エラー (keyword={keyword}): {e}")

        # 製造業判定フィルタ適用
        manufacturing_records: list[dict] = []
        for record in raw_records:
            if not self._is_manufacturing(record):
                continue
            mapped = self.map_to_company_data(record)
            # 従業員規模フィルタ
            emp = mapped.get("employee_count")
            if emp is not None:
                if not (min_employees <= int(emp) <= max_employees):
                    continue
            manufacturing_records.append(mapped)
            if len(manufacturing_records) >= limit:
                break

        logger.info(
            f"製造業フィルタ: 取得={len(raw_records)}件 → "
            f"製造業+規模フィルタ後={len(manufacturing_records)}件"
        )
        return manufacturing_records

    @staticmethod
    def _is_manufacturing(gbiz_data: dict) -> bool:
        """gBizINFO レコードが製造業かどうかを判定する。

        業種コード（大分類）が "E" に該当するか、
        または業務概要・業種名にMANUFACTURING_KEYWORDSが含まれる場合に True を返す。

        Args:
            gbiz_data: gBizINFO APIの生レスポンス dict

        Returns:
            True: 製造業と判定 / False: 製造業以外
        """
        # 業種大分類コードによる判定
        business_items: list[str] = gbiz_data.get("business_items", [])
        for item in business_items:
            for code in MANUFACTURING_INDUSTRY_CODES:
                if str(item).startswith(code):
                    return True

        # キーワードによる判定（業務概要・業種名）
        check_fields = [
            gbiz_data.get("business_summary", ""),
            gbiz_data.get("name", ""),
            " ".join(str(b) for b in business_items),
        ]
        combined_text = " ".join(f for f in check_fields if f)
        return any(kw in combined_text for kw in MANUFACTURING_KEYWORDS)

    async def write_record(self, resource: str, data: dict) -> dict:
        """gBizINFO は読み取り専用API。書き込みは未サポート。"""
        raise NotImplementedError("gBizINFO API は読み取り専用です")

    async def health_check(self) -> bool:
        """API疎通確認。検索エンドポイントに軽量リクエストを送る。"""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    BASE_URL,
                    params={"name": "テスト", "limit": 1},
                    headers=self.headers,
                )
                return resp.status_code < 500
        except Exception:
            return False

    @staticmethod
    def map_to_company_data(gbiz_data: dict) -> dict:
        """gBizINFO レスポンスを共通企業データ形式にマッピング。

        Returns:
            dict: 企業データ（name, corporate_number, industry, employee_count 等）
        """
        business_items = gbiz_data.get("business_items", [])
        return {
            "name": gbiz_data.get("name", ""),
            "corporate_number": gbiz_data.get("corporate_number", ""),
            "industry": business_items[0] if business_items else "",
            "employee_count": gbiz_data.get("employee_number"),
            "capital": gbiz_data.get("capital_stock"),
            "representative": gbiz_data.get("representative_name", ""),
            "prefecture": gbiz_data.get("prefecture", ""),
            "city": gbiz_data.get("city", ""),
            "address": gbiz_data.get("location", ""),
            "establishment_year": gbiz_data.get("date_of_establishment"),
            "business_overview": gbiz_data.get("business_summary", ""),
            "website_url": gbiz_data.get("company_url", ""),
        }
