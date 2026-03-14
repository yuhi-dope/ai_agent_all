"""PII Detection and Masking — regex-based MVP implementation.

Detects Japanese PII patterns:
- 電話番号 (phone numbers)
- メールアドレス (email addresses)
- マイナンバー (My Number / national ID)
- クレジットカード (credit card numbers)
- 銀行口座 (bank account numbers)
- 郵便番号 (postal codes)
- 生年月日 (dates of birth)

Phase 2+ will add NER and LLM-based detection.
"""
import re
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class PIIType(str, Enum):
    """PII categories detected by the system."""
    PHONE = "phone"
    EMAIL = "email"
    MY_NUMBER = "my_number"
    CREDIT_CARD = "credit_card"
    BANK_ACCOUNT = "bank_account"
    POSTAL_CODE = "postal_code"
    DATE_OF_BIRTH = "date_of_birth"


# Human-readable mask labels per PII type
_MASK_LABELS: dict[PIIType, str] = {
    PIIType.PHONE: "[電話番号]",
    PIIType.EMAIL: "[メール]",
    PIIType.MY_NUMBER: "[マイナンバー]",
    PIIType.CREDIT_CARD: "[クレジットカード]",
    PIIType.BANK_ACCOUNT: "[口座番号]",
    PIIType.POSTAL_CODE: "[郵便番号]",
    PIIType.DATE_OF_BIRTH: "[生年月日]",
}


class PIIMatch(BaseModel):
    """A single PII detection result."""
    pii_type: PIIType
    value: str = Field(description="The matched PII text")
    start: int = Field(description="Start position in the original text")
    end: int = Field(description="End position in the original text")
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Detection confidence (1.0 = exact pattern match)",
    )


class PIIReport(BaseModel):
    """Structured report of all PII found in a text."""
    has_pii: bool = Field(description="Whether any PII was detected")
    matches: list[PIIMatch] = Field(default_factory=list)
    pii_types_found: list[PIIType] = Field(default_factory=list)
    total_count: int = 0
    masked_text: Optional[str] = Field(
        default=None,
        description="Text with PII replaced by mask labels",
    )


# ---------------------------------------------------------------------------
# Pattern definitions
# ---------------------------------------------------------------------------

# Phone: unified pattern that captures all Japanese phone number formats.
# Mobile (070/080/090), IP (050), landline (0X/0XX), toll-free (0120/0800)
_PHONE_PATTERN = re.compile(
    r"(?<!\d)"
    r"(?:"
    r"0[789]0[- ]?\d{4}[- ]?\d{4}"       # mobile 070/080/090
    r"|050[- ]?\d{4}[- ]?\d{4}"           # IP phone
    r"|0120[- ]?\d{3}[- ]?\d{3}"          # toll-free 0120
    r"|0800[- ]?\d{3}[- ]?\d{3}"          # toll-free 0800
    r"|0[1-9][- ]?\d{4}[- ]?\d{4}"       # landline 2-digit area (03, 06, etc.)
    r"|0[1-9]\d{1,2}[- ]?\d{3,4}[- ]?\d{4}"  # landline 3-4 digit area
    r")"
    r"(?!\d)"
)

# Email address (RFC 5322 simplified)
_EMAIL_PATTERN = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
)

# My Number (マイナンバー): exactly 12 digits, optionally with spaces
_MY_NUMBER_PATTERN = re.compile(
    r"(?<!\d)(\d{4}[\s ]?\d{4}[\s ]?\d{4})(?!\d)"
)
_MY_NUMBER_CONTEXT = re.compile(
    r"マイナンバー|個人番号|my\s*number", re.IGNORECASE
)

# Credit card: 16 digits in groups of 4 (with hyphens or spaces) or continuous
_CREDIT_CARD_PATTERN = re.compile(
    r"(?<!\d)(\d{4})[- ]?(\d{4})[- ]?(\d{4})[- ]?(\d{4})(?!\d)"
)
_CREDIT_CARD_CONTEXT = re.compile(
    r"カード|card|クレジット|credit|visa|master|jcb|amex", re.IGNORECASE
)

