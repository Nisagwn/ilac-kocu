"""Embedding sağlayıcıları.

Varsayılan: `LocalHashEmbedding` — ağ erişimi ve ağır bağımlılık gerektirmeyen, deterministik
"hashing trick" tabanlı vektörler (kelime + Türkçe kök yaklaşımı + kavram eşanlamlıları +
karakter 3-gramları). Anlamsal bir dil modeli değildir; küçük, kürate bir korpus üzerinde
sözcüksel benzerliği aksan/ek farklarına dayanıklı biçimde yakalar.

İsteğe bağlı: `VoyageEmbedding` — EMBEDDING_PROVIDER=voyage ve VOYAGE_API_KEY tanımlıysa
Voyage AI REST API'si (httpx, SDK'sız) kullanılır.

İsteğe bağlı: `FastEmbedEmbedding` — EMBEDDING_PROVIDER=fastembed ile yerel (CPU, ONNX) ve anlamsal,
çok dilli `intfloat/multilingual-e5-small` modeli (384 boyut; mevcut vector(384) sütunu, migration yok).
`fastembed` paketi varsayılan bağımlılıklarda yoktur (requirements-ml.txt); yüklenemezse uyarı loglanır ve
yerel embedding'e düşülür. Sağlayıcı değişince `embedding_model` farklılaştığı için satırlar ilk aramada ya da
`python -m app.services.rag.reindex` ile yeniden embed edilir.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from collections import Counter
from functools import lru_cache
from typing import Protocol

import httpx

from app.config import get_settings
from app.services.guardrails import normalize
from app.services.rag.vector_type import EMBEDDING_DIM

log = logging.getLogger(__name__)

LOCAL_MODEL_ID = f"local-hash-v1:{EMBEDDING_DIM}"


class EmbeddingProvider(Protocol):
    model_id: str
    dim: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


def l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0:
        return vec
    return [x / norm for x in vec]


def cosine(a: list[float], b: list[float]) -> float:
    # vektörler L2-normalize olduğundan iç çarpım = cosine benzerliği
    return sum(x * y for x, y in zip(a, b, strict=False))


# ---------------------------------------------------------------- metin işleme
_TOKEN_RE = re.compile(r"[a-z0-9]+")

# normalize edilmiş (aksansız) Türkçe + İngilizce (openFDA metinleri) dolgu kelimeleri
STOPWORDS = frozenset(
    """
    ve veya ile bir bu su o da de mi mu ne nasil neden niye icin gibi ben sen biz siz onlar cok daha en her
    ama fakat ki ya ise kadar sonra once var yok olarak olan olur olursa sey bana beni benim senin bunu
    simdi hep hic hem ya acaba lazim gerek gerekir mi miyim misin mudur musun nedir kac hangi nerede zaman
    the and of to in a an is are be with for or on by as at may can this that it from not if
    """.split()
)

# anlam taşımayan yaygın fiil kökleri ("yapmalı mıyım", "istiyorum", "ediyor")
STOP_PREFIXES = ("yap", "istiyor", "isterim", "ediyor", "ederim", "edeyim", "oluyor", "olmali")

# Çok kelimeli ifadeler -> tek kavram kelimesi (normalize edilmiş metin üzerinde)
PHRASES = [
    (re.compile(r"\bkan bas\w*"), " tansiyon "),
    (re.compile(r"\bdusuk (kan )?seker\w*"), " hipoglisemi "),
    (re.compile(r"\bseker\w* dus\w*"), " hipoglisemi "),
    (re.compile(r"\byuksek (kan )?seker\w*"), " hiperglisemi "),
    (re.compile(r"\bkan seker\w*"), " seker "),
    (re.compile(r"\bhasta gun\w*"), " hastagunu "),
]

# kelime öneki -> kavram etiketi (eşanlamlıları aynı boyuta toplar)
CONCEPTS: dict[str, tuple[str, ...]] = {
    "tansiyon": ("tansiyon", "hipertansi", "hypertens", "blood pressure"),
    "seker": ("seker", "glukoz", "glikoz", "glisemi", "glucose", "diyabet", "diabet"),
    "hipo": ("hipoglisemi", "hypoglyc", "titre", "terle"),
    "unut": ("unut", "atla", "kacir", "kacmis", "hatirla", "hatirlat", "aksat", "forget", "missed"),
    "olc": ("olc", "olcer", "olcum", "olcmek", "measur"),
    "ilac": ("ilac", "hap", "doz", "tablet", "medicat", "drug"),
    "ayak": ("ayak", "foot", "feet"),
    "tuz": ("tuz", "sodyum", "salt", "sodium"),
    "aktivite": ("egzersiz", "spor", "yuru", "aktivite", "hareket", "exercis", "activity"),
    "beslenme": ("beslen", "diyet", "yemek", "gida", "yiyecek", "tabak", "ogun"),
    "yanetki": ("yan etki", "yanetki", "bulanti", "ishal", "karin agri", "mide"),
    "hastagunu": ("hastagunu", "ates", "kusma", "kusuyor", "kustum", "grip", "enfeksiyon", "fever"),
    "acil": ("acil", "112", "bayil", "bilinc", "gogus agri", "nefes darli"),
    "sigara": ("sigara", "tutun", "tobacco", "smok"),
}

_CONCEPT_PREFIXES = sorted(
    ((p.replace(" ", ""), c) for c, ps in CONCEPTS.items() for p in ps if " " not in p), key=lambda x: -len(x[0])
)
_CONCEPT_PHRASES = [(p, c) for c, ps in CONCEPTS.items() for p in ps if " " in p]


def preprocess(text: str) -> str:
    t = normalize(text)
    for rx, repl in PHRASES:
        t = rx.sub(repl, t)
    return t


def content_tokens(text: str) -> list[str]:
    """Normalize edilmiş, dolgu kelimesi atılmış kelimeler."""
    return [w for w in _TOKEN_RE.findall(preprocess(text))
            if w not in STOPWORDS and not w.startswith(STOP_PREFIXES) and (len(w) > 1 or w.isdigit())]


def concept_counts(text: str) -> Counter[str]:
    t = preprocess(text)
    found: Counter[str] = Counter({c: t.count(p) for p, c in _CONCEPT_PHRASES if p in t})
    for w in _TOKEN_RE.findall(t):
        for p, c in _CONCEPT_PREFIXES:
            if w.startswith(p):
                found[c] += 1
                break
    return found


def concepts_of(text: str) -> set[str]:
    return set(concept_counts(text))


def keyword_keys(text: str) -> set[str]:
    """Hibrit puanlama için anahtar küme: 5 harflik önek kökleri + kavramlar."""
    return {w[:5] for w in content_tokens(text) if len(w) >= 3} | {f"#{c}" for c in concepts_of(text)}


CONCEPT_WEIGHT = 2.0


def features(text: str) -> Counter[str]:
    feats: Counter[str] = Counter()
    for w in content_tokens(text):
        feats[f"w:{w}"] += 1.0
        if len(w) >= 5:
            # Türkçe sondan eklemeli: önek, basit bir kök yaklaşımıdır ("tansiyonu" ~ "tansiyon")
            feats[f"p4:{w[:4]}"] += 0.7
            feats[f"p5:{w[:5]}"] += 0.7
        padded = f"#{w}#"
        grams = [padded[i : i + 3] for i in range(len(padded) - 2)]
        for g in grams:
            feats[f"c3:{g}"] += 0.8 / len(grams)
    for c, n in concept_counts(text).items():
        feats[f"k:{c}"] += CONCEPT_WEIGHT * n
    return feats


def _bucket(feature: str, dim: int) -> tuple[int, float]:
    h = int.from_bytes(hashlib.blake2b(feature.encode(), digest_size=8).digest(), "big")
    return h % dim, (1.0 if (h >> 63) & 1 else -1.0)


@lru_cache(maxsize=4096)
def _local_embed(text: str, dim: int) -> tuple[float, ...]:
    vec = [0.0] * dim
    for feat, tf in features(text).items():
        idx, sign = _bucket(feat, dim)
        vec[idx] += sign * (1.0 + math.log(tf) if tf >= 1 else tf)
    return tuple(l2_normalize(vec))


class LocalHashEmbedding:
    """Deterministik, çevrimdışı embedding (signed feature hashing, alt-doğrusal tf, L2-normalize)."""

    model_id = LOCAL_MODEL_ID

    def __init__(self, dim: int = EMBEDDING_DIM):
        self.dim = dim

    def _embed(self, text: str) -> list[float]:
        return list(_local_embed(text, self.dim))

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class VoyageEmbedding:
    """Voyage AI embeddings (opt-in). Model 384 boyut desteklemediği için 512 boyut istenir ve
    Matryoshka özelliğine güvenilerek ilk 384 bileşen alınıp yeniden normalize edilir."""

    API_URL = "https://api.voyageai.com/v1/embeddings"
    REQUEST_DIM = 512

    def __init__(self, api_key: str, model: str = "voyage-3.5-lite", client: httpx.Client | None = None,
                 dim: int = EMBEDDING_DIM):
        self.api_key = api_key
        self.model = model
        self.dim = dim
        self.client = client
        self.model_id = f"voyage:{model}:{dim}"

    def _call(self, texts: list[str], input_type: str) -> list[list[float]]:
        own = self.client is None
        client = self.client or httpx.Client(timeout=15)
        try:
            resp = client.post(
                self.API_URL,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"input": texts, "model": self.model, "input_type": input_type,
                      "output_dimension": self.REQUEST_DIM},
            )
            resp.raise_for_status()
            data = sorted(resp.json()["data"], key=lambda d: d["index"])
        finally:
            if own:
                client.close()
        return [l2_normalize([float(x) for x in d["embedding"][: self.dim]]) for d in data]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), 64):
            out.extend(self._call(texts[i : i + 64], "document"))
        return out

    def embed_query(self, text: str) -> list[float]:
        return self._call([text], "query")[0]


class FastEmbedEmbedding:
    """fastembed (ONNX, yerel) ile anlamsal embedding. e5 ailesi modeller asimetrik eğitilmiştir:
    sorgular "query: ", belgeler "passage: " önekiyle embed edilmelidir."""

    QUERY_PREFIX = "query: "
    PASSAGE_PREFIX = "passage: "
    BATCH_SIZE = 32

    def __init__(self, model_name: str = "intfloat/multilingual-e5-small", cache_dir: str | None = None,
                 dim: int = EMBEDDING_DIM, model=None):
        self.model_name = model_name
        self.dim = dim
        self.model_id = f"fastembed:{model_name}:{dim}"
        self._model = model if model is not None else _load_fastembed_model(model_name, cache_dir, dim)
        self._uses_prefix = "e5" in model_name.lower()

    def _embed(self, texts: list[str], prefix: str) -> list[list[float]]:
        if self._uses_prefix:
            texts = [prefix + t for t in texts]
        out: list[list[float]] = []
        for vec in self._model.embed(texts, batch_size=self.BATCH_SIZE):
            values = [float(x) for x in vec]
            if len(values) != self.dim:
                raise ValueError(f"{self.model_name} {len(values)} boyut döndürdü, {self.dim} bekleniyordu")
            out.append(l2_normalize(values))
        return out

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, self.PASSAGE_PREFIX)

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], self.QUERY_PREFIX)[0]


def _load_fastembed_model(model_name: str, cache_dir: str | None, dim: int):
    """fastembed.TextEmbedding örneği (tembel içe aktarma). Model fastembed'in hazır listesinde yoksa
    Hugging Face'teki ONNX dosyasıyla özel model olarak kaydedilir (ortalama havuzlama + normalize)."""
    from fastembed import TextEmbedding

    kwargs = {"cache_dir": cache_dir} if cache_dir else {}
    try:
        return TextEmbedding(model_name=model_name, **kwargs)
    except ValueError:  # desteklenmeyen model adı
        from fastembed.common.model_description import ModelSource, PoolingType

        TextEmbedding.add_custom_model(
            model=model_name, pooling=PoolingType.MEAN, normalization=True,
            sources=ModelSource(hf=model_name), dim=dim, model_file="onnx/model.onnx",
        )
        return TextEmbedding(model_name=model_name, **kwargs)


