"""OpenRouter istemcisi — Anthropic Messages arayüzünü taklit eden ince adaptör.

Koç ve özet kodu Anthropic biçiminde yazılmıştır (`client.messages.create(system=..., tools=..., messages=...)`,
yanıtta `content` blokları, `stop_reason`, `usage`). Bu adaptör aynı çağrıyı OpenRouter'ın OpenAI uyumlu
/chat/completions uç noktasına çevirir ve yanıtı geri çevirir; böylece araç döngüsü, kaynak doğrulama,
guardrail ve bütçe kodu sağlayıcıdan bağımsız kalır. Ek bağımlılık yok (httpx).

Gizlilik: `provider.data_collection="deny"` ile istekler yalnızca istemleri saklamayan / eğitimde
kullanmayan sağlayıcılara yönlendirilir. Modele hasta adı/e-postası zaten gönderilmez.
Maliyet: `usage.include=true` ile OpenRouter her yanıtta gerçek maliyeti (USD) döndürür; bütçe bunu kullanır.
"""

import json
from types import SimpleNamespace
from typing import Any

import httpx

from app.config import Settings

# OpenAI finish_reason -> Anthropic stop_reason
_STOP_REASONS = {
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "length": "max_tokens",
    "content_filter": "refusal",
    "stop": "end_turn",
}


class OpenRouterError(RuntimeError):
    pass


def _text_of_blocks(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")


def _block_attr(block: Any, name: str, default=None):
    return block.get(name, default) if isinstance(block, dict) else getattr(block, name, default)


def convert_messages(system: Any, messages: list[dict]) -> list[dict]:
    """Anthropic system + messages -> OpenAI chat messages."""
    out: list[dict] = []
    system_text = _text_of_blocks(system) if system else ""
    if system_text:
        out.append({"role": "system", "content": system_text})

    for msg in messages:
        role, content = msg["role"], msg["content"]
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        if role == "assistant":
            text_parts, tool_calls = [], []
            for b in content:
                btype = _block_attr(b, "type")
                if btype == "text":
                    text_parts.append(_block_attr(b, "text", ""))
                elif btype == "tool_use":
                    tool_calls.append({
                        "id": _block_attr(b, "id"),
                        "type": "function",
                        "function": {"name": _block_attr(b, "name"),
                                     "arguments": json.dumps(_block_attr(b, "input") or {}, ensure_ascii=False)},
                    })
            item: dict = {"role": "assistant", "content": "".join(text_parts) or None}
            if tool_calls:
                item["tool_calls"] = tool_calls
            out.append(item)
            continue
        # user: tool_result blokları ayrı "tool" mesajları olur; metin blokları tek kullanıcı mesajında birleşir
        texts = []
        for b in content:
            btype = _block_attr(b, "type")
            if btype == "tool_result":
                out.append({"role": "tool", "tool_call_id": _block_attr(b, "tool_use_id"),
                            "content": _text_of_blocks(_block_attr(b, "content", ""))})
            elif btype == "text":
                texts.append(_block_attr(b, "text", ""))
        if texts:
            out.append({"role": "user", "content": "\n\n".join(texts)})
    return out


def convert_tools(tools: list[dict]) -> list[dict]:
    return [
        {"type": "function", "function": {
            "name": t["name"], "description": t.get("description", ""),
            "parameters": t["input_schema"], "strict": bool(t.get("strict", False)),
        }}
        for t in tools
    ]


def build_payload(settings: Settings, **kwargs) -> dict:
    """Anthropic messages.create argümanlarından OpenRouter istek gövdesi üretir.
    Anthropic'e özgü alanlar (cache_control, effort, betas) yok sayılır."""
    payload: dict = {
        "model": kwargs.get("model") or settings.model_name,
        "max_tokens": kwargs.get("max_tokens") or settings.llm_max_tokens,
        "messages": convert_messages(kwargs.get("system"), kwargs.get("messages") or []),
        "usage": {"include": True},
    }
    if settings.openrouter_data_collection:
        payload["provider"] = {"data_collection": settings.openrouter_data_collection}
    if kwargs.get("tools"):
        payload["tools"] = convert_tools(kwargs["tools"])
        choice = (kwargs.get("tool_choice") or {}).get("type")
        if choice in ("none", "auto"):
            payload["tool_choice"] = choice
    fmt = ((kwargs.get("output_config") or {}).get("format")) or {}
    if fmt.get("type") == "json_schema":
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "response", "strict": True, "schema": fmt["schema"]},
        }
    return payload


def convert_response(data: dict) -> SimpleNamespace:
    """OpenAI chat completion -> Anthropic benzeri yanıt nesnesi."""
    if data.get("error"):
        raise OpenRouterError(str(data["error"].get("message", data["error"])))
    choices = data.get("choices") or []
    if not choices:
        raise OpenRouterError("boş yanıt")
    choice = choices[0]
    msg = choice.get("message") or {}
    blocks: list[SimpleNamespace] = []
    if msg.get("content"):
        blocks.append(SimpleNamespace(type="text", text=msg["content"]))
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        blocks.append(SimpleNamespace(type="tool_use", id=tc.get("id"), name=fn.get("name"), input=args))

    finish = choice.get("finish_reason") or choice.get("native_finish_reason")
    stop = "tool_use" if any(b.type == "tool_use" for b in blocks) else _STOP_REASONS.get(finish, "end_turn")
    if msg.get("refusal"):
        stop = "refusal"

    u = data.get("usage") or {}
    cached = ((u.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0
    usage = SimpleNamespace(
        input_tokens=max((u.get("prompt_tokens") or 0) - cached, 0),
        output_tokens=u.get("completion_tokens") or 0,
        cache_read_input_tokens=cached,
        cache_creation_input_tokens=0,
        cost=u.get("cost"),  # OpenRouter'ın bildirdiği gerçek maliyet (USD)
    )
    return SimpleNamespace(content=blocks, stop_reason=stop, usage=usage, model=data.get("model"))


class _Messages:
    def __init__(self, client: "OpenRouterClient"):
        self._client = client

    def create(self, **kwargs) -> SimpleNamespace:
        return self._client.complete(**kwargs)


class OpenRouterClient:
    """`client.messages.create(...)` arayüzü sunar (Anthropic SDK ile aynı)."""

    def __init__(self, settings: Settings, http: httpx.Client | None = None):
        self.settings = settings
        self.messages = _Messages(self)
        self._http = http or httpx.Client(
            base_url=settings.openrouter_base_url,
            timeout=settings.llm_timeout_seconds,
            transport=httpx.HTTPTransport(retries=settings.llm_max_retries),
            headers={
                "Authorization": f"Bearer {settings.openrouter_api_key}",
                "X-Title": "Ilac Uyumu Kocu",
            },
        )

    def complete(self, **kwargs) -> SimpleNamespace:
        payload = build_payload(self.settings, **kwargs)
        resp = self._http.post("/chat/completions", json=payload)
        if resp.status_code >= 400:
            # Gövde hasta verisi içerebilecek istemi yansıtabilir; yalnızca durum kodu loglanır
            raise OpenRouterError(f"OpenRouter HTTP {resp.status_code}")
        return convert_response(resp.json())
