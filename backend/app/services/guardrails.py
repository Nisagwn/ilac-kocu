"""Deterministik guardrail katmanları (bkz. docs/M1/02-guardrail-politikasi.md).

Katman 1 (check_input): LLM'den ÖNCE çalışır. Acil belirti ve doz/ilaç değişikliği taleplerini yakalar.
Katman 3 (check_output): LLM'den SONRA çalışır. Yanıtta doz önerisi veya teşhis ifadesi varsa engeller.

Bu modül LLM'e bağımlı değildir; güvenlik garantisi buradan gelir.
"""

import enum
import re
from dataclasses import dataclass

_TR_MAP = str.maketrans({"ç": "c", "ğ": "g", "ı": "i", "ö": "o", "ş": "s", "ü": "u", "â": "a", "î": "i", "û": "u"})


def normalize(text: str) -> str:
    """Türkçe büyük/küçük harf ve aksan farklarını yok sayar: 'GÖĞSÜM' -> 'gogsum'."""
    text = text.replace("İ", "i").replace("I", "ı").lower()
    text = text.translate(_TR_MAP)
    return re.sub(r"\s+", " ", text).strip()


class Action(str, enum.Enum):
    allow = "allow"
    emergency = "emergency"
    self_harm = "self_harm"
    dose_request = "dose_request"
    blocked_output = "blocked_output"


@dataclass(frozen=True)
class GuardrailResult:
    action: Action
    category: str | None = None
    matched: str | None = None

    @property
    def allowed(self) -> bool:
        return self.action == Action.allow


# --- Katman 1: acil belirti kalıpları (normalize edilmiş metin üzerinde) ---
EMERGENCY_PATTERNS: dict[str, list[str]] = {
    "gogus_agrisi": [
        r"gogs(um|ume|umde|un)?\w* (baski|agri|sikis|yan)",
        r"gogus (agri|baski|sikis)",
        r"gogsum(de)? .{0,20}(baski|agri|sikis|yan)",
        r"kalp krizi",
        r"sol kolum(a)? .{0,15}(agri|uyus|vur)",
    ],
    "inme": [
        r"yuzum(un)? .{0,10}(kay|dus|uyus)",
        r"konusam(iyorum|adim)",
        r"dilim (dolas|tutul)",
        r"(kolum|bacagim|bir tarafim) .{0,10}(uyustu|tutmuyor|hissetmiyor)",
        r"ani (gorme kaybi|goremiyorum)",
        r"felc",
    ],
    "nefes_darligi": [
        r"nefes ?alam(iyorum|adim)",
        r"nefesim (daral|kesil|yetmi)",
        r"bogul(uyorum|acak)",
    ],
    "bilinc": [
        r"bayil(dim|acak|iyorum)",
        r"kendimi kaybett?im",
        r"bilincim",
        r"nobet geciriyor",
    ],
    "hipoglisemi": [
        r"sekerim (cok )?dust?u",
        r"seker(im)? (cok )?(dusuk|dustu)",
        r"(titreme|titriyorum).{0,30}(terleme|terliyorum)",
        r"hipo(ya)? girdim",
    ],
    "kritik_deger": [
        r"seker(im)? ([3-9]\d\d|[1-9]\d{3})\b",
        r"seker(im)? ([1-4]\d)\b",
        r"tansiyon(um)? (1[89]\d|2\d\d)",
    ],
    "siddetli_bas_agrisi": [r"(cok|asiri|dayanilmaz) (siddetli )?bas agri", r"hayatimin en kotu bas agrisi"],
}

SELF_HARM_PATTERNS = [
    r"kendime zarar",
    r"yasamak istemiyorum",
    r"intihar",
    r"olmek istiyorum",
    r"canima kiy",
    r"hepsini (birden )?(icip|ictim|icecegim)",
]

# --- Katman 1: doz / ilaç değişikliği talepleri ---
DOSE_REQUEST_PATTERNS = [
    r"\bdoz(u|unu|umu|umu|lari|larimi)?\b.{0,30}(artir|azalt|degis|yukselt|dusur|ikiye|yariya|bol)",
    r"(artir|azalt|degistir|yukselt|dusur)(ayim|abilir|sam|mali|ir mi|mak).{0,20}(doz|ilac|hap|insulin|metformin)",
    r"(ilac|hap|metformin|insulin|tansiyon ilac)\w* (birak|kes|keseyim|birakayim|birakabilir)",
    r"(ilac|hap|metformin|insulin)\w* .{0,20}(birakabilir|kesebilir|birakmali|kesmeli|birakayim|keseyim)",
    r"kac (mg|miligram|tane|unite|doz)",
    r"(iki|2|cift|ekstra|fazladan) (doz|tane|hap|tablet)",
    r"(unuttugum|kacirdigim|atladigim) doz(u)?.{0,20}(simdi|ikisini|birlikte|telafi)",
    r"(unuttum|kacirdim|atladim).{0,40}(simdi al|telafi|birlikte|ikisini)",
    r"ikisini (birlikte|birden|beraber)",
    r"(ilac|insulin|metformin|doz|hap)\w* (azalt|artir|yukselt|dusur|degistir)\w*",
    r"\b\d+\s?(mg|miligram|unite)\b.{0,20}(alayim|icsem|alsam|alabilir|yeter)",
    r"(baska|yeni) (bir )?(ilac|hap)\w* .{0,25}(oner|alayim|kullanayim|baslayayim)",
    r"(yarim|ceyrek) (tablet|hap)",
    r"(atlayayim|atlasam|atlayabilir)",
    r"ilacimi .{0,10}(almasam|icmesem) (olur mu|ne olur)",
]

