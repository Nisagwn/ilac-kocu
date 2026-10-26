"""TİTCK "Ruhsatlı Beşeri Tıbbi Ürünler Listesi"nden Türkçe ilaç kataloğu üretimi.

Kaynak: Türkiye İlaç ve Tıbbi Cihaz Kurumu, https://www.titck.gov.tr/dinamikmodul/85 (haftalık Excel).
Sayfa sütunları (2026-10 itibarıyla, 2. satır başlık): SIRA NO, BARKOD, ÜRÜN ADI, ETKİN MADDE, ATC KODU,
RUHSAT SAHİBİ, RUHSAT TARİHİ, RUHSAT NUMARASI, DEĞİŞİKLİK, ..., DEĞİŞİKLİK TARİHİ, askı durumu (0: askıda değil,
1/2/3: askıda), ASKIYA ALINMA TARİHİ. Başlık satırı ada göre bulunur; sütun sırası değişse de çalışır.

Akış (CLI: `python -m app.scripts.import_titck`):
  1. `read_titck`: Excel (openpyxl) ya da CSV okunur, ATC koduna göre süzülür (TARGET_ATC_PREFIXES).
  2. `ingredient_to_generic`: Türkçe etken madde metni ("metformin hidroklorür", "valsartan and diuretics")
     İngilizce generic anahtara çevrilir (SUBSTANCES tablosu + ATC yedeği). Çözülemeyen satırlar raporlanır.
  3. `brand_from_product`: ürün adından marka sadeleştirilir ("GLİFOR 1000 MG 100 FİLM KAPLI TABLET" -> "Glifor").
  4. `build_catalog`: generic başına marka listesi, ATC kodları, Türkçe sınıf adı (ATC_CLASSES); kürate katman
     (app/data/drug_catalog_curated.yaml) görünen ad, takma ad ve sınıf adını sağlar.
  5. `render_yaml`: app/data/drug_catalog_tr.yaml yeniden üretilir (başında kaynak + liste tarihi + komut).

Generic adları openFDA sorgusunda kullanıldığı için ABD adı tercih edilir (glyburide, chlorthalidone, aspirin,
torsemide); diğerleri INN. Koç/sistem bu katalogla yalnızca ilacı tanır; doz bilgisi üretilmez.
"""

from __future__ import annotations

import csv
import io
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import yaml

from app.services.drug_catalog import canonical_generic, display_from_generic
from app.services.guardrails import normalize

SOURCE_URL = "https://www.titck.gov.tr/dinamikmodul/85"
TARGET_ATC_PREFIXES = ("A10", "B01", "C02", "C03", "C07", "C08", "C09", "C10")
MAX_COMPONENTS = 4

