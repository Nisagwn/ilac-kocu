# Arayüz Tasarımı

Arayüz tek bir Next.js 15 (App Router) uygulamasıdır: hasta ve bakım veren için **mobil öncelikli**, hekim için **masaüstü öncelikli**. Bileşenler Tailwind v4 + shadcn/ui, grafikler Recharts ile yazılmıştır. Tüm metinler Türkçedir.

## 1. Rota haritası

Tüm API çağrıları tarayıcıdan `/api/proxy/<yol>` üzerinden FastAPI'ye gider (bkz. [`guvenlik-ve-pwa.md`](guvenlik-ve-pwa.md)); tabloda yazılan yollar FastAPI yollarıdır.

| Yol | Rol | Ana bileşen | Kullandığı API uçları |
|---|---|---|---|
| `/giris` | Herkes | `app/giris/page.tsx` | `POST /api/auth/login` (Next route handler → FastAPI `POST /auth/login`) |
| `/bugun` | Hasta | `patient/today-screen.tsx`, `push-toggle.tsx` | `GET /patients/{id}/doses/today`, `PUT /patients/{id}/doses`, `GET /patients/{id}/adherence`, `GET/POST/DELETE /push/*` |
| `/olcum` | Hasta | `patient/measurement-screen.tsx`, `measurement-charts.tsx` | `GET/POST /patients/{id}/measurements`, `DELETE /patients/{id}/measurements/{mid}` |
| `/koc` | Hasta | `patient/coach-screen.tsx` | `POST /coach/chat`, `GET /coach/history` |
| `/ilaclar` | Hasta | `patient/medications-screen.tsx`, `medication-form.tsx`, `drug-info.tsx` | `GET/POST /patients/{id}/medications`, `PATCH/DELETE .../{med_id}`, `GET /drugs/label` |
| `/ozet` | Hasta | `patient/summary-screen.tsx` | `GET /patients/{id}/summary`, `GET /patients/{id}/adherence` |
| `/hekim` | Hekim | `clinician/patient-list.tsx` | `GET /patients` |
| `/hekim/hasta/[id]` | Hekim | `clinician/patient-detail.tsx`, `dose-calendar.tsx`, `measurement-charts.tsx` | `GET /patients/{id}`, `/adherence`, `/doses`, `/measurements`, `/medications`, `/alerts`, `/summary`; `POST /alerts/{id}/ack` |
| `/hekim/uyarilar` | Hekim | `clinician/alerts-board.tsx`, `alert-item.tsx` | `GET /alerts`, `POST /alerts/{id}/ack` |
| `/yakin` | Bakım veren | `clinician/caregiver-patient-list.tsx` | `GET /patients` |
| `/yakin/hasta/[id]` | Bakım veren | `clinician/patient-detail.tsx` (`readOnly`) | Hekim detayıyla aynı **okuma** uçları; yazma uçları çağrılmaz |

Ortak parçalar: `components/app-shell.tsx` (sayfa çerçevesi), `lib/api.ts` (tipli istemci), `hooks/use-me.ts` (oturumdaki kullanıcı), `states.tsx` (yükleniyor / hata / boş durum), `use-async.ts` (veri çekme).

## 2. Bilgi mimarisi

```
/giris ── rol çerezine göre ──┬─ Hasta        /bugun  /olcum  /koc  /ilaclar  /ozet   (alt sekme çubuğu)
                              ├─ Hekim        /hekim ─ /hekim/hasta/[id]  ·  /hekim/uyarilar   (üst menü)
                              └─ Bakım veren  /yakin ─ /yakin/hasta/[id]               (salt okunur)
```

- **Hasta** akışı kullanım sıklığına göre sıralanır: *Bugün* (her gün, her doz) → *Ölçüm* → *Koç* → *İlaçlar* (nadiren değişir) → *Özet*. "Bugün" ekranının altındaki "Ölçüm gir" kısayolu en sık ikinci işlemi tek dokunuşa indirir.
- **Hekim** için iki soru vardır: "Önce kime bakmalıyım?" (`/hekim`) ve "Bu hastada ne oluyor?" (`/hekim/hasta/[id]`). Uyarılar sayfası hastalar arası çapraz görünümdür; her uyarıdan hasta detayına gidilir.
- **Bakım veren** hekim detay bileşenini `readOnly` olarak yeniden kullanır.
- Her rolün kendi `layout.tsx` dosyası kendi gezinme listesini `AppShell`'e verir; rol dışı alana gidilirse middleware kullanıcıyı kendi ana sayfasına döndürür.

## 3. Mobil öncelikli tasarım kararları

