"""LLM katmanı (Claude): tool-use koç, hekim özeti anlatısı, maliyet limiti.

Modüller:
- coach_agent: ClaudeCoachProvider (tool-use döngüsü + JSON şema çıktısı + kaynak doğrulama)
- narrative: haftalık özet anlatısı (LLM veya deterministik şablon, günlük önbellek)
- budget: bütçe kontrolü ve LlmUsage kaydı
- pricing: model fiyat tablosu
- patient_data: araçların döndürdüğü kimliksiz veri özetleri
- client: Anthropic istemcisi ve ortak çağrı yardımcıları

Döngüsel içe aktarmayı önlemek için burada alt modüller içe aktarılmaz.
"""
