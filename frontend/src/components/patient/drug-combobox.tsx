"use client";

import { useEffect, useId, useState } from "react";
import { Loader2, Plus } from "lucide-react";
import { Input } from "@/components/ui/input";
import { drugCatalogApi } from "@/lib/api";
import type { DrugSearchResult } from "@/lib/types";
import { cn } from "@/lib/utils";

const DEBOUNCE_MS = 250;
const MIN_CHARS = 2;

export type DrugChoice = { kind: "catalog"; item: DrugSearchResult } | { kind: "unlisted" };

/**
 * İlaç adı için erişilebilir otomatik tamamlama (WAI-ARIA 1.2 combobox + listbox).
 * Yerel ilaç kataloğunda arar (GET /drugs/search); son seçenek her zaman "Listede yok, yine de ekle".
 * Yazmak önceki seçimi geçersiz kılar (onType), seçim onChoose ile bildirilir.
 */
export function DrugCombobox({
  id,
  value,
  onType,
  onChoose,
}: {
  id: string;
  value: string;
  onType: (text: string) => void;
  onChoose: (choice: DrugChoice, label: string) => void;
}) {
  const listId = useId();
  const [open, setOpen] = useState(false);
  const [results, setResults] = useState<DrugSearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [active, setActive] = useState(-1);
  const [query, setQuery] = useState("");

  const q = query.trim();
  const searchable = q.length >= MIN_CHARS;

  useEffect(() => {
    if (!searchable) return;
    const ctrl = new AbortController();
    const timer = window.setTimeout(async () => {
      setLoading(true);
      setFailed(false);
      try {
        const res = await drugCatalogApi.search(q, 8, ctrl.signal);
        setResults(res);
        setActive(res.length ? 0 : -1);
      } catch (e) {
        if ((e as Error).name !== "AbortError") {
          setResults([]);
          setFailed(true);
        }
      } finally {
        if (!ctrl.signal.aborted) setLoading(false);
      }
    }, DEBOUNCE_MS);
    return () => {
      window.clearTimeout(timer);
      ctrl.abort();
    };
  }, [q, searchable]);

  const shown = searchable ? results : [];
  // Son seçenek: listede yok, yine de ekle (yalnızca yazılmış bir ad varsa)
  const optionCount = shown.length + (q ? 1 : 0);
  const expanded = open && q.length > 0;

  function choose(index: number) {
    if (index < shown.length) {
      const item = shown[index];
      onChoose({ kind: "catalog", item }, item.matched_brand ?? item.display_name);
    } else {
      onChoose({ kind: "unlisted" }, value.trim());
    }
    setOpen(false);
    setQuery("");
    setActive(-1);
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (!expanded) setOpen(true);
      else setActive((a) => (optionCount ? (a + 1) % optionCount : -1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (expanded) setActive((a) => (optionCount ? (a <= 0 ? optionCount - 1 : a - 1) : -1));
    } else if (e.key === "Enter") {
      if (expanded && active >= 0 && active < optionCount) {
        e.preventDefault(); // formu göndermek yerine seçimi yap
        choose(active);
      }
    } else if (e.key === "Escape") {
      if (expanded) {
        e.preventDefault();
        setOpen(false);
      }
    }
  }

  const optionId = (i: number) => `${listId}-opt-${i}`;
  const status = !searchable
    ? "En az 2 harf yazın."
    : loading
      ? "Aranıyor…"
      : failed
        ? "Katalog araması şu an yapılamadı."
        : shown.length
          ? `${shown.length} ilaç bulundu. Ok tuşlarıyla seçin.`
          : "Katalogda eşleşen ilaç yok.";

  return (
    <div className="relative">
      <div className="relative">
        <Input
          id={id}
          role="combobox"
          aria-expanded={expanded}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={expanded && active >= 0 ? optionId(active) : undefined}
          autoComplete="off"
          className="h-10 pr-8"
          value={value}
          maxLength={120}
          placeholder="ör. Glifor, Concor, metformin"
          required
          onChange={(e) => {
            onType(e.target.value);
            setQuery(e.target.value);
            setOpen(true);
            if (e.target.value.trim().length < MIN_CHARS) {
              setResults([]);
              setActive(e.target.value.trim() ? 0 : -1);
            }
          }}
          onFocus={() => query && setOpen(true)}
          onBlur={() => setOpen(false)}
          onKeyDown={onKeyDown}
        />
        {loading && (
          <Loader2 className="absolute top-3 right-2.5 size-4 animate-spin text-muted-foreground" aria-hidden />
        )}
      </div>
      <p role="status" aria-live="polite" className="sr-only">
        {expanded ? status : ""}
      </p>
      <ul
        id={listId}
        role="listbox"
        aria-label="İlaç önerileri"
        hidden={!expanded}
        className="absolute z-20 mt-1 max-h-72 w-full overflow-y-auto rounded-lg border bg-popover p-1 text-sm shadow-md"
      >
        {shown.map((item, i) => (
          <li
            key={item.id}
            id={optionId(i)}
            role="option"
            aria-selected={i === active}
            className={cn("cursor-pointer rounded-md px-2.5 py-2", i === active && "bg-muted")}
            onMouseDown={(e) => e.preventDefault()} // odak giriş alanında kalsın
            onMouseEnter={() => setActive(i)}
            onClick={() => choose(i)}
          >
            <span className="font-medium">{item.matched_brand ?? item.display_name}</span>
            {item.matched_brand && <span className="text-muted-foreground"> · {item.display_name}</span>}
            {item.pharm_class && <span className="block text-xs text-muted-foreground">{item.pharm_class}</span>}
          </li>
        ))}
        {searchable && !loading && shown.length === 0 && (
          <li role="presentation" className="px-2.5 py-2 text-xs text-muted-foreground">
            {failed ? "Katalog araması şu an yapılamadı." : "Katalogda eşleşen ilaç bulunamadı."}
          </li>
        )}
        {q && (
          <li
            id={optionId(shown.length)}
            role="option"
            aria-selected={active === shown.length}
            className={cn(
              "flex cursor-pointer items-center gap-1.5 rounded-md border-t px-2.5 py-2 text-amber-900",
              active === shown.length && "bg-amber-50",
            )}
            onMouseDown={(e) => e.preventDefault()}
            onMouseEnter={() => setActive(shown.length)}
            onClick={() => choose(shown.length)}
          >
            <Plus className="size-4" aria-hidden /> Listede yok, yine de ekle: “{q}”
          </li>
        )}
      </ul>
    </div>
  );
}
