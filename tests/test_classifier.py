from __future__ import annotations

import asyncio

import pytest

from classifier import ClassifierUnavailable, ROUTE_UTTERANCES, classify, routellm_to_complexity
from models import ComplexityLevel


class StaticSemantic:
    def __init__(self, result):
        self.result = result

    def classify(self, text):
        return self.result


class StaticRouteLLM:
    def __init__(self, result):
        self.result = result

    def classify(self, text):
        return self.result


def test_semantic_router_result_is_used():
    result = asyncio.run(classify(
        [{"role": "user", "content": "你好"}],
        semantic_engine=StaticSemantic((ComplexityLevel.SIMPLE, 0.91, "semantic_router")),
    ))
    assert result.level == ComplexityLevel.SIMPLE
    assert result.classifier_used == "semantic_router"


def test_routellm_used_when_semantic_has_no_route():
    result = asyncio.run(classify(
        [{"role": "user", "content": "请进行深度分析"}],
        semantic_engine=StaticSemantic(None),
        routellm_engine=StaticRouteLLM((ComplexityLevel.RESEARCH, 0.77, "routellm_mf")),
    ))
    assert result.level == ComplexityLevel.RESEARCH
    assert result.classifier_used == "routellm_mf"


def test_no_heuristic_fallback_when_engines_unavailable():
    class FailingRouteLLM:
        def classify(self, text):
            raise RuntimeError("routellm unavailable")

    with pytest.raises(ClassifierUnavailable):
        asyncio.run(classify(
            [{"role": "user", "content": "任意文本"}],
            semantic_engine=StaticSemantic(None),
            routellm_engine=FailingRouteLLM(),
        ))


def test_research_route_contains_web_search_paper_utterances():
    utterances = ROUTE_UTTERANCES["RESEARCH"]
    assert "帮我写一篇论文，百度搜索下" in utterances
    assert "搜索资料并写一篇论文" in utterances


def test_routellm_mapping():
    assert routellm_to_complexity(0.1)[0] == ComplexityLevel.SIMPLE
    assert routellm_to_complexity(0.3)[0] == ComplexityLevel.MEDIUM
    assert routellm_to_complexity(0.5)[0] == ComplexityLevel.COMPLEX
    assert routellm_to_complexity(0.7)[0] == ComplexityLevel.RESEARCH
    assert routellm_to_complexity(0.9)[0] == ComplexityLevel.REASONING