@lru_cache(maxsize=2)
def _fastembed_provider(model_name: str, cache_dir: str | None) -> EmbeddingProvider:
    # Model yükleme pahalıdır (ONNX oturumu): süreç başına bir kez; başarısızlık da önbelleğe alınır (log taşmasın)
    try:
        return FastEmbedEmbedding(model_name, cache_dir)
    except ImportError:
        log.warning("EMBEDDING_PROVIDER=fastembed ama 'fastembed' paketi yüklü değil (pip install -r "
                    "requirements-ml.txt); yerel embedding kullanılıyor")
    except Exception as exc:
        log.warning("fastembed modeli (%s) yüklenemedi (%s); yerel embedding kullanılıyor", model_name,
                    type(exc).__name__)
    return LocalHashEmbedding()


def reset_provider_cache() -> None:
    """Testler için: önbelleğe alınmış fastembed sağlayıcısını sıfırlar."""
    _fastembed_provider.cache_clear()


def get_provider() -> EmbeddingProvider:
    s = get_settings()
    provider = (s.embedding_provider or "local").lower()
    if provider == "voyage":
        if s.voyage_api_key:
            return VoyageEmbedding(s.voyage_api_key, s.voyage_model)
        log.warning("EMBEDDING_PROVIDER=voyage ama VOYAGE_API_KEY yok; yerel embedding kullanılıyor")
    elif provider == "fastembed":
        return _fastembed_provider(s.fastembed_model, s.fastembed_cache_dir)
    return LocalHashEmbedding()


def document_text(title: str, body: str, tags: str | None) -> str:
    """Bir eğitim içeriğinin embed edilecek metni (başlık ağırlıklı)."""
    return f"{title}. {title}. {body} {(tags or '').replace(',', ' ')}"
