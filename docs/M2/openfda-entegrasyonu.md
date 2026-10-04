# openFDA Entegrasyonu (M2)

Spec: "openFDA'dan ilaç etiketi çeken uç nokta". Uç nokta: `GET /drugs/label?name=<ilaç adı>`. Kod: `backend/app/routers/drugs.py` (HTTP katmanı), `backend/app/services/openfda.py` (istemci, eşleme, önbellek), `backend/app/services/rag/ingest.py` (eğitim içeriğine ekleme). Testler: `backend/tests/test_api.py` (`test_openfda_parse_with_mock_transport`, `test_openfda_not_found`, `test_drug_label_endpoint`).

İstek/yanıt örneği ve sıra diyagramı: [api-referansi.md](api-referansi.md) §5.6 ve §6.2.

## 1. Akış

1. Kullanıcı ilaç adını verir (ör. `Glifor`, `metformin`). Uç nokta giriş yapmış herhangi bir rol için açıktır (`name`: 2..80 karakter).
2. `resolve_generic`: ad normalize edilir (küçük harf, Türkçe karakter sadeleştirme: `guardrails.normalize`), **ilk kelime** alınır ve Türkiye ticari isim tablosunda aranır. Tabloda yoksa girilen ad etken madde kabul edilir (ör. `Metformin 1000 mg` yazan kullanıcı `metformin` olarak aranır).
3. Önbellek kontrolü (anahtar: etken madde).
4. Önbellekte yoksa openFDA'ya `search=openfda.generic_name:"<etken madde>"`, `limit=1` sorgusu atılır.
5. Dönen ilk etiket `parse_label` ile sade bir özete indirgenir ve önbelleğe yazılır.
6. Özet, aynı etken madde için eğitim içeriğinde (RAG) kayıt yoksa `education_contents`'a eklenir (bkz. §6).

## 2. Çıkarılan alanlar

`parse_label` (`openfda.py`) etiketten şu alanları üretir:

| Yanıt alanı | openFDA kaynağı | Not |
|---|---|---|
| `query` | kullanıcının girdiği ad | Olduğu gibi |
| `resolved_generic` | eşleme sonucu | Sorguda kullanılan etken madde |
| `brand_names` | `openfda.brand_name` | İlk 5 |
| `generic_names` | `openfda.generic_name` | İlk 3 |
| `active_ingredients` | `openfda.substance_name`, yoksa `generic_name` | |
| `indications` | `indications_and_usage` | Kullanım amacı |
| `warnings` | `warnings_and_cautions`, yoksa `warnings` | |
| `drug_interactions` | `drug_interactions` | |
| `source_name` | sabit | "openFDA Drug Label API (ABD FDA etiketi)" |
| `source_url` | sorgu adresi | `https://api.fda.gov/drug/label.json?search=openfda.generic_name:"<etken madde>"` |
| `set_id` | `set_id` | Etiket sürüm kimliği |
| `disclaimer` | sabit | "Bu bilgi ABD ilaç etiketinden alınmıştır, eğitim amaçlıdır; tedavi kararı için hekiminize danışın." |

Metin alanları (`indications`, `warnings`, `drug_interactions`) birden çok paragraf olabilir; birleştirilir ve her biri en çok **1200 karaktere** kesilir (`MAX_FIELD_CHARS`, kesilirse sonuna `…` eklenir). Bölüm yoksa alan `null` olur.

## 3. Türkiye ticari ismi → etken madde eşlemesi

openFDA yalnızca ABD etiketlerini içerir; Türkiye'deki ticari isimler (Glifor, Delix...) orada bulunmaz. Bu yüzden küçük, elle kürate edilmiş bir tablo kullanılır (`TR_BRAND_TO_GENERIC`, anahtarlar normalize edilmiş):

| Ticari isim | Etken madde | Grup |
|---|---|---|
| glifor | metformin | Diyabet |
| glucophage | metformin | Diyabet |
| matofin | metformin | Diyabet |
| diaformin | metformin | Diyabet |
| amaryl | glimepiride | Diyabet |
| diamicron | gliclazide | Diyabet |
| januvia | sitagliptin | Diyabet |
| galvus | vildagliptin | Diyabet |
| jardiance | empagliflozin | Diyabet |
| forziga | dapagliflozin | Diyabet |
| norvasc | amlodipine | Hipertansiyon |
| coversyl | perindopril | Hipertansiyon |
| delix | ramipril | Hipertansiyon |
| beloc | metoprolol | Hipertansiyon |
| concor | bisoprolol | Hipertansiyon |
| micardis | telmisartan | Hipertansiyon |
| diovan | valsartan | Hipertansiyon |
| atacand | candesartan | Hipertansiyon |
| lasix | furosemide | Hipertansiyon / diüretik |
| lipitor | atorvastatin | Lipid (eşlik eden) |
| ator | atorvastatin | Lipid (eşlik eden) |

Tablo "Grup" sütunu bu belgede okunabilirlik için eklenmiştir, kodda yoktur. Tabloda olmayan ad, etken madde varsayılarak doğrudan aranır; bulunamazsa 404 döner. Yeni ilaç eklemek için `TR_BRAND_TO_GENERIC` sözlüğüne bir satır eklenir. Eşleme klinisyence gözden geçirilmelidir.

## 4. Hata yönetimi

