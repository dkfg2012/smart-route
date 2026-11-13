from __future__ import annotations

import os
import time
from typing import Any

from models import RouteDecision, RouteTier
from token_estimator import count_tokens_text, text_from_messages


class ModelClientError(RuntimeError):
    pass


def _mock_answer(messages: list[dict[str, Any]], tier: RouteTier) -> str:
    prompt = text_from_messages(messages, user_only=True)
    lower = prompt.lower()
    if "keyerror" in lower or "model_info" in lower:
        return (
            "问题在于 selected 可能为 None，或 selected 的结构中没有 model_info。"
            "修复方式是校验 complexity 映射结果，并使用配置中真实存在的字段，例如 selected['name']。"
        )
    if (
        "本地文件" in prompt
        or "本机文件" in prompt
        or "当前目录" in prompt
        or "本机资料目录" in prompt
        or "项目目录" in prompt
        or "日志文件" in prompt
        or "安装手册" in prompt
        or "local file" in lower
    ):
        return (
            "已识别为本地文件检索请求。调度策略会优先在端侧处理本地数据，并默认阻止上云；"
            "当前演示环境尚未接入真实文件检索工具，因此不会实际扫描磁盘。"
            "接入受控文件检索后，应限制搜索根目录、记录命中文件路径，并只把必要片段交给后续总结模型。"
        )
    if "连通无环图" in prompt or "数学推导" in prompt or "graph theory" in lower:
        return (
            "证明思路：取一个连通无环图 G。由于无环，G 是树。对节点数 n 做归纳："
            "n=1 时边数为 0，命题成立；若 n>1，树中必存在叶子节点，删除该叶子及其唯一 incident edge 后，"
            "剩余图仍为连通无环图，节点数为 n-1。由归纳假设其边数为 n-2，"
            "加回被删除的一条边后总边数为 n-1。因此任意连通无环图的边数等于节点数减一。"
        )
    if "改得更自然" in prompt or "润色" in prompt or "改顺" in prompt:
        return "这个方案整体可行，但目前执行效率偏低，建议进一步优化性能后再推进。"
    if "行动项" in prompt or "售后反馈" in prompt:
        return "行动项：1. 排查交付慢的瓶颈并给出新的交付排期；2. 重写关键文档并补充示例；3. 建立售后响应 SLA 和升级机制。"
    if "论文" in prompt or "百度搜索" in prompt or "搜索" in prompt or "research paper" in lower:
        return (
            "论文初稿建议题目：《端边云协同推理调度系统的架构设计与性能优化》。"
            "摘要：本文围绕端侧低延迟、边侧高吞吐与云侧强推理能力的协同调度展开，"
            "分析任务复杂度分类、隐私分级、语义缓存和成本控制在混合推理系统中的作用。"
            "正文可按研究背景、相关工作、系统架构、路由策略、实验设计、结果分析与结论展开；"
            "需要真实引用时，应接入联网检索或人工补充可核验来源。"
        )
    if "会议" in prompt or "待办" in prompt:
        return "关键决策：完成演示目标并追加预算。待办事项：李工采购 GPU 服务器，软件接口补齐，周五 10 点继续会议。"
    if "合同" in prompt or "知识产权" in prompt or "违约" in prompt:
        return "主要风险包括交付逾期、知识产权边界不完整和争议解决成本。建议补充验收细则、保密义务和违约上限条款。"
    if "趋势" in prompt or "销售额" in prompt or "退货率" in prompt:
        return "趋势分析：3 月后销售恢复增长，5 月表现最好。建议复盘 2 月下滑原因，延续低退货率策略并给出 Q2 滚动预测。"
    if "moe" in lower or "混合专家" in prompt:
        return "邮件草稿、MoE 解释和 Markdown 表格已拆分完成。MoE 通过激活部分专家降低成本，并提升大模型容量利用率。"
    if "内存" in prompt and "tops" in lower:
        return "开天 X7 最多可扩展到 64GB 内存，AI 算力为 8 TOPS，在 40°C 环境下处于 0~55°C 工作范围内。"
    if "风险" in prompt or "综合" in prompt or "分析" in prompt:
        return "核心问题包括成本上升、交付周期和竞争压力。建议优化定价、缩短交付链路，并用 P 系列产品降低生产成本。"
    if "总结" in prompt or "摘要" in prompt:
        return "摘要：材料描述了关键背景、约束和结论。敏感内容已按本地策略处理，未扩大传播范围。"
    return "已将原句改写为更专业的表达：我会在明天前向您提交该报告。"


