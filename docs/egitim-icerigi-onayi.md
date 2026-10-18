# Eğitim içeriği hekim onayı — politika

Koçun kaynak gösterdiği eğitim içeriği (`education_contents`, RAG korpusu) iki kaynaktan gelir:
kürate içerik (`app/data/education.yaml`, ekip tarafından kurumsal kaynaklardan derlenir) ve
openFDA ilaç etiketinden otomatik üretilen özetler (`kind = openfda`).

## Durumlar

| `review_status` | Anlamı | RAG / koç |
|---|---|---|
| `pending` | Hekim kararı yok (varsayılan) | Kullanılır. openFDA içeriği kaynak başlığında **"(hekim onayı bekliyor)"** notuyla gösterilir. |
| `approved` | Hekim onayladı (`reviewed = true`) | Kullanılır, not yok. |
| `rejected` | Hekim reddetti | **Hiç kullanılmaz**: arama sonuçlarından çıkarılır, koç kaynak gösteremez; hasta/bakım veren listesinde görünmez. |

`reviewed_by` ve `reviewed_at` kararı veren hekimi ve zamanı tutar (hesap verebilirlik). `reviewed` sütunu
geriye dönük uyumluluk için korunur ve `review_status == approved` ile eş tutulur (migration `0010_education_review`).

## Neden onaylanmamış içerik kullanılabiliyor?

openFDA özetleri resmi ABD etiketinden alınır, doz içeren cümleler ve doz bölümü atılır, her metin
"kararları hekiminizle verin" uyarısıyla biter. Onayı beklemek koçun ilaç uyarılarını hiç
gösterememesine yol açacağından, içerik kullanılır ama kaynağında onay beklediği açıkça yazılır.
Hekim uygunsuz bulduğu içeriği tek tıkla reddeder ve içerik anında RAG'den çıkar. Kürate içerik
ekip tarafından kurumsal kaynaklardan derlendiği için onay beklerken işaretlenmez.

## Kurallar

- Onay/ret yalnızca hekim rolündedir: `POST /education/{id}/review`, `POST /education/{id}/reject`
  (diğer roller 403). Karar geri alınabilir.
- Onay bekleyenler: `GET /education?reviewed=false`; tüm durumlar için `review_status=pending|approved|rejected`.
- openFDA etiketi değişip içerik yeniden üretilirse önceki karar yeni metni kapsamaz → `pending`'e döner.
- Kürate içerikte hekim kararı, seed sırasında YAML'daki `reviewed` değerinden önceliklidir; metin
  değişirse karar sıfırlanır.
- `POST /education/ingest-drug` yalnızca hasta ve hekime açıktır; bakım veren (salt okunur) 403 alır.
- Arayüz: Hekim Paneli → **Eğitim içeriği** (`/hekim/egitim`).
