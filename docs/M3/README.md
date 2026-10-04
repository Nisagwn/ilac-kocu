# M3 — Arayüz MVP: Teslim Özeti

**Proje:** Kronik Hastalar için İlaç Uyumu ve Yaşam Tarzı Koçu Platformu
**Hazırlayan:** Hayrunnisa Güven

**Spec M3 teslimi** (`docs/PROJE_TANIMI.md`): *"Arayüz MVP: Hasta günlük akışı ve hekim dashboard'u uçtan uca; prototip koç yanıtı."*

| Belge | İçerik |
|---|---|
| [`arayuz-tasarimi.md`](arayuz-tasarimi.md) | Rota haritası, bilgi mimarisi, mobil öncelikli kararlar, erişilebilirlik, hekim dashboard'unun klinik anlamı |
| [`guvenlik-ve-pwa.md`](guvenlik-ve-pwa.md) | Kimlik doğrulama mimarisi, güvenlik başlıkları, PWA / service worker, web push |
| [`prototip-koc.md`](prototip-koc.md) | Kural tabanlı koç, guardrail hattı, RAG kaynağı, M4'e geçiş |

## 1. Spec maddeleri → kanıt

| Spec maddesi | Durum | Kanıt |
|---|---|---|
| Hasta günlük akışı: bugünün dozları, tek dokunuşla "aldım / almadım" | Tamam | `/bugun` — `frontend/src/components/patient/today-screen.tsx`; ekran: [02](../gorseller/02-hasta-bugun.png) |
| Ölçüm girişi, grafik güncellenmesi, hedef dışı değer uyarısı | Tamam | `/olcum` — `measurement-screen.tsx`, `measurement-charts.tsx`; ekran: [03](../gorseller/03-hasta-olcum.png) |
| İlaç planı yönetimi + openFDA bilgi kartı | Tamam | `/ilaclar` — `medications-screen.tsx`, `drug-info.tsx`; ekran: [04](../gorseller/04-hasta-ilaclar.png) |
| Haftalık özet (hasta) | Tamam | `/ozet` — `summary-screen.tsx`; ekran: [05](../gorseller/05-hasta-ozet.png) |
| Prototip koç yanıtı (uyum verisine dayalı, kaynaklı) | Tamam | `/koc` — `coach-screen.tsx` + `backend/app/services/coach.py` (`RuleBasedCoach`); ekran: [06](../gorseller/06-hasta-koc.png); bkz. [`prototip-koc.md`](prototip-koc.md) |
| Acil belirti → 112 kartı ve hekime bayrak | Tamam | Koç ekranında kırmızı kart + "Hekiminize bildirildi"; hekim panelinde kritik uyarı ([09](../gorseller/09-hekim-uyarilar.png)) |
| Hekim dashboard'u: hasta listesi, uyum, riskli hasta önceliği | Tamam | `/hekim` — `patient-list.tsx`; ekran: [07](../gorseller/07-hekim-hasta-listesi.png) |
| Hekim: hasta detayı (30 gün eğri, doz takvimi, uyarılar, haftalık özet) | Tamam | `/hekim/hasta/[id]` — `patient-detail.tsx`; ekran: [08](../gorseller/08-hekim-hasta-detay.png) |
| Hekim: uyarıları görme ve kapatma | Tamam | `/hekim/uyarilar` — `alerts-board.tsx`; ekran: [09](../gorseller/09-hekim-uyarilar.png) |
| Bakım veren salt okunur görünüm | Tamam | `/yakin`, `/yakin/hasta/[id]`; ekran: [10](../gorseller/10-yakin-hastalar.png) |
| Mobil öncelikli PWA, ana ekrana eklenebilir | Tamam | `public/manifest.webmanifest`, `public/sw.js`, `public/offline.html` |
| Web push hatırlatma | Tamam | `backend/app/routers/push.py`, `services/reminders.py`, `services/push.py`, `push-toggle.tsx` |
| Rol bazlı erişim (arayüz katmanı) | Tamam | `src/middleware.ts` (yalnızca kullanıcı deneyimi); asıl yetki backend'de |
| API dokümantasyonu | Tamam | Swagger: [11](../gorseller/11-swagger.png) |

