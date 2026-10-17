"use client";

import { useEffect, useId, useRef, useState } from "react";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import type { DrugSearchHit } from "@/lib/types";
import { cn } from "@/lib/utils";

const GROUP_LABEL: Record<DrugSearchHit["indication_group"], string> = {
  diabetes: "Diyabet",
  hypertension: "Tansiyon",
  other: "Diğer",
};

/**
 * İlaç adı girişi + katalogdan otomatik tamamlama (WAI-ARIA combobox, liste açılır kutusu).
 * Yazdıkça GET /drugs/search sorgulanır; seçim `onSelect` ile bildirilir. Elle yazmak seçimi kaldırır.
 */
export function DrugCombobox({
  id,
  value,
  onChange,
  onSelect,
}: {
  id: string;
  value: string;
  onChange: (name: string) => void;
  onSelect: (hit: DrugSearchHit) => void;
}) {
  const listId = useId();
  const [hits, setHits] = useState<DrugSearchHit[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [query, setQuery] = useState<string | null>(null); // yalnızca kullanıcı yazınca arama yapılır
  const blurTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const q = query?.trim() ?? "";
    if (q.length < 2) {
      setHits([]);
      return;
    }
    const ctrl = new AbortController();
    const t = setTimeout(() => {
      api
        .searchDrugs(q, { signal: ctrl.signal })
        .then((res) => {
          setHits(res);
          setActive(-1);
          setOpen(true);
        })
        .catch(() => {
          if (!ctrl.signal.aborted) setHits([]); // arama hatası formu engellemez; elle yazılabilir
        });
    }, 250);
    return () => {
      clearTimeout(t);
      ctrl.abort();
    };
  }, [query]);

  useEffect(() => () => {
    if (blurTimer.current) clearTimeout(blurTimer.current);
  }, []);

  function choose(hit: DrugSearchHit) {
    onSelect(hit);
    setQuery(null);
    setHits([]);
    setOpen(false);
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (!open || hits.length === 0) {
      if (e.key === "ArrowDown" && hits.length > 0) setOpen(true);
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => (i + 1) % hits.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => (i <= 0 ? hits.length - 1 : i - 1));
    } else if (e.key === "Enter" && active >= 0) {
      e.preventDefault();
      choose(hits[active]);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  }

  const expanded = open && hits.length > 0;

  return (
    <div className="relative">
      <Input
        id={id}
        className="h-10"
        value={value}
        maxLength={120}
        placeholder="ör. Glifor"
        required
        role="combobox"
        autoComplete="off"
        aria-autocomplete="list"
        aria-expanded={expanded}
        aria-controls={listId}
        aria-activedescendant={expanded && active >= 0 ? `${listId}-${active}` : undefined}
        onChange={(e) => {
          onChange(e.target.value);
          setQuery(e.target.value);
        }}
        onKeyDown={onKeyDown}
        onFocus={() => hits.length > 0 && setOpen(true)}
        onBlur={() => {
          blurTimer.current = setTimeout(() => setOpen(false), 120);
        }}
      />
      <ul
        id={listId}
        role="listbox"
        aria-label="İlaç önerileri"
        hidden={!expanded}
        className="absolute z-20 mt-1 max-h-72 w-full overflow-auto rounded-lg border bg-popover p-1 text-sm shadow-md"
      >
        {hits.map((h, i) => (
          <li
            key={`${h.id}-${h.brand ?? ""}`}
            id={`${listId}-${i}`}
            role="option"
            aria-selected={i === active}
            className={cn(
              "flex cursor-pointer items-center justify-between gap-2 rounded-md px-2 py-2",
              i === active ? "bg-muted" : "hover:bg-muted/60",
            )}
            onMouseDown={(e) => e.preventDefault()} // seçimden önce input odağı kaybolmasın
            onClick={() => choose(h)}
            onMouseEnter={() => setActive(i)}
          >
            <span>
              <span className="font-medium">{h.brand ?? h.generic_name}</span>
              {h.brand && <span className="text-muted-foreground"> · {h.generic_name}</span>}
            </span>
            <span className="shrink-0 text-xs text-muted-foreground">{GROUP_LABEL[h.indication_group]}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
