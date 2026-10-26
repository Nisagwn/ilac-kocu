# openFDA katalog senkronu: gerçek API ölçümü

**Tarih:** 2026-10-26 · **Hedef:** `https://api.fda.gov/drug/label.json` (openFDA verisi `last_updated: 2026-10-23`) · **API anahtarı:** yok (anahtarsız sınır)
**Veritabanı:** geçici SQLite dosyası (kullanıcının demo veritabanı kullanılmadı) · **Türkçe katalog:** TİTCK 23.10.2026 listesinden üretilmiş `drug_catalog_tr.yaml` (230 etken madde/kombinasyon, 932 TR marka)

Senkron kodu: `backend/app/services/drug_catalog.py` (`sync_from_openfda`), CLI: `cd backend && python -m app.scripts.sync_drug_catalog`. Ölçüm, aynı fonksiyonu boş bir SQLite veritabanında (`Base.metadata.create_all`) varsayılan ayarlarla (`sleep=0.3`, `max_generics=500`, marka sorguları açık) çalıştırıp her HTTP yanıtını kaydederek yapıldı. Yöntem ve sonraki tekrarlar için: §4.

## 1. Son başarılı koşu (koşu 6)

| Ölçüt | Değer |
|---|---|
| Süre | **182 sn** (istekler arası 0,3 sn bekleme dahil; yanıt süreleri toplamı 94 sn) |
| İstek | **292** (30 EPC sınıf keşfi + 262 etken madde marka sorgusu) |
| Yanıt durumları | 215 × HTTP 200, 77 × HTTP 404 ("No matches found!"), 0 × 429, 0 × 5xx |
| Tekrar / atlanan sorgu | 0 / 0 |
| Yanıt süresi | medyan 0,30 sn · p95 0,43 sn · en uzun 1,1 sn |
| Oran sınırı | 429 yok; yanıtlarda `X-RateLimit-*` başlığı dönmedi. Anahtarsız günlük sınır 1.000 istek olduğundan tam senkron günde en çok 3 kez çalıştırılabilir |
| Sınıflardan keşfedilen etken madde | 187 aday → gürültü süzgecinden sonra **127** (60 elendi) |
| Katalog satırı | 262 = 230 TR (TİTCK) + **32 yeni openFDA** etken maddesi |

### Türkçe katalogla eşleşme

