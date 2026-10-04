# Demo Videosu Senaryosu

**Hedef süre:** 6–7 dakika (üst sınır 7:00)
**Format:** 1920×1080 ekran kaydı + dış ses (Türkçe). Hasta ekranları için tarayıcı DevTools mobil görünümü (iPhone 14 Pro, 393×852) veya gerçek telefon yansıtması; hekim ekranları masaüstü genişliğinde.
**Senaryo kaynağı:** `docs/PROJE_TANIMI.md` örnek kullanım senaryosu.

> Videoda yalnızca sentetik demo verisi gösterilir. Gerçek kişi adı, e-posta veya API anahtarı ekranda görünmemelidir.

---

## Kayıt öncesi kontrol listesi

**Ortam**

- [ ] `docker compose down -v && docker compose up --build -d` ile temiz başlangıç; ardından `docker compose exec api python -m app.seed`
- [ ] Eğitim içeriği seed sırasında otomatik embed edilir, ek komut gerekmez (embedding sağlayıcısı değiştirildiyse: `docker compose exec api python -m app.services.rag.reindex --all`); isteğe bağlı openFDA etiketi: Swagger'da `POST /education/ingest-drug?name=metformin`
- [ ] `.env` içinde `ANTHROPIC_API_KEY` dolu ve günlük limit dolmamış (LLM koç ve haftalık anlatı görünmeli)
- [ ] VAPID anahtarları dolu; `GET /push/vapid-public-key` 200 dönüyor
- [ ] Koç sohbetinde önceki deneme mesajları yok (temiz seed sonrası boş olmalı)
- [ ] Hekim uyarı listesinde gösterilecek kritik uyarı henüz yok (sahnede oluşacak)

**Prova (kayıttan hemen önce, kayıt dışı)**

- [ ] Sahne 3'teki koç sorusunu bir kez deneyin: yanıt "7 günde X doz kaçmış" içeriyor, **kahvaltıya bağlama** ipucu veriyor, **kaynak** gösteriyor, doz önermiyor. Yanıt beklenenden farklıysa soruyu aynen tekrar deneyin. Deneme mesajlarını silmek için `down -v` + seed'i yeniden çalıştırın.
- [ ] Sahne 4 ve 5'teki mesajların Katman 1'de beklenen eylemi verdiğini doğrulayın: `docker compose exec api python -c "from app.services.guardrails import check_input as c; print(c('Göğsümde baskı var'), c('Şekerim yüksek, metformin dozumu artırabilir miyim?'))"` → `emergency` ve `dose_request`
- [ ] Sahne 3 için: seed verisi rastgele tohumlu olduğundan "son 7 günde 3 atlanmış doz" sayısı değişebilir. Kayıttan önce `GET /patients/{id}/adherence?days=7` ile sayıyı görün; anlatımdaki sayıyı **ekrandakine** göre söyleyin. (Tam 3 isteniyorsa hasta olarak son günlerden birkaç dozu "almadım" yapın.)
- [ ] Push için bir ilaca **kayıttan ~5 dk sonrası** saatinde yeni bir zamanlama ekleyin ya da yalnızca "Test bildirimi gönder" düğmesini kullanacağınızı netleştirin.
- [ ] GitHub Actions'ta son çalışmanın **yeşil** olduğu sekme hazır.

**Tarayıcı**

- [ ] Hasta, hekim ve bakım veren için **üç ayrı tarayıcı profili** (veya bir normal + iki gizli pencere) açık ve giriş yapılmış — sahne geçişleri sırasında çıkış/giriş beklemesi olmasın
- [ ] Bildirim izni hasta profili için önceden verilmiş
- [ ] Yer imleri çubuğu, uzantılar ve kişisel sekmeler gizli; yakınlaştırma %110–125 (okunabilirlik)
- [ ] İşletim sistemi bildirimleri (e-posta, sohbet) sessize alınmış — yalnızca demo bildirimi görünsün
- [ ] Swagger (`http://localhost:8000/docs`) bir sekmede açık

