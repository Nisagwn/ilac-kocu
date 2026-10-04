# M1 — Proje Önerisi ve Teknoloji Seçimi

**Proje:** Kronik Hastalar için İlaç Uyumu ve Yaşam Tarzı Koçu Platformu
**Hazırlayan:** Hayrunnisa Güven
**Teslim:** 23 Ekim 2026

## 1. Problem

Kronik hastalıklarda tedavi başarısının en büyük engeli ilaç uyumsuzluğudur. DSÖ'nün uzun süreli tedavilere uyum raporuna göre gelişmiş ülkelerde bile hastaların yaklaşık yarısı tedavilerine düzenli uymamaktadır. Tip 2 diyabet ve hipertansiyon gibi belirtisi az ama sonuçları ağır hastalıklarda bu oran daha da kritik hale gelir.

Mevcut uygulamalar büyük ölçüde hatırlatıcıdan ibarettir. Eksik olan katman:

- Hastanın kendi verisini **yorumlayan** ve onu **motive eden** bir geri bildirim,
- Hekime kontrol aralarında **anlamlı, özetlenmiş** bir uyum ve risk görünümü.

## 2. Hedef

Hastanın ilaç planını, kan şekeri/tansiyon ölçümlerini ve günlük notlarını girdiği; LLM tabanlı koçun bu veriye dayanarak **tıbbi tavsiye vermeden** motivasyonel geri bildirim, eğitim içeriği ve "hekime danışın" tetikleyicileri ürettiği; hekimin hastalarının uyum skorunu ve risk sinyallerini görebildiği bir platform.

### Başarı ölçütleri

| Ölçüt | Hedef |
|---|---|
| Guardrail test seti başarısı | 40/40 senaryo geçer (CI'da zorunlu) |
| Acil belirti → 112 yönlendirmesi | %100, LLM çağrısından bağımsız |
| Koç yanıtlarında kaynak | Eğitim içeriği kullanılan her yanıtta en az 1 kaynak |
| Uyum skoru doğruluğu | Elle hesaplanmış örneklerle birebir (birim testleri) |
| Rol bazlı erişim | Yetkisiz erişim denemelerinin tümü 403/404 döner (testli) |

## 3. Kapsam

**Kapsam içi**
- İlaç planı, zamanlanmış dozlar, "aldım / almadım" kaydı
- Açlık/tokluk kan şekeri ve tansiyon ölçümleri, grafikler
- LLM koç (tool-use + structured output + RAG + guardrail)
- Hekim dashboard'u: hasta listesi, uyum skoru, 30 günlük eğriler, risk bayrakları, haftalık özet
- Bakım veren (hasta yakını) için salt okunur erişim
- openFDA ilaç etiketi entegrasyonu, web push hatırlatmaları

**Kapsam dışı**
- Teşhis koyma, ilaç dozu önerme veya değiştirme
- Glukometre / tansiyon aleti gibi cihaz entegrasyonları
- e-Nabız bağlantısı
- Native mobil uygulama (platform mobil öncelikli PWA olarak geliştirilir)

## 4. Hedef kullanıcılar ve roller

| Rol | Cihaz | Yetki |
|---|---|---|
| Hasta | Telefon (PWA) | Kendi ilaç, doz, ölçüm ve koç verisini okur/yazar |
| Hekim / diyabet hemşiresi | Masaüstü | Kendine atanmış hastaların verisini okur, uyarıları kapatır |
| Bakım veren | Telefon (PWA) | Bağlı olduğu hastanın verisini **salt okunur** görür |

## 5. Teknoloji seçimi ve gerekçeleri

| Katman | Seçim | Gerekçe |
|---|---|---|
| Frontend | Next.js (PWA), TypeScript, shadcn/ui, Recharts | Tek kod tabanıyla mobil (hasta) ve masaüstü (hekim) arayüzü; PWA ile ana ekrana ekleme ve web push; Recharts ölçüm grafikleri için yeterli |
| Backend | FastAPI, SQLAlchemy 2, Pydantic | Otomatik OpenAPI/Swagger; LLM ve RAG kütüphaneleriyle aynı dil (Python); tip güvenli şemalar |
| Veritabanı | PostgreSQL 16 + pgvector, Alembic | İlişkisel klinik veri ve vektör araması tek veritabanında; Alembic ile sürümlü şema |
| Zamanlayıcı | APScheduler + pywebpush (VAPID) | Hatırlatmalar için ayrı bir kuyruk sistemine gerek yok; proje ölçeğinde yeterli |
| Önbellek | Redis | openFDA yanıtlarının önbelleklenmesi, LLM için hasta başına oran/maliyet sınırı |
| LLM | Claude API (tool use + structured output) | Araç çağrısı ve şemaya uyan çıktı yerleşik; sağlayıcı soyutlama katmanı ile OpenAI'ye geçilebilir |
| Altyapı | Docker Compose, GitHub Actions | Tek komutla kurulum; her push'ta test + guardrail seti + build |

## 6. Riskler ve önlemler

| Risk | Önlem |
|---|---|
| LLM'in doz önermesi veya teşhis koyması | 3 katmanlı guardrail (bkz. `02-guardrail-politikasi.md`), çıktı doğrulayıcı, CI'da red-team testi |
| Acil belirtinin gözden kaçması | Acil tespiti LLM'den **önce**, deterministik kurallarla yapılır |
| Hasta verisinin sızması | Rol bazlı yetki, LLM'e kimlik bilgisi gönderilmemesi (bkz. `03-veri-gizliligi.md`) |
| openFDA'nın ABD kaynaklı olması | Türk ticari isimleri için etken madde eşleme tablosu; raporda sınırlılık olarak belirtilir |
| LLM maliyetinin kontrolden çıkması | Hasta başına günlük mesaj ve token limiti (Redis sayaç) |

## 7. Ara teslim planı

| Teslim | Tarih | Çıktı |
|---|---|---|
| M1 | 23 Eki 2026 | Bu belge seti: öneri, akışlar, guardrail politikası, gizlilik, mimari ve ER diyagramı |
| M2 | 13 Kas 2026 | Şema + migration, JWT rolleri, ilaç/doz/ölçüm CRUD, openFDA uç noktası, uyum skoru, Swagger, CI |
| M3 | 11 Ara 2026 | Hasta günlük akışı ve hekim dashboard'u uçtan uca, PWA + web push, prototip koç |
| M4 | 1 Oca 2027 | Tool-use koç, RAG, 40 senaryoluk guardrail seti, maliyet limiti, haftalık özet |
| M5 | 17 Oca 2027 | Final rapor (etik/güvenlik, sınırlılıklar), demo videosu, kurulum talimatı |

## 8. Gerçekleşen sistem

M1'de önerilen platform M5 itibarıyla çalışır durumdadır. Hasta ekranları mobil, hekim ekranı masaüstü içindir (sentetik demo verisi).

<table>
<tr>
<td align="center"><img src="../gorseller/02-hasta-bugun.png" width="260" alt="Hasta: Bugün"><br><sub>Hasta: Bugün</sub></td>
<td align="center"><img src="../gorseller/06-hasta-koc.png" width="260" alt="Hasta: Koç"><br><sub>Hasta: Koç</sub></td>
</tr>
</table>

**Hekim paneli: hasta listesi (masaüstü)**

![Hekim hasta listesi](../gorseller/07-hekim-hasta-listesi.png)