## 2. Ekran galerisi

Ekran görüntüleri çalışan uygulamadan, **sentetik demo verisiyle** alınmıştır. Hasta ekranları mobil (390 px genişlik), hekim ve yakın ekranları masaüstüdür.

### Giriş ve hasta (mobil)

<table>
<tr>
<td align="center"><img src="../gorseller/01-giris.png" width="260"><br><sub><b>01 — Giriş.</b> E-posta/şifre; demo hesap kısayolları (Hasta, Hekim, Yakın).</sub></td>
<td align="center"><img src="../gorseller/02-hasta-bugun.png" width="260"><br><sub><b>02 — Bugün.</b> 7 günlük uyum %55 (Düşük), bildirim kartı, saat saat doz kartları, Aldım / Almadım düğmeleri.</sub></td>
<td align="center"><img src="../gorseller/03-hasta-olcum.png" width="260"><br><sub><b>03 — Ölçüm.</b> 262 mg/dL için "Hedef aralık dışında" uyarısı; 30 günlük kan şekeri ve tansiyon grafikleri.</sub></td>
</tr>
<tr>
<td align="center"><img src="../gorseller/04-hasta-ilaclar.png" width="260"><br><sub><b>04 — İlaçlar.</b> İlaç planı, saat rozetleri; Bilgi (openFDA), Düzenle, Pasife al.</sub></td>
<td align="center"><img src="../gorseller/05-hasta-ozet.png" width="260"><br><sub><b>05 — Özet.</b> Koçun haftalık değerlendirmesi (akşam dozu örüntüsü dahil), uyum, uyarı sayısı, ölçüm istatistikleri.</sub></td>
<td align="center"><img src="../gorseller/06-hasta-koc.png" width="260"><br><sub><b>06 — Koç.</b> Kaynaklı uyum yanıtı; doz sorusunda ret; "göğsümde baskı var" için kırmızı 112 kartı.</sub></td>
</tr>
</table>

### Hekim ve bakım veren (masaüstü)

**07 — Hekim: hasta listesi.** Beş hasta, özet kutucukları; kritik uyarısı olanlar önce, sonra uyumu en düşükler.

![Hekim hasta listesi](../gorseller/07-hekim-hasta-listesi.png)

**08 — Hekim: hasta detayı.** Eğilim kutucukları, ilaç bazında uyum, 30 günlük doz takvimi, kan şekeri grafiği, uyarılar ve haftalık özet.

![Hekim hasta detayı](../gorseller/08-hekim-hasta-detay.png)

**09 — Hekim: açık uyarılar.** Önem derecesine göre gruplu (Kritik / Uyarı / Bilgi), tek tıkla "Okundu".

![Hekim uyarılar](../gorseller/09-hekim-uyarilar.png)

**10 — Bakım veren: Hastalarım.** "Salt okunur" rozeti; bağlı hastanın uyum ve uyarı özeti.

![Yakın hastalar](../gorseller/10-yakin-hastalar.png)

**11 — Swagger (FastAPI).** M2'de oluşturulan API'nin etkileşimli dokümantasyonu; arayüz bu uçları kullanır.

![Swagger](../gorseller/11-swagger.png)

## 3. Uçtan uca demo akışı

Spec'teki örnek senaryo, demo hesaplarla (`hasta@demo.local`, `hekim@demo.local`, `yakin@demo.local`; şifre `demo12345`):

