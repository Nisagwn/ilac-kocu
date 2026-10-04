# Kronik Hastalar için İlaç Uyumu ve Yaşam Tarzı Koçu Platformu

Tip 2 diyabet ve hipertansiyon hastalarının ilaç/ölçüm takibini yaptığı, LLM koçun kişiselleştirilmiş ve güvenli geri bildirim verdiği, hekim için uyum dashboard'u sunan sağlık platformu.

## Problem ve Hedef

Kronik hastalıklarda tedavi başarısının en büyük engeli ilaç uyumsuzluğu: hastaların yaklaşık yarısı ilaçlarını düzenli kullanmıyor, ölçümlerini kaydetmiyor ve kontrol aralarında hekimle temas kurmuyor. Mevcut uygulamalar hatırlatıcıdan öteye geçmiyor; hastanın verisini yorumlayıp motive eden ve hekime anlamlı özet sunan bir katman eksik.

Hedef: Hastanın ilaç planını, kan şekeri/tansiyon ölçümlerini ve günlük notlarını girdiği; LLM tabanlı koçun bu veriye dayanarak tıbbi tavsiye vermeden motivasyonel geri bildirim, eğitim içeriği ve "hekime danışın" tetikleyicileri ürettiği; hekimin hastalarının uyum skorunu ve risk sinyallerini görebildiği bir platform. Güvenlik katmanı (guardrail) ve kaynaklı yanıt zorunludur.

Örnek kullanım senaryosu:

- Hasta sabah metformin dozunu "aldım" olarak işaretler, açlık kan şekerini girer; grafik güncellenir.
- Koç, son 7 günde 3 atlanmış doz görür ve motive edici bir mesajla birlikte "ilaç saatini kahvaltıya bağlama" ipucu verir; doz değişikliği önermez.
- Hasta "göğsümde baskı var" yazar; guardrail acil yönlendirme (112) üretir ve hekime risk bayrağı düşer.

**Kapsam dışı:** Teşhis, ilaç dozu önerisi, cihaz (glukometre) entegrasyonu ve e-Nabız bağlantısı kapsam dışıdır.

## Hedef Kullanıcı

- Tip 2 diyabet / hipertansiyon hastaları (mobil web)
- Aile hekimleri ve diyabet hemşireleri (hasta listesi, uyum dashboard'u)
- Hasta yakınları (bakım veren rolü, salt okunur)

## Zorunlu Bileşenler

1. **Arayüz + Dashboard (Next.js PWA):** İlaç planı ve günlük "aldım/almadım" ekranı, ölçüm girişi ve grafikler, koç sohbet arayüzü, haftalık özet; hekim dashboard'u: hasta listesi, uyum skoru, son 30 gün ölçüm eğrileri, risk bayrakları.
2. **Backend REST API (FastAPI):** /patients, /medications, /doses, /measurements, /coach/chat, /summaries, /alerts; hatırlatma zamanlayıcısı (web push); JWT auth (hasta/hekim/bakım veren rolleri); OpenAPI.
3. **Veritabanı + Migration (PostgreSQL + Alembic):** users, patients, clinicians, medications, medication_schedules, dose_logs, measurements, coach_messages, alerts, education_contents. ER diyagramı.
4. **Yapay Zekâ Bileşeni:** Agentic LLM koç: tool-use ile hastanın son 7/30 gün verisini sorgular, uyum skorunu hesaplar, structured output ile message, risk_level, escalate_to_clinician, sources döner; RAG ile kürate edilmiş hasta eğitim içeriğinden kaynak gösterir; guardrail kuralları (doz değişikliği önermeme, acil belirti → 112 yönlendirmesi); hekim için haftalık özet üretimi.
5. **Harici API Entegrasyonu:** openFDA Drug Label API ile ilaç adından etken madde, kullanım uyarıları ve etkileşim bilgisinin çekilip eğitim içeriğine eklenmesi; web push için Web Push Protocol (VAPID).
6. **GitHub Repo + README + CI:** Monorepo, Docker Compose, GitHub Actions (pytest + guardrail test seti + build), README'de ER diyagramı, güvenlik ve veri gizliliği bölümü.

## Önerilen Teknoloji Yığını

| Katman | Teknoloji |
|---|---|
| Frontend | Next.js (PWA), TypeScript, Recharts, shadcn/ui |
| Backend | FastAPI, SQLAlchemy, Alembic, APScheduler, pywebpush |
| LLM | Claude/OpenAI API (tool use + structured output), pgvector (RAG) |
| Altyapı | PostgreSQL, Redis, Docker, GitHub Actions |

## Ara Teslimler (M1–M5)

- **M1 — Öneri + Mimari:** Kullanıcı akışları, guardrail politikası taslağı (neyi asla yapmaz), veri gizliliği yaklaşımı, mimari diyagram.
- **M2 — Veritabanı + Backend:** Şema + migration, ilaç/doz/ölçüm CRUD, openFDA'dan ilaç etiketi çeken uç nokta, Swagger.
- **M3 — Arayüz MVP:** Hasta günlük akışı ve hekim dashboard'u uçtan uca; prototip koç yanıtı.
- **M4 — Yapay Zekâ Bileşeni:** Tool-use koç üretim kalitesinde, 40 senaryoluk guardrail test seti (red-team: doz sorusu, acil belirti), uyum skoru doğrulaması, maliyet limiti.
- **M5 — Final Rapor + Demo:** Rapor (etik/güvenlik değerlendirmesi, sınırlılıklar), demo videosu, kurulum talimatı.

## Değerlendirme Odak Noktaları

- Guardrail'lerin test edilmiş ve belgelenmiş olması
- Rol bazlı yetkilendirme ve hasta verisi gizliliği
- Koç yanıtlarının veriye dayalı ve kaynaklı olması
- Hekim dashboard'unun klinik olarak anlamlı özet sunması
- Kod kalitesi, test ve CI

## Başlangıç Kaynakları

- openFDA Drug Label API: https://open.fda.gov/apis/drug/label/
- Claude tool use dokümantasyonu: https://docs.claude.com/en/docs/agents-and-tools/tool-use/overview
- Web Push (pywebpush): https://github.com/web-push-libs/pywebpush
- WHO — Adherence to long-term therapies (rapor): https://iris.who.int/handle/10665/42682
- pgvector: https://github.com/pgvector/pgvector