| | Toplam | openFDA'da etiketi olan | Oran |
|---|---|---|---|
| TR etken madde / kombinasyon | 230 | **153** | %67 |
| — tekli etken madde | 146 | 116 | %79 |
| — kombinasyon | 84 | 37 | %44 |
| TR marka (etken maddesi openFDA'da olan) | 932 | **764** | %82 |

- **ABD marka adı bağlanan TR satırı:** 84; toplam **251** ABD marka adı eklendi (ör. `insulin glargine` → Lantus Solostar, Toujeo Max, Basaglar KwikPen, Semglee, Rezvoglar Kwikpen; `metformin + sitagliptin` → Janumet XR, Zituvimet, Zituvimet XR; `amlodipine` → Norliqva, Katerzia).
- **openFDA'da etiketi olmayan 77 TR satırı** (`openfda_available=false`): ABD'de ruhsatı olmayan ya da openFDA'da farklı adla geçen etken maddeler. Tekliler: gliclazide, vildagliptin, chlorpropamide, glibornuride, voglibose, insulin isophane, lercanidipine, lacidipine, manidipine, benidipine, nitrendipine, lidoflazine, cilazapril, imidapril, zofenopril, eprosartan, moxonidine, rilmenidine, urapidil, finerenone, s-metoprolol, ticlopidine, sulodexide, bemiparin, abciximab, reteplase, antithrombin iii, protein c, evolocumab, pyridinol carbamate. Geri kalan 47'si Avrupa/Türkiye'ye özgü sabit doz kombinasyonları (ör. amlodipine + perindopril, indapamide + perindopril, ezetimibe + rosuvastatin). Bu ilaçlar için etiket uç noktası 404 döner; katalog ve arama etkilenmez.
- **Yeni openFDA satırları (32):** bumetanide, clonidine, nadolol, pindolol, isradipine, nisoldipine, clevidipine, metolazone, chlorothiazide, chlorthalidone (tekli), triamterene, ethacrynic acid, alirocumab, bexagliflozin, sotagliflozin, sparsentan, silodosin, insulin, human insulin ile ABD kombinasyonları (glipizide + metformin, glimepiride + pioglitazone, dapagliflozin + saxagliptin, empagliflozin + linagliptin + metformin, captopril + hydrochlorothiazide, hydrochlorothiazide + metoprolol, amlodipine + indapamide + telmisartan, aspirin + dipyridamole). openFDA'nın kendi yazım hataları da gelir: `aspifin`, `clevipidine`, `nebibolol`, `acetylsalicyclic acid`, `carospir` (marka adı generic alanında).

## 2. Karşılaşılan hatalar ve yapılan düzeltmeler

Aynı gün altı koşu yapıldı; dördü hata verdi ya da kalite sorunu gösterdi, her biri kodda düzeltilip test eklendi.

| Koşu | Sonuç | Bulgu | Düzeltme (commit) |
|---|---|---|---|
| 1 (eski kod) | ❌ 20. istekte iptal, 67 sn | `Potassium-sparing Diuretic [EPC]` sorgusu **HTTP 500** `search_phase_execution_exception` (25 sn sonra). Aynı sorgu hemen tekrarlanınca 200 döndü: geçici sunucu hatası. Eski kod 5xx'te tüm senkronu iptal ediyordu | 5xx iki kez tekrar edilir; yine başarısızsa yalnızca o sorgu atlanır (`failed_queries`), satırı değişmez; %20'den fazla başarısızlıkta iptal |
| 2 | ❌ 56. istekte iptal, 134 sn | **ReadTimeout** (istemci zaman aşımı 20–30 sn); ağır `count` sorguları 8–25 sn sürebiliyor | Zaman aşımı da geçici sayılır (tekrar + atla); varsayılan istemci zaman aşımı 45 sn |
| 3 (eski 83 satırlık kürate liste) | ✅ 417 sn, 251 istek | 138 yeni satırın çoğu gürültü: homeopatik ürünler (`attention deficit mild drops` "Insulin Analog" sınıfında), `water`, `diluent`, `homeopathic liquid`, OTC aspirin kombinasyonları (`aspirin + caffeine + dextromethorphan + ...`), ayırıcısız yazımlar (`acetaminophen aspirin caffeine`) | Keşif süzgeci (`_filter_discovered`): gürültü sözcükleri; kombinasyonun her bileşeni katalogda ya da tekli olarak openFDA'da olmalı; FDA biyobenzer son eki (`-yfgn`) atılır |
| 4 (TİTCK kataloğu) | ✅ 368 sn, 292 istek | Metformin'in ABD markaları arasında `Glipizide and Metformin Hydrochloride`, `Metformin Hydrochloride Extended-Release 500mg` gibi jenerik adlar; TİTCK kataloğuyla hedef etken madde sayısı (357) eski 300 sınırını aşıyordu | Jenerik kombinasyon adları ve bitişik doz (`500mg`) marka sayılmaz; `max_generics` 500 |
| 5 | ❌ ~30 istekte iptal, 151 sn | **RemoteProtocolError** "peer closed connection without sending complete message body (incomplete chunked read)": openFDA bağlantıyı yanıt ortasında kapattı | Yarıda kopan yanıt (`RemoteProtocolError`, `ReadError`) da geçici sayılır |
| 6 | ✅ 182 sn, 292 istek | Hata yok (§1) | — |

Gözlem: openFDA yanıt süresi gün içinde çok değişti (koşu 3'te medyan 1,1 sn, en uzun 8 sn; koşu 6'da medyan 0,3 sn). Altı koşunun hiçbirinde 429 görülmedi; toplam ~950 istekle günlük anahtarsız sınıra (1.000) yaklaşıldı.

## 3. Kalan sınırlılıklar

- **Kataloğumuzda olmayan ABD kombinasyonlarının markaları tekli etken maddeye düşer:** metformin satırında `Invokamet` (canagliflozin + metformin), `Kazano` (alogliptin + metformin), `Segluromet` (ertugliflozin + metformin) görünür; bu kombinasyonlar Türkiye'de ruhsatlı değildir ve openFDA keşfinde DPP-4/SGLT2 sınıflarından ayrıca gelmez.
- **openFDA yazım hataları** (`aspifin`, `nebibolol`, `clevipidine`) yeni satır olarak eklenebilir; arama ve etiket uç noktasını bozmaz ama listeyi kirletir.
- **EPC sınıfı boş etiketler:** openFDA'da birçok tekli etiketin `pharm_class_epc` alanı boş (ör. metformin); bu yüzden TR katalogdaki etken maddeler sınıf keşfinden bağımsız olarak doğrudan sorgulanır.
- **`openfda_available=false` "ilaç yok" demek değildir:** yalnızca ABD etiketinin olmadığını gösterir (gliklazid, vildagliptin vb. Türkiye'de yaygın kullanılır).
- Senkron tek işlemdir: başarısız bir koşu veritabanını değiştirmez, ama kısmi ilerleme de korunmaz; yeniden başlatmak gerekir.

## 4. Tekrar ve izleme

```bash
cd backend
# Ayrı bir veritabanında (demo veritabanına değil):
DATABASE_URL=sqlite:///./openfda-senkron.db python -c "from app.db import Base, engine; Base.metadata.create_all(engine)"
DATABASE_URL=sqlite:///./openfda-senkron.db python -m app.scripts.sync_drug_catalog
```

CLI özet çıktısı istek sayısını, keşfedilen/elenen etken maddeyi, eklenen/güncellenen satırı, tekrar ve atlanan sorgu sayısını yazar; ağ hatası, 429 ya da çok sayıda başarısız sorguda 2 koduyla çıkar ve veritabanı değişmez.

- Gerçek yanıt kesitleri test fikstürü olarak saklandı: `backend/tests/fixtures/openfda_*.json` (DPP-4 sınıf keşfi, metformin ve metformin + sitagliptin marka sayımları, bir etiket, 404). Ayrıştırma ve senkron testleri bunlarla koşar (`tests/test_openfda_fixtures.py`).
- Haftalık canlı alan kontrolü: [`.github/workflows/openfda-smoke.yml`](../.github/workflows/openfda-smoke.yml) → `pytest -m openfda_live tests/test_openfda_live.py` (4 istek). Normal CI'da atlanır.
