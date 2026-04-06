"""建設業 積算AIパイプライン"""
import json
import logging
import math
from decimal import Decimal
from datetime import datetime, timezone
from typing import Optional

from db.supabase import get_service_client as get_client
from llm.client import LLMClient, LLMTask, ModelTier, ReasoningTrace
from llm.prompts.construction import (
    SYSTEM_QUANTITY_EXTRACTION,
    SYSTEM_UNIT_PRICE_ESTIMATION,
)
from workers.bpo.construction.models import (
    EstimationItemCreate,
    EstimationItemWithPrice,
    OverheadBreakdown,
    IngestionResult,
    PriceSource,
    ProjectType,
)

logger = logging.getLogger(__name__)


class EstimationPipeline:
    """
    積算AIパイプライン

    Step 1: 図面・数量計算書の取り込み
    Step 2: 数量の構造化抽出（LLM）
    Step 3: 単価の推定・候補表示
    Step 4: 諸経費計算
    Step 5: 内訳書生成
    """

    def __init__(self) -> None:
        self.llm = LLMClient()

    def _log_llm_call(
        self,
        company_id: str,
        task_type: str,
        input_text: str,
        response_content: str,
        status: str,
        parse_method: str,
        items_count: int,
        model_used: str = "",
        error_message: str = "",
        latency_ms: int = 0,
        project_id: str = "",
    ):
        """LLM呼び出しログをDBに記録（成功・失敗両方）"""
        try:
            client = get_client()
            client.table("llm_call_logs").insert({
                "company_id": company_id if company_id != "test" else None,
                "task_type": task_type,
                "model_used": model_used,
                "input_text_length": len(input_text),
                "input_summary": input_text[:200],
                "output_text_length": len(response_content),
                "output_summary": response_content[:200],
                "status": status,
                "items_extracted": items_count,
                "parse_method": parse_method,
                "error_message": error_message[:500] if error_message else None,
                "raw_response": response_content if status != "success" else None,
                "latency_ms": latency_ms,
                "project_id": project_id if project_id and project_id != "test" else None,
            }).execute()
        except Exception as e:
            logger.warning(f"Failed to log LLM call: {e}")

    @staticmethod
    def _parse_llm_json(content: str) -> list[dict] | None:
        """LLMレスポンスからJSON配列をパース（3段階フォールバック）"""
        import re
        # 1. コードブロック除去 → 直接パース
        try:
            c = content.strip()
            if c.startswith("```"):
                first_nl = c.index("\n")
                last_bt = c.rfind("```")
                if last_bt > first_nl:
                    c = c[first_nl + 1:last_bt].strip()
            return json.loads(c)
        except (json.JSONDecodeError, ValueError):
            pass

        # 2. [ ] ブラケット抽出
        start = content.find("[")
        end = content.rfind("]")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(content[start:end + 1])
            except json.JSONDecodeError:
                pass

        # 3. 不完全JSON修復
        start = content.find("[")
        if start != -1:
            last_brace = content.rfind("}")
            if last_brace > start:
                try:
                    return json.loads(content[start:last_brace + 1] + "]")
                except json.JSONDecodeError:
                    pass

        return None

    async def extract_quantities(
        self,
        project_id: str,
        company_id: str,
        raw_text: str,
    ) -> tuple[list[EstimationItemCreate], Optional[ReasoningTrace]]:
        """
        テキストから数量を構造化抽出

        Returns:
            (items, reasoning_trace): 抽出した積算明細リストとAI推論トレース（ルールベース時はNone）

        対応:
        - 数量計算書のテキスト（Excel→テキスト変換済み）
        - 設計書PDFのテキスト
        - 手入力テキスト
        """
        import re
        import time as _time

        # ─── 前処理: PDFテキストからパイプ区切りのコンパクト形式に変換 ───
        # Gemini Flashは入力が長いと出力が短く切れるため、
        # 前処理で「細別|規格|単位|数量」形式に変換してからLLMに渡す
        UNITS = {"m3", "m2", "m", "t", "kg", "本", "箇所", "個", "枚", "組", "基", "台", "回", "日", "人"}
        SKIP_EXACT = {"金額増減", "金額", "数量", "単位", "規格", "工事区分・工種・種別・細別",
                      "摘要", "数量増減", "工事区分", "事業区分", "工事名", "単価", "設計内訳書",
                      "(当\u3000初)", "(当　初)", "式"}

        lines = [l.strip() for l in raw_text.split("\n")]
        pipe_rows = []
        i = 0
        text_buffer = []
        while i < len(lines):
            s = lines[i]
            if not s or s in SKIP_EXACT:
                i += 1
                continue
            if "国土交通省" in s or (s.startswith("- ") and s.endswith(" -")):
                i += 1
                continue
            if s.startswith("令和") or (s.startswith("[") and s.endswith("]")):
                i += 1
                continue

            # 単位行を検出 → 数量+単価+金額+参照をセットで1レコード
            if s in UNITS:
                unit = s
                next_str = lines[i + 1].strip().replace(",", "") if i + 1 < len(lines) else ""
                # フォーマット1: 次行に "数量　　単価" がスペース区切りで同一行
                # フォーマット2: 次行に数量のみ、その次に単価
                import re as _re_inline
                inline_match = _re_inline.match(r'^(\d+\.?\d*)\s+(\d+\.?\d*)$', next_str.strip())
                try:
                    if inline_match:
                        qty = float(inline_match.group(1))
                        unit_price = float(inline_match.group(2))
                    else:
                        qty = float(next_str)
                        # 単価（数量の次の行）
                        price_str = lines[i + 2].strip().replace(",", "") if i + 2 < len(lines) else ""
                        try:
                            unit_price = float(price_str)
                        except ValueError:
                            unit_price = 0

                    # 参照番号を探す
                    ref = ""
                    for k in range(i + 3, min(i + 6, len(lines))):
                        lk = lines[k].strip()
                        if lk.startswith("単-"):
                            ref = lk
                            break

                    # text_bufferから細別・規格を取得
                    detail = ""
                    spec = ""
                    if len(text_buffer) >= 2:
                        tb = text_buffer[-3:] if len(text_buffer) >= 3 else text_buffer[:]
                        merged = []
                        for t in tb:
                            if merged and len(t) <= 10 and not t.endswith("工"):
                                merged[-1] = merged[-1] + t
                            else:
                                merged.append(t)
                        if len(merged) >= 2:
                            detail = merged[-2]
                            spec = merged[-1]
                        elif len(merged) == 1:
                            detail = merged[-1]
                    elif len(text_buffer) == 1:
                        detail = text_buffer[-1]

                    if detail:
                        pipe_rows.append(f"{detail}|{spec}|{unit}|{qty}|{ref}|{unit_price}")

                    i += 2
                    continue
                except (ValueError, IndexError):
                    pass

            # 数値行（金額・単価）をスキップ
            s_clean = s.replace(",", "").replace(".", "")
            if s_clean.isdigit():
                i += 1
                continue

            # 番号行 (1)〜(99) をスキップ（コンクリート配合表の番号等）
            if re.match(r"^\(\d{1,2}\)$", s):
                i += 1
                continue

            text_buffer.append(s)
            if len(text_buffer) > 6:
                text_buffer = text_buffer[-6:]

            i += 1

        # ─── 工種推定: ルールベース（LLM不要。即座に完了） ───
        CATEGORY_RULES = {
            "土工": ["掘削", "盛土", "埋戻", "法面", "残土", "土砂", "路床", "整地", "切土", "積込",
                    "不陸整正", "土のう", "路面切削"],
            "舗装工": ["路盤", "アスファルト", "ｱｽﾌｧﾙﾄ", "舗装", "プライム", "タック",
                       "ﾌｨﾙﾀｰ", "フィルター", "砕石", "粒調", "瀝青", "薄層", "ｶﾗｰ",
                       "ｸﾗｯｼｬﾗﾝ", "クラッシャラン", "ｸﾗｯｼｬｰﾗﾝ", "改質As", "ﾎﾟﾘﾏｰ改質"],
            "コンクリート工": ["型枠", "鉄筋", "コンクリート", "ｺﾝｸﾘｰﾄ", "生コン",
                              "高炉", "ﾓﾙﾀﾙ", "モルタル", "18-", "21-", "24-"],
            "防護柵工": ["防護柵", "ガードレール", "ｶﾞｰﾄﾞﾚｰﾙ", "ガードパイプ", "ｶﾞｰﾄﾞﾊﾟｲﾌﾟ",
                        "転落防止", "横断防止", "車止め", "ﾎﾟｽﾄ", "転落(横断)防止"],
            "区画線工": ["区画線", "路面標示", "ﾍﾟｲﾝﾄ", "溶融式"],
            "構造物撤去工": ["撤去", "取壊", "取り壊"],
            "排水構造物工": ["側溝", "排水", "集水", "暗渠", "管渠", "ます"],
            "仮設工": ["仮設", "仮締切", "土留", "矢板", "敷鉄板", "足場",
                       "ｷｬｯﾄ", "キャット", "朝顔", "ﾌﾞﾙｰｼｰﾄ", "ブルーシート",
                       "防護", "安全ﾈｯﾄ", "安全ネット"],
            "橋梁補修工": ["橋梁", "橋面", "伸縮", "支承", "床版", "橋台", "橋脚",
                          "ﾗﾊﾞﾄｯﾌﾟ", "ジョイント", "受圧板", "塗膜剥落防止"],
            "塗装工": ["塗装", "塗替", "素地", "樹脂発泡"],
            "法面工": ["法面保護", "モルタル吹付", "植生", "吹付", "ｱﾝｶｰ", "アンカー"],
            "鋼構造物工": ["鋼", "溶接", "ボルト"],
            "砂防工": ["砂防", "堰堤", "谷止", "B1000×H"],
            "道路付属施設工": ["標識", "視線誘導", "道路鋲", "縁石", "ﾎﾞﾗｰﾄﾞ", "ボラード",
                              "境界ﾌﾞﾛｯｸ", "境界ブロック", "歩車道", "中央帯", "階段"],
            "除草・伐木工": ["除草", "伐木", "伐竹", "集草"],
            "基礎工": ["基礎", "ﾌﾞﾛｯｸ基礎", "ブロック基礎"],
            "現場発生品処理工": ["現場発生品", "運搬処分", "産廃"],
        }

        def _infer_category(detail: str, spec: str) -> str:
            combined = (detail + " " + spec).lower()
            for cat, keywords in CATEGORY_RULES.items():
                if any(kw.lower() in combined for kw in keywords):
                    return cat
            return "その他"

        start_time = _time.monotonic()

        # reasoning_trace はLLMフォールバック時のみ取得（ルールベース時はNone）
        _reasoning_trace: Optional[ReasoningTrace] = None

        if pipe_rows:
            items_data = []
            for idx, row in enumerate(pipe_rows):
                parts = row.split("|")
                if len(parts) < 4:
                    continue
                detail_raw = parts[0]
                spec_raw = parts[1] if len(parts) > 1 else ""
                unit = parts[2] if len(parts) > 2 else ""
                try:
                    qty = float(parts[3])
                except (ValueError, IndexError):
                    continue
                ref = parts[4] if len(parts) > 4 else ""
                # 単価（前処理で取得済み）
                try:
                    unit_price = float(parts[5]) if len(parts) > 5 and parts[5] else None
                except (ValueError, IndexError):
                    unit_price = None

                category = _infer_category(detail_raw, spec_raw)

                item_dict = {
                    "sort_order": idx + 1,
                    "category": category,
                    "subcategory": detail_raw,
                    "detail": detail_raw,
                    "specification": spec_raw if spec_raw else None,
                    "quantity": qty,
                    "unit": unit,
                    "source_document": ref if ref else None,
                }
                if unit_price and unit_price > 0:
                    item_dict["unit_price"] = unit_price
                    item_dict["price_source"] = PriceSource.MANUAL.value
                    item_dict["price_confidence"] = 0.95  # 設計書記載値は信頼度高

                items_data.append(item_dict)

            latency = int((_time.monotonic() - start_time) * 1000)
            self._log_llm_call(
                company_id=company_id, task_type="quantity_extraction",
                input_text=f"pipe_rows:{len(pipe_rows)},rule_based",
                response_content=f"items:{len(items_data)}",
                status="success", parse_method="rule_based", items_count=len(items_data),
                latency_ms=latency, project_id=project_id,
            )
        else:
            # フォールバック: LLMにフルテキストを渡す（前処理で取れなかった場合のみ）
            truncated_text = raw_text[:4000]
            task = LLMTask(
                messages=[
                    {"role": "system", "content": SYSTEM_QUANTITY_EXTRACTION},
                    {"role": "user", "content": f"以下の設計書から数量を抽出:\n\n{truncated_text}"},
                ],
                tier=ModelTier.FAST,
                max_tokens=8192,
                task_type="quantity_extraction",
                with_trace=True,
            )
            try:
                response = await self.llm.generate(task)
            except Exception as e:
                self._log_llm_call(
                    company_id=company_id, task_type="quantity_extraction",
                    input_text=truncated_text[:200], response_content="",
                    status="api_error", parse_method="none", items_count=0,
                    error_message=str(e), project_id=project_id,
                )
                raise
            latency = int((_time.monotonic() - start_time) * 1000)
            items_data = self._parse_llm_json(response.content) or []
            _reasoning_trace = response.reasoning_trace

        if not items_data:
            logger.error(f"No items extracted (pipe_rows={len(pipe_rows)})")
            return [], _reasoning_trace

        # 正規化辞書を取得（自社 + 全社共通）
        client = get_client()
        try:
            norm_result = client.table("term_normalization").select(
                "original_term, normalized_term"
            ).eq("domain", "construction").or_(
                f"company_id.eq.{company_id},company_id.is.null"
            ).execute()
            norm_dict = {r["original_term"]: r["normalized_term"] for r in (norm_result.data or [])}
        except Exception:
            norm_dict = {}

        def normalize(term: str) -> str:
            return norm_dict.get(term, term)

        items = []
        for item in items_data:
            items.append(EstimationItemCreate(
                sort_order=item.get("sort_order", len(items) + 1),
                category=normalize(item["category"]),
                subcategory=normalize(item["subcategory"]) if item.get("subcategory") else None,
                detail=normalize(item["detail"]) if item.get("detail") else None,
                specification=item.get("specification"),
                quantity=Decimal(str(item["quantity"])),
                unit=item["unit"],
                unit_price=Decimal(str(item["unit_price"])) if item.get("unit_price") else None,
                price_source=item.get("price_source"),
                price_confidence=Decimal(str(item["price_confidence"])) if item.get("price_confidence") else None,
                source_document=item.get("source_document"),
                notes=item.get("notes"),
            ))

        # DBに保存（project_id/company_id がUUID形式でない場合はスキップ）
        import re as _re
        _uuid_re = _re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', _re.I)
        if _uuid_re.match(str(project_id)) and _uuid_re.match(str(company_id)):
            for item in items:
                try:
                    client.table("estimation_items").insert({
                        "project_id": project_id,
                        "company_id": company_id,
                        **item.model_dump(mode="json"),
                    }).execute()
                except Exception as e:
                    logger.warning(f"estimation_items insert failed: {e}")
        else:
            logger.debug(f"DB insert skipped (non-UUID ids): project_id={project_id}")

        # 全チャンク完了の総合ログ
        logger.info(f"Extraction complete: {len(items)} items in {latency}ms")

        return items, _reasoning_trace

    async def suggest_unit_prices(
        self,
        project_id: str,
        company_id: str,
        region: str,
        fiscal_year: int,
        items_override: list[dict] | None = None,
    ) -> list[EstimationItemWithPrice]:
        """
        各項目に単価候補を付与

        優先順位:
        1. 自社過去実績（同一工種・同一地域・直近2年以内）
        2. 公共工事設計労務単価（労務費の場合）
        3. 自社過去実績（類似工種）
        4. LLM推定

        Args:
            items_override: DBを介さずに直接アイテムを渡す場合（project_idが非UUIDの場合等）
        """
        client = get_client()

        # 積算明細を取得（items_overrideがある場合はDBをスキップ）
        if items_override is not None:
            items_data = items_override
        else:
            items_result = client.table("estimation_items").select("*").eq(
                "project_id", project_id
            ).order("sort_order").execute()
            items_data = items_result.data or []

        if not items_data:
            return []

        results = []
        for item_data in items_data:
            candidates = []

            # 1. 自社過去実績（加重平均 + 動的confidence）
            past_prices = client.table("unit_price_master").select("*").eq(
                "company_id", company_id
            ).eq("category", item_data["category"]).order(
                "updated_at", desc=True
            ).limit(10).execute()

            past_data = past_prices.data or []
            if past_data:
                now = datetime.now(timezone.utc)
                weighted_sum = 0.0
                weight_total = 0.0
                for pp in past_data:
                    # 直近重視の重み（月数が増えるほど重みが下がる）
                    updated = pp.get("updated_at", "")
                    try:
                        months_ago = max(0, (now.year * 12 + now.month) - (int(updated[:4]) * 12 + int(updated[5:7]))) if updated else 6
                    except (ValueError, IndexError):
                        months_ago = 6
                    w = 1.0 / (1.0 + months_ago * 0.1)
                    weighted_sum += float(pp["unit_price"]) * w
                    weight_total += w

                weighted_avg = weighted_sum / weight_total if weight_total > 0 else 0
                count = len(past_data)

                # 動的confidence計算
                base = 0.5
                count_bonus = min(count * 0.05, 0.3)  # 実績件数ボーナス（最大+0.3）
                region_match = sum(1 for p in past_data if p.get("region") == region)
                region_bonus = 0.1 if region_match > 0 else 0.0
                # accuracy_rateがある場合はペナルティ判定
                acc_rates = [float(p["accuracy_rate"]) for p in past_data if p.get("accuracy_rate") is not None]
                acc_penalty = -0.1 if acc_rates and (sum(acc_rates) / len(acc_rates)) < 0.8 else 0.0
                dyn_confidence = max(0.1, min(0.95, base + count_bonus + region_bonus + acc_penalty))

                candidates.append({
                    "source": PriceSource.PAST_RECORD.value,
                    "unit_price": round(weighted_avg, 2),
                    "confidence": round(dyn_confidence, 2),
                    "detail": f"自社実績 加重平均（{count}件、地域一致{region_match}件）",
                })

                # used_count を更新（参照されたレコードのカウントを+1）
                for pp in past_data:
                    current_count = pp.get("used_count") or 0
                    client.table("unit_price_master").update({
                        "used_count": current_count + 1,
                    }).eq("id", pp["id"]).execute()

            # 2. 公共工事設計労務単価
            labor_rates = client.table("public_labor_rates").select("*").eq(
                "fiscal_year", fiscal_year
            ).eq("region", region).execute()

            for lr in (labor_rates.data or []):
                if lr["occupation"].lower() in item_data.get("detail", "").lower():
                    candidates.append({
                        "source": PriceSource.LABOR_RATE.value,
                        "unit_price": lr["daily_rate"],
                        "confidence": 0.95,
                        "detail": f"公共工事設計労務単価 {lr['occupation']} {lr['fiscal_year']}年度",
                    })

            # 候補がない場合のみLLM推定
            if not candidates:
                candidates.append({
                    "source": PriceSource.AI_ESTIMATED.value,
                    "unit_price": None,
                    "confidence": 0.3,
                    "detail": "AI推定（要確認）",
                })

            item_with_price = EstimationItemWithPrice(
                **item_data,
                price_candidates=candidates,
            )
            results.append(item_with_price)

        return results

    async def calculate_overhead(
        self,
        project_id: str,
        company_id: str,
        project_type: ProjectType,
    ) -> OverheadBreakdown:
        """
        諸経費を計算

        公共土木:
          共通仮設費率 / 現場管理費率 / 一般管理費等率
          → 工事規模（直接工事費）によって率が変わる
        民間:
          会社設定の諸経費率（デフォルト27%）
        """
        client = get_client()

        # 直接工事費を集計
        items = client.table("estimation_items").select(
            "quantity, unit_price"
        ).eq("project_id", project_id).execute()

        direct_cost = 0
        for item in (items.data or []):
            if item["unit_price"]:
                qty = Decimal(str(item["quantity"]))
                price = Decimal(str(item["unit_price"]))
                direct_cost += int(qty * price)

        # 諸経費率の決定
        if project_type in (ProjectType.PUBLIC_CIVIL, ProjectType.PUBLIC_BUILDING):
            # 公共工事の標準諸経費率（簡易版）
            common_temp_rate = Decimal("0.05")   # 共通仮設費 5%
            site_mgmt_rate = Decimal("0.20")     # 現場管理費 20%
            general_rate = Decimal("0.12")       # 一般管理費等 12%
        else:
            # 民間工事
            common_temp_rate = Decimal("0.03")
            site_mgmt_rate = Decimal("0.12")
            general_rate = Decimal("0.12")

        common_temporary = int(direct_cost * common_temp_rate)
        site_management = int((direct_cost + common_temporary) * site_mgmt_rate)
        general_admin = int((direct_cost + common_temporary + site_management) * general_rate)
        total = direct_cost + common_temporary + site_management + general_admin

        breakdown = OverheadBreakdown(
            direct_cost=direct_cost,
            common_temporary=common_temporary,
            common_temporary_rate=common_temp_rate,
            site_management=site_management,
            site_management_rate=site_mgmt_rate,
            general_admin=general_admin,
            general_admin_rate=general_rate,
            total=total,
        )

        # プロジェクトの積算金額を更新
        client.table("estimation_projects").update({
            "estimated_amount": total,
            "overhead_rates": {
                "common_temporary": float(common_temp_rate),
                "site_management": float(site_mgmt_rate),
                "general_admin": float(general_rate),
            },
        }).eq("id", project_id).execute()

        return breakdown

    async def generate_breakdown_data(
        self,
        project_id: str,
        company_id: str,
    ) -> dict:
        """内訳書データを生成（Excel生成用）"""
        client = get_client()

        project = client.table("estimation_projects").select("*").eq(
            "id", project_id
        ).single().execute()

        items = client.table("estimation_items").select("*").eq(
            "project_id", project_id
        ).order("sort_order").execute()

        proj = project.data
        rows = []
        for item in (items.data or []):
            amount = None
            if item["unit_price"] and item["quantity"]:
                amount = int(Decimal(str(item["quantity"])) * Decimal(str(item["unit_price"])))
            rows.append([
                item["category"],
                item.get("subcategory", ""),
                item.get("detail", ""),
                item.get("specification", ""),
                float(item["quantity"]),
                item["unit"],
                float(item["unit_price"]) if item["unit_price"] else "",
                amount or "",
            ])

        return {
            "title": f"工事費内訳書 — {proj['name']}",
            "meta": {
                "工事名": proj["name"],
                "発注者": proj.get("client_name", ""),
                "地域": proj["region"],
                "年度": proj["fiscal_year"],
            },
            "headers": ["工種", "種別", "細別", "規格", "数量", "単位", "単価", "金額"],
            "rows": rows,
            "totals": {
                "直接工事費": proj.get("estimated_amount", 0),
            },
        }

    async def learn_from_result(
        self,
        project_id: str,
        company_id: str,
    ) -> int:
        """
        ユーザーが確定した単価をunit_price_masterに反映

        Returns: 保存された単価レコード数
        """
        client = get_client()

        project = client.table("estimation_projects").select(
            "region, fiscal_year"
        ).eq("id", project_id).single().execute()

        items = client.table("estimation_items").select("*").eq(
            "project_id", project_id
        ).not_.is_("unit_price", "null").execute()

        count = 0
        for item in (items.data or []):
            # AI推定値をnotesから取得してaccuracy_rate計算
            ai_price = None
            acc_rate = None
            try:
                meta = json.loads(item["notes"]) if isinstance(item.get("notes"), str) else item.get("notes")
                if meta and isinstance(meta, dict):
                    ai_price = meta.get("original_ai_price")
            except (ValueError, TypeError):
                pass

            confirmed = float(item["unit_price"])
            if ai_price is not None and confirmed > 0:
                acc_rate = round(max(0.0, min(1.0, 1.0 - abs(ai_price - confirmed) / confirmed)), 4)

            insert_data = {
                "company_id": company_id,
                "category": item["category"],
                "subcategory": item.get("subcategory"),
                "detail": item.get("detail"),
                "specification": item.get("specification"),
                "unit": item["unit"],
                "unit_price": item["unit_price"],
                "price_type": "composite",
                "region": project.data["region"],
                "year": project.data["fiscal_year"],
                "source": "past_estimation",
                "source_detail": f"Project: {project_id}",
            }
            if ai_price is not None:
                insert_data["ai_estimated_price"] = ai_price
            if acc_rate is not None:
                insert_data["accuracy_rate"] = acc_rate

            client.table("unit_price_master").insert(insert_data).execute()
            count += 1

        return count
