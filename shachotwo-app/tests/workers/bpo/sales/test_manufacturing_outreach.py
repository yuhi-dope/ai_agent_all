"""製造業向けアウトリーチテンプレートのユニットテスト。"""
import pytest

from workers.bpo.sales.templates.manufacturing_outreach import (
    OutreachMessage,
    generate_outreach_message,
    get_pain_point,
    list_sub_industries,
    SUPPORTED_SUB_INDUSTRIES,
    DEFAULT_CTA_URL,
    DEFAULT_SENDER_NAME,
    DEFAULT_SENDER_TITLE,
)


# ---------------------------------------------------------------------------
# list_sub_industries / get_pain_point
# ---------------------------------------------------------------------------

class TestListSubIndustries:
    def test_returns_all_7_industries(self) -> None:
        result = list_sub_industries()
        assert len(result) == 7

    def test_contains_all_expected_industries(self) -> None:
        result = list_sub_industries()
        expected = ["金属加工", "樹脂加工", "機械製造", "電子部品", "食品製造", "化学製品", "自動車部品"]
        for ind in expected:
            assert ind in result

    def test_matches_supported_sub_industries_constant(self) -> None:
        assert list_sub_industries() == SUPPORTED_SUB_INDUSTRIES


class TestGetPainPoint:
    def test_returns_string_for_valid_industry(self) -> None:
        for ind in SUPPORTED_SUB_INDUSTRIES:
            result = get_pain_point(ind)
            assert isinstance(result, str)
            assert len(result) > 0

    def test_returns_none_for_unknown_industry(self) -> None:
        assert get_pain_point("未対応業種") is None

    def test_kinzoku_pain_contains_mitsumori(self) -> None:
        pain = get_pain_point("金属加工")
        assert "見積" in pain  # type: ignore[operator]

    def test_jushi_pain_contains_kanagata(self) -> None:
        pain = get_pain_point("樹脂加工")
        assert "金型" in pain  # type: ignore[operator]

    def test_food_pain_contains_hygiene(self) -> None:
        pain = get_pain_point("食品製造")
        assert "衛生" in pain or "HACCP" in pain  # type: ignore[operator]


# ---------------------------------------------------------------------------
# generate_outreach_message: 正常系
# ---------------------------------------------------------------------------

class TestGenerateOutreachMessageBasic:
    def test_returns_outreach_message_instance(self) -> None:
        msg = generate_outreach_message(
            company_name="テスト株式会社",
            sub_industry="金属加工",
        )
        assert isinstance(msg, OutreachMessage)

    def test_subject_contains_company_name(self) -> None:
        msg = generate_outreach_message(
            company_name="株式会社山田金属",
            sub_industry="金属加工",
            template_variant=0,
        )
        assert "株式会社山田金属" in msg.subject

    def test_subject_variant1_does_not_contain_company_name(self) -> None:
        # バリアント1は業種訴求のため企業名なし
        msg = generate_outreach_message(
            company_name="株式会社山田金属",
            sub_industry="金属加工",
            template_variant=1,
        )
        # バリアント1の件名は「【金属加工業の方へ】...」形式
        assert "金属加工" in msg.subject

    def test_body_text_contains_company_name(self) -> None:
        msg = generate_outreach_message(
            company_name="株式会社テスト",
            sub_industry="樹脂加工",
        )
        assert "株式会社テスト" in msg.body_text

    def test_body_text_contains_representative(self) -> None:
        msg = generate_outreach_message(
            company_name="株式会社テスト",
            sub_industry="機械製造",
            representative="鈴木部長",
        )
        assert "鈴木部長" in msg.body_text

    def test_body_text_contains_cta_url(self) -> None:
        msg = generate_outreach_message(
            company_name="株式会社テスト",
            sub_industry="電子部品",
            cta_url="https://example.com/demo",
        )
        assert "https://example.com/demo" in msg.body_text

    def test_cta_url_field_set_correctly(self) -> None:
        msg = generate_outreach_message(
            company_name="テスト",
            sub_industry="食品製造",
            cta_url="https://custom.example.com/demo",
        )
        assert msg.cta_url == "https://custom.example.com/demo"

    def test_default_cta_url_is_used_when_not_specified(self) -> None:
        msg = generate_outreach_message(
            company_name="テスト",
            sub_industry="化学製品",
        )
        assert msg.cta_url == DEFAULT_CTA_URL

    def test_body_html_contains_html_tags(self) -> None:
        msg = generate_outreach_message(
            company_name="テスト",
            sub_industry="自動車部品",
        )
        assert "<html" in msg.body_html
        assert "</html>" in msg.body_html
        assert "<body" in msg.body_html

    def test_body_html_contains_company_name(self) -> None:
        msg = generate_outreach_message(
            company_name="株式会社HTMLテスト",
            sub_industry="金属加工",
        )
        assert "株式会社HTMLテスト" in msg.body_html

    def test_sub_industry_detail_replaces_placeholder(self) -> None:
        msg = generate_outreach_message(
            company_name="テスト",
            sub_industry="金属加工",
            sub_industry_detail="精密切削加工",
        )
        assert "精密切削加工" in msg.body_text

    def test_sub_industry_used_as_detail_when_empty(self) -> None:
        msg = generate_outreach_message(
            company_name="テスト",
            sub_industry="樹脂加工",
            sub_industry_detail="",
        )
        assert "樹脂加工" in msg.body_text

    def test_sender_name_appears_in_body(self) -> None:
        msg = generate_outreach_message(
            company_name="テスト",
            sub_industry="機械製造",
            sender_name="山本",
        )
        assert "山本" in msg.body_text

    def test_default_sender_name_used(self) -> None:
        msg = generate_outreach_message(
            company_name="テスト",
            sub_industry="電子部品",
        )
        assert DEFAULT_SENDER_NAME in msg.body_text


