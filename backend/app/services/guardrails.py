"""Deterministik guardrail katmanları (bkz. docs/M1/02-guardrail-politikasi.md, docs/M4/guardrail-raporu.md).

Katman 1 (check_input): LLM'den ÖNCE çalışır. Kendine zarar, acil belirti ve doz/ilaç değişikliği
taleplerini (rol yapma / jailbreak denemeleri dahil) yakalar.
Katman 3 (check_output): LLM'den SONRA çalışır. Yanıtta doz önerisi, ilacı bırakma önerisi, teşhis
ifadesi veya sağlık profesyoneli kimliği varsa engeller.

Bu modül LLM'e bağımlı değildir; güvenlik garantisi buradan gelir. Tüm kurallar açıklanabilir
düzenli ifadelerdir; her eşleşme `GuardrailResult.matched` alanında hangi metin parçasının kuralı
tetiklediğini gösterir.

Metin hazırlama hattı (_prepare):
    1. normalize(): Unicode NFKC, görünmez karakterleri silme, Türkçe küçük harf, aksan kaldırma
    2. ölçüm birimi birleştirme ("142 mg/dL" -> "142 mgdl", doz birimi mg ile karışmaz) ve
       sayı+birim ayırma: "500mg" -> "500 mg"
    3. leet düzeltme: harfler arasındaki rakam/simge -> harf ("d0z" -> "doz")
    4. noktalama -> boşluk ("/" tansiyon değerleri için korunur)
    5. 3+ tekrar eden harfi teke indirme ("alamiyorummm" -> "alamiyorum")
    6. aralanmış harfleri birleştirme ("d o z" -> "doz"); bu hile tespit edilirse metnin boşluksuz
       hali de boşluksuz kalıplarla ayrıca taranır.
"""

import enum
import re
import unicodedata
from dataclasses import dataclass

_TR_MAP = str.maketrans({"ç": "c", "ğ": "g", "ı": "i", "ö": "o", "ş": "s", "ü": "u", "â": "a", "î": "i", "û": "u"})
_INVISIBLE = re.compile("[­​-‏⁠﻿]")  # yumuşak tire, sıfır genişlikli karakterler


def normalize(text: str) -> str:
    """Türkçe büyük/küçük harf ve aksan farklarını yok sayar: 'GÖĞSÜM' -> 'gogsum'.

    Ayrıca Unicode biçim farklarını (tam genişlik harfler, ayrık aksanlar) ve görünmez
    karakterleri (sıfır genişlikli boşluk vb.) temizler. Boşluklar teke indirilir.
    """
    text = _INVISIBLE.sub("", unicodedata.normalize("NFKC", text))
    text = text.replace("İ", "i").replace("I", "ı").lower()
    text = text.translate(_TR_MAP)
    # Kalan birleşik aksan işaretlerini kaldır (é -> e); Türkçe harfler yukarıda zaten çevrildi
    text = "".join(ch for ch in unicodedata.normalize("NFD", text) if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text).strip()


# Ölçüm birimleri: "142 mg/dL", "142mg / dl", "7,8 mmol/L" -> tek belirteç (mgdl / mmoll)
_GLUCOSE_UNIT = re.compile(r"(?:\b|(?<=\d))(mg|mmol) ?/ ?(dl|l)\b")
_UNIT_SPLIT = re.compile(r"(\d)(mgdl|mmoll|mg|mcg|ml|iu|ui|unite|units?|tablet)\b")
_LEET_TOKEN = re.compile(r"[a-z][0-9@$]+[a-z]")
_LEET_MAP = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"})
_PUNCT = re.compile(r"[^a-z0-9/\s]")
_REPEAT = re.compile(r"([a-z])\1{2,}")
_SPACED_RUN = re.compile(r"\b(?:[a-z] ){2,}[a-z]\b")