| Durum | İstemci yanıtı | İç neden |
|---|---|---|
| Ad boş ya da çözülemedi | 404 | `DrugLabelNotFound` |
| openFDA HTTP 404 | **404** `'<ad>' için openFDA'da etiket bulunamadı` | openFDA eşleşme yoksa 404 döner |
| openFDA 200 ama `results` boş | **404** | `DrugLabelNotFound` |
| Ağ hatası / zaman aşımı (10 sn) | **503** `openFDA şu an yanıt vermiyor` | `httpx.HTTPError` -> `OpenFDAUnavailable` |
| openFDA HTTP >= 400 (404 hariç; ör. 429, 5xx) | **503** | `OpenFDAUnavailable` |
| `name` 2..80 dışı | 422 | FastAPI doğrulaması |
| Token yok | 401 | |
| Eğitim içeriğine ekleme başarısız | **200** (etiket yine döner) | En iyi çaba; `rollback` + uyarı günlüğü |

Hatalar önbelleğe yazılmaz; geçici bir 503 sonraki isteği etkilemez. Koç tarafında openFDA arızası koçu durdurmaz: koç kendi eğitim korpusuyla çalışır.

## 5. Önbellek

`LabelCache` (`openfda.label_cache`):

- Anahtar: `openfda:label:<etken madde>` (aynı etken maddeye çözülen tüm ticari isimler tek kaydı paylaşır; `Glifor` ve `Glucophage` ikinci kez dış çağrı yapmaz).
- **`REDIS_URL` tanımlıysa Redis** (24 saat TTL, `SETEX`); docker compose'da `redis://redis:6379/0`.
- Tanımlı değilse veya Redis'e ulaşılamıyorsa **süreç içi sözlük** kullanılır (süreç yeniden başlayınca silinir). Redis okuma/yazma hatası isteği bozmaz, uyarı günlüğüne yazılıp bellek önbelleğine düşülür. Bağlantı zaman aşımı 2 sn.
- Testler `drugs._cache.clear()` ile temizler; dış ağ çağrısı yapılmaz.

## 6. Eğitim içeriğine ekleme (RAG)

Etiket sorgusu başarılı olunca (ve `POST /education/ingest-drug` çağrısında) özet `education_contents`'a eklenir:

- `kind = openfda`, `drug_key = <etken madde>` (UNIQUE: aynı etken madde için tek kayıt, tekrar çağrı idempotent), `reviewed = false` (klinisyen gözden geçirmesi bekler), kaynak adı ve URL etikettendir.
- Gövde Türkçe başlıklarla kısa bir özettir: etken madde, kullanım amacı, önemli uyarılar, etkileşimler ve uyarı notu. Her alan en çok ~350 karaktere indirilir ve **sayısal doz içeren cümleler (mg, mcg, mL...) çıkarılır**; koçun doz bilgisi vermesini önlemek için (bkz. [../M1/02-guardrail-politikasi.md](../M1/02-guardrail-politikasi.md)).
- Kayıt embedding'lenir ve koçun `search_education` aracı bu kaydı kaynak olarak gösterebilir.

Bu adım M4'te eklendi; M2'deki uç nokta yalnızca etiketi döndürüyordu.

## 7. Hız limiti ve API anahtarı

- Anahtarsız kullanımda openFDA sınırı düşüktür (yaklaşık dakikada 240 istek, IP başına günde 1.000 istek); anahtarla günlük sınır belirgin biçimde artar. Güncel değerler openFDA belgelerinde doğrulanmalıdır.
- API anahtarı isteğe bağlıdır: `OPENFDA_API_KEY` ortam değişkeni (`settings.openfda_api_key`) tanımlıysa her istekte `api_key` parametresi olarak eklenir. Taban adres `OPENFDA_BASE_URL` ile değiştirilebilir (varsayılan `https://api.fda.gov/drug/label.json`).
- 24 saatlik önbellek ve etken madde bazlı anahtar, aynı ilaç için tekrar çağrıları büyük ölçüde ortadan kaldırır. Limit aşılırsa openFDA 429 döner ve kullanıcı 503 görür; önbellekteki ilaçlar etkilenmez.
- Uç nokta kimlik doğrulaması ister; anonim kullanımla kotanın tüketilmesi engellenir.

## 8. Sınırlılıklar

- **ABD etiketleri**: Türkiye'deki ruhsatlı ürünlerin prospektüsü (TİTCK/KÜB-KT) ile içerik, endikasyon ve uyarılar farklı olabilir. Çıktı yalnızca eğitim amaçlıdır; yanıtta `disclaimer` alanı ve kaynak bağlantısı bulunur.
- **Eşleme tablosu küçük**: yalnızca diyabet ve hipertansiyon odaklı yaklaşık 20 ticari isim. Tabloda olmayan Türkçe marka adı bulunamaz (404).
- **Yalnızca ilk kelime** çözülür; kombinasyon ilaçlar (ör. iki etken maddeli ürünler) tek etken madde olarak aranabilir.
- **Tek sonuç** (`limit=1`): openFDA'nın ilk etiketi alınır; aynı etken madde için birden çok üretici etiketi olabilir, hangisinin geldiği garanti edilmez.
- Etiket metni İngilizcedir; çeviri yapılmaz. Metinler kısaltılır (1200 karakter), bu nedenle tam prospektüsün yerini tutmaz.
- Dış servis erişilebilirliğine bağlıdır (503 durumu); önbellek yalnızca daha önce sorgulanmış ilaçları korur.
- Etiket içeriği `reviewed = false` olarak eklenir; üretimde klinisyen onayı olmadan hasta karşısında kesin bilgi gibi sunulmamalıdır.
