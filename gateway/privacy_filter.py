from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from models import PrivacyCheckResult, PrivacyLevel
from token_estimator import text_from_messages


PII_PATTERNS: dict[str, str] = {
    "id_card": r"\b\d{17}[\dXx]\b",
    "phone": r"\b1[3-9]\d{9}\b",
    "bank_card": r"\b\d{16,19}\b",
    "email": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
    "ip_address": r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
    "salary": r"薪[酬资]|工资|年薪|月薪|奖金",
    "financial": r"账户|余额|转账|汇款|密码|PIN|财务报表|合同金额|预算",
    "confidential": r"机密|保密|内部|涉密|绝密",
    "patent": r"专利|技术秘密|研发|配方|工艺|图纸",
}

S1_KEYWORDS = ["机密", "绝密", "涉密", "研发图纸", "专利", "人事", "工资单", "财务报表", "技术秘密"]
S2_KEYWORDS = ["客户", "合同", "报价", "内部", "采购", "供应商", "预算", "营收", "市场占有率"]


@dataclass(frozen=True)
class ReplacementRule:
    name: str
    pattern: str
    label: str


REPLACEMENT_RULES = [
    ReplacementRule("id_card", PII_PATTERNS["id_card"], "ID"),
    ReplacementRule("phone", PII_PATTERNS["phone"], "PHONE"),
    ReplacementRule("bank_card", PII_PATTERNS["bank_card"], "BANK"),
    ReplacementRule("email", PII_PATTERNS["email"], "EMAIL"),
    ReplacementRule("ip_address", PII_PATTERNS["ip_address"], "IP"),
    ReplacementRule("money", r"\d+(?:\.\d+)?\s*(?:万|万元|元|亿|人民币)", "AMOUNT"),
    ReplacementRule("person", r"张三|李四|王五|张某某|李工|王总|张总", "PERSON"),
    ReplacementRule("company", r"[A-Z] 公司|[A-Z]公司|某[\u4e00-\u9fff]{1,8}公司", "ORG"),
]


def desensitize(text: str) -> tuple[str, dict[str, str]]:
    replacement_map: dict[str, str] = {}
    counters: dict[str, int] = {}
    result = text

    for rule in REPLACEMENT_RULES:
        counters.setdefault(rule.label, 0)

        def replace(match: re.Match[str]) -> str:
            original = match.group(0)
            if original in replacement_map:
                return replacement_map[original]
            counters[rule.label] += 1
            token = f"【{rule.label}-{counters[rule.label]:04d}】"
            replacement_map[original] = token
            return token

        result = re.sub(rule.pattern, replace, result)

    return result, replacement_map


def _detect_patterns(text: str) -> list[str]:
    detected: list[str] = []
    for name, pattern in PII_PATTERNS.items():
        if re.search(pattern, text, flags=re.IGNORECASE):
            detected.append(name)
    return detected


def _has_any(text: str, keywords: list[str]) -> bool:
    return any(keyword in text for keyword in keywords)


async def check_privacy(messages: list[dict[str, Any]]) -> PrivacyCheckResult:
    text = text_from_messages(messages)
    detected = _detect_patterns(text)

    if _has_any(text, S1_KEYWORDS) or "patent" in detected:
        return PrivacyCheckResult(
            level=PrivacyLevel.S1,
            detected_patterns=detected,
            block_cloud=True,
        )

    if detected or _has_any(text, S2_KEYWORDS):
        sanitized, replacement_map = desensitize(text)
        return PrivacyCheckResult(
            level=PrivacyLevel.S2,
            detected_patterns=detected,
            desensitized_text=sanitized,
            replacement_map=replacement_map,
            block_cloud=False,
        )

    return PrivacyCheckResult(
        level=PrivacyLevel.S3,
        detected_patterns=[],
        block_cloud=False,
    )

