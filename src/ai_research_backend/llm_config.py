"""Lightweight LLM configuration using litellm directly.

Provides the same ``.call(messages=...)`` interface that ``agents.py`` expects,
without importing crewai (which transitively pulls in chromadb + onnxruntime and
causes OOM kills on memory-constrained containers).
"""

import logging
import os

import litellm

logger = logging.getLogger(__name__)


class LiteLLMWrapper:
    """Thin wrapper around ``litellm.completion`` with a crewai-compatible
    ``.call()`` interface so ``agents.py`` can use it as a drop-in replacement.
    """

    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: str,
        temperature: float = 0.7,
        max_tokens: int = 8192,
    ):
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self.temperature = temperature
        self.max_tokens = max_tokens

    def call(self, messages: list[dict]) -> str:
        response = litellm.completion(
            model=self.model,
            messages=messages,
            api_base=self.base_url,
            api_key=self.api_key,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        return response.choices[0].message.content


# ---------------------------------------------------------------------------
# Module-level singletons (mirror the old crew.py exports)
# ---------------------------------------------------------------------------

active_llm = LiteLLMWrapper(
    model=f"openai/{os.getenv('OLLAMA_MODEL', 'gpt-oss:120b-cloud')}",
    base_url=os.getenv("OLLAMA_API_BASE", "https://ollama.com/v1"),
    api_key=os.getenv("OLLAMA_API_KEY", ""),
    temperature=0.7,
    max_tokens=8192,
)

_sub_model = os.getenv("OLLAMA_SUB_MODEL", "ministral-3:3b")
if _sub_model:
    sub_llm = LiteLLMWrapper(
        model=f"openai/{_sub_model}",
        base_url=os.getenv("OLLAMA_SUB_API_BASE", "https://ollama.com/v1"),
        api_key=os.getenv("OLLAMA_SUB_API_KEY", "ollama"),
        temperature=0.5,
        max_tokens=4096,
    )
else:
    sub_llm = active_llm

logger.info(
    "LLM config loaded (litellm direct): main=%s  sub=%s",
    active_llm.model,
    sub_llm.model,
)
