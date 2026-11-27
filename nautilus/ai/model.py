"""ChatOpenAI subclass for OpenAI-compatible Chinese providers.

``langchain-openai`` intentionally only parses the official OpenAI spec:
DeepSeek / Moonshot / Qwen-compatible endpoints stream thinking under the
non-standard ``delta.reasoning_content`` (Ollama: ``delta.reasoning``) and the
base class silently drops it. Nautilus shows the reasoning live, so we
re-attach it to ``additional_kwargs`` per chunk; chunk merging then builds the
full thinking text on the aggregated message.
"""

from __future__ import annotations

from typing import Any

from langchain_openai import ChatOpenAI


class ReasoningChatOpenAI(ChatOpenAI):
    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict,
        default_chunk_class: type,
        base_generation_info: dict | None,
    ) -> Any:
        generation = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        if generation is None:
            return None
        try:
            choices = chunk.get("choices") or []
            delta = (choices[0] or {}).get("delta") if choices else None
            thinking = ""
            if isinstance(delta, dict):
                rc = delta.get("reasoning_content") or delta.get("reasoning")
                if isinstance(rc, str):
                    thinking = rc
            if thinking:
                generation.message.additional_kwargs["reasoning_content"] = thinking
        except (AttributeError, IndexError, TypeError):
            pass
        return generation


def message_thinking(message: Any) -> str:
    kwargs = getattr(message, "additional_kwargs", None) or {}
    value = kwargs.get("reasoning_content", "")
    return value if isinstance(value, str) else ""