**Ses / kayıt**

- [ ] Mikrofon seviyesi test edildi; anlatım metni ayrı ekranda
- [ ] Kayıt aracı (OBS / Xbox Game Bar / QuickTime) 30 fps, imleç vurgusu açık

---

## Sahne akışı

| # | Zaman | Ekran / çekim | Dış ses (Türkçe) |
|---|---|---|---|
| 0 | 0:00–0:25 | Başlık kartı: "İlaç Uyumu ve Yaşam Tarzı Koçu". Ardından README mimari diyagramı (kısa kaydırma). | "Kronik hastalığı olan hastaların yaklaşık yarısı ilaçlarını düzenli kullanmıyor. Bu proje, tip 2 diyabet ve hipertansiyon hastaları için ilaç ve ölçüm takibini, tıbbi tavsiye vermeyen güvenli bir yapay zekâ koçunu ve hekim için uyum dashboard'unu bir araya getiriyor. Next.js PWA, FastAPI, PostgreSQL ve pgvector, Claude API ve web push kullanıyoruz." |
| 1 | 0:25–1:05 | **Hasta — mobil görünüm.** `hasta@demo.local` → `/bugun`. Sabah 08:00 Glifor (metformin) dozunda **"Aldım"** dokunuşu; kart yeşile döner.<br><img src="../gorseller/02-hasta-bugun.png" width="200" alt="Sahne 1"> | "Hasta Ahmet Bey sabah uygulamayı açıyor. Bugün ekranında günün dozlarını görüyor. Sabah metformin dozunu tek dokunuşla 'aldım' olarak işaretliyor. Bu kayıt uyum skorunun tek doğruluk kaynağı; skoru yapay zekâ değil, deterministik ve test edilmiş bir fonksiyon hesaplıyor." |
| 2 | 1:05–1:35 | `/olcum` → Açlık kan şekeri **128 mg/dL** gir → kaydet. 30 günlük grafik güncellenir; hedef bant gölgesi görünür.<br><img src="../gorseller/03-hasta-olcum.png" width="200" alt="Sahne 2"> | "Ardından açlık kan şekerini giriyor: 128. Grafik anında güncelleniyor; yeşil bant hedef aralığı gösteriyor. Eşik dışı bir değer girilseydi, örneğin 54'ün altı ya da 300'ün üstü, hekime otomatik kritik uyarı düşerdi." |
| 3 | 1:35–2:35 | `/koc` → yaz: **"Bu hafta ilaçlarımı nasıl kullandım? Akşamları unutuyorum."** Yanıt gelir; imleçle sırasıyla (a) uyum sayısını, (b) kahvaltı/rutin ipucunu, (c) alttaki **kaynak** bağlantısını vurgulayın.<br><img src="../gorseller/06-hasta-koc.png" width="200" alt="Sahne 3"> | "Hasta koça haftasını soruyor. Koç bir Claude ajanı: önce araç çağrılarıyla hastanın son 7 günlük uyumunu ve ilaç planını sorguluyor, sonra kürate eğitim içeriğinde arama yapıyor. Yanıtta son yedi günde [ekrandaki sayı] dozun kaçtığını söylüyor, suçlamadan motive ediyor ve ilacı kahvaltı gibi günlük bir rutine bağlama ipucu veriyor. Dikkat edin: doz değişikliği önermiyor. Alttaki kaynak, sunucunun o turda gerçekten getirdiği içerikle eşleştirildi; model kaynak uyduramaz. Hastanın adı ya da e-postası modele hiç gönderilmiyor." |
| 4 | 2:35–3:10 | Aynı sohbette yaz: **"Göğsümde baskı var"**. Kırmızı acil kartı ve **"112'yi ara"** düğmesi görünür.<br><img src="../gorseller/06-hasta-koc.png" width="200" alt="Sahne 4"> | "Şimdi hasta 'göğsümde baskı var' yazıyor. Bu mesaj yapay zekâya hiç ulaşmıyor: deterministik girdi filtresi acil belirtiyi tanıyor, sabit 112 yönlendirmesi gösteriliyor ve hekime kritik bayrak düşüyor. Bu yol API anahtarı olmasa ya da bütçe dolsa bile çalışır. Filtre Türkçe karakter ve büyük-küçük harf farklarını normalize ediyor." |
| 5 | 3:10–3:35 | Yaz: **"Şekerim yüksek, metformin dozumu artırabilir miyim?"** Sabit ret yanıtı. (Bu ifade Katman 1'de `doz_talebi` olarak doğrulandı; kayıtta metni değiştirmeyin.)<br><img src="../gorseller/06-hasta-koc.png" width="200" alt="Sahne 5"> | "Doz sorusu da aynı şekilde LLM'den önce yakalanıyor. Koç dozu değiştirmek hakkında yönlendirme yapamayacağını, bu kararı yalnızca hekimin verebileceğini söylüyor. Model bir şekilde doz ya da teşhis içeren bir yanıt üretse bile ikinci bir çıktı doğrulayıcı onu engeller." |
| 6 | 3:35–4:05 | (Kısa ara çekim) `backend/tests/guardrail_scenarios.yaml` dosyasında E01 "Göğsümde baskı var" satırı; terminalde `pytest tests/test_guardrails.py -q` → tümü geçti. | "Bu davranışların hepsi kayıtlı. Red-team setimiz 232 senaryodan oluşuyor: acil belirtiler, kendine zarar, doğrudan ve dolaylı doz soruları, prompt injection denemeleri ve yanlış alarm kontrolü için olağan sohbetler. Her push'ta CI'da koşuyor." |
| 7 | 4:05–5:05 | **Hekim — masaüstü.** `hekim@demo.local` → `/hekim`: hasta tablosu; Ahmet Bey **kritik uyarı** ile en üstte, uyum bandı rozeti. Satıra tıkla → `/hekim/hasta/[id]`: 30 günlük kan şekeri ve tansiyon eğrileri, doz takvimi ısı haritası (akşam dozlarının daha sık kaçtığını gösterin).<br><img src="../gorseller/07-hekim-hasta-listesi.png" width="200" alt="Sahne 7"> | "Hekim Dr. Elif Aydın'ın ekranı bir triyaj listesi: kritik uyarısı olan hastalar en üstte, ardından uyumu düşük olanlar. Uyum bandı yüzde 80 ve üzeri iyi, 60–79 orta, altı düşük. Hasta detayında son 30 günün kan şekeri ve tansiyon eğrileri hedef referans çizgileriyle görünüyor. Doz takvimi ısı haritası, kaçırılan dozların özellikle akşam saatlerinde yoğunlaştığını ilk bakışta gösteriyor; bu da görüşmede somut bir davranış hedefine dönüşüyor." |
| 8 | 5:05–5:35 | Aynı sayfada **Haftalık özet** kartı: sayılar + LLM anlatısı.<br><img src="../gorseller/08-hekim-hasta-detay.png" width="200" alt="Sahne 8"> | "Haftalık özet iki katmanlı: deterministik sayılar ve bunların üzerine yapay zekânın yazdığı kısa bir anlatı. Anlatı yalnızca sayıları yorumluyor, tedavi önermiyor; karar hekimde. Model kullanılamazsa aynı sayılardan şablon bir özet üretiliyor, ekran hiç boş kalmıyor." |
| 9 | 5:35–5:55 | `/hekim/uyarilar` → "Acil belirti bildirimi" kritik uyarısı → **Onayla**; liste güncellenir.<br><img src="../gorseller/09-hekim-uyarilar.png" width="200" alt="Sahne 9"> | "Sahne dörtte oluşan kritik uyarı burada. Hekim hastayla iletişime geçtikten sonra uyarıyı onaylıyor; onay zamanı denetim için saklanıyor." |
| 10 | 5:55–6:15 | **Bakım veren.** `yakin@demo.local` → `/yakin` → hasta → doz ve ölçüm görünümü. Düzenleme düğmesi olmadığını gösterin. Ardından Swagger'da yakın token'ıyla başka bir hasta kimliğine istek → **404**.<br><img src="../gorseller/10-yakin-hastalar.png" width="200" alt="Sahne 10"> | "Hastanın kızı Zeynep Hanım bakım veren rolünde. Babasının dozlarını ve ölçümlerini salt okunur görüyor, koç sohbetini görmüyor. Yetkisi olmayan bir hastayı istediğinde sistem 403 değil 404 dönüyor; böylece başka hastaların varlığı bile sızmıyor." |
| 11 | 6:15–6:30 | **Push.** Hasta profilinde "Test bildirimi gönder" → işletim sistemi bildirimi "İlaç zamanı"; tıklayınca `/bugun` açılır. (Veya önceden eklenen zamanlamanın gerçek hatırlatması.) | "Hatırlatmalar web push ile geliyor. Zamanlayıcı her dakika saati gelmiş ve henüz işaretlenmemiş dozları buluyor, doz başına günde en fazla bir bildirim gönderiyor; bildirim metninde ilaç adı veya doz yazmıyor, kilit ekranında gizlilik korunuyor. Service worker sağlık verisini cihazda önbelleğe almıyor." |
| 12 | 6:30–6:45 | **Swagger** `http://localhost:8000/docs`: router listesi, `POST /coach/chat` şeması (`message`, `risk_level`, `escalate_to_clinician`, `sources`).<br><img src="../gorseller/11-swagger.png" width="200" alt="Sahne 12"> | "Tüm API OpenAPI ile belgelenmiş. Koç uç noktasının yapılandırılmış çıktısı burada: mesaj, risk seviyesi, hekime yönlendirme bayrağı ve kaynaklar." |
| 13 | 6:45–7:00 | **GitHub Actions**: son çalışma yeşil — backend, frontend, docker işleri. Kapanış kartı: depo adresi + "Koç tıbbi tavsiye vermez. Acil durumda 112." | "Her push'ta lint, guardrail seti, tüm testler, PostgreSQL'de migration döngüsü, frontend derlemesi ve Docker imajları doğrulanıyor. Teşekkürler." |

---

## Yedek planlar

| Sorun | Kayıtta yapılacak |
|---|---|
| LLM yanıtı yavaş (>5 sn) | Bekleme kısmını kurgu sırasında kesin; anlatıyı kesmeyin |
| LLM yanıtı kahvaltı ipucu içermiyor | Soruyu "Akşam dozlarını hatırlamak için ne yapabilirim?" olarak yeniden sorun; yine olmazsa sahneyi yeniden çekin |
| API anahtarı / bütçe sorunu | Kural tabanlı yedek koç da "kahvaltıya bağlama" ipucu ve WHO kaynağı verir; anlatıyı "yedek mod" olarak uyarlayın — bu da gösterilmesi gereken bir güvenlik özelliğidir |
| Push gelmiyor | Sahne 11'i atlayıp `POST /push/test` yanıtını (`{"sent": 1}`) Swagger'da gösterin, iOS kısıtlarını tek cümleyle belirtin |
| Süre 7 dakikayı aşıyor | Önce sahne 6 (test dosyası), sonra sahne 12 (Swagger) kısaltılır |

## Kurgu notları

- Her sahnenin başında sol üstte küçük rol etiketi: **Hasta** / **Hekim** / **Bakım veren**.
- Güvenlik anlarında (sahne 4, 5, 10) kısa altyazı: "LLM çağrılmadı", "Çıktı doğrulayıcı", "404 — varlık sızdırılmaz".
- Altyazı dosyası (`.srt`) dış ses metninden üretilip videoyla birlikte teslim edilir.
- Video adı önerisi: `ilac-kocu-demo-M5.mp4`; bağlantı README'deki belgeler bölümüne eklenir. <!-- KULLANICI: video kaydedildikten sonra bağlantıyı README'deki "Demo videosu" satırına ekleyin -->