# ----------------------------------------------------------------- ATC -> Türkçe sınıf adı (en uzun ön ek kazanır)
ATC_CLASSES: dict[str, str] = {
    # A10 Diyabet
    "A10A": "İnsülin",
    "A10AB": "İnsülin (hızlı / kısa etkili)",
    "A10AC": "İnsülin (orta etkili)",
    "A10AD": "İnsülin (karışım: hızlı + orta etkili)",
    "A10AE": "İnsülin (uzun etkili)",
    "A10AE54": "Uzun etkili insülin + GLP-1 reseptör agonisti",
    "A10AE56": "Uzun etkili insülin + GLP-1 reseptör agonisti",
    "A10B": "Antidiyabetik (insülin dışı)",
    "A10BA": "Biguanid (oral antidiyabetik)",
    "A10BB": "Sülfonilüre (oral antidiyabetik)",
    "A10BD": "Oral antidiyabetik kombinasyonu",
    "A10BF": "Alfa-glukozidaz inhibitörü",
    "A10BG": "Tiyazolidindion (oral antidiyabetik)",
    "A10BH": "DPP-4 inhibitörü",
    "A10BJ": "GLP-1 reseptör agonisti",
    "A10BK": "SGLT2 inhibitörü",
    "A10BX": "Diğer antidiyabetik",
    "A10BX02": "Meglitinid (oral antidiyabetik)",
    "A10BX03": "Meglitinid (oral antidiyabetik)",
    "A10BX16": "GIP/GLP-1 reseptör agonisti",
    # B01 Antitrombotik
    "B01A": "Antitrombotik",
    "B01AA": "Antikoagülan (K vitamini antagonisti)",
    "B01AB": "Antikoagülan (heparin grubu)",
    "B01AC": "Antiagregan",
    "B01AC04": "Antiagregan (P2Y12 inhibitörü)",
    "B01AC05": "Antiagregan (P2Y12 inhibitörü)",
    "B01AC22": "Antiagregan (P2Y12 inhibitörü)",
    "B01AC24": "Antiagregan (P2Y12 inhibitörü)",
    "B01AC25": "Antiagregan (P2Y12 inhibitörü)",
    "B01AC30": "Antiagregan kombinasyonu",
    "B01AD": "Trombolitik (enzim)",
    "B01AE": "Antikoagülan (doğrudan trombin inhibitörü)",
    "B01AF": "Antikoagülan (faktör Xa inhibitörü)",
    "B01AX": "Diğer antitrombotik",
    # C02 Antihipertansifler
    "C02": "Antihipertansif",
    "C02A": "Santral etkili antiadrenerjik",
    "C02C": "Periferik etkili antiadrenerjik (alfa bloker)",
    "C02D": "Arteriyolar düz kas vazodilatörü",
    "C02K": "Diğer antihipertansif (pulmoner hipertansiyon)",
    "C02L": "Antihipertansif + diüretik",
    # C03 Diüretikler
    "C03": "Diüretik",
    "C03A": "Tiyazid diüretik",
    "C03B": "Tiyazid benzeri diüretik",
    "C03C": "Kıvrım (loop) diüretiği",
    "C03D": "Potasyum tutucu diüretik",
    "C03DA": "Aldosteron antagonisti (potasyum tutucu diüretik)",
    "C03E": "Diüretik + potasyum tutucu diüretik",
    "C03X": "Vazopressin antagonisti",
    # C07 Beta blokerler
    "C07": "Beta bloker",
    "C07AG": "Alfa/beta bloker",
    "C07B": "Beta bloker + tiyazid",
    "C07C": "Beta bloker + diüretik",
    "C07F": "Beta bloker + diğer antihipertansif",
    # C08 Kalsiyum kanal blokerleri
    "C08": "Kalsiyum kanal blokeri",
    "C08C": "Kalsiyum kanal blokeri (dihidropiridin)",
    "C08D": "Kalsiyum kanal blokeri (dihidropiridin dışı)",
    "C08G": "Kalsiyum kanal blokeri + diüretik",
    # C09 Renin-anjiyotensin sistemi
    "C09": "Renin-anjiyotensin sistemi ilacı",
    "C09A": "ACE inhibitörü",
    "C09B": "ACE inhibitörü kombinasyonu",
    "C09BA": "ACE inhibitörü + diüretik",
    "C09BB": "ACE inhibitörü + kalsiyum kanal blokeri",
    "C09C": "Anjiyotensin reseptör blokeri (ARB)",
    "C09D": "ARB kombinasyonu",
    "C09DA": "ARB + diüretik",
    "C09DB": "ARB + kalsiyum kanal blokeri",
    "C09DX": "ARB kombinasyonu",
    "C09DX04": "Anjiyotensin reseptör-neprilisin inhibitörü (ARNI)",
    # C10 Lipid düşürücüler
    "C10": "Lipid düşürücü",
    "C10AA": "Statin (HMG-KoA redüktaz inhibitörü)",
    "C10AB": "Fibrat",
    "C10AC": "Safra asidi bağlayıcı",
    "C10AD": "Nikotinik asit türevi",
    "C10AX": "Diğer lipid düşürücü",
    "C10AX09": "Kolesterol emilim inhibitörü",
    "C10AX13": "PCSK9 inhibitörü",
    "C10AX14": "PCSK9 inhibitörü",
    "C10AX15": "ATP sitrat liyaz inhibitörü",
    "C10AX16": "PCSK9 siRNA",
    "C10B": "Lipid düşürücü kombinasyonu",
    "C10BA": "Statin + diğer lipid düşürücü",
    "C10BX": "Statin + diğer ilaç",
}


def atc_class(atc: str) -> str | None:
    """ATC kodundan Türkçe sınıf adı (en uzun eşleşen ön ek)."""
    code = clean_atc(atc)
    for n in range(len(code), 0, -1):
        if code[:n] in ATC_CLASSES:
            return ATC_CLASSES[code[:n]]
    return None


def clean_atc(atc: object) -> str:
    return re.sub(r"\s+", "", str(atc or "").replace("\xa0", " ")).upper()


