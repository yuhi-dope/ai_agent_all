"""Tests for security.pii_handler — PII detection and masking."""
import pytest

from security.pii_handler import PIIDetector, PIIType, PIIMatch, PIIReport


@pytest.fixture
def detector():
    return PIIDetector()


# =============================================================================
# Phone number detection
# =============================================================================

class TestPhoneDetection:
    def test_mobile_with_hyphens(self, detector: PIIDetector):
        matches = detector.detect("電話番号は090-1234-5678です")
        assert len(matches) == 1
        assert matches[0].pii_type == PIIType.PHONE
        assert matches[0].value == "090-1234-5678"
        assert matches[0].confidence >= 0.9

    def test_mobile_without_hyphens(self, detector: PIIDetector):
        matches = detector.detect("09012345678に連絡ください")
        assert len(matches) == 1
        assert matches[0].pii_type == PIIType.PHONE

    def test_mobile_080(self, detector: PIIDetector):
        matches = detector.detect("080-9876-5432")
        assert len(matches) == 1
        assert matches[0].pii_type == PIIType.PHONE

    def test_mobile_070(self, detector: PIIDetector):
        matches = detector.detect("070-1111-2222")
        assert len(matches) == 1
        assert matches[0].pii_type == PIIType.PHONE

    def test_landline_tokyo(self, detector: PIIDetector):
        matches = detector.detect("オフィスは03-1234-5678です")
        assert len(matches) == 1
        assert matches[0].pii_type == PIIType.PHONE
        assert matches[0].value == "03-1234-5678"

    def test_landline_without_hyphens(self, detector: PIIDetector):
        matches = detector.detect("0312345678")
        assert len(matches) == 1
        assert matches[0].pii_type == PIIType.PHONE

    def test_ip_phone(self, detector: PIIDetector):
        matches = detector.detect("IP電話: 050-1234-5678")
        assert len(matches) == 1
        assert matches[0].pii_type == PIIType.PHONE

    def test_toll_free(self, detector: PIIDetector):
        matches = detector.detect("フリーダイヤル: 0120-123-456")
        assert len(matches) == 1
        assert matches[0].pii_type == PIIType.PHONE

    def test_multiple_phones(self, detector: PIIDetector):
        text = "携帯: 090-1111-2222、会社: 03-3333-4444"
        matches = detector.detect(text)
        phone_matches = [m for m in matches if m.pii_type == PIIType.PHONE]
        assert len(phone_matches) == 2


# =============================================================================
# Email detection
# =============================================================================

class TestEmailDetection:
    def test_basic_email(self, detector: PIIDetector):
        matches = detector.detect("連絡先: user@example.com")
        assert len(matches) == 1
        assert matches[0].pii_type == PIIType.EMAIL
        assert matches[0].value == "user@example.com"
        assert matches[0].confidence >= 0.95

    def test_email_with_dots(self, detector: PIIDetector):
        matches = detector.detect("first.last@company.co.jp")
        assert len(matches) == 1
        assert matches[0].pii_type == PIIType.EMAIL

    def test_email_with_plus(self, detector: PIIDetector):
        matches = detector.detect("user+tag@gmail.com")
        assert len(matches) == 1
        assert matches[0].pii_type == PIIType.EMAIL

    def test_no_false_positive_at_sign(self, detector: PIIDetector):
        # Should not match partial patterns
        matches = detector.detect("twitter@handle without domain")
        email_matches = [m for m in matches if m.pii_type == PIIType.EMAIL]
        assert len(email_matches) == 0


# =============================================================================
# My Number detection
# =============================================================================

