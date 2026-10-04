"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { BellRing, ExternalLink, Loader2, Phone, SendHorizontal, Siren } from "lucide-react";
import { EmergencyBanner } from "@/components/app-shell";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import type { CoachMessage } from "@/lib/types";
import { cn } from "@/lib/utils";
import { ErrorState, LoadingState } from "./states";
import { useAsync } from "./use-async";
import { errorMessage } from "./utils";

const SUGGESTIONS = [
  "Bu hafta ilaçlarımı nasıl aldım?",
  "Metformini unutmamak için ne yapabilirim?",
  "Açlık şekerim neden yüksek olabilir?",
];

export function CoachScreen() {
  const history = useAsync(useCallback(() => api.chatHistory(50), []));
  const { data: messages, setData: setMessages } = history;
  const [text, setText] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const tempId = useRef(-1);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, pending]);

  async function send(raw: string) {
    const message = raw.trim();
    if (!message || pending) return;
    setError(null);
    setPending(true);
    const now = new Date().toISOString();
    const mine: CoachMessage = {
      id: tempId.current--,
      sender: "patient",
      content: message,
      risk_level: null,
      escalate_to_clinician: false,
      sources: null,
      created_at: now,
    };
    setMessages((list) => [...(list ?? []), mine]);
    setText("");
    try {
      const reply = await api.chat(message);
      const coach: CoachMessage = {
        id: tempId.current--,
        sender: "coach",
        content: reply.message,
        risk_level: reply.risk_level,
        escalate_to_clinician: reply.escalate_to_clinician,
        sources: reply.sources,
        created_at: new Date().toISOString(),
      };
      setMessages((list) => [...(list ?? []), coach]);
    } catch (e) {
      setMessages((list) => list?.filter((m) => m.id !== mine.id) ?? list);
      setText(message);
      setError(errorMessage(e, "Mesaj gönderilemedi"));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="flex min-h-[calc(100dvh-12rem)] flex-col gap-3">
      <div>
        <h1 className="mb-2 text-xl font-semibold">Koç</h1>
        <EmergencyBanner />
      </div>

      <section aria-label="Sohbet geçmişi" aria-live="polite" className="flex-1 space-y-3">
        {history.loading && !messages && <LoadingState label="Sohbet yükleniyor…" />}
        {history.error && <ErrorState message={history.error} onRetry={history.reload} />}
        {messages && messages.length === 0 && (
          <Card>
            <CardContent className="space-y-3 text-sm">
              <p>
                Merhaba! İlaç düzeniniz, ölçümleriniz ve yaşam tarzınız hakkında soru sorabilirsiniz. Koç teşhis koymaz ve
                doz önermez.
              </p>
              <div className="flex flex-wrap gap-2">
                {SUGGESTIONS.map((s) => (
                  <Button key={s} variant="outline" size="sm" className="h-auto whitespace-normal py-1.5 text-left" onClick={() => void send(s)} disabled={pending}>
                    {s}
                  </Button>
                ))}
              </div>
            </CardContent>
          </Card>
        )}
        {messages?.map((m) => <ChatBubble key={m.id} message={m} />)}
        {pending && (
          <div role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" aria-hidden /> Koç yazıyor…
          </div>
        )}
        <div ref={bottomRef} />
      </section>

      {error && <ErrorState message={error} />}

      <form
        className="sticky bottom-20 flex items-end gap-2 rounded-xl border bg-background p-2 shadow-sm md:bottom-4"
        onSubmit={(e) => {
          e.preventDefault();
          void send(text);
        }}
      >
        <label htmlFor="coach-input" className="sr-only">
          Koça mesajınız
        </label>
        <Textarea
          id="coach-input"
          value={text}
          maxLength={2000}
          rows={1}
          placeholder="Mesajınızı yazın…"
          className="max-h-40 min-h-10 resize-none border-0 shadow-none focus-visible:ring-0"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              void send(text);
            }
          }}
        />
        <Button type="submit" size="icon-lg" aria-label="Gönder" disabled={pending || !text.trim()}>
          {pending ? <Loader2 className="animate-spin" /> : <SendHorizontal />}
        </Button>
      </form>
    </div>
  );
}

function ChatBubble({ message }: { message: CoachMessage }) {
  const isPatient = message.sender === "patient";
  const emergency = message.risk_level === "emergency";
  const time = new Date(message.created_at).toLocaleTimeString("tr-TR", { hour: "2-digit", minute: "2-digit" });

  if (isPatient) {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-teal-700 px-3 py-2 text-sm text-white">
          <p className="whitespace-pre-wrap">{message.content}</p>
          <p className="mt-1 text-right text-[10px] text-teal-100">{time}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex justify-start">
      <div className="max-w-[90%] space-y-2">
        {emergency && (
          <div role="alert" className="rounded-xl border-2 border-red-600 bg-red-50 p-3 text-red-900">
            <p className="flex items-center gap-2 font-semibold">
              <Siren className="size-5" aria-hidden /> Acil durum olabilir
            </p>
            <p className="mt-1 text-sm">Belirtileriniz acil değerlendirme gerektirebilir. Beklemeyin, hemen arayın.</p>
            <a
              href="tel:112"
              className="mt-3 flex h-12 items-center justify-center gap-2 rounded-lg bg-red-600 text-base font-semibold text-white hover:bg-red-700 focus-visible:ring-3 focus-visible:ring-red-300 focus-visible:outline-none"
            >
              <Phone className="size-5" aria-hidden /> 112&apos;yi ara
            </a>
          </div>
        )}
        <div
          className={cn(
            "rounded-2xl rounded-bl-sm bg-background px-3 py-2 text-sm ring-1 ring-foreground/10",
            emergency && "ring-red-300",
          )}
        >
          <p className="whitespace-pre-wrap">{message.content}</p>
          {message.sources && message.sources.length > 0 && (
            <div className="mt-2 border-t pt-2">
              <p className="text-xs font-medium text-muted-foreground">Kaynaklar</p>
              <ul className="mt-1 space-y-0.5">
                {message.sources.map((s, i) => (
                  <li key={`${s.title}-${i}`} className="text-xs">
                    {s.url ? (
                      <a
                        href={s.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1 text-teal-700 underline underline-offset-2"
                      >
                        {s.title}
                        <ExternalLink className="size-3" aria-hidden />
                        <span className="sr-only">(yeni sekmede açılır)</span>
                      </a>
                    ) : (
                      <span className="text-muted-foreground">{s.title}</span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
          <p className="mt-1 text-[10px] text-muted-foreground">{time}</p>
        </div>
        {message.escalate_to_clinician && (
          <p className="flex items-center gap-1.5 text-xs font-medium text-amber-800">
            <BellRing className="size-3.5" aria-hidden /> Hekiminize bildirildi
          </p>
        )}
      </div>
    </div>
  );
}