# ----------------------------------------------------------------- etken madde: Türkçe -> İngilizce generic
# generic -> (Türkçe görünen ad, TİTCK listesinde görülen ek yazımlar / yazım hataları)
SUBSTANCES: dict[str, tuple[str, tuple[str, ...]]] = {
    # Diyabet
    "insulin human": ("İnsan insülini", ("insulin human", "insan insulini", "biyosentetik insan insulini",
                                        "monokomponent biyosentetik insan insulini")),
    "insulin isophane": ("İzofan insan insülini (NPH)", ("izofan nph biyosentetik insan insulini",)),
    "insulin lispro": ("İnsülin lispro", ()),
    "insulin lispro protamine": ("İnsülin lispro protamin", ("insulin lispro protamin",)),
    "insulin aspart": ("İnsülin aspart", ()),
    "insulin aspart protamine": ("İnsülin aspart protamin", ("insulin aspart protamin",)),
    "insulin glulisine": ("İnsülin glulizin", ("insulin glulisin",)),
    "insulin glargine": ("İnsülin glarjin", ()),
    "insulin detemir": ("İnsülin detemir", ()),
    "insulin degludec": ("İnsülin degludek", ()),
    "metformin": ("Metformin", ()),
    "glyburide": ("Glibenklamid", ("glibenclamide",)),
    "chlorpropamide": ("Klorpropamid", ()),
    "glibornuride": ("Glibornurid", ()),
    "glipizide": ("Glipizid", ()),
    "gliclazide": ("Gliklazid", ()),
    "glimepiride": ("Glimepirid", ("glimeprid",)),
    "repaglinide": ("Repaglinid", ()),
    "nateglinide": ("Nateglinid", ()),
    "acarbose": ("Akarboz", ()),
    "miglitol": ("Miglitol", ()),
    "voglibose": ("Vogliboz", ()),
    "pioglitazone": ("Pioglitazon", ()),
    "sitagliptin": ("Sitagliptin", ("sitagligtin",)),
    "vildagliptin": ("Vildagliptin", ()),
    "saxagliptin": ("Saksagliptin", ()),
    "linagliptin": ("Linagliptin", ("linaglipin",)),
    "alogliptin": ("Alogliptin", ("aloglipin",)),
    "dapagliflozin": ("Dapagliflozin", ()),
    "empagliflozin": ("Empagliflozin", ()),
    "exenatide": ("Eksenatid", ()),
    "liraglutide": ("Liraglutid", ()),
    "lixisenatide": ("Liksisenatid", ()),
    "dulaglutide": ("Dulaglutid", ()),
    "semaglutide": ("Semaglutid", ()),
    "tirzepatide": ("Tirzepatid", ()),
    "thioctic acid": ("Alfa lipoik asit", ("alfa lipoik asit", "alpha lipoic acid")),
    # Antitrombotik
    "warfarin": ("Varfarin", ()),
    "heparin": ("Heparin", ("heparın",)),
    "antithrombin iii": ("Antitrombin III", ()),
    "enoxaparin": ("Enoksaparin", ("enoksanarin",)),
    "sulodexide": ("Sulodeksid", ()),
    "bemiparin": ("Bemiparin", ()),
    "clopidogrel": ("Klopidogrel", ()),
    "ticlopidine": ("Tiklopidin", ()),
    "aspirin": ("Asetilsalisilik asit (aspirin)", ("asetilsalisilik asit", "asetil salisilik asit",
                                                   "acetylsalicylic acid")),
    "dipyridamole": ("Dipiridamol", ()),
    "epoprostenol": ("Epoprostenol", ()),
    "iloprost": ("İloprost", ()),
    "abciximab": ("Absiksimab", ()),
    "eptifibatide": ("Eptifibatid", ()),
    "tirofiban": ("Tirofiban", ()),
    "treprostinil": ("Treprostinil", ()),
    "prasugrel": ("Prasugrel", ()),
    "cilostazol": ("Silostazol", ()),
    "ticagrelor": ("Tikagrelor", ()),
    "cangrelor": ("Kangrelor", ()),
    "selexipag": ("Seleksipag", ()),
    "streptokinase": ("Streptokinaz", ("streptokinne",)),
    "alteplase": ("Alteplaz", ()),
    "reteplase": ("Reteplaz", ()),
    "tenecteplase": ("Tenekteplaz", ()),
    "protein c": ("İnsan protein C", ("insan protein c",)),
    "argatroban": ("Argatroban", ()),
    "bivalirudin": ("Bivalirudin", ("bivaluridin",)),
    "dabigatran": ("Dabigatran", ()),
    "rivaroxaban": ("Rivaroksaban", ("rivoraksaban",)),
    "apixaban": ("Apiksaban", ()),
    "edoxaban": ("Edoksaban", ()),
    "fondaparinux": ("Fondaparinuks", ()),
    "caplacizumab": ("Kaplasizumab", ()),
    # Antihipertansif / diüretik / beta bloker / KKB / RAS
    "methyldopa": ("Metildopa", ()),
    "guanfacine": ("Guanfasin", ()),
    "moxonidine": ("Moksonidin", ()),
    "rilmenidine": ("Rilmenidin", ()),
    "doxazosin": ("Doksazosin", ()),
    "urapidil": ("Urapidil", ()),
    "nitroprusside": ("Sodyum nitroprusid", ("nitroprusid",)),
    "bosentan": ("Bosentan", ()),
    "ambrisentan": ("Ambrisentan", ()),
    "macitentan": ("Masitentan", ()),
    "riociguat": ("Riosiguat", ()),
    "tadalafil": ("Tadalafil", ()),
    "reserpine": ("Rezerpin", ()),
    "chlorthalidone": ("Klortalidon", ("chlortalidone",)),
    "indapamide": ("İndapamid", ()),
    "hydrochlorothiazide": ("Hidroklorotiyazid", ("hidroklorotiyazit", "hidroklorotiazid", "hidroklortitazid",
                                                  "vehidroklorotiyazid")),
    "furosemide": ("Furosemid", ("furasemid",)),
    "torsemide": ("Torasemid", ("torasemide",)),
    "spironolactone": ("Spironolakton", ("spirinolakton",)),
    "eplerenone": ("Eplerenon", ()),
    "finerenone": ("Finerenon", ()),
    "amiloride": ("Amilorid", ()),
    "triamterene": ("Triamteren", ()),
    "tolvaptan": ("Tolvaptan", ()),
    "propranolol": ("Propranolol", ()),
    "timolol": ("Timolol", ()),
    "sotalol": ("Sotalol", ()),
    "metoprolol": ("Metoprolol", ("metaprolol",)),
    "s-metoprolol": ("S-metoprolol", ()),
    "atenolol": ("Atenolol", ()),
    "acebutolol": ("Asebutolol", ()),
    "bisoprolol": ("Bisoprolol", ()),
    "esmolol": ("Esmolol", ()),
    "nebivolol": ("Nebivolol", ()),
    "labetalol": ("Labetalol", ()),
    "carvedilol": ("Karvedilol", ("karvedillol",)),
    "amlodipine": ("Amlodipin", ("amlodipinb",)),
    "levamlodipine": ("S-amlodipin (levamlodipin)", ("s-amlodipin", "levamlodipin")),
    "felodipine": ("Felodipin", ()),
    "nicardipine": ("Nikardipin", ()),
    "nifedipine": ("Nifedipin", ()),
    "nimodipine": ("Nimodipin", ()),
    "nitrendipine": ("Nitrendipin", ()),
    "lacidipine": ("Lasidipin", ()),
    "manidipine": ("Manidipin", ()),
    "lercanidipine": ("Lerkanidipin", ()),
    "benidipine": ("Benidipin", ()),
    "verapamil": ("Verapamil", ()),
    "diltiazem": ("Diltiazem", ("diltizem",)),
    "lidoflazine": ("Lidoflazin", ()),
    "captopril": ("Kaptopril", ()),
    "enalapril": ("Enalapril", ()),
    "lisinopril": ("Lisinopril", ()),
    "perindopril": ("Perindopril", ()),
    "ramipril": ("Ramipril", ()),
    "benazepril": ("Benazepril", ()),
    "cilazapril": ("Silazapril", ()),
    "fosinopril": ("Fosinopril", ()),
    "trandolapril": ("Trandolapril", ()),
    "moexipril": ("Moeksipril", ()),
    "zofenopril": ("Zofenopril", ()),
    "imidapril": ("İmidapril", ()),
    "losartan": ("Losartan", ()),
    "eprosartan": ("Eprosartan", ()),
    "valsartan": ("Valsartan", ()),
    "irbesartan": ("İrbesartan", ()),
    "candesartan": ("Kandesartan", ()),
    "telmisartan": ("Telmisartan", ("tekmisartan", "temisartan")),
    "olmesartan": ("Olmesartan", ()),
    "azilsartan": ("Azilsartan", ()),
    "sacubitril": ("Sakubitril", ()),
    # Lipid
    "simvastatin": ("Simvastatin", ()),
    "lovastatin": ("Lovastatin", ()),
    "pravastatin": ("Pravastatin", ()),
    "atorvastatin": ("Atorvastatin", ()),
    "rosuvastatin": ("Rosuvastatin", ("rosuvastatinkalsiyum",)),
    "pitavastatin": ("Pitavastatin", ()),
    "gemfibrozil": ("Gemfibrozil", ()),
    "fenofibrate": ("Fenofibrat", ()),
    "fenofibric acid": ("Fenofibrik asit", ()),
    "colesevelam": ("Kolesevelam", ("kolesevelan",)),
    "niacin": ("Niasin", ("nicotinic acid",)),
    "icosapent ethyl": ("İkosapent etil", ()),
    "ezetimibe": ("Ezetimib", ("ezetimip",)),
    "evolocumab": ("Evolokumab", ()),
    "bempedoic acid": ("Bempedoik asit", ()),
    "inclisiran": ("İnklisiran", ()),
    "pyridinol carbamate": ("Piridinol karbamat", ("pyridinoline carbamate",)),
}

