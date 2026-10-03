# Kullanıcı Akışları

## A. Hasta — günlük akış

```mermaid
flowchart TD
    A[Web push: "Metformin zamanı"] --> B[Bugün ekranı]
    B --> C{Dozu aldı mı?}
    C -- Aldım --> D[dose_log: taken]
    C -- Almadım --> E[dose_log: skipped + isteğe bağlı neden]
    D --> F[Ölçüm gir: açlık kan şekeri]
    E --> F
    F --> G{Eşik dışı mı?}
    G -- Evet --> H[Kritik uyarı + hekime bayrak]
    G -- Hayır --> I[Grafik güncellenir]
    I --> J[Koç kartı: günün geri bildirimi]
```

## B. Hasta — koç sohbeti

```mermaid
flowchart TD
    A[Hasta mesaj yazar] --> B[Katman 1: girdi filtresi]
    B -- Acil belirti --> C[Sabit 112 yanıtı + kritik alert]
    B -- Doz talebi --> D[Sabit güvenli yanıt + hekime not önerisi]
    B -- Temiz --> E[LLM koç]
    E --> F[Tool: get_adherence 7/30 gün]
    E --> G[Tool: get_measurements]
    E --> H[Tool: search_education RAG]
    F & G & H --> I[Structured output]
    I --> J[Katman 3: çıktı doğrulayıcı]
    J -- Geçerli --> K[Yanıt + kaynaklar]
    J -- İhlal --> L[Güvenli yedek yanıt]
    K --> M{escalate_to_clinician?}
    M -- Evet --> N[Hekime alert]
```

## C. Hekim — haftalık inceleme

```mermaid
flowchart TD
    A[Giriş] --> B[Hasta listesi: uyum skoruna göre sıralı]
    B --> C[Kırmızı bayraklı hastalar üstte]
    C --> D[Hasta detayı: 30 gün eğri + doz takvimi]
    D --> E[LLM haftalık özet]
    E --> F[Uyarıyı incele / kapat]
```

## D. Bakım veren

Hasta, ayarlardan e-posta ile bakım veren ekler → bakım veren hesabıyla giriş yapar → bağlı hastanın bugün ekranını, grafiklerini ve uyarılarını **salt okunur** görür.
