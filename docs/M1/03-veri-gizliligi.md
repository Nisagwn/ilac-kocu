# Veri Gizliliği Yaklaşımı

Platform özel nitelikli kişisel veri (sağlık verisi) işler. Yaklaşım KVKK'nın sağlık verisine ilişkin ilkelerini ve "privacy by design" ilkesini temel alır.

## 1. İlkeler

| İlke | Uygulama |
|---|---|
| Veri minimizasyonu | TC kimlik no, adres, telefon toplanmaz. Hasta için yalnızca ad, e-posta, doğum yılı, hastalık türü tutulur. |
| Amaçla sınırlılık | Veri yalnızca uyum takibi ve hekim bilgilendirmesi için kullanılır; reklam/analitik üçüncü taraf yok. |
| Açık rıza | Kayıtta ayrı onay: (a) verinin hekimle paylaşılması, (b) mesajların LLM ile işlenmesi, (c) bakım veren eklenmesi. |
| Erişim kontrolü | Rol bazlı yetki (aşağıda), tüm yetki kontrolleri backend'de yapılır. |
| Silme hakkı | Hasta hesabını sildiğinde kişisel veriler silinir, istatistik için yalnızca anonim toplamlar kalır. |

## 2. Rol bazlı erişim matrisi

| Kaynak | Hasta | Hekim | Bakım veren |
|---|---|---|---|
| Kendi profil | Oku/Yaz | — | — |
| İlaç planı | Oku/Yaz (kendi) | Oku (atanmış hastalar) | Oku (bağlı hasta) |
| Doz kayıtları | Oku/Yaz (kendi) | Oku | Oku |
| Ölçümler | Oku/Yaz (kendi) | Oku | Oku |
| Koç mesajları | Oku/Yaz (kendi) | Yalnızca risk bayraklı mesajlar | — |
| Uyarılar (alerts) | Oku | Oku / Kapat | Oku |
| Haftalık özet | Oku | Oku | Oku |

Yetkisiz erişimde kaynak varlığını sızdırmamak için **404** döner.

## 3. LLM'e giden veri

- Hastanın adı, e-postası ve kimlik bilgisi **gönderilmez**; koç yalnızca takma kimlik (`patient_ref`) ve tool çağrısıyla dönen sayısal özetleri görür.
- Tool'lar yalnızca o oturumdaki hastanın verisine erişebilir (hasta kimliği sunucu tarafında bağlanır, model parametre olarak veremez).
- Sağlayıcı tarafında veri saklama/eğitime kullanma kapalı API ayarları tercih edilir; README'de belirtilir.

## 4. Teknik önlemler

- Şifreler bcrypt ile hash'lenir; JWT kısa ömürlü (60 dk).
- Tüm trafik HTTPS (production); gizli anahtarlar `.env` / ortam değişkeni, repoda yok.
- Veritabanı yedekleri şifreli; demo ortamında yalnızca sentetik veri kullanılır.
- Guardrail olayları (engellenen yanıtlar) mesaj içeriği olmadan loglanır.

## 5. Sınırlılık

Bu bir ders projesidir; gerçek hasta verisiyle kullanılmadan önce VERBİS kaydı, hukuki inceleme ve sağlık bakanlığı mevzuatına uygunluk değerlendirmesi gerekir. Demo yalnızca sentetik verilerle yapılır.