| Karar | Gerekçe ve uygulama |
|---|---|
| **Büyük dokunma hedefleri** | **Aldım / Almadım** düğmeleri `h-12`, iki eşit sütun; "Ölçüm gir" satırı da `h-12`. Günlük ilaç işaretleme tek elle, hedefi kaçırmadan yapılabilmeli ([02](../gorseller/02-hasta-bugun.png)). |
| **Alt sekme çubuğu** | `md` altı genişlikte gezinme ekranın altında sabit (`fixed inset-x-0 bottom-0`), başparmak erişiminde; ikon ve metin etiketi birlikte. `md` ve üstünde aynı liste üst menüye taşınır. Aktif öğe en uzun eşleşen yola göre belirlenir (`/hekim/uyarilar`'da yalnızca "Uyarılar" yanar). İçerik çubuğun arkasında kalmasın diye sayfaya `pb-20` verilir. |
| **İyimser güncelleme** | "Aldım/Almadım"a basınca kart anında güncellenir (yerel durum), istek arka planda `PUT /patients/{id}/doses` ile gider; hata olursa önceki durum geri yüklenir (`today-screen.tsx`). Zayıf mobil bağlantıda arayüz takılmış hissettirmez; kayıt sonrası uyum kartı yenilenir. |
| **Tek sütun, kısa formlar** | Ölçüm formu tek sütundur; ölçüm türüne göre birim ve alanlar değişir (tansiyonda iki değer). Seçici, mobilde yerel seçiciyi açar (`native-select.tsx`). |
| **Acil durum bilgisi her zaman görünür** | Koç ekranının üstünde sabit uyarı: "Koç teşhis koymaz ve ilaç dozu önermez. Acil durumda 112'yi arayın." Acil yanıtta tam genişlik `tel:112` düğmesi; çevrimdışı sayfada da 112 hatırlatması var. |
| **Ölçüm girişinde anında geri bildirim** | Hedef dışı değer kaydedilince sayfada sarı kutu ile bildirilir ve hekime uyarı düştüğü söylenir; hasta değerle yalnız bırakılmaz ([03](../gorseller/03-hasta-olcum.png)). |
| **Masaüstünde yoğun görünüm** | Hekim ekranları `max-w-6xl` kapsayıcıda, tablo ve çok sütunlu ızgaralarla bilgi yoğunluğunu artırır; aynı bileşenler dar ekranda tek sütuna iner. |
| **Yükleniyor / hata / boş durumlar** | Her veri bloğu için iskelet (skeleton), `aria-live` ile okunan hata mesajı ve boş durum bileşeni; 404 yanıtı "Kayıt bulunamadı veya bu veriye erişim yetkiniz yok" olarak gösterilir. |

## 4. Erişilebilirlik

Ana ilke: **hiçbir durum yalnızca renkle iletilmez.** Renk körlüğü, parlak ışıkta kullanım ve ekran okuyucu için her renk bir metin veya glifle eşlenir.

| Öğe | Renk dışı gösterim |
|---|---|
| **30 günlük doz takvimi** (`dose-calendar.tsx`) | Hücre glifleri: **✓** tümü alındı, **½** kısmen alındı, **–** atlandı, **✕** kaydedilmedi; planlı doz yoksa boş hücre. Gün numarası hücre köşesinde; her hücrede `sr-only` durum metni. Lejant sayıları gösterir ("Tümü alındı (15)"). Bkz. [08](../gorseller/08-hekim-hasta-detay.png). |
| **Uyum rozetleri** (`badges.tsx`) | Metinli: "%55 · Düşük", "%79 · Orta", "%85 · İyi"; `aria-label="Uyum %55, Düşük"`. Veri yoksa "Uyum verisi yok". |
| **Uyarı önem derecesi** | Renkli şeridin yanında ikon + metin rozeti ("Kritik", "Uyarı", "Bilgi"); gruplar `role="region"` ve `aria-labelledby` ile adlandırılmıştır. |
| **Eğilim kutucukları** | Ok ikonu + "önceki 7 güne göre" metni + `sr-only` "(yükseliş) / (düşüş) / (değişim yok)"; hedef dışıysa "Hedef aralığın dışında" yazısı. |
| **Grafikler** | `figure` + `aria-label` ("Son 30 gün kan şekeri grafiği"); hekim ekranında aynı veriyi veren **Tablo** sekmesi; hedef bant altında metinle açıklanır ("Yeşil bant 70–180 mg/dL"). Hedef dışı noktalar hem büyük hem kırmızıdır; seriler farklı çizgi stilindedir (düz / kesikli). |
| **Tablolar** | Her tabloda `sr-only` `caption`; satır eylemi için ayrı metin ("Detay"). |
| **Düğmeler** | Metinsiz ikon düğmelerinde `aria-label` ("Gönder", "Uyarıyı okundu olarak işaretle: …"); süs ikonları `aria-hidden`. |
| **Form alanları** | Her girişe görünür veya `sr-only` `label` (`htmlFor`). |
| **Dinamik içerik** | Sohbet geçmişi `aria-live="polite"`; yükleme alanları `role="status"`. |
| **Doz durumu** | Kartta renk + metin ("Alındı") + dolu düğme birlikte gösterilir. |

## 5. Hekim dashboard'unun klinik anlamı

Hekimin kısa kontrol seansındaki amacı "ilaç uyumunda veya ölçümlerde dikkat gerektiren hastayı bulmak"tır. Ekran bu soruya doğrudan cevap verecek şekilde tasarlanmıştır ([07](../gorseller/07-hekim-hasta-listesi.png), [08](../gorseller/08-hekim-hasta-detay.png)).

### 5.1 Hasta listesi sıralaması

1. **Açık kritik uyarısı olan hastalar** en üstte (satır hafif kırmızı zeminli, bayrak ikonu + "N kritik").
2. Kalanlar **7 günlük uyumu en düşükten yükseğe**.

Özet kutucukları: hasta sayısı, kritik uyarılı hasta sayısı, düşük uyumlu (<%60) hasta sayısı, ortalama 7 günlük uyum.

### 5.2 Uyum bantları

Uyum = alınan doz / planlanan doz (yalnızca vakti gelmiş planlı dozlar paydadadır). Backend'deki `adherence_band` ile aynı eşikler arayüzde `bandOf()` ile uygulanır:

| Bant | Skor | Rozet |
|---|---|---|
| İyi | ≥ %80 | yeşil, "İyi" |
| Orta | %60 – %79 | sarı, "Orta" |
| Düşük | < %60 | kırmızı, "Düşük" |
| Veri yok | plan yok | gri, "Veri yok" |

Listede **7 gün** ve **30 gün** yan yana gösterilir: 7 günlük düşüş "yeni ortaya çıkan", 30 günlük düşüklük "süregelen" sorunu gösterir (ör. Ahmet Yılmaz %55 Düşük / %79 Orta: son haftada kötüleşme). Hasta detayında skor **ilaç bazında** kırılır (Glifor %46, Norvasc %71): hekim sorunun hangi ilaçta olduğunu görür.

### 5.3 Trend: 7 gün ve önceki 7 gün

Kutucuklar son 7 günün ortalamasını **önceki 7 günle** karşılaştırır (açlık/tokluk kan şekeri, sistolik, diyastolik). Fark, gürültüyü elemek için bir eşiği aşarsa yükseliş/düşüş sayılır (açlık kan şekeri 10, tokluk 15 mg/dL; tansiyon 5 mmHg). Önceki dönem verisi yoksa "önceki dönem verisi yok" yazılır; trend uydurulmaz.

### 5.4 Hedef aralık bantları

| Ölçüm | Grafikteki hedef | Gösterim |
|---|---|---|
| Kan şekeri | **70–180 mg/dL** yeşil bant (hekim grafiği); hasta ekranında açlık için 70–130 | Aralık dışı noktalar büyük kırmızı |
| Tansiyon | **130/80 mmHg** kesikli referans çizgileri | Sistolik ≥130, diyastolik ≥80 veya sistolik <90 hedef dışı sayılır |

Bu bantlar **görselleştirme** içindir. Uyarı üreten eşikler (kan şekeri <54 veya >300, tansiyon ≥180/120 kritik) backend'de (`services/thresholds.py`) uygulanır ve M1 guardrail politikasıyla aynıdır; danışman hekim tarafından gözden geçirilmelidir.

### 5.5 Akşam dozu kaçırma örüntüsü

Haftalık özet, kaçırılan dozları gün saatine göre gruplar ve örüntüyü açıkça söyler: *"Kaçırılan dozlar en çok akşam saatlerinde (5/6)."* ([05](../gorseller/05-hasta-ozet.png), [08](../gorseller/08-hekim-hasta-detay.png)). Klinik anlamı: sorun "hasta ilaca uymuyor" değil "akşam rutininde ilaç yok" olabilir; hekim ilaç saatini veya hatırlatma stratejisini konuşabilir. Özet metni "Otomatik oluşturulmuş özettir; teşhis veya tedavi önerisi değildir" notunu taşır. Koçun akşam sorusuna verdiği yanıt da aynı kaçırılan doz verisine dayanır ([`prototip-koc.md`](prototip-koc.md)).

### 5.6 Uyarılar

Uyarılar önem derecesine göre gruplanır: Kritik (acil belirti, çok yüksek kan şekeri), Uyarı (hedef dışı ölçüm, koç hekime yönlendirdi), Bilgi (hasta doz sorusu sordu). Hekim **Okundu** ile kapatır; eylem `POST /alerts/{id}/ack` ile kaydedilir ([09](../gorseller/09-hekim-uyarilar.png)).

## 6. Bakım veren: salt okunur

Bakım veren, bağlı olduğu hastanın uyum, ölçüm ve özet verisini hekim detay bileşeniyle **aynı görünümde** ama `readOnly` modunda görür ([10](../gorseller/10-yakin-hastalar.png)):

- Başlıkta "Salt okunur" rozeti ve "Bu ekranda değişiklik yapılamaz" metni.
- Uyarı kapatma gibi yazma eylemleri render edilmez.
- Asıl koruma backend'dedir: bakım veren rolü yalnızca okuma uçlarına, yalnızca bağlı olduğu hastaya erişir; başkasının hastası için 404 döner. Arayüzdeki salt okunurluk ikinci katmandır, birincisi değildir.
