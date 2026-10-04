# Prototip Koç (Kural Tabanlı)

M3'te koç, LLM kullanmadan **kural tabanlı** çalışır (`backend/app/services/coach.py`, sınıf `RuleBasedCoach`). Amaç, arayüz ve guardrail hattını uçtan uca doğrulamak ve LLM anahtarı olmadan da yanıtların **veriye dayalı ve kaynaklı** olmasını sağlamaktır. Üretim kalitesinde tool-use koç M4 kapsamındadır.

![Koç ekranı](../gorseller/06-hasta-koc.png)

*Ekran 06 — Koç sohbeti (mobil). Yukarıdan aşağıya: kaynaklı uyum yanıtı, doz sorusuna sabit ret, acil belirtiye kırmızı 112 kartı.*

## 1. Üç katmanlı guardrail hattı

`handle_message()` her hasta mesajını şu sırayla işler ([M1 guardrail politikası](../M1/02-guardrail-politikasi.md)):

```
Hasta mesajı
   │
   ▼
[Katman 1] Girdi filtresi — deterministik, koçtan ÖNCE (guardrails.check_input)
   ├─ kendine zarar → sabit yanıt + kritik uyarı        (koç çağrılmaz)
   ├─ acil belirti  → sabit 112 yanıtı + kritik uyarı   (koç çağrılmaz)
   ├─ doz/ilaç değişikliği talebi → sabit güvenli yanıt + "bilgi" uyarısı (koç çağrılmaz)
   └─ temiz → devam
   │
   ▼
[Katman 2] Koç sağlayıcısı (CoachProvider) — M3'te RuleBasedCoach
   │   hata verirse kural tabanlı koça düşülür (guardrail_action = "llm_fallback")
   ▼
[Katman 3] Çıktı doğrulayıcı — deterministik (guardrails.check_output)
   ├─ doz/teşhis ifadesi içeriyor → yanıt engellenir, güvenli yanıt döner, olay kaydedilir
   ├─ risk = emergency → kritik uyarı ("coach_emergency")
   ├─ hekime yönlendirme → uyarı ("coach_escalation")
   └─ geçerli → hastaya gönderilir
```

Katman 1 ve 3 sağlayıcıdan bağımsızdır; koç (kural tabanlı ya da ileride LLM) ne üretirse üretsin güvenlik garantisi bu iki katmandan gelir. Her mesaj ve yanıt `coach_messages` tablosuna `risk_level`, `escalate_to_clinician`, `sources` ve `guardrail_action` ile kaydedilir.

Ekran 06'daki üç tur bu hattın üç yolunu gösterir:

| Hasta yazısı | Hattın yolu | Arayüzde |
|---|---|---|
| "Son günlerde akşam ilacımı unutuyorum, ne yapabilirim?" | Katman 1 temiz → koç → Katman 3 geçerli | Uyum verisi + ipucu + **Kaynaklar** bağlantıları |
| "metformini ikiye çıkarabilir miyim?" | Katman 1 `dose_request` | Sabit ret metni, **"Hekiminize bildirildi"** etiketi; hekime *bilgi* uyarısı |
| "göğsümde baskı var" | Katman 1 `emergency` | Kırmızı **"Acil durum olabilir"** kartı, **112'yi ara** düğmesi, sabit 112 yanıtı; hekime *kritik* uyarı |

## 2. Uyum verisine dayalı mesaj

