# Frontend (M3 — 11 Aralık 2026)

Next.js (App Router) + TypeScript + shadcn/ui + Recharts, PWA.

## Planlanan ekranlar

**Hasta (mobil öncelikli)**
- `/bugun` — günün dozları, tek dokunuşla "aldım / almadım" → `GET /patients/{id}/doses/today`, `PUT /patients/{id}/doses`
- `/olcum` — kan şekeri / tansiyon girişi ve 30 günlük grafik → `/patients/{id}/measurements`
- `/koc` — sohbet; acil yanıtlar kırmızı kartla, kaynaklar yanıtın altında → `/coach/chat`
- `/ilaclar` — ilaç planı yönetimi, openFDA bilgi kartı → `/patients/{id}/medications`, `/drugs/label`
- `/ozet` — haftalık özet → `/patients/{id}/summary`

**Hekim (masaüstü)**
- `/hekim` — hasta tablosu: uyum bandı (iyi/orta/düşük), kritik uyarı sayısı → `GET /patients`
- `/hekim/hasta/[id]` — 30 gün ölçüm eğrileri, doz takvimi ısı haritası, uyarılar, haftalık özet
- `/hekim/uyarilar` — açık uyarılar, onaylama → `GET /alerts`, `POST /alerts/{id}/ack`

**Bakım veren** — hasta ekranlarının salt okunur hali.

## PWA
- `manifest.json`, service worker, ana ekrana ekleme
- Web push: backend VAPID anahtarlarıyla (`pywebpush`); iOS'ta yalnızca ana ekrana eklenmiş PWA'da çalışır (iOS 16.4+)