# Etken madde metninden atılan tuz/ester/form sözcükleri (normalize edilmiş, Türkçe + İngilizce)
_TR_NOISE = {
    "hidroklorur", "hidroklorurr", "hidrokloru", "dihidroklorur", "hcl", "hci", "hidroklorid",
    "besilat", "bes.", "bes", "maleat", "maleate", "mesilat", "mesylate", "potasyum", "sodyum", "disodyum",
    "tetrasodyum", "kalsiyum", "magnezyum", "trihidrat", "monohidrat", "dihidrat", "anhidrus", "tartarat",
    "suksinat", "fumarat", "fumarate", "medoksomil", "medoksamil", "medoxomil", "nedoksomil", "kamedoksomil",
    "sileksetil", "sileksatil", "erbumin", "arjinin", "tert-butilamin", "tertbutilamin", "tert", "butilamin",
    "propandiol", "fosfat", "malat", "benzoat", "tosilat", "bisulfat", "hidrojen", "sulfat", "trometamol",
    "baz", "etexilate", "mikronize", "levorotatory", "cozelti", "suspansiyon", "vandf", "potassium",
    "sodium", "calcium", "hydrochloride", "besylate", "monohydrate", "anhydrous",
}
_SEPARATORS = re.compile(r"[,/+;()\[\]&]|\bve\b|\band\b|\bwith\b|%\s*\d+(?:[.,]\d+)?|\d+(?:[.,]\d+)?\s*%")

