from __future__ import annotations

import os
import re
from typing import Any

from token_estimator import text_from_messages


LOCAL_FILE_PATTERNS = [
    r"(本地|本机|电脑|当前目录|项目目录|文件夹|目录|磁盘|硬盘).{0,12}(文件|文档|资料|代码|日志)",
    r"(文件|文档|资料|代码|日志).{0,12}(本地|本机|电脑|当前目录|项目目录|文件夹|目录|磁盘|硬盘)",
    r"\blocal\s+(file|files|folder|folders|directory|directories|repo|repository)\b",
    r"\b(search|find|scan|grep|rg)\b.{0,20}\b(file|files|folder|directory|repo|repository)\b",
]

LOCAL_ACTION_PATTERNS = [
    r"(打开|启动|关闭|切换|点击|输入|复制|粘贴|截图|截屏|运行|执行).{0,20}(浏览器|chrome|edge|应用|app|软件|窗口|终端|命令|文件夹|目录|文件|网页|url|链接)",
    r"(浏览器|chrome|edge|应用|app|软件|窗口|终端|命令|文件夹|目录|文件|网页|url|链接).{0,20}(打开|启动|关闭|切换|点击|输入|复制|粘贴|截图|截屏|运行|执行)",
    r"\b(open|launch|start|close|switch|click|type|copy|paste|screenshot|run|execute)\b.{0,24}\b(browser|chrome|edge|app|application|window|terminal|command|file|folder|directory|url|link)\b",
]

SYNTHESIS_PATTERNS = [
    r"总结|分析|比较|归纳|提取|审阅|报告|论文|综合|回答|生成|改写|解释",
    r"\b(summarize|analyze|compare|extract|review|report|paper|synthesize|explain)\b",
]


def request_text(messages: list[dict[str, Any]]) -> str:
    return text_from_messages(messages, user_only=True)


def is_local_file_request_text(text: str) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in LOCAL_FILE_PATTERNS)


def is_local_file_request(messages: list[dict[str, Any]]) -> bool:
    return is_local_file_request_text(request_text(messages))


def is_local_action_request_text(text: str) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in LOCAL_ACTION_PATTERNS)


def is_local_action_request(messages: list[dict[str, Any]]) -> bool:
    return is_local_action_request_text(request_text(messages))


def needs_local_file_synthesis_text(text: str) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in SYNTHESIS_PATTERNS)


def needs_local_file_synthesis(messages: list[dict[str, Any]]) -> bool:
    return needs_local_file_synthesis_text(request_text(messages))


def configured_device_model_size_b() -> float:
    configured = os.getenv("DEVICE_MODEL_SIZE_B")
    if configured:
        try:
            return float(configured)
        except ValueError:
            return 0.0

    model_name = os.getenv("OLLAMA_DEVICE_MODEL", "qwen3:1.5b")
    match = re.search(r"(\d+(?:\.\d+)?)\s*b\b", model_name, flags=re.IGNORECASE)
    if not match:
        return 0.0
    return float(match.group(1))