| # | Adım | Ekran | Beklenen sonuç |
|---|---|---|---|
| 1 | Hasta olarak giriş yapılır ("Hasta" kısayolu) | `/giris` → `/bugun` ([01](../gorseller/01-giris.png)) | Middleware rolüne göre `/bugun`'e yönlendirir |
| 2 | Sabah metformin (Glifor 08:00) için **Aldım** | `/bugun` ([02](../gorseller/02-hasta-bugun.png)) | Kart yeşile döner ("Alındı"); sayaç "1/3 işaretlendi"; uyum kartı yenilenir |
| 3 | Açlık kan şekeri girilir (ör. 262) | `/olcum` ([03](../gorseller/03-hasta-olcum.png)) | Grafik güncellenir; "Hedef aralık dışında" uyarısı; hekim paneline uyarı düşer |
| 4 | Koça "Son günlerde akşam ilacımı unutuyorum, ne yapabilirim?" yazılır | `/koc` ([06](../gorseller/06-hasta-koc.png)) | Son 7 gündeki kaçırılan doz sayısı, kahvaltıya bağlama / alarm ipucu, kaynak bağlantıları; doz önerisi yok |
| 5 | "metformini ikiye çıkarabilir miyim?" | `/koc` ([06](../gorseller/06-hasta-koc.png)) | Sabit güvenli yanıt (doz kararı hekimindir) + "Hekiminize bildirildi" |
| 6 | "göğsümde baskı var" | `/koc` ([06](../gorseller/06-hasta-koc.png)) | Kırmızı "Acil durum olabilir" kartı, **112'yi ara** düğmesi; koç/LLM çağrılmaz; hekime kritik uyarı |
| 7 | Haftalık özet incelenir | `/ozet` ([05](../gorseller/05-hasta-ozet.png)) | Akşam saatlerinde kaçırılan dozlar vurgulanır |
| 8 | Çıkış yapılıp hekim olarak girilir ("Hekim") | `/hekim` ([07](../gorseller/07-hekim-hasta-listesi.png)) | Kritik uyarılı hastalar listenin başında |
| 9 | Ahmet Yılmaz'ın detayı açılır | `/hekim/hasta/[id]` ([08](../gorseller/08-hekim-hasta-detay.png)) | Eğilim kutucukları, doz takvimi, glukoz grafiği ve kırmızı hedef dışı nokta, haftalık özet |
| 10 | Uyarılar sayfasında kritik uyarı **Okundu** yapılır | `/hekim/uyarilar` ([09](../gorseller/09-hekim-uyarilar.png)) | Uyarı açık listeden kalkar |
| 11 | Bakım veren olarak girilir ("Yakın") | `/yakin` ([10](../gorseller/10-yakin-hastalar.png)) | Bağlı hasta salt okunur görünür; değişiklik düğmesi yok |

## 4. Nasıl çalıştırılır

**Docker (önerilen)** — depo kökünde:

```bash
docker compose up --build                     # PostgreSQL + pgvector, Redis, API (:8000), web (:3000)
docker compose exec api python -m app.seed    # sentetik demo verisi
```

Web: <http://localhost:3000> · Swagger: <http://localhost:8000/docs>

**Geliştirme modu (frontend)**

```bash
cd frontend
cp .env.example .env.local     # API_URL=http://localhost:8000
npm ci
npm run dev                    # http://localhost:3000
```

Web push için backend `.env` dosyasında `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` tanımlanır (`npx web-push generate-vapid-keys`). Tanımlı değilse arayüz "Bildirim servisi sunucuda henüz yapılandırılmamış" mesajı gösterir (bkz. [02](../gorseller/02-hasta-bugun.png)); diğer her şey çalışır.

## 5. Test ve derleme durumu

| Kontrol | Komut | Sonuç |
|---|---|---|
| Frontend tip denetimi | `npx tsc --noEmit` | Geçti |
| Frontend lint | `npm run lint` (eslint) | Geçti |
| Frontend derleme | `npm run build` (`output: "standalone"`) | Geçti |
| Backend testleri | `pytest` | 395 test geçti |
| CI | GitHub Actions: backend ve frontend işleri paralel, ardından Docker imaj derlemesi | `.github/workflows/ci.yml` |

## 6. Bilinen sınırlar (M3 kapsamı)

- Koç M3'te kural tabanlıdır; serbest dilde yanıt ve tool-use M4 kapsamındadır ([`prototip-koc.md`](prototip-koc.md)).
- Frontend için otomatik arayüz testi (Playwright vb.) yoktur; arayüz tip denetimi, lint, derleme ve elle uçtan uca senaryo ile doğrulanmıştır.
- Web push gerçek bir cihazda HTTPS (veya `localhost`) ve VAPID anahtarı gerektirir.