# ATC 5. düzey kodu etken maddeyi kesin tanımlıyorsa metin yerine bu kullanılır (insan insülini / karışımlar).
# Lispro/aspart karışım kodları (A10AD04/05) yalnızca metinde "protamin" ya da ürün adında "mix" varsa uygulanır:
# TİTCK listesinde karışım olmayan bazı Humalog ürünleri de A10AD04 ile kodlanmış.
ATC_FORCED_REQUIRES_MIX = {"A10AD04", "A10AD05"}
ATC_FORCED: dict[str, str] = {
    "A10AB01": "insulin human",
    "A10AC01": "insulin isophane",
    "A10AD01": "insulin human + insulin isophane",
    "A10AD30": "insulin human + insulin isophane",
    "A10AD04": "insulin lispro + insulin lispro protamine",
    "A10AD05": "insulin aspart + insulin aspart protamine",
}
# Metin kısmen çözülebiliyorsa ("valsartan and diuretics", yapışık yazımlar) ATC kombinasyon kodundan tamamlanır
ATC_FALLBACK: dict[str, str] = {
    "C02LA01": "chlorthalidone + reserpine",
    "C07BB07": "bisoprolol + hydrochlorothiazide",
    "C09BA02": "enalapril + hydrochlorothiazide",
    "C09BA04": "indapamide + perindopril",
    "C09BA05": "hydrochlorothiazide + ramipril",
    "C09BB04": "amlodipine + perindopril",
    "C09BB06": "enalapril + nitrendipine",
    "C09BB10": "trandolapril + verapamil",
    "C09DA01": "hydrochlorothiazide + losartan",
    "C09DA03": "hydrochlorothiazide + valsartan",
    "C09DA04": "hydrochlorothiazide + irbesartan",
    "C09DA06": "candesartan + hydrochlorothiazide",
    "C09DA08": "hydrochlorothiazide + olmesartan",
    "C09DB01": "amlodipine + valsartan",
    "C09DB02": "amlodipine + olmesartan",
    "C09DB04": "amlodipine + telmisartan",
    "C09DX06": "amlodipine + candesartan + hydrochlorothiazide",
    "C10BX05": "aspirin + rosuvastatin",
    "C10BX09": "amlodipine + rosuvastatin",
}


def _phrase_table() -> dict[tuple[str, ...], str]:
    table: dict[tuple[str, ...], str] = {}
    for generic, (tr_display, variants) in SUBSTANCES.items():
        for name in (generic, tr_display, *variants):
            words = tuple(w for w in normalize(name).replace("(", " ").replace(")", " ").split() if w)
            if words:
                table.setdefault(words, generic)
    return table


_PHRASES = _phrase_table()
_MAX_PHRASE = max(len(k) for k in _PHRASES)


def parse_ingredient(text: str) -> tuple[list[str], list[str]]:
    """Etken madde metni -> (tanınan generic bileşenler, tanınmayan sözcükler).

    Ayırıcılar (, / + ve and) ve tuz sözcükleri atılır; kalan sözcükler SUBSTANCES tablosundaki (çok sözcüklü)
    adlarla en uzun eşleşmeyle tüketilir. Ayırıcısız yazımlar da ("metformin pioglitazon") çözülür."""
    n = _SEPARATORS.sub(" ", normalize(text or ""))
    words = [w.strip(".-") for w in n.split()]
    words = [w for w in words if w and w not in _TR_NOISE and not re.fullmatch(r"[\d.,]+", w)]
    found: list[str] = []
    unknown: list[str] = []
    i = 0
    while i < len(words):
        for k in range(min(_MAX_PHRASE, len(words) - i), 0, -1):
            hit = _PHRASES.get(tuple(words[i:i + k]))
            if hit:
                if hit not in found:
                    found.append(hit)
                i += k
                break
        else:
            unknown.append(words[i])
            i += 1
    return found, unknown


def _is_mix(text: str, product: str) -> bool:
    return "protamin" in normalize(text) or bool(re.search(r"\bmix", normalize(product)))


def ingredient_to_generic(text: str, atc: str = "", product: str = "") -> str | None:
    """TİTCK etken madde metni + ATC kodu -> katalog anahtarı (canonical_generic). Çözülemezse None.

    - ATC_FORCED kodlarında metin yok sayılır (karışım insülinlerin yazımı çok değişken).
    - Metin hiç tanınmıyorsa None (ATC koduna rağmen yanlış etken madde yazılmış satırlar, ör. tatlandırıcılar
      A10X altında; bir antihistaminik C09DA08 altında listelenmiş).
    - Metin kısmen tanınıyorsa ya da ATC kombinasyonunun alt kümesiyse ATC_FALLBACK kullanılır."""
    code = clean_atc(atc)
    if code in ATC_FORCED and (code not in ATC_FORCED_REQUIRES_MIX or _is_mix(text, product)):
        return canonical_generic(ATC_FORCED[code])
    found, unknown = parse_ingredient(text)
    if not found:
        return None
    fallback = ATC_FALLBACK.get(code)
    if fallback:
        fb = set(fallback.split(" + "))
        if unknown or set(found) < fb:
            if set(found) <= fb:
                return canonical_generic(fallback)
            return None
    elif unknown:
        return None
    if len(found) > MAX_COMPONENTS:
        return None
    return canonical_generic(" + ".join(found))


def generic_display(generic: str) -> str:
    """Türkçe görünen ad: "hydrochlorothiazide + valsartan" -> "Hidroklorotiyazid + Valsartan"."""
    parts = []
    for comp in generic.split(" + "):
        tr = SUBSTANCES.get(comp)
        parts.append(tr[0] if tr else display_from_generic(comp))
    return " + ".join(parts)