_COMPILED_EMERGENCY = {k: [re.compile(p) for p in v] for k, v in EMERGENCY_PATTERNS.items()}
_COMPILED_SELF_HARM = [re.compile(p) for p in SELF_HARM_PATTERNS]
_COMPILED_DOSE = [re.compile(p) for p in DOSE_REQUEST_PATTERNS]


def check_input(message: str) -> GuardrailResult:
    """Katman 1. Öncelik sırası: kendine zarar > acil belirti > doz talebi."""
    text = normalize(message)

    for rx in _COMPILED_SELF_HARM:
        if m := rx.search(text):
            return GuardrailResult(Action.self_harm, "kendine_zarar", m.group(0))

    for category, patterns in _COMPILED_EMERGENCY.items():
        for rx in patterns:
            if m := rx.search(text):
                return GuardrailResult(Action.emergency, category, m.group(0))

    for rx in _COMPILED_DOSE:
        if m := rx.search(text):
            return GuardrailResult(Action.dose_request, "doz_talebi", m.group(0))

    return GuardrailResult(Action.allow)


# --- Katman 3: çıktı doğrulayıcı ---
FORBIDDEN_OUTPUT_PATTERNS: dict[str, list[str]] = {
    "doz_onerisi": [
        r"\b\d+([.,]\d+)?\s?(mg|miligram|unite|ml|tablet)\b",
        r"doz(unu|u|unuzu)? (artir|azalt|yukselt|dusur|ikiye katla|degistir)",
        r"(artirabilir|azaltabilir|dusurebilir|yukseltebilir)(sin|siniz)",
        r"ilac(ini|inizi|i) (birak|kes)(abilirsin|abilirsiniz|in|)\b",
        r"(iki|2|cift) doz al",
        r"(atla|atlayabilirsin|atlayabilirsiniz)\b",
    ],
    "teshis": [
        r"(sende|sizde) .{0,30}(var|olabilir|gorunuyor)\b.{0,10}(diyabet|hastalik|neuropati|noropati|bobrek|retinopati|kalp)",
        r"(sende|sizde) .{0,40}(diyabet|hastalig|noropati|retinopati|yetmezlig)\w* (var|olabilir|baslamis|gelismis)",
        r"(teshis|tani) (koy|konul)",
        r"(diyabet|hipertansiyon|noropati|retinopati|bobrek yetmezligi)(in|iniz)? (var|baslamis|gelismis)",
    ],
    "kimlik": [r"ben (bir )?(doktor|hekim|hemsire)(um|im|yim)"],
}
_COMPILED_OUTPUT = {k: [re.compile(p) for p in v] for k, v in FORBIDDEN_OUTPUT_PATTERNS.items()}


def check_output(reply: str) -> GuardrailResult:
    text = normalize(reply)
    for category, patterns in _COMPILED_OUTPUT.items():
        for rx in patterns:
            if m := rx.search(text):
                return GuardrailResult(Action.blocked_output, category, m.group(0))
    return GuardrailResult(Action.allow)


# --- Sabit yanıtlar ---
EMERGENCY_REPLY = (
    "Yazdıkların acil bir durumun belirtisi olabilir. Lütfen hemen 112'yi ara ya da en yakın "
    "acil servise git. Yalnızsan kapıyı aralık bırak ve yakınlarından birine haber ver. "
    "Hekimine de bildirim gönderildi."
)
SELF_HARM_REPLY = (
    "Bunu benimle paylaştığın için teşekkür ederim. Şu an kendini güvende hissetmiyorsan lütfen hemen "
    "112'yi ara. Yalnız değilsin; şu anda yanında olabilecek birine ulaşmanı çok isterim. "
    "Hekimine de bildirim gönderildi."
)
DOSE_REPLY = (
    "İlaç dozunu değiştirmek, atlamak ya da ilacı bırakmak hakkında yönlendirme yapamam; bu kararı "
    "yalnızca hekimin verebilir. Bu soruyu bir sonraki görüşmende sormak üzere not alabilirsin. "
    "Kendini kötü hissediyorsan hekimine ya da 112'ye ulaş."
)
BLOCKED_OUTPUT_REPLY = (
    "Bu konuda sana güvenli bir yanıt veremiyorum. İlacın ya da ölçümlerinle ilgili bir sorun varsa "
    "lütfen hekimine danış."
)
