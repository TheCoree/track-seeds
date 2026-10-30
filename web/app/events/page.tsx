"use client";

import { ChevronsDown } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { useApp } from "@/components/app-shell";
import { EventsList } from "@/components/events-list";
import { Button } from "@/components/ui/button";
import { Field, FieldLabel } from "@/components/ui/field";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Skeleton } from "@/components/ui/skeleton";
import { Toggle } from "@/components/ui/toggle";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { api } from "@/lib/api";
import { EVENT_KINDS, type EventKind, KIND_ICON, KIND_ORDER } from "@/lib/event-kinds";
import type { EventRow } from "@/lib/types";
import { cn } from "@/lib/utils";

const PAGE = 50;
// выбранная кнопка фильтра — заметнее, чем просто серый фон
const PRESSED = "aria-pressed:border-foreground/40 aria-pressed:bg-muted";

/** Фильтр — в адресе страницы (?crusher=…&kind=empty,stopped): обновили страницу или поделились ссылкой — тот же. */
function fromUrl(): { crusher: string; kinds: EventKind[] } {
  if (typeof window === "undefined") return { crusher: "", kinds: [] };
  const params = new URLSearchParams(window.location.search);
  const kinds = (params.get("kind") ?? "").split(",").filter((k): k is EventKind => k in EVENT_KINDS);
  return { crusher: params.get("crusher") ?? "", kinds };
}

export default function EventsPage() {
  const { overview, isAdmin } = useApp();
  const [crusher, setCrusher] = useState("");
  const [kinds, setKinds] = useState<EventKind[]>([]);
  const [ready, setReady] = useState(false);
  const [counts, setCounts] = useState<Partial<Record<EventKind, number>>>({});
  const [events, setEvents] = useState<EventRow[] | null>(null);
  const [more, setMore] = useState(true);
  const lastSeq = overview?.last_seq;
  const crusherId = useId();
  const filterKey = `${crusher}|${kinds.join(",")}`;

  useEffect(() => {
    const initial = fromUrl();
    setCrusher(initial.crusher);
    setKinds(initial.kinds);
    setReady(true);
  }, []);

  useEffect(() => {
    if (!ready) return;
    const params = new URLSearchParams();
    if (crusher) params.set("crusher", crusher);
    if (kinds.length) params.set("kind", kinds.join(","));
    const search = params.size ? `?${params}` : "";
    if (search !== window.location.search) window.history.replaceState(null, "", `${window.location.pathname}${search}`);
  }, [ready, filterKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const query = (before?: number) => {
    const params = new URLSearchParams({ limit: String(PAGE) });
    if (crusher) params.set("camera_id", crusher);
    if (before) params.set("before", String(before));
    for (const k of kinds) params.append("kind", k);
    return api.get<EventRow[]>(`/events?${params}`);
  };

  useEffect(() => {
    setEvents(null);
    setMore(true);
  }, [filterKey]);

  // первая страница: при смене фильтров и когда приходят новые события (у показанных обновляются доставки)
  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    query().then((rows) => {
      if (cancelled) return;
      setEvents((current) => {
        if (!current) return rows;
        const fresh = new Map(rows.map((e) => [e.uid, e]));
        const known = new Set(current.map((e) => e.uid));
        return [...rows.filter((e) => !known.has(e.uid)), ...current.map((e) => fresh.get(e.uid) ?? e)];
      });
      setMore((m) => m && rows.length === PAGE);
    });
    return () => {
      cancelled = true;
    };
  }, [ready, filterKey, lastSeq]); // eslint-disable-line react-hooks/exhaustive-deps

  // числа на кнопках — за весь журнал выбранной дробилки
  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    api.get<Partial<Record<EventKind, number>>>(`/events/kinds${crusher ? `?camera_id=${encodeURIComponent(crusher)}` : ""}`).then((c) => {
      if (!cancelled) setCounts(c);
    });
    return () => {
      cancelled = true;
    };
  }, [ready, crusher, lastSeq]);

  const loadMore = async () => {
    if (!events?.length) return;
    const rows = await query(events[events.length - 1].seq);
    setEvents([...events, ...rows]);
    setMore(rows.length === PAGE);
  };

  // кнопки — только для видов, которые есть в журнале (и выбранных, даже если их стало 0)
  const shown = KIND_ORDER.filter((k) => (counts[k] ?? 0) > 0 || kinds.includes(k)).filter((k) => isAdmin || !EVENT_KINDS[k].adminOnly);
  const total = Object.values(counts).reduce((sum, n) => sum + (n ?? 0), 0);

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">События</h1>
        <p className="text-sm text-muted-foreground">
          {isAdmin ? "Тревоги, остановки и запуски, связь с камерами — с доставкой уведомлений и озвученными фразами" : "Тревоги, остановки и запуски, связь с камерами"}
        </p>
      </div>
      <div className="flex flex-col gap-3">
        <Field className="w-auto self-start">
          <FieldLabel htmlFor={crusherId}>Дробилка</FieldLabel>
          <NativeSelect id={crusherId} value={crusher} onChange={(e) => setCrusher(e.target.value)}>
            <NativeSelectOption value="">Обе дробилки</NativeSelectOption>
            {overview?.crushers.map((c) => (
              <NativeSelectOption key={c.id} value={c.id}>
                {c.name}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </Field>
        <div role="group" aria-label="Какие события показать" className="flex flex-wrap items-center gap-1.5">
          <Toggle variant="outline" size="sm" className={PRESSED} pressed={kinds.length === 0} onPressedChange={() => setKinds([])}>
            Все <Count n={total} />
          </Toggle>
          {/* contents: кнопки видов — в одном ряду с «Все», переносятся вместе */}
          <ToggleGroup multiple variant="outline" size="sm" spacing={1.5} className="contents" value={kinds} onValueChange={(next) => setKinds(KIND_ORDER.filter((k) => next.includes(k)))}>
            {shown.map((k) => {
              const { label, icon: Icon, tone } = EVENT_KINDS[k];
              return (
                <ToggleGroupItem key={k} value={k} aria-label={label} className={PRESSED}>
                  <Icon className={cn(KIND_ICON[tone])} />
                  {label}
                  <Count n={counts[k] ?? 0} />
                </ToggleGroupItem>
              );
            })}
          </ToggleGroup>
        </div>
      </div>
      {events === null ? (
        <Skeleton className="h-64 rounded-xl" />
      ) : (
        <EventsList events={events} crushers={overview?.crushers} canEdit={isAdmin} emptyText={kinds.length ? "Таких событий нет" : undefined} />
      )}
      {events && more && events.length > 0 && (
        <Button variant="outline" className="self-center" onClick={loadMore}>
          <ChevronsDown /> Показать ещё
        </Button>
      )}
    </div>
  );
}

function Count({ n }: { n: number }) {
  return <span className="text-xs tabular-nums text-muted-foreground">{n}</span>;
}
