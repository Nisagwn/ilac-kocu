# openFDA Entegrasyonu (M2)

Spec: "openFDA'dan ilaç etiketi çeken uç nokta". Uç nokta: `GET /drugs/label?name=<ilaç adı>`. Kod: `backend/app/routers/drugs.py` (HTTP katmanı), `backend/app/services/openfda.py` (istemci, eşleme, önbellek), `backend/app/services/rag/ingest.py` (eğitim içeriğine ekleme). Testler: `backend/tests/test_api.py` (`test_openfda_parse_with_mock_transport`, `test_openfda_not_found`, `test_drug_label_endpoint`), `backend/tests/test_openfda_fixtures.py` (gerçek openFDA yanıt kesitleriyle), `backend/tests/test_drug_catalog.py`, `backend/tests/test_titck.py`; canlı kontrol `backend/tests/test_openfda_live.py` (§11).

İstek/yanıt örneği ve sıra diyagramı: [api-referansi.md](api-referansi.md) §5.6 ve §6.2.

## 1. Akış

1. Kullanıcı ilaç adını verir (ör. `Glifor`, `metformin`). Uç nokta giriş yapmış herhangi bir rol için açıktır (`name`: 2..80 karakter).
2. `resolve_generic` (`drug_catalog.resolve_name`): ad normalize edilir (küçük harf, Türkçe karakter sadeleştirme: `guardrails.normalize`) ve Türkçe ilaç kataloğunda (§3) en uzun kelime ön ekiyle aranır: `Glifor 1000 mg` → `metformin`, `Galvus Met` → `metformin + vildagliptin`. Katalogda yoksa ilk kelime etken madde kabul edilir (ör. `Metformin 1000 mg` → `metformin`).
3. Önbellek kontrolü (anahtar: etken madde).
4. Önbellekte yoksa openFDA'ya `search=openfda.generic_name:"<etken madde>"`, `limit=1` sorgusu atılır; kombinasyonlarda her bileşen ayrı ifadeyle `AND`'lenir.
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

## 3. Türkiye ticari ismi → etken madde eşlemesi (TİTCK tabanlı katalog)

openFDA yalnızca ABD etiketlerini içerir; Türkiye'deki ticari isimler (Glifor, Delix...) orada bulunmaz. Eşleme, TİTCK'nın yayımladığı **Ruhsatlı Beşeri Tıbbi Ürünler Listesi**nden üretilen Türkçe ilaç kataloğuyla yapılır: [`backend/app/data/drug_catalog_tr.yaml`](../../backend/app/data/drug_catalog_tr.yaml). Ayrıntı §9'da.

