# Etik ve Güvenlik Kontrol Listesi

Proje tanımındaki değerlendirme odak noktalarının her biri için **kanıt** (dosya, test, CI adımı) eşlemesi. Ayrıntılı tartışma: [`final-rapor.md`](final-rapor.md).

Durum: ✅ karşılandı · ⚠️ kısmen / bilinen sınırlılık · ⏳ bekliyor (kullanıcı eylemi)

## 1. Guardrail'lerin test edilmiş ve belgelenmiş olması

| Kontrol | Durum | Kanıt |
|---|---|---|
| Koçun "asla yapmadıkları" yazılı politika olarak tanımlı | ✅ | `docs/M1/02-guardrail-politikasi.md` |
| Acil belirti tespiti LLM'den **önce**, deterministik | ✅ | `backend/app/services/guardrails.py` → `check_input`; `backend/app/services/coach.py` → `handle_message` |
| Acil belirti → sabit 112 yanıtı + hekime `critical` uyarı | ✅ | `tests/test_api.py::test_coach_emergency_creates_critical_alert` |
| Kendine zarar → 112 + destek mesajı + kritik uyarı | ✅ | `guardrails.py` `SELF_HARM_PATTERNS`, `SELF_HARM_REPLY`; YAML setindeki kendine zarar senaryoları |
| Doz / ilaç değişikliği sorusu → sabit ret | ✅ | `tests/test_api.py::test_coach_refuses_dose_change` |
| Çıktı doğrulayıcı (doz, teşhis, "ben doktorum") | ✅ | `guardrails.py` → `check_output`; `tests/test_api.py::test_coach_output_guardrail_blocks_bad_provider` |
| Türkçe karakter / büyük harf normalizasyonu | ✅ | `guardrails.py` → `normalize`; YAML'daki aksansız senaryolar (ör. E02) |
| Red-team seti (hedef ~80–100 senaryo) | ✅ | `backend/tests/guardrail_scenarios.yaml` — 232 senaryo: girdi 61 acil, 12 kendine zarar, 60 doz, 46 olağan sohbet; çıktı 30 engellenmesi gereken, 23 izinli. Riskli kategorilerde recall %100, yanlış pozitif 0 |
| Red-team seti CI'da ayrı, zorunlu adım | ✅ | `.github/workflows/ci.yml` → "Guardrail red-team seti" adımı |
| Guardrail test raporu | ✅ | [`docs/M4/guardrail-raporu.md`](../M4/guardrail-raporu.md) |
| Ölçüm eşik denetimi (bağımsız ikinci kanal) | ✅ | `backend/app/services/thresholds.py`; `tests/test_api.py::test_measurements_and_threshold_alert` |
| Bütçe/limit dolsa da acil yol çalışır | ✅ | Katman 1, sağlayıcı çağrısından önce (`coach.py` `handle_message` akış sırası); `tests/test_coach_llm.py::test_input_guardrail_runs_before_llm` acil/doz mesajında LLM çağrısı ve `llm_usage` kaydı olmadığını doğrular |
| Bilinen recall sınırı belgelenmiş | ⚠️ | `final-rapor.md` §7.2, §12-4 |

## 2. Rol bazlı yetkilendirme ve hasta verisi gizliliği

| Kontrol | Durum | Kanıt |
|---|---|---|
| Erişim matrisi belgelenmiş | ✅ | `docs/M1/03-veri-gizliligi.md` §2 |
| Yetki kararları backend'de, her istekte | ✅ | `backend/app/deps.py` → `get_patient_for_read`, `get_patient_for_write`, `require_roles` |
| Hasta başka hastayı göremez (404) | ✅ | `tests/test_api.py::test_patient_cannot_see_other_patient` |
| Hekim yalnızca atanmış hastaları görür | ✅ | `tests/test_api.py::test_clinician_access_flow`, `::test_other_clinician_cannot_access` |
| Bakım veren salt okunur | ✅ | `tests/test_api.py::test_caregiver_read_only` |
| Yetkisiz erişimde varlık sızdırılmaz (404, 403 değil) | ✅ | `deps.py` `_not_found`; yukarıdaki testler |
| Frontend rol yönlendirmesi güvenlik sınırı değil, yalnızca UX | ✅ | `frontend/src/middleware.ts`; `frontend/README.md` |
| Açık rıza olmadan hasta kaydı yok | ✅ / ⚠️ | `backend/app/schemas.py` `patient_needs_consent`; `tests/test_api.py::test_patient_requires_consent` — tek onay alanı, ayrıştırılmış rıza yok (`final-rapor.md` §12-14) |
| Veri minimizasyonu (TC no, adres, telefon yok; doğum yılı) | ✅ | `backend/app/models.py` `Patient`; `docs/er-diyagrami.md` |
| LLM'e ad / e-posta / kullanıcı kimliği gönderilmez | ✅ | `backend/app/services/llm/client.py`, `coach_agent.py`; `tests/test_coach_llm.py::test_no_personal_data_sent_to_llm` |
| Tool şemalarında `patient_id` yok (sunucu bağlar) | ✅ | `backend/app/services/llm/coach_agent.py` (strict tool tanımları, `CoachToolbox`); `tests/test_coach_llm.py::test_patient_id_is_bound_server_side` (modelin verdiği kimlik yok sayılır) |
| JWT httpOnly + SameSite=Lax (+ üretimde Secure) çerezde | ✅ | `frontend/src/app/api/auth/login/route.ts` |
| Token tarayıcı JS'ine verilmez; aynı kökenli proxy | ✅ | `frontend/src/app/api/proxy/[...path]/route.ts`, `frontend/src/lib/api.ts` |
| Service worker `/api/*` ve kişisel HTML'i önbelleğe almaz | ✅ | `frontend/public/sw.js` |
| Güvenlik başlıkları | ✅ | `frontend/next.config.ts` |
| Şifre hash (bcrypt), kısa ömürlü JWT (60 dk) | ✅ | `backend/app/security.py`, `.env.example` `JWT_EXPIRE_MINUTES` |
| Sırlar repoda yok | ✅ | `.gitignore`, `.env.example` |
| Guardrail olayları mesaj içeriği olmadan loglanır | ✅ | `coach_messages.guardrail_action` (eylem:kategori) |
| Push bildirim metninde sağlık verisi yok | ✅ | `backend/app/services/reminders.py` `build_payload` ilaç adı/doz içermez ("Saat HH:MM dozunu işaretlemeyi unutma."); `tests/test_push.py::test_reminder_due_sends_once` yükü birebir doğrular |
| Yalnızca sentetik veri | ✅ | `backend/app/seed.py`; CLAUDE.md |

