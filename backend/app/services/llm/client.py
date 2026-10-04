"""Anthropic istemcisi ve ortak çağrı yardımcıları.

İstemci tembel oluşturulur (oluşturma ağ çağrısı yapmaz); testler sahte istemci enjekte eder.
"""

from functools import lru_cache

from app.config import Settings

# Güvenlik sınıflandırıcısı isteği reddederse sunucu tarafında önerilen yedek modelle tekrar dener
SERVER_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LlmError(RuntimeError):
    """Model çağrısı kullanılabilir bir yanıt üretmedi (ret, kesilme, şema dışı çıktı...)."""


@lru_cache(maxsize=4)
def _cached_client(api_key: str, timeout: float, max_retries: int):
    import anthropic

    return anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=max_retries)


def get_client(settings: Settings):
    if not settings.anthropic_api_key:
        raise LlmError("ANTHROPIC_API_KEY tanımlı değil")
    return _cached_client(settings.anthropic_api_key, settings.llm_timeout_seconds, settings.llm_max_retries)


def create_message(client, settings: Settings, **kwargs):
    """messages.create; yapılandırmaya göre sunucu tarafı yedek model (fallbacks) ve effort ekler."""
    output_config = dict(kwargs.pop("output_config", None) or {})
    if settings.llm_effort:
        output_config.setdefault("effort", settings.llm_effort)
    if output_config:
        kwargs["output_config"] = output_config
    kwargs.setdefault("model", settings.llm_model)
    kwargs.setdefault("max_tokens", settings.llm_max_tokens)
    if settings.llm_server_fallbacks:
        return client.beta.messages.create(betas=[SERVER_FALLBACK_BETA], fallbacks="default", **kwargs)
    return client.messages.create(**kwargs)


def assistant_content_for_replay(content: list) -> list:
    """Yanıt içeriğini döngüde geri göndermek için hazırlar.

    Yanıt ortasında yedek modele geçildiyse (fallback bloğu), son fallback bloğundan önceki
    thinking/tool_use blokları geri gönderilmez (API kuralı). Aksi halde içerik aynen döner.
    """
    idx = max((i for i, b in enumerate(content) if getattr(b, "type", None) == "fallback"), default=None)
    if idx is None:
        return list(content)
    drop = {"thinking", "redacted_thinking", "tool_use"}
    return [b for i, b in enumerate(content) if i > idx or getattr(b, "type", None) not in drop]


def text_of(content: list) -> str:
    return "".join(getattr(b, "text", "") for b in content if getattr(b, "type", None) == "text").strip()