# Bank account: 7-digit number with context keywords
_BANK_ACCOUNT_PATTERN = re.compile(r"(?<!\d)(\d{7})(?!\d)")
_BANK_ACCOUNT_CONTEXT = re.compile(
    r"口座|振込|振替|預金|bank|account"
)

# Postal code: must have 〒 prefix to distinguish from random 3-4 digit groups
_POSTAL_CODE_PATTERN = re.compile(
    r"〒\s?(\d{3})[- ](\d{4})(?!\d)"
)
# Secondary: XXX-XXXX without 〒 but only if it doesn't overlap with phone
_POSTAL_CODE_BARE_PATTERN = re.compile(
    r"(?<!\d)(\d{3})-(\d{4})(?!\d)"
)

# Date of birth: YYYY年MM月DD日, YYYY/MM/DD, YYYY-MM-DD
_DOB_PATTERN = re.compile(
    r"(\d{4})[年/\-](\d{1,2})[月/\-](\d{1,2})[日]?"
)
_DOB_CONTEXT = re.compile(
    r"生年月日|誕生日|birthday|date\s*of\s*birth|dob|born", re.IGNORECASE
)


def _overlaps_any(start: int, end: int, ranges: set[tuple[int, int]]) -> bool:
    """Check if a span overlaps with any existing range."""
    for rs, re_ in ranges:
        if not (end <= rs or start >= re_):
            return True
    return False


def _luhn_check(card_number: str) -> bool:
    """Validate credit card number using Luhn algorithm."""
    digits = [int(d) for d in card_number]
    digits.reverse()
    total = 0
    for i, d in enumerate(digits):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