# ----------------------------------------------------------------- ürün adı -> marka
# Bu sözcüklerden (ya da rakam/yüzde içeren ilk sözcükten) itibaren ürün adının geri kalanı atılır
_STOP_WORDS = {
    "tablet", "tb", "tab", "film", "kapli", "kaplı", "kapsul", "kapsül", "efervesan", "enjeksiyonluk", "enjeksiyon",
    "infuzyon", "infüzyon", "cozelti", "çözelti", "suspansiyon", "toz", "flakon", "ampul", "kalem", "kartus",
    "kartuş", "enjektor", "enjektör", "kullanima", "kullanıma", "hazir", "hazır", "sase", "şase", "surup", "şurup",
    "damla", "oral", "uzatilmis", "uzatılmış", "kontrollu", "kontrollü", "modifiye", "salimli", "salımlı",
    "agizda", "ağızda", "dagilan", "dağılan", "cigneme", "çiğneme", "yumusak", "yumuşak", "sert", "gastro",
    "mikropellet", "pellet", "granul", "granül", "liyofilize", "konsantre", "steril", "iv", "i.v.", "im", "sc",
    "s.c.", "mg", "mcg", "µg", "g", "ml", "iu", "ui", "u", "unite", "ünite", "solostar", "flexpen", "kwikpen",
    "penfill", "flextouch", "innolet", "draje", "lak", "enterik", "çentikli", "centikli", "doz", "dozluk", "ve", "+", "-", "içeren", "iceren", "icin", "için",
}
_KEEP_UPPER = {"mr", "xr", "sr", "xl", "la", "hct", "odt", "er", "cr", "ld", "dr", "hm", "nph", "ii", "iii"}


def _title_word(word: str) -> str:
    low = word.replace("İ", "i").replace("I", "i").lower()
    if low in _KEEP_UPPER:
        return low.upper()
    return "-".join(p.upper() if p in _KEEP_UPPER else p[:1].upper() + p[1:] for p in low.split("-"))


def brand_from_product(product: str) -> str | None:
    """"GLİFOR 1000 MG 100 FİLM KAPLI TABLET" -> "Glifor"; "GALVUS MET 50/1000 MG..." -> "Galvus Met";
    "DIAMICRON MR 60 MG" -> "Diamicron MR". Ürün adı form/doz ile başlıyorsa None."""
    text = re.sub(r"[®™©]", " ", str(product or "").replace("\xa0", " ")).strip()
    text = re.split(r"[,(]", text, maxsplit=1)[0]
    out: list[str] = []
    for raw in text.split():
        w = raw.strip(".;:")
        low = w.replace("İ", "i").replace("I", "ı").lower()
        glued = re.match(r"([^\W\d_]{2,})\d", w)  # "MIX25" -> "Mix" (harf + doz bitişik)
        if glued and normalize(glued.group(1)) not in _STOP_WORDS:
            out.append(_title_word(glued.group(1)))
            break
        if not w or re.search(r"[\d%]", w) or low in _STOP_WORDS or normalize(w) in _STOP_WORDS or "/" in w:
            break
        out.append(_title_word(w))
    brand = " ".join(out).strip(" -&")
    return brand if len(normalize(brand)) >= 3 else None


_SUBSTANCE_WORDS = {normalize(n).split()[0] for g, (tr, _v) in SUBSTANCES.items() for n in (g, tr)}


def is_generic_named(brand: str) -> bool:
    """"Metformin Teva", "Amlodipin Sandoz" gibi etken madde adıyla başlayan (jenerik adlı) ürünler.

    Bunlar marka listesine eklenmez; etken madde adıyla zaten bulunurlar."""
    first = normalize(brand).split()[0] if normalize(brand) else ""
    return first in _SUBSTANCE_WORDS


# ----------------------------------------------------------------- okuma
@dataclass(frozen=True)
class TitckRow:
    product: str
    ingredient: str
    atc: str
    suspended: bool = False
    barcode: str = ""


_HEADER_KEYS = {"product": ("urun adi",), "ingredient": ("etkin madde",), "atc": ("atc kodu", "atc"),
                "barcode": ("barkod",)}


def _find_columns(header: list[object]) -> dict[str, int] | None:
    norm = [normalize(str(h or "")) for h in header]
    cols: dict[str, int] = {}
    for key, names in _HEADER_KEYS.items():
        for i, h in enumerate(norm):
            if h in names:
                cols[key] = i
                break
    for i, h in enumerate(norm):  # askı durumu sütunu: "RUHSATI ASKIDA OLMAYAN ÜRÜN: 0 ..."
        if h.startswith("ruhsati askida olmayan"):
            cols["suspended"] = i
    return cols if {"product", "ingredient", "atc"} <= cols.keys() else None


def _iter_raw_rows(path: Path) -> Iterator[list[object]]:
    if path.suffix.lower() in {".xlsx", ".xlsm"}:
        try:
            import openpyxl
        except ImportError as e:  # pragma: no cover - requirements.txt'te sabit
            raise RuntimeError("Excel okumak için openpyxl gerekli: pip install openpyxl") from e
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            ws = wb.worksheets[0]  # "RUHSATLI ÜRÜNLER LİSTESİ"; diğer sayfalar alerjen/radyofarmasötik
            for row in ws.iter_rows(values_only=True):
                yield list(row)
        finally:
            wb.close()
        return
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1254"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    sample = text[:4096]
    delim = ";" if sample.count(";") > sample.count(",") else ","
    yield from csv.reader(io.StringIO(text), delimiter=delim)