class TestMyNumberDetection:
    def test_my_number_with_context(self, detector: PIIDetector):
        text = "マイナンバーは123456789012です"
        matches = detector.detect(text)
        mn_matches = [m for m in matches if m.pii_type == PIIType.MY_NUMBER]
        assert len(mn_matches) == 1
        assert mn_matches[0].confidence >= 0.85

    def test_my_number_with_spaces(self, detector: PIIDetector):
        text = "個人番号: 1234 5678 9012"
        matches = detector.detect(text)
        mn_matches = [m for m in matches if m.pii_type == PIIType.MY_NUMBER]
        assert len(mn_matches) == 1

    def test_12_digits_without_context_low_confidence(self, detector: PIIDetector):
        # Without context keywords, confidence should be lower
        text = "注文番号: 123456789012"
        matches = detector.detect(text)
        mn_matches = [m for m in matches if m.pii_type == PIIType.MY_NUMBER]
        if mn_matches:
            assert mn_matches[0].confidence <= 0.55


# =============================================================================
# Credit card detection
# =============================================================================

class TestCreditCardDetection:
    def test_card_with_hyphens(self, detector: PIIDetector):
        text = "カード番号: 4111-1111-1111-1111"
        matches = detector.detect(text)
        cc_matches = [m for m in matches if m.pii_type == PIIType.CREDIT_CARD]
        assert len(cc_matches) == 1
        assert cc_matches[0].confidence >= 0.8

    def test_card_with_spaces(self, detector: PIIDetector):
        text = "クレジットカード: 4111 1111 1111 1111"
        matches = detector.detect(text)
        cc_matches = [m for m in matches if m.pii_type == PIIType.CREDIT_CARD]
        assert len(cc_matches) == 1

    def test_card_continuous(self, detector: PIIDetector):
        text = "カード: 4111111111111111"
        matches = detector.detect(text)
        cc_matches = [m for m in matches if m.pii_type == PIIType.CREDIT_CARD]
        assert len(cc_matches) == 1

    def test_valid_luhn_higher_confidence(self, detector: PIIDetector):
        # 4111-1111-1111-1111 passes Luhn check
        text = "カード: 4111111111111111"
        matches = detector.detect(text)
        cc_matches = [m for m in matches if m.pii_type == PIIType.CREDIT_CARD]
        assert len(cc_matches) == 1
        assert cc_matches[0].confidence >= 0.9


# =============================================================================
# Bank account detection
# =============================================================================

class TestBankAccountDetection:
    def test_account_with_context(self, detector: PIIDetector):
        text = "振込先口座番号: 1234567"
        matches = detector.detect(text)
        bank_matches = [m for m in matches if m.pii_type == PIIType.BANK_ACCOUNT]
        assert len(bank_matches) == 1
        assert bank_matches[0].value == "1234567"

    def test_no_match_without_context(self, detector: PIIDetector):
        # Without bank-related context, 7 digits should NOT match
        text = "注文番号は1234567です"
        matches = detector.detect(text)
        bank_matches = [m for m in matches if m.pii_type == PIIType.BANK_ACCOUNT]
        assert len(bank_matches) == 0


# =============================================================================
# Postal code detection
# =============================================================================

class TestPostalCodeDetection:
    def test_with_postal_mark(self, detector: PIIDetector):
        matches = detector.detect("住所: 〒100-0001 東京都千代田区")
        postal_matches = [m for m in matches if m.pii_type == PIIType.POSTAL_CODE]
        assert len(postal_matches) == 1
        assert postal_matches[0].confidence >= 0.85

    def test_without_postal_mark(self, detector: PIIDetector):
        matches = detector.detect("郵便番号: 150-0002")
        postal_matches = [m for m in matches if m.pii_type == PIIType.POSTAL_CODE]
        assert len(postal_matches) == 1


# =============================================================================
# Date of birth detection
# =============================================================================

class TestDateOfBirthDetection:
    def test_japanese_format(self, detector: PIIDetector):
        text = "生年月日: 1990年3月15日"
        matches = detector.detect(text)
        dob_matches = [m for m in matches if m.pii_type == PIIType.DATE_OF_BIRTH]
        assert len(dob_matches) == 1
        assert dob_matches[0].confidence >= 0.85

    def test_slash_format(self, detector: PIIDetector):
        text = "誕生日は1985/12/25です"
        matches = detector.detect(text)
        dob_matches = [m for m in matches if m.pii_type == PIIType.DATE_OF_BIRTH]
        assert len(dob_matches) == 1

    def test_hyphen_format(self, detector: PIIDetector):
        text = "生年月日: 2000-01-01"
        matches = detector.detect(text)
        dob_matches = [m for m in matches if m.pii_type == PIIType.DATE_OF_BIRTH]
        assert len(dob_matches) == 1

    def test_no_match_without_context(self, detector: PIIDetector):
        # A date without birthday context should not be detected
        text = "会議は2024年3月15日に開催されます"
        matches = detector.detect(text)
        dob_matches = [m for m in matches if m.pii_type == PIIType.DATE_OF_BIRTH]
        assert len(dob_matches) == 0


