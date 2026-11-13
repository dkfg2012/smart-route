from __future__ import annotations

from functools import lru_cache
from typing import Any


class TokenEstimatorUnavailable(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _encoding():
    try:
        import tiktoken
    except Exception as exc:  # pragma: no cover - exercised in deployment checks
        raise TokenEstimatorUnavailable(
            "tiktoken is required for token estimation. Install gateway requirements."
        ) from exc

    try:
        return tiktoken.encoding_for_model("gpt-4o")
    except Exception:
        return tiktoken.get_encoding("cl100k_base")


def content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                elif "text" in item:
                    parts.append(str(item.get("text", "")))
            else:
                parts.append(str(item))
        return " ".join(part for part in parts if part)
    return "" if content is None else str(content)


def text_from_messages(messages: list[dict[str, Any]], user_only: bool = False) -> str:
    parts: list[str] = []
    for message in messages:
        if user_only and message.get("role") != "user":
            continue
        parts.append(content_to_text(message.get("content", "")))
    return "\n".join(part for part in parts if part)


def count_tokens_text(text: str) -> int:
    return len(_encoding().encode(text))


def count_message_tokens(messages: list[dict[str, Any]]) -> int:
    text = text_from_messages(messages)
    return count_tokens_text(text)