Koç, genel öğüt yerine hastanın **kendi** son 7 günlük verisini kullanır. `get_adherence()` hastanın aktif ilaç planını ve doz kayıtlarını okuyup `compute_adherence()` ile skoru hesaplar (M1/M2'deki, elle hesaplanmış örneklerle doğrulanan aynı fonksiyon). Mesaj şablonları:

| Durum | Yanıt | Risk / yönlendirme |
|---|---|---|
| Plan yok (skor yok) | "Henüz ilaç planın görünmüyor. Planını ekledikten sonra ilerlemeni birlikte takip edebiliriz." | düşük |
| Hiç kaçırılan doz yok | "Son 7 günde planlanan N dozun hepsini almışsın, harika gidiyorsun!" + rutin önerisi | düşük |
| Kaçırılan doz var | "Son 7 günde 20 dozun 11 tanesini almışsın (%55). 9 doz kaçmış görünüyor." + **davranışsal ipucu** (ilacı kahvaltı gibi bir işe bağlamak, telefona alarm kurmak) | Skor < 0,6 ise `medium`; skor < 0,5 ise `escalate_to_clinician = true` (hekime "coach_escalation" uyarısı) |

Mesaj **motive eder ve davranışsal ipucu verir**; doz değişikliği, ilaç bırakma veya teşhis içermez. Bu, spec'teki örnek senaryoyla birebir örtüşür (son 7 günde atlanan dozlar + "ilaç saatini kahvaltıya bağlama" ipucu).

## 3. RAG kaynağı ekleme

`RuleBasedCoach.reply()` önce uyum yanıtını üretir, ardından hastanın mesajıyla `search_education(db, message, k=1)` ile **kürate eğitim içeriğinde** arama yapar. Eşleşme varsa:

1. Maddenin ilk cümlesi "İlgili bilgi — <başlık>: <cümle>" olarak mesaja eklenir.
2. Maddenin kaynağı (`başlık (kaynak adı)`, URL) `sources` listesinin **başına** eklenir.

Uyum yanıtı ayrıca varsayılan olarak DSÖ kaynağını (*Adherence to long-term therapies: evidence for action, 2003*) taşır. Sonuç: eğitim içeriği kullanılan her yanıtta en az bir kaynak vardır (M1 başarı ölçütü ve guardrail kuralı 6). Ekran 06'da akşam dozlarıyla ilgili eşleşen madde ve WHO raporu "Kaynaklar" altında tıklanabilir bağlantı olarak görünür.

## 4. M4'e geçiş: aynı `CoachProvider` arayüzü

Sağlayıcı bir arayüzün ardında tutulur:

```python
class CoachProvider(Protocol):
    def reply(self, db: Session, patient: Patient, message: str) -> CoachReply: ...
```

- `CoachReply` (`message`, `risk_level`, `escalate_to_clinician`, `sources`, `guardrail_action`) spec'teki structured output şemasıdır; **iki sağlayıcı da aynı şemayı döndürür**.
- `get_coach_provider()` yapılandırmaya göre sağlayıcıyı seçer: Claude anahtarı ve ayarı yoksa `RuleBasedCoach`, varsa `ClaudeCoachProvider` (`app/services/llm/coach_agent.py`).
- `get_adherence()` ve RAG araması bugünkü koçta düz fonksiyonlardır; M4'te Claude'a **tool** olarak verilecek şekilde ayrılmıştır (koç kodundaki not).
- `handle_message()` yalnızca arayüzü bilir: Katman 1 ve 3, kayıt ve uyarı mantığı sağlayıcıdan bağımsızdır; sağlayıcı hata verirse (ağ, bütçe) kural tabanlı koça düşülür.
- Frontend sağlayıcıyı bilmez: `POST /coach/chat` aynı `CoachReply` biçimini döndürür, `coach-screen.tsx` aynı kartları çizer.

Bu yüzden M4'te arayüz, endpoint ve guardrail testleri değişmeden kalır; yalnızca Katman 2'nin içi (kural tabanlı → Claude tool-use) değişir. Kural tabanlı koç, LLM kullanılamadığında da çalışan **yedek** olarak kalır.

## 5. Sınırlar

- Yanıtlar şablonludur; serbest dilde, bağlama duyarlı sohbet yoktur (M4).
- RAG araması mesajla en yakın tek maddeyi (`k=1`) ekler; eşleşme yoksa yalnızca uyum yanıtı döner.
- Eşikler ve acil belirti kalıpları danışman hekim tarafından gözden geçirilmelidir (M1 notu).