| Örnek ad | Etken madde (generic) | Sınıf (ATC'den) |
|---|---|---|
| Glifor, Matofin, Diaformin | metformin | Biguanid (A10BA02) |
| Galvus Met | metformin + vildagliptin | DPP-4 inhibitörü + biguanid (A10BD08) |
| Diamicron MR | gliclazide | Sülfonilüre (A10BB09) |
| Norvasc | amlodipine | Kalsiyum kanal blokeri (C08CA01) |
| Delix / Delix Plus | ramipril / hydrochlorothiazide + ramipril | ACE inhibitörü (C09AA05) / ACE inhibitörü + diüretik (C09BA05) |
| Co-Diovan | hydrochlorothiazide + valsartan | ARB + diüretik (C09DA03) |
| Coraspin | aspirin | Antiagregan (B01AC06) |

Generic adları openFDA sorgusunda kullanıldığından ABD adı tercih edilir (`glyburide` ← glibenklamid, `chlorthalidone` ← klortalidon, `aspirin` ← asetilsalisilik asit, `torsemide` ← torasemid); diğerleri İngilizce INN. Katalogda olmayan ad, etken madde varsayılarak doğrudan aranır; bulunamazsa 404 döner. Hasta ilaç eklerken katalogdan seçerse ilaç `verified=true` olur; serbest metin `verified=false` kaydedilir ve hekim panelinde işaretlenir.

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
- **Katalog ATC filtresiyle sınırlı** (A10, B01, C02, C03, C07, C08, C09, C10): bu grupların dışındaki Türkçe marka adı katalogda yoksa ilk kelime etken madde sayılır; bulunamazsa 404.
- **Katalog elle yenilenir**: TİTCK listesi haftalık güncellenir, `import_titck` otomatik çalışmaz (liste tarihi YAML başlığında yazar).
- **Tek sonuç** (`limit=1`): openFDA'nın ilk etiketi alınır; aynı etken madde için birden çok üretici etiketi olabilir, hangisinin geldiği garanti edilmez.
- Etiket metni İngilizcedir; çeviri yapılmaz. Metinler kısaltılır (1200 karakter), bu nedenle tam prospektüsün yerini tutmaz.
- Dış servis erişilebilirliğine bağlıdır (503 durumu); önbellek yalnızca daha önce sorgulanmış ilaçları korur.
- Etiket içeriği `reviewed = false` olarak eklenir; üretimde klinisyen onayı olmadan hasta karşısında kesin bilgi gibi sunulmamalıdır.

## 9. Türkçe ilaç kataloğu: TİTCK kaynağı

- **Kaynak:** TİTCK → [Ruhsatlı Beşeri Tıbbi Ürünler Listesi](https://www.titck.gov.tr/dinamikmodul/85), haftalık Excel (`RuhsatlBeeriTbbirnlerListesi<GG.AA.YYYY>_<uuid>.xlsx`, ~2,3 MB). Bu sürümde kullanılan liste: **23.10.2026** (23.120 ürün satırı). Ham dosya repoya konmaz (boyut/lisans); yalnızca süzülmüş, üretilmiş YAML commit edilir.
- **Sütunlar** (ilk sayfa "RUHSATLI ÜRÜNLER LİSTESİ"; 1. satır başlık metni, 2. satır sütun adları): `SIRA NO`, `BARKOD`, `ÜRÜN ADI`, `ETKİN MADDE`, `ATC KODU`, `RUHSAT SAHİBİ`, `RUHSAT TARİHİ`, `RUHSAT NUMARASI`, `DEĞİŞİKLİK`, `DEĞİŞİKLİK TARİHİ`, askı durumu (0: askıda değil; 1/2/3: askıda), `ASKIYA ALINMA TARİHİ`. Diğer sayfalar (geçici izinli alerjenler, radyofarmasötikler) okunmaz. Sütunlar başlık adına göre bulunur; sıra değişse de çalışır. ATC kodlarının bir kısmı sonunda bölünmez boşluk taşır; temizlenir.
- **Üretim:** `cd backend && python -m app.scripts.import_titck <indirilen .xlsx | URL> [--list-date GG.AA.YYYY] [--update-db] [--report]` (kod: `app/services/titck.py`). Adımlar:
  1. ATC filtresi: A10 (diyabet), B01 (antitrombotik), C02, C03, C07, C08, C09 (tansiyon/kalp), C10 (lipid); askıdaki ürünler hariç (`--include-suspended` ile dahil).
  2. Etken madde: Türkçe metin ("metformin hidroklorür", "valsartan and diuretics", "Sitagliptin fosfat monohidrat, Metformin hidroklorür") tuz/ester sözcükleri atılarak `SUBSTANCES` tablosundaki Türkçe/İngilizce adlarla en uzun eşleşmeyle eşlenir; listedeki yazım hataları ("rivoraksaban", "linaglipin") tabloda varyant olarak durur. Kısmen çözülen kombinasyonlar ATC kombinasyon kodundan tamamlanır (`ATC_FALLBACK`, ör. C09DA03 → valsartan + hidroklorotiyazid); insan insülini ve karışım insülinler ATC koduyla belirlenir. Hiç tanınmayan metin (yanlış ATC ile listelenmiş tatlandırıcılar vb.) atlanır ve raporlanır.
  3. Marka: ürün adındaki ilk rakam/yüzde içeren sözcükten ya da form sözcüğünden (TABLET, FİLM, KALEM, SOLOSTAR...) itibaren kısım atılır: "GLİFOR 1000 MG 100 FİLM KAPLI TABLET" → `Glifor`, "DIAMICRON MR 60 MG" → `Diamicron MR`, "HUMALOG MIX25" → `Humalog Mix`. Etken madde adıyla başlayan jenerik ürünler ("METFORMİN TEVA") marka listesine girmez (etken madde adıyla zaten bulunur).
  4. Sınıf: ATC kodundan en uzun ön ek eşleşmesiyle Türkçe sınıf adı (`ATC_CLASSES`); YAML'da `atc` ve ruhsatlı ürün/ambalaj sayısı (`products`) da tutulur.
  5. Kürate katman ([`drug_catalog_curated.yaml`](../../backend/app/data/drug_catalog_curated.yaml)): görünen ad, sınıf adı düzeltmesi, takma ad ve öncelikli marka yazımı. Buradaki her TR marka TİTCK'da ilgili etken maddenin ürünü olarak bulunmalıdır.
  6. `--update-db` üretilen YAML'ı `drug_catalog` tablosuna upsert eder; aksi halde bir sonraki `python -m app.seed` yükler.
- **Sonuç (23.10.2026 listesi):** ATC filtresinden 3.667 ürün geçti; 161'i askıda olduğu için atlandı; 3.476'sının etken maddesi çözüldü, 30'u çözülemedi (27 tatlandırıcı A10X altında, 1 boş etken madde, 1 omega-3 karışımı, 1 C09DA08 altında listelenmiş antihistaminik). Katalog: **230 etken madde/kombinasyon, 932 TR marka**. İki marka çakışması (Delix, Olmetec: TİTCK'da bazı tekli ürünler kombinasyon ATC koduyla listelenmiş) çoğunluk kuralıyla tekli etken maddeye bağlandı.
- **Çapraz kontrol** (`tests/test_titck.py`): kürate listedeki 98 TR markadan 15'i TİTCK listesinde bulunamadı (Glucophage, Minidiab, NovoNorm, Galvusmet, Kombiglyze XR, Jentadueto, Zanidip, Renitec, Zestril, Concor Plus, Pritor, Aprovel, Zocor, Atozet, Coumadin); bunlar `brands`'ten `aliases`'a (aranabilir uluslararası ad) taşındı. TİTCK'da ürünü olmayan iki kürate etken madde (`metformin + saxagliptin`, tekli `hydrochlorothiazide`) katalogda markasız kalır. Test ayrıca üretilmiş YAML'ın tutarlılığını (yinelenen etken madde/marka yok, ATC filtresi, ATC'li her kaydın sınıfı var, başlıkta kaynak + tarih + komut) ve temsili TİTCK örneğinden (`tests/fixtures/titck_sample.csv`, Excel'e de dönüştürülerek) üretimi doğrular; gerçek dosyaya bağımlı değildir.

## 10. openFDA katalog senkronu

`python -m app.scripts.sync_drug_catalog` (isteğe bağlı) Türkçe katalogu yükledikten sonra openFDA `count` uç noktasıyla (1) ilgili EPC sınıflarındaki etken maddeleri keşfeder, (2) her etken madde için ABD marka adlarını bağlar ve `openfda_available` bayrağını günceller. Gerçek API'ye karşı ölçüm ve sonuçlar: [`docs/openfda-senkron.md`](../openfda-senkron.md).

- **Geçici sunucu hataları:** openFDA zaman zaman `HTTP 500 search_phase_execution_exception` ve zaman aşımı veriyor (gerçek koşuda gözlendi). 5xx ve zaman aşımı iki kez tekrar edilir; yine başarısız olan tek sorgu atlanır (o etken maddenin satırı değişmez). Sorguların %20'sinden fazlası başarısızsa ya da 429/ağ hatasında senkron iptal edilir, veritabanı değişmez. İstemci zaman aşımı 45 sn (ağır `count` sorguları 20+ sn sürebiliyor).
- **Keşif gürültüsü:** EPC sınıfları homeopatik ürünler ("attention deficit mild drops" İnsülin analoğu sınıfında), çözücüler ("water", "diluent") ve OTC kombinasyonlar ("aspirin + caffeine + dextromethorphan") döndürüyor. Bunlar sözcük süzgeci ve "kombinasyonun her bileşeni katalogda ya da tekli olarak openFDA'da olmalı" kuralıyla elenir; FDA biyobenzer son eki (`insulin glargine-yfgn`) atılır.

## 11. İzleme

- **Fikstürler:** `backend/tests/fixtures/openfda_*.json` gerçek yanıtlardan kısaltılmış kesitlerdir (2026-10-26): DPP-4 sınıf keşfi, metformin ve metformin + sitagliptin marka sayımları, bir etiket ve 404 yanıtı. Ayrıştırma ve senkron testleri bunlarla koşar (`test_openfda_fixtures.py`).
- **Canlı kontrol:** `pytest -m openfda_live tests/test_openfda_live.py` openFDA'ya 4 küçük istek atar ve senkronun dayandığı alanları (`results[].term/count`, `openfda.generic_name`, `brand_name`, `indications_and_usage`, 404 biçimi) denetler. `openfda_live` işaretli testler varsayılan koşuda atlanır (`tests/conftest.py`); normal CI'da çalışmaz.
- **İş akışı:** [`.github/workflows/openfda-smoke.yml`](../../.github/workflows/openfda-smoke.yml) her pazartesi 06:00 UTC ve elle (`workflow_dispatch`) canlı kontrolü koşar. Depo sırrı `OPENFDA_API_KEY` tanımlıysa kullanılır. Kırılırsa önce openFDA değişiklik notlarına bakılır, ardından fikstürler yenilenir.