## 3. Koç yanıtlarının veriye dayalı ve kaynaklı olması

| Kontrol | Durum | Kanıt |
|---|---|---|
| Uyum skoru deterministik, LLM hesaplamaz | ✅ | `backend/app/services/adherence.py`; `tests/test_adherence.py` |
| Uyum skoru doğrulama belgesi (elle hesaplanmış örnekler) | ✅ | [`docs/M4/uyum-skoru-dogrulama.md`](../M4/uyum-skoru-dogrulama.md) (doğrulama sırasında 3 hata bulundu ve düzeltildi: erken kaydedilen doz, banker yuvarlaması, saat dilimi) |
| Tool-use: `get_adherence`, `get_measurements`, `get_medications`, `search_education` | ✅ | `backend/app/services/llm/coach_agent.py`, `patient_data.py`; `tests/test_coach_llm.py::test_tool_loop_executes_tools_and_returns_structured_reply`, `test_invalid_tool_input_is_clamped_and_unknown_tool_is_error` |
| Structured output şeması (`message`, `risk_level`, `escalate_to_clinician`, `source_ids` → API'de `sources`) | ✅ | `backend/app/services/llm/coach_agent.py` `COACH_OUTPUT_SCHEMA`, `LlmCoachOutput`; `backend/app/services/coach.py` `CoachReply`; Swagger `POST /coach/chat` |
| RAG: kürate Türkçe eğitim içeriği + pgvector | ✅ | `backend/app/services/rag/`; 31 kayıt: `backend/app/data/education.yaml` (hepsi `reviewed: false`); `tests/test_rag.py` (embedding, arama, seed, tembel yeniden embed) |
| Sunucu tarafı kaynak doğrulama (uydurma kaynak atılır) | ✅ | `coach_agent.py` `CoachToolbox.grounded_sources`; `tests/test_coach_llm.py::test_ungrounded_sources_are_stripped` |
| Normal yanıtta kaynak bulunur | ✅ | `tests/test_api.py::test_coach_normal_reply_has_sources_and_history` |
| openFDA etiketi → eğitim içeriği | ✅ | `backend/app/services/openfda.py`, `backend/app/services/rag/ingest.py`; `tests/test_api.py::test_openfda_parse_with_mock_transport`; `tests/test_rag.py::test_ingest_label_summary_and_idempotent`, `test_ingest_endpoint` (doz bölümleri ve "NNN mg" ifadeleri ayıklanır) |
| Maliyet limiti (günlük istek/token, aylık USD, `llm_usage`) | ✅ | `backend/app/services/llm/budget.py`, `pricing.py`; migration `0004_llm_usage`; `tests/test_coach_llm.py::test_daily_request_budget_exceeded_falls_back`, `test_daily_token_budget_exceeded_falls_back`, `test_monthly_budget_exceeded_falls_back`, `test_usage_is_recorded_across_iterations` |
| LLM kullanılamazsa kural tabanlı yedek | ✅ | `coach.py` `RuleBasedCoach`; `tests/test_coach_llm.py::test_provider_exception_falls_back_to_rule_based`, `test_unusable_model_output_falls_back`, `test_loop_limit_forces_final_answer_then_falls_back` |

## 4. Hekim dashboard'unun klinik olarak anlamlı özet sunması

| Kontrol | Durum | Kanıt |
|---|---|---|
| Triyaj sıralaması (kritik uyarı → düşük uyum) | ✅ | `backend/app/routers/patients.py` `GET /patients`; `frontend/src/components/clinician/patient-list.tsx` |
| Uyum bandı (≥%80 / %60–79 / <%60), gerekçeli eşik | ✅ | `adherence.py` `adherence_band`; `final-rapor.md` §9 |
| 30 günlük ölçüm eğrileri + hedef referans çizgileri | ✅ | `frontend/src/components/clinician/measurement-charts.tsx` |
| Doz takvimi (30 gün, renk + simge) | ✅ | `frontend/src/components/clinician/dose-calendar.tsx` |
| Uyarı onaylama + zaman damgası | ✅ | `backend/app/routers/alerts.py` `POST /alerts/{id}/ack`; `frontend/src/components/clinician/alert-item.tsx` |
| Haftalık özet: deterministik sayılar + LLM anlatı + yedek | ✅ | `backend/app/services/summary.py`, `backend/app/services/llm/narrative.py`; `tests/test_summary_llm.py::test_llm_narrative_is_generated_cached_and_recorded`, `test_template_narrative_when_llm_unavailable`, `test_llm_narrative_blocked_by_guardrail_uses_template` |
| Dashboard tedavi önermez (bilgi özeti, karar destek değil) | ✅ | `final-rapor.md` §9 |

## 5. Kod kalitesi, test ve CI

| Kontrol | Durum | Kanıt |
|---|---|---|
| Lint (ruff, ESLint) ve tip denetimi (tsc) | ✅ | `.github/workflows/ci.yml` |
| Backend test sayısı | ✅ | 395 test geçer (M3: 102); `ruff` temiz; frontend `tsc`, `eslint`, `next build` geçer |
| Testler gerçek LLM'e istek atmaz (deterministik, ücretsiz CI) | ✅ | `backend/tests/conftest.py` (`ANTHROPIC_API_KEY` boşaltılır, `COACH_PROVIDER=rule`); `tests/test_coach_llm.py` sahte `FakeClient`; `tests/test_summary_llm.py` sahte anlatı istemcisi |
| Migration upgrade → downgrade → upgrade (PostgreSQL) | ✅ | `ci.yml` "Migration (PostgreSQL)" adımı |
| Docker imajları CI'da derlenir; root olmayan kullanıcı | ✅ | `ci.yml` `docker` işi; `backend/Dockerfile`, `frontend/Dockerfile` |
| Tek komut kurulum | ✅ | `docker-compose.yml`; `docs/M5/kurulum.md` |
| OpenAPI / Swagger | ✅ | `http://localhost:8000/docs` |

## 6. M5 teslim kalemleri

| Kalem | Durum | Kanıt |
|---|---|---|
| Final rapor — etik/güvenlik değerlendirmesi | ✅ | `docs/M5/final-rapor.md` §11 |
| Final rapor — sınırlılıklar | ✅ | `docs/M5/final-rapor.md` §12 |
| Kurulum talimatı | ✅ | `docs/M5/kurulum.md` |
| Demo videosu senaryosu | ✅ | `docs/M5/demo-senaryosu.md` |
| Demo videosu | ⏳ | <!-- KULLANICI: video kaydedilince bağlantıyı buraya ve README'ye ekleyin --> kayıt bekliyor |

## Ekran görüntüsü kanıtları

| Kontrol | Görsel kanıt |
|---|---|
| Acil belirti → 112 kartı, doz sorusu reddi, kaynaklı yanıt | [`06-hasta-koc.png`](../gorseller/06-hasta-koc.png) |
| Eşik dışı ölçüm uyarısı | [`03-hasta-olcum.png`](../gorseller/03-hasta-olcum.png) |
| Hekim uyarı listesi (kritik, bilgi, uyarı; onaylama) | [`09-hekim-uyarilar.png`](../gorseller/09-hekim-uyarilar.png) |
| Triyaj sıralı hasta listesi | [`07-hekim-hasta-listesi.png`](../gorseller/07-hekim-hasta-listesi.png) |
| Uyum bandı, 30 günlük doz takvimi, eğriler, haftalık özet | [`08-hekim-hasta-detay.png`](../gorseller/08-hekim-hasta-detay.png) |
| Bakım veren salt okunur görünüm | [`10-yakin-hastalar.png`](../gorseller/10-yakin-hastalar.png) |
| OpenAPI belgesi | [`11-swagger.png`](../gorseller/11-swagger.png) |

<table>
<tr>
<td align="center"><img src="../gorseller/06-hasta-koc.png" width="200" alt="Koç"><br><sub>Koç: guardrail</sub></td>
<td align="center"><img src="../gorseller/03-hasta-olcum.png" width="200" alt="Ölçüm"><br><sub>Eşik dışı ölçüm</sub></td>
<td align="center"><img src="../gorseller/10-yakin-hastalar.png" width="200" alt="Yakın"><br><sub>Salt okunur erişim</sub></td>
</tr>
</table>
