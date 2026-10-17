# CLAUDE.md

Bu repo "İlaç Uyumu ve Yaşam Tarzı Koçu" projesidir. Proje tanımı (her oturumda bağlayıcı):

@docs/PROJE_TANIMI.md

## Çalışma kuralları

- Dil: dokümanlar, UI metinleri ve commit mesajları Türkçe; kod tanımlayıcıları İngilizce.
- Koç asla teşhis koymaz, doz önermez; acil belirti → 112 + hekime kritik uyarı. Guardrail değişikliği yapılırsa `backend/tests/guardrail_scenarios.yaml` güncellenir ve testler koşar.
- Rol bazlı erişim: hasta kendini, hekim atanmış hastalarını, bakım veren bağlı hastayı (salt okunur) görür; yetkisiz erişim 404.
- Yalnızca sentetik demo verisi.
- Backend: `cd backend && pytest -q && ruff check app tests scripts` (gerçek altyapı testleri için `REDIS_TEST_URL`, `TEST_DATABASE_URL`). Frontend: `cd frontend && npm run lint && npm run build`.
- Faz durumu README'deki "Durum" tablosunda tutulur; bir faz bitince orayı güncelle.