# ---------------------------------------------------------------------------
# generate_outreach_message: フォームテンプレート
# ---------------------------------------------------------------------------

class TestFormTemplate:
    def test_form_body_is_shorter_than_email_body(self) -> None:
        email_msg = generate_outreach_message(
            company_name="テスト",
            sub_industry="金属加工",
            use_form_template=False,
        )
        form_msg = generate_outreach_message(
            company_name="テスト",
            sub_industry="金属加工",
            use_form_template=True,
        )
        assert len(form_msg.body_text) < len(email_msg.body_text)

    @pytest.mark.parametrize("sub_industry", SUPPORTED_SUB_INDUSTRIES)
    def test_form_body_main_under_500_chars(self, sub_industry: str) -> None:
        msg = generate_outreach_message(
            company_name="テスト株式会社",
            sub_industry=sub_industry,
            use_form_template=True,
        )
        # フッター（---以降）を除いたメイン本文が500文字以内
        main_body = msg.body_text.split("---")[0].strip()
        assert len(main_body) <= 500, (
            f"{sub_industry}: フォーム本文が500文字超 ({len(main_body)}文字)"
        )

    def test_form_body_contains_opt_out_notice(self) -> None:
        msg = generate_outreach_message(
            company_name="テスト",
            sub_industry="金属加工",
            use_form_template=True,
        )
        assert "配信停止" in msg.body_text or "ご不要" in msg.body_text

    def test_email_body_contains_opt_out_notice(self) -> None:
        msg = generate_outreach_message(
            company_name="テスト",
            sub_industry="金属加工",
            use_form_template=False,
        )
        assert "配信停止" in msg.body_text

    def test_email_body_contains_sender_info(self) -> None:
        msg = generate_outreach_message(
            company_name="テスト",
            sub_industry="樹脂加工",
            use_form_template=False,
        )
        assert "info@shachotwo.com" in msg.body_text


# ---------------------------------------------------------------------------
# generate_outreach_message: テンプレートバリアント
# ---------------------------------------------------------------------------

class TestTemplateVariant:
    def test_variant_0_1_2_produce_different_subjects(self) -> None:
        subjects = set()
        for variant in range(3):
            msg = generate_outreach_message(
                company_name="テスト",
                sub_industry="金属加工",
                template_variant=variant,
            )
            subjects.add(msg.subject)
        assert len(subjects) == 3, "3バリアントは全て異なる件名を持つべき"

    def test_out_of_range_variant_falls_back_to_0(self) -> None:
        msg_0 = generate_outreach_message(
            company_name="テスト",
            sub_industry="金属加工",
            template_variant=0,
        )
        msg_99 = generate_outreach_message(
            company_name="テスト",
            sub_industry="金属加工",
            template_variant=99,
        )
        assert msg_0.subject == msg_99.subject

    def test_negative_variant_falls_back_to_0(self) -> None:
        msg_0 = generate_outreach_message(
            company_name="テスト",
            sub_industry="金属加工",
            template_variant=0,
        )
        msg_neg = generate_outreach_message(
            company_name="テスト",
            sub_industry="金属加工",
            template_variant=-1,
        )
        assert msg_0.subject == msg_neg.subject


# ---------------------------------------------------------------------------
# generate_outreach_message: 全業種対応確認
# ---------------------------------------------------------------------------

