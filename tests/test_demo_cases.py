from __future__ import annotations

import asyncio

from classifier import classify
from demo_cases import DEMO_CASES
from models import ComplexityLevel
from privacy_filter import check_privacy
from router_middleware import decide_route


class DemoSemanticEngine:
    def classify(self, text: str):
        if "机密" in text or "会议" in text or "KeyError" in text:
            return ComplexityLevel.MEDIUM, 0.9, "semantic_router"
        if "综合深度分析" in text or "合同" in text:
            return ComplexityLevel.RESEARCH, 0.9, "semantic_router"
        if "销售数据" in text or "三件事" in text:
            return ComplexityLevel.COMPLEX, 0.9, "semantic_router"
        if "开天X7" in text:
            return ComplexityLevel.COMPLEX, 0.9, "semantic_router"
        return ComplexityLevel.SIMPLE, 0.9, "semantic_router"


def test_demo_case_privacy_and_s1_cloud_block():
    for case in DEMO_CASES:
        classification = asyncio.run(classify(
            [{"role": "user", "content": case.prompt}],
            semantic_engine=DemoSemanticEngine(),
        ))
        privacy = asyncio.run(check_privacy([{"role": "user", "content": case.prompt}]))
        decision = decide_route(classification, privacy, context_tokens=1000, edge_available=True)
        assert privacy.level.value == case.expected_privacy
        if privacy.level.value == "S1":
            assert decision.tier.value != "cloud"