def read_titck(path: Path, prefixes: Iterable[str] = TARGET_ATC_PREFIXES) -> tuple[list[TitckRow], int]:
    """TİTCK listesini okur, ATC ön ekiyle süzer. Döndürür: (süzülmüş satırlar, toplam ürün satırı)."""
    prefixes = tuple(prefixes)
    cols: dict[str, int] | None = None
    rows: list[TitckRow] = []
    total = 0
    for raw in _iter_raw_rows(Path(path)):
        if cols is None:
            cols = _find_columns(raw)
            continue
        if not raw or len(raw) <= max(cols.values()):
            continue
        product = str(raw[cols["product"]] or "").strip()
        if not product:
            continue
        total += 1
        atc = clean_atc(raw[cols["atc"]])
        if not atc.startswith(prefixes):
            continue
        susp = raw[cols["suspended"]] if "suspended" in cols else 0
        rows.append(TitckRow(
            product=product,
            ingredient=str(raw[cols["ingredient"]] or "").strip(),
            atc=atc,
            suspended=str(susp).strip() not in {"", "0", "0.0", "None"},
            barcode=str(raw[cols["barcode"]] or "").strip() if "barcode" in cols else "",
        ))
    if cols is None:
        raise ValueError("TİTCK dosyasında başlık satırı bulunamadı (ÜRÜN ADI / ETKİN MADDE / ATC KODU)")
    return rows, total


def list_date_from_name(name: str) -> date | None:
    """"RuhsatlBeeriTbbirnlerListesi23.10.2026_<uuid>.xlsx" -> 2026-10-23."""
    m = re.search(r"(\d{2})[.\-_](\d{2})[.\-_](\d{4})", name)
    if not m:
        return None
    try:
        return datetime.strptime("".join(m.groups()), "%d%m%Y").date()
    except ValueError:
        return None


# ----------------------------------------------------------------- katalog üretimi
@dataclass
class CatalogEntry:
    generic: str
    display: str
    pharm_class: str | None
    atc: list[str]
    brands: list[str]
    aliases: list[str] = field(default_factory=list)
    products: int = 0
    curated: bool = False


@dataclass
class ImportReport:
    total_rows: int = 0
    filtered_rows: int = 0
    suspended_skipped: int = 0
    resolved_rows: int = 0
    unresolved: Counter = field(default_factory=Counter)  # (atc, etken madde) -> ürün sayısı
    brand_conflicts: dict[str, list[str]] = field(default_factory=dict)
    curated_missing_generics: list[str] = field(default_factory=list)  # kürate etken madde TİTCK'da yok
    curated_missing_brands: list[tuple[str, str]] = field(default_factory=list)  # (generic, marka)


def _curated_entries(path: Path | None) -> list[dict]:
    if path is None or not Path(path).exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [it for it in (yaml.safe_load(f) or []) if isinstance(it, dict) and it.get("generic")]


def brand_matches(curated_brand: str, products: Iterable[str]) -> bool:
    """Kürate marka TİTCK ürün adlarından birinin kelime başı ön eki mi ("Lantus" ~ "LANTUS SOLOSTAR 100 IU")."""
    b = normalize(curated_brand).replace("-", " ")
    return any((p := normalize(x).replace("-", " ")) == b or p.startswith(b + " ") for x in products)


def build_catalog(rows: Iterable[TitckRow], curated_path: Path | None = None, *,
                  include_suspended: bool = False) -> tuple[list[CatalogEntry], ImportReport]:
    report = ImportReport()
    by_generic_products: dict[str, list[str]] = defaultdict(list)
    by_generic_atc: dict[str, Counter] = defaultdict(Counter)
    brand_generics: dict[str, Counter] = defaultdict(Counter)  # normalize(marka) -> generic sayaçları
    brand_label: dict[str, str] = {}
    for r in rows:
        report.filtered_rows += 1
        if r.suspended and not include_suspended:
            report.suspended_skipped += 1
            continue
        g = ingredient_to_generic(r.ingredient, r.atc, r.product)
        if not g:
            report.unresolved[(r.atc, r.ingredient)] += 1
            continue
        report.resolved_rows += 1
        by_generic_products[g].append(r.product)
        by_generic_atc[g][r.atc] += 1
        brand = brand_from_product(r.product)
        if brand and not is_generic_named(brand):
            key = normalize(brand)
            brand_generics[key][g] += 1
            brand_label.setdefault(key, brand)

    # Aynı marka birden çok etken maddede: en çok ürünü olana bağlanır (ör. hatalı ATC/etken madde satırları)
    brands_of: dict[str, list[str]] = defaultdict(list)
    for key, counter in sorted(brand_generics.items()):
        owner = max(counter.items(), key=lambda kv: (kv[1], -len(kv[0])))[0]
        if len(counter) > 1:
            report.brand_conflicts[brand_label[key]] = sorted(counter)
        brands_of[owner].append(brand_label[key])

    entries: dict[str, CatalogEntry] = {}
    for g, products in by_generic_products.items():
        atc_codes = sorted(by_generic_atc[g], key=lambda c: (-by_generic_atc[g][c], c))
        entries[g] = CatalogEntry(
            generic=g, display=generic_display(g), pharm_class=atc_class(atc_codes[0]), atc=sorted(atc_codes),
            brands=sorted(brands_of.get(g, []), key=normalize), products=len(products))

    # Kürate katman: görünen ad, sınıf adı, takma adlar; kürate markalar TİTCK ürünleriyle doğrulanır
    for it in _curated_entries(curated_path):
        g = canonical_generic(str(it["generic"]))
        entry = entries.get(g)
        if entry is None:
            report.curated_missing_generics.append(g)
            entry = entries[g] = CatalogEntry(generic=g, display=generic_display(g), pharm_class=None, atc=[],
                                              brands=[])
        entry.curated = True
        if it.get("display"):
            entry.display = str(it["display"])
        if it.get("pharm_class"):
            entry.pharm_class = str(it["pharm_class"])
        entry.aliases = [str(a) for a in it.get("aliases") or []]
        products = by_generic_products.get(g, [])
        verified: list[str] = []
        for b in it.get("brands") or []:
            b = str(b).strip()
            if brand_matches(b, products):
                verified.append(b)
            else:
                report.curated_missing_brands.append((g, b))
        # Kürate yazım (NovoRapid, Beloc Zok) TİTCK'dan üretilmiş eşdeğerinin yerini alır ve başa gelir
        keys = {normalize(b) for b in verified}
        entry.brands = verified + [x for x in entry.brands if normalize(x) not in keys]
    return sorted(entries.values(), key=_entry_sort_key), report


