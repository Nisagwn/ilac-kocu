"""Model fiyat tablosu (USD / 1M token, Anthropic birinci taraf API fiyatları, 2026-09).

Önbellek yazma (5 dk TTL) girdi fiyatının 1.25 katıdır; önbellek okuma modele göre tabloda.
Bilinmeyen model için en pahalı satır kullanılır: maliyet limiti asla eksik hesaplanmaz.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPrice:
    input: float
    output: float
    cache_read: float


PRICES: dict[str, ModelPrice] = {
    "claude-fable-5-1": ModelPrice(10.0, 50.0, 0.25),
    "claude-fable-5": ModelPrice(10.0, 50.0, 1.0),
    "claude-opus-5-5": ModelPrice(4.0, 20.0, 0.20),
    "claude-opus-5": ModelPrice(5.0, 25.0, 0.50),
    "claude-opus-4-8": ModelPrice(5.0, 25.0, 0.50),
    "claude-opus-4-7": ModelPrice(5.0, 25.0, 0.50),
    "claude-opus-4-6": ModelPrice(5.0, 25.0, 0.50),
    "claude-sonnet-5-5": ModelPrice(2.0, 10.0, 0.20),
    "claude-sonnet-5": ModelPrice(2.0, 10.0, 0.20),
    "claude-sonnet-4-6": ModelPrice(3.0, 15.0, 0.30),
    "claude-haiku-4-5": ModelPrice(1.0, 5.0, 0.10),
    # OpenRouter kimlikleri (openrouter.ai/api/v1/models, 2026-10). OpenRouter yanıtta gerçek maliyeti de
    # döndürür; tablo yalnızca maliyet bildirilmezse kullanılır.
    "anthropic/claude-opus-5.5": ModelPrice(4.0, 20.0, 0.40),
    "anthropic/claude-sonnet-5.5": ModelPrice(2.0, 10.0, 0.20),
    "anthropic/claude-haiku-4.5": ModelPrice(1.0, 5.0, 0.10),
    "openai/gpt-5.4-mini": ModelPrice(0.75, 4.5, 0.075),
    "google/gemini-3.5-flash": ModelPrice(1.5, 9.0, 0.15),
}
FALLBACK_PRICE = ModelPrice(10.0, 50.0, 1.0)
CACHE_WRITE_MULTIPLIER = 1.25


FREE_PRICE = ModelPrice(0.0, 0.0, 0.0)


def price_for(model: str) -> ModelPrice:
    if model.endswith(":free"):  # OpenRouter ücretsiz uçları kredi harcamaz
        return FREE_PRICE
    return PRICES.get(model, FALLBACK_PRICE)


def cost_usd(model: str, input_tokens: int, output_tokens: int, cache_read_tokens: int = 0,
             cache_write_tokens: int = 0) -> float:
    p = price_for(model)
    total = (
        input_tokens * p.input
        + cache_write_tokens * p.input * CACHE_WRITE_MULTIPLIER
        + cache_read_tokens * p.cache_read
        + output_tokens * p.output
    )
    return round(total / 1_000_000, 6)
