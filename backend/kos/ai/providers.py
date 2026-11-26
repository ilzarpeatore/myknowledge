"""Model providers behind one small interface.

Only the Anthropic provider is implemented; others (OpenAI-compatible, Ollama)
would implement the same ``complete_structured`` contract. Calls never include
tools: a model can only return data matching the requested schema, so text read
from a source document cannot make it act (prompt-injection containment).
"""

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Protocol

from pydantic import BaseModel

from kos.config import get_settings

FALLBACK_BETA = "server-side-fallback-2026-07-01"


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass
class ProviderResult:
    parsed: BaseModel | None
    stop_reason: str | None
    model_served: str
    usage: Usage = field(default_factory=Usage)
    request_id: str | None = None
    refusal_category: str | None = None


class ProviderError(Exception):
    """A failure talking to the provider. ``retryable`` follows HTTP semantics."""

    def __init__(self, message: str, retryable: bool):
        super().__init__(message)
        self.retryable = retryable


class Provider(Protocol):
    async def complete_structured(
        self,
        *,
        model: str,
        system: str,
        user_content: list[dict[str, Any]],
        schema: type[BaseModel],
        effort: str,
        max_tokens: int,
        use_fallback: bool,
    ) -> ProviderResult: ...


class AnthropicProvider:
    def __init__(self) -> None:
        import anthropic

        self._anthropic = anthropic
        self._client_instance = None

    @property
    def _client(self):
        """Created on first use. Credentials are resolved by the SDK
        (ANTHROPIC_API_KEY, ...); it also retries 408/409/429/5xx and connection errors."""
        if self._client_instance is None:
            a = self._anthropic
            try:
                self._client_instance = a.AsyncAnthropic(
                    timeout=get_settings().ai_request_timeout_seconds
                )
            except a.CredentialsError as exc:
                raise ProviderError(f"Anthropic credentials error: {exc}", retryable=False) from exc
        return self._client_instance

    async def complete_structured(
        self,
        *,
        model: str,
        system: str,
        user_content: list[dict[str, Any]],
        schema: type[BaseModel],
        effort: str,
        max_tokens: int,
        use_fallback: bool,
    ) -> ProviderResult:
        a = self._anthropic
        kwargs: dict[str, Any] = {}
        if use_fallback:
            # On a safety decline the API re-runs the request on the fallback model
            # Anthropic recommends for that refusal category, inside the same call.
            kwargs = {"betas": [FALLBACK_BETA], "fallbacks": "default"}
        try:
            resp = await self._client.beta.messages.parse(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user_content}],
                output_format=schema,
                output_config={"effort": effort},
                **kwargs,
            )
        except a.BadRequestError as exc:
            raise ProviderError(f"bad request: {exc.message}", retryable=False) from exc
        except (a.AuthenticationError, a.PermissionDeniedError) as exc:
            raise ProviderError(f"credentials rejected: {exc.message}", retryable=False) from exc
        except a.NotFoundError as exc:
            raise ProviderError(
                f"model or endpoint not found: {exc.message}", retryable=False
            ) from exc
        except a.RateLimitError as exc:
            raise ProviderError("rate limited", retryable=True) from exc
        except a.APIStatusError as exc:
            raise ProviderError(
                f"API error {exc.status_code}: {exc.message}", retryable=exc.status_code >= 500
            ) from exc
        except a.APIConnectionError as exc:
            raise ProviderError(f"connection error: {exc}", retryable=True) from exc
        except a.CredentialsError as exc:
            raise ProviderError(f"Anthropic credentials error: {exc}", retryable=False) from exc
        except TypeError as exc:
            # Raised by the SDK before sending when no credentials can be resolved.
            if "authentication" in str(exc).lower():
                raise ProviderError(
                    "no Anthropic credentials configured (set ANTHROPIC_API_KEY)", retryable=False
                ) from exc
            raise

        u = resp.usage
        category = None
        if resp.stop_reason == "refusal" and getattr(resp, "stop_details", None):
            category = getattr(resp.stop_details, "category", None)
        return ProviderResult(
            parsed=resp.parsed_output if resp.stop_reason != "refusal" else None,
            stop_reason=resp.stop_reason,
            model_served=resp.model,
            usage=Usage(
                input_tokens=u.input_tokens or 0,
                output_tokens=u.output_tokens or 0,
                cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
                cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
            ),
            request_id=getattr(resp, "_request_id", None),
            refusal_category=category,
        )


@lru_cache
def get_provider() -> Provider:
    return AnthropicProvider()