def _entry_sort_key(e: CatalogEntry) -> tuple:
    group = e.atc[0][:3] if e.atc else "ZZZ"
    return (group, e.generic.count("+"), e.generic)


_GROUP_TITLES = {
    "A10": "A10 — Diyabet ilaçları", "B01": "B01 — Antitrombotikler (antiagregan / antikoagülan)",
    "C02": "C02 — Antihipertansifler", "C03": "C03 — Diüretikler", "C07": "C07 — Beta blokerler",
    "C08": "C08 — Kalsiyum kanal blokerleri", "C09": "C09 — Renin-anjiyotensin sistemi",
    "C10": "C10 — Lipid düşürücüler", "ZZZ": "TİTCK listesinde tek başına ürünü olmayan kürate etken maddeler",
}


def _flow(items: Iterable[str]) -> str:
    return "[" + ", ".join(_scalar(i) for i in items) + "]"


def _scalar(value: str) -> str:
    """YAML'da güvenli skaler: gerekiyorsa çift tırnak (JSON uyumlu)."""
    if re.fullmatch(r"[A-Za-zÇĞİÖŞÜçğıöşü][\wÇĞİÖŞÜçğıöşü .\-'()]*", value) and not value.endswith(" ") and \
            value.lower() not in {"yes", "no", "true", "false", "null", "on", "off"}:
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_yaml(entries: list[CatalogEntry], *, source: str, command: str, report: ImportReport) -> str:
    total_brands = sum(len(e.brands) for e in entries)
    lines = [
        "# Türkçe ilaç kataloğu — OTOMATİK ÜRETİLMİŞTİR, ELLE DÜZENLEMEYİN.",
        f"# Kaynak: {source} (Ruhsatlı Beşeri Tıbbi Ürünler Listesi, {SOURCE_URL})",
        f"# Üretim: {command}",
        "# Kürate katman (görünen ad, takma ad, sınıf adı düzeltmeleri): app/data/drug_catalog_curated.yaml",
        f"# ATC filtresi: {', '.join(TARGET_ATC_PREFIXES)}. Askıdaki ürünler hariç.",
        f"# İçerik: {len(entries)} etken madde / kombinasyon, {total_brands} TR marka "
        f"({report.resolved_rows} ruhsatlı ürün satırından).",
        "#",
        "# Alanlar: generic (İngilizce, openFDA sorgusu için), display (Türkçe görünen ad), pharm_class (ATC'den",
        "# Türkçe sınıf), atc (TİTCK ATC kodları), brands (ürün adından sadeleştirilmiş TR markalar; etken madde",
        "# adıyla başlayan jenerik ürünler hariç), products (ruhsatlı ürün/ambalaj sayısı), aliases (aranabilir ek ad).",
        "# Not: eğitim/demo amaçlıdır; tedavi kararı ve ruhsat durumu için güncel TİTCK kaydı esastır.",
        "",
    ]
    group = None
    for e in entries:
        g = e.atc[0][:3] if e.atc else "ZZZ"
        if g != group:
            group = g
            lines += ["", f"# {'-' * 66} {_GROUP_TITLES.get(g, g)}"]
        lines.append(f"- generic: {_scalar(e.generic)}")
        if e.display != display_from_generic(e.generic):
            lines.append(f"  display: {_scalar(e.display)}")
        if e.aliases:
            lines.append(f"  aliases: {_flow(e.aliases)}")
        if e.pharm_class:
            lines.append(f"  pharm_class: {_scalar(e.pharm_class)}")
        if e.atc:
            lines.append(f"  atc: {_flow(e.atc)}")
        lines.append(f"  products: {e.products}")
        lines.append(f"  brands: {_flow(e.brands)}")
    return "\n".join(lines).rstrip() + "\n"