async def _post_openai_compatible(
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
    timeout: float,
) -> dict[str, Any]:
    try:
        import httpx
    except Exception as exc:
        raise ModelClientError("httpx is required for real model backend calls") from exc

    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": messages, "stream": False}
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()


def _endpoint_for_tier(tier: RouteTier) -> tuple[str, str, str]:
    shared_base_url = os.getenv("REAL_BACKEND_BASE_URL")
    shared_api_key = os.getenv("REAL_BACKEND_API_KEY")
    shared_model = os.getenv("REAL_BACKEND_MODEL")
    if shared_base_url and shared_api_key and shared_model:
        tier_model = {
            RouteTier.DEVICE: os.getenv("DEVICE_BACKEND_MODEL"),
            RouteTier.EDGE: os.getenv("EDGE_BACKEND_MODEL"),
            RouteTier.CLOUD: os.getenv("CLOUD_BACKEND_MODEL"),
        }[tier]
        return shared_base_url, shared_api_key, tier_model or shared_model

    if tier == RouteTier.DEVICE:
        return (
            os.getenv("DEVICE_OPENAI_BASE_URL", os.getenv("OLLAMA_HOST", "http://ollama:11434") + "/v1"),
            os.getenv("DEVICE_API_KEY", "ollama"),
            os.getenv("DEVICE_BACKEND_MODEL", os.getenv("OLLAMA_DEVICE_MODEL", "qwen3:1.5b")),
        )
    if tier == RouteTier.EDGE:
        return (
            os.getenv("EDGE_OPENAI_BASE_URL", os.getenv("VLLM_HOST", "http://vllm:8000") + "/v1"),
            os.getenv("EDGE_API_KEY", "none"),
            os.getenv("EDGE_BACKEND_MODEL", os.getenv("VLLM_EDGE_MODEL", "deepseek-ai/DeepSeek-V4-Flash")),
        )
    return (
        os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com/v1"),
        os.getenv("DEEPSEEK_API_KEY", ""),
        os.getenv("CLOUD_BACKEND_MODEL", os.getenv("DEEPSEEK_CLOUD_MODEL", "deepseek-v4-pro")),
    )


async def complete_chat(
    decision: RouteDecision,
    messages: list[dict[str, Any]],
    trace_id: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    mode = os.getenv("MODEL_BACKEND_MODE", "mock").lower()
    content: str

    if mode == "mock":
        content = _mock_answer(messages, decision.tier)
    else:
        base_url, api_key, model = _endpoint_for_tier(decision.tier)
        if decision.tier == RouteTier.CLOUD and not api_key:
            raise ModelClientError("DEEPSEEK_API_KEY is required for cloud backend calls")
        data = await _post_openai_compatible(
            base_url=base_url,
            api_key=api_key,
            model=model,
            messages=messages,
            timeout=float(os.getenv("MODEL_BACKEND_TIMEOUT", "120")),
        )
        content = data["choices"][0]["message"]["content"]

    elapsed_ms = (time.perf_counter() - started) * 1000
    prompt_tokens = sum(count_tokens_text(text_from_messages([message])) for message in messages)
    completion_tokens = count_tokens_text(content)

    return {
        "id": f"chatcmpl-{trace_id}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": decision.model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
        "tier": decision.tier.value,
        "route_reason": decision.reason,
        "trace_id": trace_id,
        "latency_ms": elapsed_ms,
    }
