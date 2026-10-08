"""Provider-neutral errors exposed by the LLM layer."""

from __future__ import annotations

from typing import Any


class LLMError(Exception):
    """Base error with a stable code safe to send to the frontend."""

    code = "LLM_ERROR"
    default_message = "The AI provider returned an error."

    def __init__(self, message: str | None = None, *, provider: str | None = None, cause: Exception | None = None) -> None:
        self.provider = provider
        self.cause = cause
        self.message = message or self.default_message
        super().__init__(self.message)

    @property
    def user_message(self) -> str:
        return self.message

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.user_message, "provider": self.provider}


class LLMProviderUnavailable(LLMError):
    code = "LLM_PROVIDER_UNAVAILABLE"
    default_message = "The selected AI provider is unavailable."


class LLMTimeoutError(LLMError):
    code = "LLM_TIMEOUT"
    default_message = "The AI provider took too long to respond."


class LLMAuthenticationError(LLMError):
    code = "LLM_AUTHENTICATION_ERROR"
    default_message = "The AI provider credentials are invalid or missing."


class LLMModelNotFoundError(LLMError):
    code = "LLM_MODEL_NOT_FOUND"
    default_message = "The configured AI model is not available."


class LLMRateLimitError(LLMError):
    code = "LLM_RATE_LIMIT"
    default_message = "The AI provider rate limit was reached."


class FreeProvidersUnavailableError(LLMProviderUnavailable):
    code = "FREE_PROVIDERS_TEMPORARILY_UNAVAILABLE"
    default_message = "All eligible free AI providers are temporarily unavailable. Try again shortly."


class LLMResponseError(LLMError):
    code = "LLM_RESPONSE_ERROR"
    default_message = "The AI provider returned an invalid response."


class LLMThinkingUnsupportedError(LLMError):
    code = "LLM_THINKING_UNSUPPORTED"
    default_message = "This model does not support the requested thinking mode. Configure a hybrid Qwen3 model."


class NoLLMProviderError(LLMProviderUnavailable):
    code = "NO_LLM_PROVIDER"
    default_message = "No AI model is currently available."


class ModelPolicyError(LLMError):
    code = "MODEL_POLICY_BLOCKED"
    default_message = "This model is blocked by the free-only or privacy policy."