class PIIDetector:
    """Regex-based PII detector for Japanese text (MVP).

    Usage:
        detector = PIIDetector()
        matches = detector.detect("電話番号は090-1234-5678です")
        masked = detector.mask("メールはtest@example.comです")
        report = detector.detect_and_report(text)
    """

    def detect(self, text: str) -> list[PIIMatch]:
        """Detect PII in text, return list of matches with type and position.

        Detection order matters — higher-priority patterns are matched first
        and their ranges are reserved to prevent false positives from
        lower-priority patterns.
        """
        if not text:
            return []

        matches: list[PIIMatch] = []
        reserved: set[tuple[int, int]] = set()

        # 1. Phone numbers (high priority — prevents postal code false positives)
        for m in _PHONE_PATTERN.finditer(text):
            matches.append(PIIMatch(
                pii_type=PIIType.PHONE,
                value=m.group(),
                start=m.start(),
                end=m.end(),
                confidence=0.95,
            ))
            reserved.add((m.start(), m.end()))

        # 2. Email
        for m in _EMAIL_PATTERN.finditer(text):
            if _overlaps_any(m.start(), m.end(), reserved):
                continue
            matches.append(PIIMatch(
                pii_type=PIIType.EMAIL,
                value=m.group(),
                start=m.start(),
                end=m.end(),
                confidence=0.99,
            ))
            reserved.add((m.start(), m.end()))

        # 3. Postal code (〒 prefix = high confidence)
        for m in _POSTAL_CODE_PATTERN.finditer(text):
            if _overlaps_any(m.start(), m.end(), reserved):
                continue
            matches.append(PIIMatch(
                pii_type=PIIType.POSTAL_CODE,
                value=m.group(),
                start=m.start(),
                end=m.end(),
                confidence=0.90,
            ))
            reserved.add((m.start(), m.end()))

        # 3b. Bare postal code (XXX-XXXX, only if not overlapping with phone)
        for m in _POSTAL_CODE_BARE_PATTERN.finditer(text):
            if _overlaps_any(m.start(), m.end(), reserved):
                continue
            # Only match if the first group looks like a real postal code area
            first = int(m.group(1))
            if 1 <= first <= 999:
                matches.append(PIIMatch(
                    pii_type=PIIType.POSTAL_CODE,
                    value=m.group(),
                    start=m.start(),
                    end=m.end(),
                    confidence=0.60,
                ))
                reserved.add((m.start(), m.end()))

        # 4. Credit card (16 digits)
        has_cc_context = bool(_CREDIT_CARD_CONTEXT.search(text))
        for m in _CREDIT_CARD_PATTERN.finditer(text):
            if _overlaps_any(m.start(), m.end(), reserved):
                continue
            digits_only = m.group(1) + m.group(2) + m.group(3) + m.group(4)
            if len(digits_only) != 16:
                continue
            confidence = 0.85 if has_cc_context else 0.60
            if _luhn_check(digits_only):
                confidence = min(confidence + 0.10, 1.0)
            matches.append(PIIMatch(
                pii_type=PIIType.CREDIT_CARD,
                value=m.group(),
                start=m.start(),
                end=m.end(),
                confidence=confidence,
            ))
            reserved.add((m.start(), m.end()))

        # 5. My Number (12 digits)
        has_mn_context = bool(_MY_NUMBER_CONTEXT.search(text))
        for m in _MY_NUMBER_PATTERN.finditer(text):
            digits_only = re.sub(r"\s", "", m.group(1))
            if len(digits_only) != 12:
                continue
            if _overlaps_any(m.start(), m.end(), reserved):
                continue
            confidence = 0.90 if has_mn_context else 0.50
            matches.append(PIIMatch(
                pii_type=PIIType.MY_NUMBER,
                value=m.group(1),
                start=m.start(),
                end=m.end(),
                confidence=confidence,
            ))
            reserved.add((m.start(), m.end()))

        # 6. Bank account (7 digits, only with context)
        has_bank_context = bool(_BANK_ACCOUNT_CONTEXT.search(text))
        if has_bank_context:
            for m in _BANK_ACCOUNT_PATTERN.finditer(text):
                if _overlaps_any(m.start(), m.end(), reserved):
                    continue
                context_window = text[max(0, m.start() - 30):m.end() + 30]
                if _BANK_ACCOUNT_CONTEXT.search(context_window):
                    matches.append(PIIMatch(
                        pii_type=PIIType.BANK_ACCOUNT,
                        value=m.group(1),
                        start=m.start(),
                        end=m.end(),
                        confidence=0.75,
                    ))
                    reserved.add((m.start(), m.end()))

        # 7. Date of birth (only with context)
        has_dob_context = bool(_DOB_CONTEXT.search(text))
        for m in _DOB_PATTERN.finditer(text):
            if _overlaps_any(m.start(), m.end(), reserved):
                continue
            year = int(m.group(1))
            month = int(m.group(2))
            day = int(m.group(3))
            if not (1920 <= year <= 2025 and 1 <= month <= 12 and 1 <= day <= 31):
                continue
            context_window = text[max(0, m.start() - 30):m.end() + 30]
            local_context = bool(_DOB_CONTEXT.search(context_window))
            if has_dob_context or local_context:
                confidence = 0.90 if local_context else 0.60
                matches.append(PIIMatch(
                    pii_type=PIIType.DATE_OF_BIRTH,
                    value=m.group(),
                    start=m.start(),
                    end=m.end(),
                    confidence=confidence,
                ))
                reserved.add((m.start(), m.end()))

        # Sort by position
        matches.sort(key=lambda x: x.start)
        return matches

    def mask(self, text: str) -> str:
        """Replace detected PII with masked versions like [電話番号], [メール] etc."""
        if not text:
            return text

        matches = self.detect(text)
        if not matches:
            return text

        # Replace from end to preserve positions
        result = text
        for m in reversed(matches):
            label = _MASK_LABELS.get(m.pii_type, f"[{m.pii_type.value}]")
            result = result[:m.start] + label + result[m.end:]

        return result

    def detect_and_report(self, text: str) -> PIIReport:
        """Detect PII and return structured report."""
        matches = self.detect(text)
        pii_types = list({m.pii_type for m in matches})

        masked_text = None
        if matches:
            masked_text = text
            for m in reversed(matches):
                label = _MASK_LABELS.get(m.pii_type, f"[{m.pii_type.value}]")
                masked_text = masked_text[:m.start] + label + masked_text[m.end:]

        return PIIReport(
            has_pii=len(matches) > 0,
            matches=matches,
            pii_types_found=pii_types,
            total_count=len(matches),
            masked_text=masked_text,
        )