def _prepare(message: str) -> tuple[str, str | None]:
    """Eşleştirme için metni hazırlar. Dönüş: (hazır metin, aralama hilesi varsa boşluksuz metin)."""
    # Ölçüm birimi tek belirteçe indirilir; aksi halde "142 mg/dL" doz birimi "mg" gibi görünür
    text = _GLUCOSE_UNIT.sub(lambda m: m.group(1) + m.group(2), normalize(message))
    text = _UNIT_SPLIT.sub(r"\1 \2", text)
    text = " ".join(tok.translate(_LEET_MAP) if _LEET_TOKEN.search(tok) else tok for tok in text.split())
    text = _PUNCT.sub(" ", text)
    text = _REPEAT.sub(r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    obfuscated = bool(_SPACED_RUN.search(text))
    text = _SPACED_RUN.sub(lambda m: m.group(0).replace(" ", ""), text)
    return text, (text.replace(" ", "") if obfuscated else None)


def _compact_pattern(pattern: str) -> str:
    """Boşluklu kalıbı boşluksuz metne uyarlar: ' ?' ve ' ' silinir, kelime sınırı (\\b) kaldırılır."""
    return pattern.replace(" ?", "").replace(" ", "").replace(r"\b", "")


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


# =====================================================================================
# Katman 1 — kalıplar (hazırlanmış metin üzerinde: küçük harf, Türkçe karaktersiz)
# =====================================================================================

# Sık kullanılan parçalar
_W = r"(\w+ ){0,2}?"  # araya en fazla iki kelime girebilir
_LOW_GLUCOSE_TAIL = r"(?! ?(dakika|dk|saat|gun|hafta|ay|yil|yas|kilo|kg|kez|defa|adim|km))"

EMERGENCY_PATTERNS: dict[str, list[str]] = {
    "gogus_agrisi": [
        r"\bgogu?s\w* (\w+ ){0,4}?(baski|agri|agir|sikis|yan|sanci|batma|kramp)",
        r"\bkalb\w* (sikis|agri|baski|duracak)",
        r"\bkalp krizi\w* (geciriyor|gecirecek|gibi|mi\b)",
        r"\bsol kol\w* .{0,15}(agri|uyus|vur|yayil)",
        r"\bchest (pain|pressure|tightness|hurts)",
        r"\bheart attack",
    ],
    "inme": [
        r"\byuz(um|umun|u|unun)? .{0,15}(kay|dus|uyus|carpil|yamul|egil)",
        r"\bagz\w* .{0,15}(kay|yamul|carpil|egil|dus)",
        r"\bkonusam(iyorum|adim|iyor)",
        r"\bkonusma(m|si|sim)?\w* .{0,10}(bozul|peltek|anlasilmiyor|garip|kay)",
        r"\bdil(im|i) (dolas|tutul|peltek)",
        r"\bkelimeleri (bulamiyor|cikaramiyor)",
        r"\b(kolum|bacagim|elim|bir tarafim|sol tarafim|sag tarafim|yarim) .{0,15}(uyustu|tutmuyor|hissetmiyor|gucsuz)",
        r"\b(kolumu|bacagimi|elimi) (kaldiramiyorum|oynatamiyorum|hissetmiyorum)",
        r"\bani(den)? (gucsuzluk|uyusma|felc)",
        r"\bfelc",
        r"\binme (geciriyor|gecirecek|mi\b)",
        r"\bface (is )?droop",
        r"\bslurred",
        r"\b(cant|can t|cannot|can not) (speak|talk|move (my )?(arm|leg))",
        r"\barm (is )?(weak|numb)",
    ],
    "nefes_darligi": [
        r"\bnefes ?(alam|veremi)",
        r"\bnefes\w* (daral|kesil|yetmi|tikan)",
        r"\bnefes darlig",
        r"\bnefes alma\w* (zorlan|gucluk)",
        r"\bbogul(uyorum|acak|uyor)",
        r"\bhava (yetmiyor|alamiyorum)",
        r"\bnefessiz kal",
        r"\bdudak\w* (morar|mor\b)",
        r"\b(cant|can t|cannot|can not) breathe",
        r"\bshort(ness)? of breath",
        r"\bstruggling to breathe",
    ],
    "alerjik_reaksiyon": [
        r"\b(dudak|dil(im|i)|bogaz|yuz(um|u)|goz kapak)\w* .{0,15}(sis(ti|iyor|me|ik)|kapan)",
        r"\bbogaz\w* (daral|kapan|tikan)",
        r"\byutkunam",
        r"\b(kurdesen|dokuntu)\w* .{0,40}(nefes|sis|bas\w* don|bayil)",
        r"\banafilaksi|\banaphyla|\balerjik sok",
        r"\b(throat|tongue|lips|face) (is |are )?(swelling|swollen|closing)",
    ],
    "bilinc": [
        r"\bbayil(dim|di|acak|iyor|mis)",
        r"\bkendimi kaybett?im",
        r"\bbilinc\w* (bulan|kapan|gid|kayb|yerinde degil|acik degil)",
        r"\b(uyanmiyor|uyandiram\w*|ayilmiyor|tepki vermiyor|sayikl\w*)",
        r"\bnobet (geciriyor|gecirdi|geciriyorum)|\bkasiliyor|\bhavale",
        r"\b(passed out|fainted|unconscious|not waking up)",
    ],
    "hipoglisemi": [
        r"\bseker\w* (cok |hizla |birden )?(dust|dusuyor|dustu)",
        r"\bseker\w* (cok )?dusuk",
        r"\b(titreme|titri\w*|titre\w*).{0,30}(terleme|terli\w*|soguk ter)",
        r"\b(terleme|terli\w*|soguk ter).{0,30}(titreme|titri\w*|titre\w*)",
        r"\bhipo\w* (girdim|oldum|geciriyorum)|\bhipoya",
        r"\bhypo\b",
        r"\bsweat\w*.{0,30}shak\w*|\bshak\w*.{0,30}sweat\w*",
    ],
    "hiperglisemi": [
        r"\baseton (koku|kokuyor)|\baseton gibi",
        r"\bketon\w* (cikti|yuksek|pozitif|var)",
        r"\b(surekli|durmadan|sik sik|devamli) kus",
        r"\bketoasidoz",
        r"\bseker\w* (hi|high|lo|low)\b",
    ],
    "kritik_deger": [
        # kan şekeri > 300 veya < 54 mg/dL (politika §4)
        rf"\bseker\w* {_W}([3-9]\d\d|[1-9]\d\d\d)\b",
        rf"\bseker\w* {_W}([1-4]\d|5[0-3])\b{_LOW_GLUCOSE_TAIL}",
        r"\b(glukometre|cihaz|seker olcer)\w* .{0,15}\b([3-9]\d\d)\b",
        # tansiyon >= 180 sistolik veya >= 120 diyastolik; "18'e 12" konuşma dili kısaltması
        rf"\b(tansiyon\w*|bp|blood pressure|kan basinc\w*) {_W}(1[89]\d|2\d\d)\b",
        r"\btansiyon\w* (\w+ )?(1[89]|2[0-5]) ?(e|ye|a|ya|/)? ?(1[0-9]|[5-9])\b",
        r"\b(tansiyon\w*|bp|kan basinc\w*) .{0,15}\d{2,3} ?(/|e|ye|a|ya)? ?(1[2-9]\d)\b",
        r"\bkucuk tansiyon\w* (\w+ )?(1[2-9]\d)\b",
    ],
    "siddetli_bas_agrisi": [
        r"\b(cok|asiri|dayanilmaz|cekilmez) (siddetli )?bas agri",
        r"\bbas\w* (cok |asiri )?(siddetli|dayanilmaz) agri",
        r"\bhayatimin en (kotu|siddetli) bas agri",
        r"\bworst headache",
        # baş ağrısı + görme bozukluğu birlikte (hipertansif acil tablo)
        r"\bbas\w* (\w+ ){0,2}?(agri|catl|zonk)\w*.{0,40}(gor(me|emiyor|muyor)|bulanik|karar)",
        r"\b(gor(me|emiyor|muyor)|bulanik|karar)\w*.{0,40}bas\w* (\w+ ){0,2}?(agri|catl|zonk)",
        r"\bani(den)? .{0,15}(gorme kaybi|goremiyorum|gormuyor|karardi)",
        r"\bgoz\w* .{0,10}(aniden|birden) (gormuyor|karardi|kapandi)",
        r"\bblurry vision|\bvision loss",
    ],
}

# Acil eşleşmesini geçersiz kılan bağlamlar (eşleşme ile çakışırsa yok sayılır)
EMERGENCY_EXCLUSIONS = [
    r"\b(tavuk|hindi|pilic|dana|kuzu|ordek) gogs\w*",  # yemek: "tavuk göğsü"
    r"\bgogus kafes\w*",
    r"\b(tarif|yemeg|yemek|fikr|fikir|oneri|film|kitab|kitap|sarki|uygulama|manzara|hediye)\w* bayil\w*",  # "...ya bayıldım" = beğendim
    r"\b(gulmekten|kahkahadan|gulerken) nefes\w* kesil\w*",
]

# Acil eşleşmesinden hemen sonra gelirse olumsuzlayan kelimeler ("göğüs ağrım yok")
_NEGATION_AFTER = re.compile(r"\s*(yok|yoktu|degil|olmadi|olmuyor|hic olmadi)\b")

SELF_HARM_PATTERNS = [
    r"\bkendime (zarar|bir sey yap|bisey yap|kiy)",
    r"\bkendimi (oldur|as(mak|acag|arim)|kes(mek|ecegim|iyorum)|yok et)",
    r"\byasamak iste(miyorum|miyom|mem)",
    r"\bintihar",
    r"\bolmek isti?(yorum|yom)",
    r"\bcanima kiy",
    r"\b(her seye|hayatima) son ver",
    r"\b(hayata|yasamaya) devam etmek istemiyorum",
    r"\b(yasamanin|hayatin|hayatimin) (bir )?anlami (kalmadi|yok)",
    r"\bolsem (kimse|daha iyi|herkes)|\bolup gitsem",
    r"\bhepsini (birden|bir kerede|bir seferde|ayni anda) (icip|ictim|icecegim|icsem|alsam|yutsam|yutup|alip|yapsam)",
    r"\b(kutunun|kutudaki|butun kutu|tum kutu)\w* .{0,25}(hepsini|tamamini)? ?.{0,20}(icsem|icip|yutsam|yutup|ictim|alsam)",
    r"\bbilerek .{0,25}(fazla|cok) (ic|al|yap)",
    r"\b(kill myself|suicid|end my life|want to die|hurt myself|self harm)",
]

# Doz / ilaç değişikliği: Türkçe istek-kip ekleri (artırSAM, artırAYIM, artırABİLİR, artırMALI ...).
# Bilinçli olarak 3. tekil geçmiş zaman ("doktorum artırdı") DIŞARIDA bırakılır: o bir bildirimdir, talep değil.
_REQ = r"(sam|sem|sm|sak|sek|sa\b|se\b|ayim|eyim|iyim|yayim|abilir|ebilir|mali|meli|ir mi|ilir mi|ilsin|dim|tim|mak ist|mek ist|alim|elim|ip\b)"
_DOSE_VERBS = r"(artt?ir|azalt|dusur|yukselt|degistir|cikar|indir|bol|birak|kes|atla|ara ver|erte|kaydir|ayarla)"
_MED = r"(ilac|hap|metf\w*|insul\w*|tablet|tansiyon ilac)"

DOSE_REQUEST_PATTERNS = [
    rf"\bdoz\w*.{{0,30}}?{_DOSE_VERBS}{_REQ}",
    rf"\b(artt?ir|azalt|dusur|yukselt|bol|cikar|indir|atla){_REQ}.{{0,15}}\b(doz|{_MED})",
    rf"\b{_MED}\w* .{{0,20}}?(birak|kes|ara ver|atla|erte|azalt|artt?ir|dusur|yukselt|cikar|indir|bol){_REQ}",
    # "ikiye çıkarsam", "üçe çıkarayım", "iki katına çıkarabilir miyim" (nesnesiz miktar artırma)
    rf"\b(ikiye|uce|iki katina|uc katina|\d+ ?(e|ye|a|ya)) (cikar|artt?ir|yukselt|indir|dusur){_REQ}",
    rf"\b{_MED}\w* .{{0,20}}?(birak|kes)(acagim|ecegim|acam|ecem|icam|icem|iyorum)",
    rf"\b{_MED}\w* (birak|kes)(mayi|meyi) (dusunuyorum|planliyorum|istiyorum)",
    rf"\b(doz|{_MED})\w* .{{0,15}}(almasam|icmesem|kullanmasam|yapmasam|almayayim|icmeyeyim|almazsam|icmezsem)",
    r"\b(almasam|icmesem|kullanmasam|biraksam) (ne olur|olur mu|bir sey olur)",
    r"\b(doz|ilac|hap|insulin)\w* (atla|alma|icme)\w* (sorun|zarar|bir sey|bisey) (olur|olmaz|yaratir)",
    # miktar soruları
    r"\bkac (mg|miligram|mcg|unite|ml|iu)\b",
    r"\bkac (tablet|hap)\b(?! ?(kacir|atla|unut|aldim|almisim|ictim|icmisim|eksik))",
    r"\bkac (tane|doz) (ic|al|kullan|yut|yap)",
    r"\b(unite|doz|mg|tablet)\w* kac\b",
    r"\b\d+ ?(mg|miligram|mcg|unite|ml|iu|tablet)\b.{0,25}(alayim|alsam|icsem|iceyim|yapayim|yapsam|alabilir|icebilir|yapabilir|yeter|olur mu|cikar|artir|dusur|indir)",
    r"\b\d{2,4} (yerine|degil) \d{2,4}",
    r"\b\d+ mi\b.{0,15}(alayim|iceyim|yapayim|alsam|icsem)",
    r"\bne kadar (yap|al|ic)(mali|meli|ayim|eyim|sam|sem)",
    r"\b\d+ (icsem|alsam|alayim|iceyim|yapsam|yapayim)\b",
    r"\b(iki|2|uc|3|bir|1)( tane| doz| tablet| hap)+( mi)? .{0,20}?(icsem|alsam|alayim|iceyim|icebilir|alabilir|yutsam|yapsam|alirsam|icersem)",
    r"\b(cift|ekstra|fazladan) (doz|tablet|hap|insulin)",
    # kaçırılan dozu telafi
    r"\b(unuttugum|kacirdigim|atladigim) doz\w*.{0,20}(simdi|ikisini|birlikte|telafi)",
    r"\b(unuttum|kacirdim|atladim).{0,40}(simdi al|simdi ic|telafi|birlikte|ikisini)",
    r"\bikisini (birlikte|birden|beraber|ayni anda)",
    # bölme / katlama
    r"\b(yarim|ceyrek) (tablet|hap|doz)",
    r"\b(ikiye|yariya) ?(bol|indir|dusur)",
    r"\b(iki|2|uc|3) (katina|kati) (cikar|yap|al|artir)",
    # ilaç değiştirme / başlatma / başkasının ilacı
    r"\b(baska|yeni|farkli) (bir )?(ilac|hap)\w* .{0,25}(oner|alayim|kullanayim|baslayayim|yaz|gecsem|gecebilir)",
    r"\byerine .{0,25}(kullansam|alsam|icsem|gecsem|kullanayim|alayim)",
    r"\bonunkin\w* .{0,15}(icebilir|alabilir|icsem|alsam|kullan)",
    r"\b(komsu|arkadas|esim|annem|babam|kardes|baskasi)\w* (\w+ )?(ilac|hap|insulin)\w* .{0,20}(icebilir|alabilir|kullanabilir|icsem|alsam|kullansam)",
    r"\bgun ?asiri .{0,10}(alsam|icsem|kullansam|alayim|iceyim|alabilir|icebilir)",
    rf"\b{_MED}\w* (gerek|ihtiyac)\w* (var mi|kaldi mi|kalmadi|yok mu)",
    r"\b(atlayayim|atlasam|atlayabilir)",
    # İngilizce / karışık dil
    r"\b(increase|decrease|reduce|lower|raise|double|halve|skip|stop|adjust|change|cut)\b.{0,25}\b(dose|doses|dosage|medication|medicine|meds|insulin|metformin|pills?|tablets?)\b",
    r"\b(dose|dosage)\w*\b.{0,20}\b(increase|decrease|reduce|double|halve|skip|artir|azalt|dusur)",
    r"\bhow (many|much) (mg|units?|pills?|tablets?|insulin|metformin)",
    r"\bstop taking",
    r"\b(should|can|could) i take\b.{0,20}\b(\d+|two|extra|another|more|double)\b",
    r"\b(skip|stop|increase|decrease|double|cut) (etsem|edeyim|edebilir|yapsam|yapayim)",
]

# Rol yapma / talimat atlatma: koçu "hekim" rolüne sokma ya da kuralları devre dışı bırakma denemeleri.
# Güvenli yanıt doz yanıtıdır (karar yalnızca hekimin) — Action.dose_request ile döner.
JAILBREAK_PATTERNS = [
    r"\brol (yap|oyna)",
    r"\b(sen|artik) (bir )?(doktor|hekim|eczaci)(sun|sin)\b",
    r"\b(doktor|hekim|eczaci)(um)? (gibi|rolunde|rolune girip) (davran|cevap|yanit|konus|dusun)",
    r"\b(onceki|tum|butun) (talimat|kural)\w* (unut|yok say|gormezden|bir kenara)",
    r"\bkural\w* (unut|yok say|gormezden gel|bir kenara birak)",
    r"\b(kisitlama|filtre)\w* (yok|kaldir)",
    r"\bignore (all |your |the )?(previous |prior )?(instructions|rules)",
    r"\bpretend (you are|you re|to be)\b.{0,15}\b(doctor|physician|nurse)",
    r"\bact as (a |my )?(doctor|physician|nurse)",
]


def _compile(patterns: list[str]) -> list[tuple[re.Pattern, re.Pattern]]:
    return [(re.compile(p), re.compile(_compact_pattern(p))) for p in patterns]


_COMPILED_EMERGENCY = {k: _compile(v) for k, v in EMERGENCY_PATTERNS.items()}
_COMPILED_EXCLUSIONS = [re.compile(p) for p in EMERGENCY_EXCLUSIONS]
_COMPILED_SELF_HARM = _compile(SELF_HARM_PATTERNS)
_COMPILED_DOSE = _compile(DOSE_REQUEST_PATTERNS)
_COMPILED_JAILBREAK = _compile(JAILBREAK_PATTERNS)


def _search(compiled: list[tuple[re.Pattern, re.Pattern]], text: str, compact: str | None) -> str | None:
    for rx, rx_compact in compiled:
        if m := rx.search(text):
            return m.group(0)
        if compact and (m := rx_compact.search(compact)):
            return m.group(0)
    return None


def _emergency_match(rx: re.Pattern, text: str, exclusion_spans: list[tuple[int, int]]) -> str | None:
    """Acil kalıbı için bağlam istisnası ve olumsuzlama kontrolü yaparak ilk geçerli eşleşmeyi döndürür."""
    for m in rx.finditer(text):
        if any(s < m.end() and m.start() < e for s, e in exclusion_spans):
            continue
        end = m.end()
        while end < len(text) and text[end].isalnum():
            end += 1  # eşleşmenin bulunduğu kelimenin sonuna git
        if _NEGATION_AFTER.match(text, end):
            continue
        return m.group(0)
    return None


def check_input(message: str) -> GuardrailResult:
    """Katman 1. Öncelik sırası: kendine zarar > acil belirti > doz talebi / rol yapma."""
    text, compact = _prepare(message)

    if matched := _search(_COMPILED_SELF_HARM, text, compact):
        return GuardrailResult(Action.self_harm, "kendine_zarar", matched)

    exclusion_spans = [m.span() for rx in _COMPILED_EXCLUSIONS for m in rx.finditer(text)]
    for category, patterns in _COMPILED_EMERGENCY.items():
        for rx, rx_compact in patterns:
            if matched := _emergency_match(rx, text, exclusion_spans):
                return GuardrailResult(Action.emergency, category, matched)
            if compact and (m := rx_compact.search(compact)):
                return GuardrailResult(Action.emergency, category, m.group(0))

    if matched := _search(_COMPILED_DOSE, text, compact):
        return GuardrailResult(Action.dose_request, "doz_talebi", matched)
    if matched := _search(_COMPILED_JAILBREAK, text, compact):
        return GuardrailResult(Action.dose_request, "rol_yapma", matched)

    return GuardrailResult(Action.allow)


# =====================================================================================
# Katman 3 — çıktı doğrulayıcı
# =====================================================================================

# Yönerge/izin kipi ekleri: artırABİLİRSİN, artırMAN, artırMALISIN, artır (emir), düşürMEYİ DENE ...
_DIRECTIVE = r"(abilirsin|ebilirsin|abilirsiniz|ebilirsiniz|malisin|melisin|malisiniz|melisiniz|man\b|men\b|in\b|iniz\b|mayi dene|meyi dene|ip\b|\b)"
_DISEASE = (
    r"(diyabet|seker hastalig|hipertansiyon|tansiyon hastalig|noropati|nefropati|retinopati|"
    r"bobrek (hastalig|yetmezlig)|kalp yetmezlig|insulin direnc|prediyabet|hipoglisemi|hiperglisemi|ketoasidoz)"
)

FORBIDDEN_OUTPUT_PATTERNS: dict[str, list[str]] = {
    "doz_onerisi": [
        # miktar + yönerge: "1000 mg'a çıkarabilirsin", "2 ünite artırman yeterli"
        rf"\b\d+ ?(mg|miligram|mcg|unite|units?|iu|ml|tablet)\b.{{0,40}}?(\bal|\bic|\byap|kullan|cikar|artir|azalt|dusur|yukselt|indir|\bgec|ayarla|baslat){_DIRECTIVE}",
        r"\b\d+ ?(mg|miligram|mcg|unite|units?|iu|ml|tablet)\b.{0,40}(yeterli|yeter\b|uygun olur|ideal|olmali|should|enough)",
        r"\b(take|increase|reduce|lower|raise)\b.{0,20}\b\d+ ?(mg|units?|ml|tablets?)\b",
        rf"\bdoz\w* .{{0,25}}?(artt?ir|azalt|yukselt|dusur|degistir|cikar|indir|ikiye katla|iki katina|yariya|bol|atla|birlestir){_DIRECTIVE}",
        rf"\b(insulin|metf\w*|tablet|hap)\w* .{{0,25}}?(azalt|artt?ir|dusur|yukselt|yariya|ikiye bol){_DIRECTIVE}",
        r"\b(iki|2|cift) doz (al|ic)",
        r"\batla\b|\batlayabilirsin|\batlayabilirsiniz|\batlaman (sorun|bir sey)",
        r"\b(almasan|icmesen|kullanmasan|yapmasan)(iz)? da olur|\b(almasan|icmesen|kullanmasan|yapmasan)(iz)? de olur",
        r"\b(almaman|icmemen|kullanmaman|atlaman)\w* (sorun|bir sey) (olmaz|yok)",
        r"\btek seferde (al|ic)|\bbirlestir(ip|ebilirsin|ebilirsiniz)",
        r"\bgun ?asiri",
        r"\b(you can|you could|you may|you should|feel free to|it is fine to|it s fine to|ok to) (skip|double|halve|increase|decrease|reduce|take (an )?extra)",
        r"\b(increase|decrease|reduce|double|halve) (your|the) (dose|dosage|insulin|metformin)",
    ],
    "ilac_birakma": [
        rf"\b(ilac|tedavi|hap|metf\w*|insul\w*|tansiyon ilac)\w* .{{0,20}}?(birak|kes|ara ver){_DIRECTIVE}",
        r"\b(ilac|tedavi|insulin|hap)\w* (ihtiyac|gerek)\w* (yok|kalmadi|kalmamis)",
        r"\b(ilac|tedavi)\w* (ara vermen|birakman|kesmen)\w* (sorun|bir sey) (olmaz|yok)",
        r"\b(stop|quit|discontinue) (taking )?(your |the )?(medication|medicine|meds|insulin|metformin|pills?)",
        r"\bstop taking",
    ],
    "teshis": [
        rf"\b(sende|sizde|sen de)\b.{{0,40}}{_DISEASE}\w* (var|olabilir|baslamis|gelismis|olmus|gorunuyor)",
        rf"\b{_DISEASE}\w{{0,6}} (var\b|olabilir|olabilirsin|baslamis|gelismis|belirtisi|isareti|gostergesi)",
        rf"\b{_DISEASE}(in|un|iniz|unuz) (kontrolsuz|kontrol disi|kotulesmis|ilerlemis|kotuye gidiyor)",
        rf"\b{_DISEASE}\w*.{{0,20}}(oldugunu|olabilecegini|gelistigini|ilerledigini|basladigini|kotulestigini) (gosteriyor|dusunduruyor|isaret ediyor)",
        r"\b(teshis|tani)(si)? (koy|konul)",
        r"\b(you (have|might have|may have|probably have|could have)|looks like you have)( a| an)? (\w+ ){0,2}?(diabetes|hypertension|neuropathy|nephropathy|retinopathy|kidney disease|heart failure|insulin resistance)",
    ],
    "kimlik": [
        r"\bben (bir )?(doktor|hekim|hemsire|eczaci|saglik profesyoneli|uzman)(um|im|yim|uyum|iyim)\b",
        r"\b(hekimin|doktorun|hemsiren) olarak",
        r"\b(doktorunum|hekiminim|hemsirenim)\b",
        r"\b(doktor|hekim|hemsire|saglik profesyoneli) olarak (benim )?(onerim|tavsiyem|gorusum)",
        r"\bben (bir )?(doktor|hekim|hemsire|saglik profesyoneli) olarak",
        r"\b(i am|i m|im) (a |your )?(doctor|physician|nurse|pharmacist)\b",
        r"\bas your (doctor|physician|nurse)",
    ],
}
_COMPILED_OUTPUT = {k: [re.compile(p) for p in v] for k, v in FORBIDDEN_OUTPUT_PATTERNS.items()}


def check_output(reply: str) -> GuardrailResult:
    """Katman 3. İlk eşleşen yasak kategoride yanıtı engeller."""
    text, _ = _prepare(reply)
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