class TestAllIndustries:
    @pytest.mark.parametrize("sub_industry", SUPPORTED_SUB_INDUSTRIES)
    def test_all_industries_generate_message(self, sub_industry: str) -> None:
        msg = generate_outreach_message(
            company_name="株式会社全業種テスト",
            sub_industry=sub_industry,
            representative="ご担当者",
            sub_industry_detail=sub_industry,
        )
        assert isinstance(msg, OutreachMessage)
        assert len(msg.subject) > 0
        assert len(msg.body_text) > 0
        assert len(msg.body_html) > 0

    @pytest.mark.parametrize("sub_industry", SUPPORTED_SUB_INDUSTRIES)
    def test_no_placeholder_remaining_in_subject(self, sub_industry: str) -> None:
        msg = generate_outreach_message(
            company_name="テスト株式会社",
            sub_industry=sub_industry,
            representative="田中",
            sub_industry_detail=sub_industry,
        )
        assert "{" not in msg.subject, f"{sub_industry}: 件名にプレースホルダー残存"

    @pytest.mark.parametrize("sub_industry", SUPPORTED_SUB_INDUSTRIES)
    def test_no_placeholder_remaining_in_body_text(self, sub_industry: str) -> None:
        msg = generate_outreach_message(
            company_name="テスト株式会社",
            sub_industry=sub_industry,
            representative="田中",
            sub_industry_detail=sub_industry,
        )
        assert "{" not in msg.body_text, f"{sub_industry}: 本文にプレースホルダー残存"

    @pytest.mark.parametrize("sub_industry", SUPPORTED_SUB_INDUSTRIES)
    def test_no_placeholder_remaining_in_html(self, sub_industry: str) -> None:
        msg = generate_outreach_message(
            company_name="テスト株式会社",
            sub_industry=sub_industry,
            representative="田中",
            sub_industry_detail=sub_industry,
        )
        # HTMLのインラインスタイルの {} は CSS ではなく Python プレースホルダーがないこと
        # Jinja2 等を使っていないので { が残るのはバグ
        assert "{sender_name}" not in msg.body_html
        assert "{company_name}" not in msg.body_html
        assert "{cta_url}" not in msg.body_html

    @pytest.mark.parametrize("sub_industry", SUPPORTED_SUB_INDUSTRIES)
    def test_all_industries_have_3_subject_variants(self, sub_industry: str) -> None:
        subjects = set()
        for v in range(3):
            msg = generate_outreach_message(
                company_name="テスト",
                sub_industry=sub_industry,
                template_variant=v,
            )
            subjects.add(msg.subject)
        assert len(subjects) >= 2, (
            f"{sub_industry}: 件名バリアントが少なすぎる（{len(subjects)}種類）"
        )


# ---------------------------------------------------------------------------
# generate_outreach_message: エラーケース
# ---------------------------------------------------------------------------

class TestGenerateOutreachMessageErrors:
    def test_invalid_sub_industry_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="未対応のサブ業種"):
            generate_outreach_message(
                company_name="テスト",
                sub_industry="存在しない業種",
            )

    def test_error_message_lists_supported_industries(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            generate_outreach_message(
                company_name="テスト",
                sub_industry="不明業種",
            )
        error_msg = str(exc_info.value)
        assert "金属加工" in error_msg
        assert "樹脂加工" in error_msg


# ---------------------------------------------------------------------------
# 特定電子メール法準拠チェック
# ---------------------------------------------------------------------------

class TestAntiSpamCompliance:
    """特定電子メール法準拠の確認。"""

    @pytest.mark.parametrize("sub_industry", SUPPORTED_SUB_INDUSTRIES)
    def test_email_contains_unsubscribe_url(self, sub_industry: str) -> None:
        msg = generate_outreach_message(
            company_name="テスト",
            sub_industry=sub_industry,
            use_form_template=False,
        )
        assert "unsubscribe" in msg.body_text

    @pytest.mark.parametrize("sub_industry", SUPPORTED_SUB_INDUSTRIES)
    def test_email_contains_sender_email(self, sub_industry: str) -> None:
        msg = generate_outreach_message(
            company_name="テスト",
            sub_industry=sub_industry,
            use_form_template=False,
        )
        assert "info@shachotwo.com" in msg.body_text

    @pytest.mark.parametrize("sub_industry", SUPPORTED_SUB_INDUSTRIES)
    def test_html_contains_unsubscribe_link(self, sub_industry: str) -> None:
        msg = generate_outreach_message(
            company_name="テスト",
            sub_industry=sub_industry,
        )
        assert "unsubscribe" in msg.body_html

    def test_email_marked_as_advertisement(self) -> None:
        msg = generate_outreach_message(
            company_name="テスト",
            sub_industry="金属加工",
            use_form_template=False,
        )
        assert "広告" in msg.body_text