# =============================================================================
# Masking
# =============================================================================

class TestMasking:
    def test_mask_phone(self, detector: PIIDetector):
        result = detector.mask("電話は090-1234-5678です")
        assert "090-1234-5678" not in result
        assert "[電話番号]" in result

    def test_mask_email(self, detector: PIIDetector):
        result = detector.mask("メールはuser@example.comです")
        assert "user@example.com" not in result
        assert "[メール]" in result

    def test_mask_multiple(self, detector: PIIDetector):
        text = "電話: 090-1111-2222、メール: a@b.com"
        result = detector.mask(text)
        assert "090-1111-2222" not in result
        assert "a@b.com" not in result
        assert "[電話番号]" in result
        assert "[メール]" in result

    def test_mask_preserves_non_pii(self, detector: PIIDetector):
        text = "田中さんの電話は090-1234-5678です。明日会議があります。"
        result = detector.mask(text)
        assert "田中さんの電話は" in result
        assert "です。明日会議があります。" in result

    def test_mask_no_pii(self, detector: PIIDetector):
        text = "特に個人情報はありません"
        result = detector.mask(text)
        assert result == text

    def test_mask_empty(self, detector: PIIDetector):
        assert detector.mask("") == ""


# =============================================================================
# detect_and_report
# =============================================================================

class TestDetectAndReport:
    def test_report_with_pii(self, detector: PIIDetector):
        text = "電話: 090-1234-5678、メール: test@example.com"
        report = detector.detect_and_report(text)
        assert report.has_pii is True
        assert report.total_count == 2
        assert PIIType.PHONE in report.pii_types_found
        assert PIIType.EMAIL in report.pii_types_found
        assert report.masked_text is not None
        assert "090-1234-5678" not in report.masked_text

    def test_report_without_pii(self, detector: PIIDetector):
        report = detector.detect_and_report("会議の議事録です。特記事項なし。")
        assert report.has_pii is False
        assert report.total_count == 0
        assert report.pii_types_found == []
        assert report.masked_text is None

    def test_report_empty_text(self, detector: PIIDetector):
        report = detector.detect_and_report("")
        assert report.has_pii is False
        assert report.total_count == 0


# =============================================================================
# Edge cases
# =============================================================================

class TestEdgeCases:
    def test_mixed_pii_types(self, detector: PIIDetector):
        text = (
            "田中太郎\n"
            "電話: 090-1234-5678\n"
            "メール: tanaka@example.com\n"
            "住所: 〒100-0001\n"
            "生年月日: 1990年5月10日\n"
        )
        report = detector.detect_and_report(text)
        assert report.has_pii is True
        assert report.total_count >= 4
        types = {m.pii_type for m in report.matches}
        assert PIIType.PHONE in types
        assert PIIType.EMAIL in types
        assert PIIType.POSTAL_CODE in types
        assert PIIType.DATE_OF_BIRTH in types

    def test_positions_are_correct(self, detector: PIIDetector):
        text = "call 090-1234-5678 now"
        matches = detector.detect(text)
        assert len(matches) == 1
        assert text[matches[0].start:matches[0].end] == "090-1234-5678"

    def test_no_overlapping_matches(self, detector: PIIDetector):
        # Ensure postal code doesn't also match as bank account
        text = "〒123-4567"
        matches = detector.detect(text)
        types = [m.pii_type for m in matches]
        assert PIIType.BANK_ACCOUNT not in types
