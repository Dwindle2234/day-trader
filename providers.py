"""
AI provider abstraction layer.
Supports Groq (default) and Anthropic Claude.
Switch via AI_PROVIDER in .env — no code changes needed.

Groq:      fast, free tier, Llama 3.3 70B
Anthropic: Claude Sonnet, more consistent JSON, better for production
"""
import json
import os
import re
from abc import ABC, abstractmethod

from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential


# ── Base interface ─────────────────────────────────────────────────────────────

class AIProvider(ABC):
    """All providers must implement this interface."""

    @abstractmethod
    def complete(self, system: str, user: str, max_tokens: int = 1000) -> tuple[str, int, int]:
        """
        Send a prompt and return (response_text, prompt_tokens, completion_tokens).
        """
        ...

    def name(self) -> str:
        return self.__class__.__name__


# ── Groq provider ──────────────────────────────────────────────────────────────

class GroqProvider(AIProvider):
    """
    Uses the Groq API with Llama 3.3 70B (or configured model).
    Free tier: 30 req/min, 500 req/day — plenty for paper trading.
    Get a key at: https://console.groq.com
    """

    DEFAULT_MODEL = "llama-3.3-70b-versatile"

    def __init__(self):
        try:
            from groq import Groq
        except ImportError:
            raise RuntimeError("groq package not installed — run: pip install groq")

        api_key = os.environ.get("GROQ_API_KEY", "")
        if not api_key:
            raise RuntimeError("GROQ_API_KEY not set in environment")

        self.client = Groq(api_key=api_key)
        self.model  = os.environ.get("GROQ_MODEL", self.DEFAULT_MODEL)
        logger.info(f"[ai] Using Groq provider — model: {self.model}")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30), reraise=True)
    def complete(self, system: str, user: str, max_tokens: int = 1000) -> tuple[str, int, int]:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system",    "content": system},
                {"role": "user",      "content": user},
            ],
            max_tokens=max_tokens,
            temperature=0.1,   # low temperature = more deterministic JSON
        )
        text   = response.choices[0].message.content or ""
        usage  = response.usage
        return text, usage.prompt_tokens, usage.completion_tokens

    def name(self) -> str:
        return f"groq/{self.model}"


# ── Anthropic provider ────────────────────────────────────────────────────────

class AnthropicProvider(AIProvider):
    """
    Uses the Anthropic Claude API.
    More reliable JSON output — recommended for live trading.
    Get a key at: https://console.anthropic.com
    """

    DEFAULT_MODEL = "claude-sonnet-4-5"

    def __init__(self):
        try:
            import anthropic as anthropic_sdk
        except ImportError:
            raise RuntimeError("anthropic package not installed — run: pip install anthropic")

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set in environment")

        self.client = anthropic_sdk.Anthropic(api_key=api_key)
        self.model  = os.environ.get("ANTHROPIC_MODEL", self.DEFAULT_MODEL)
        logger.info(f"[ai] Using Anthropic provider — model: {self.model}")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30), reraise=True)
    def complete(self, system: str, user: str, max_tokens: int = 1000) -> tuple[str, int, int]:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text  = response.content[0].text if response.content else ""
        usage = response.usage
        return text, usage.input_tokens, usage.output_tokens

    def name(self) -> str:
        return f"anthropic/{self.model}"


# ── Factory ───────────────────────────────────────────────────────────────────

_provider_instance: AIProvider | None = None

def get_provider() -> AIProvider:
    """
    Returns a singleton AI provider based on AI_PROVIDER env var.
    AI_PROVIDER=groq       → GroqProvider      (default)
    AI_PROVIDER=anthropic  → AnthropicProvider
    """
    global _provider_instance
    if _provider_instance is not None:
        return _provider_instance

    provider_name = os.environ.get("AI_PROVIDER", "groq").strip().lower()

    if provider_name == "groq":
        _provider_instance = GroqProvider()
    elif provider_name == "anthropic":
        _provider_instance = AnthropicProvider()
    else:
        raise ValueError(f"Unknown AI_PROVIDER='{provider_name}'. Use 'groq' or 'anthropic'.")

    return _provider_instance
