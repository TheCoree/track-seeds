"use client";

import { ChevronsDown } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { useApp } from "@/components/app-shell";
import { EventsList } from "@/components/events-list";
import { Button } from "@/components/ui/button";
import { Field, FieldLabel } from "@/components/ui/field";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import type { EventRow } from "@/lib/types";

const TYPES: [string, string, string[]][] = [
  ["", "Все события", []],
  ["seeds", "Семена и работа дробилки", ["SeedsLevelChanged"]],
  ["camera", "Связь с камерой", ["CameraOffline", "CameraOnline"]],
];

export default function EventsPage() {
  const { overview, isAdmin } = useApp();
  const [crusher, setCrusher] = useState("");
  const [type, setType] = useState("");
  const [events, setEvents] = useState<EventRow[] | null>(null);
  const [more, setMore] = useState(true);
  const lastSeq = overview?.last_seq;
  const ids = { crusher: useId(), type: useId() };

  const query = (before?: number) => {
    const params = new URLSearchParams({ limit: "50" });
    if (crusher) params.set("camera_id", crusher);
    if (before) params.set("before", String(before));
    for (const t of TYPES.find(([id]) => id === type)?.[2] ?? []) params.append("type", t);
    return api.get<EventRow[]>(`/events?${params}`);
  };

  useEffect(() => {
    setEvents(null);
    setMore(true);
  }, [crusher, type]);

  // первая страница: при смене фильтров и когда приходят новые события (у показанных обновляются доставки)
  useEffect(() => {
    let cancelled = false;
    query().then((rows) => {
      if (cancelled) return;
      setEvents((current) => {
        if (!current) return rows;
        const fresh = new Map(rows.map((e) => [e.uid, e]));
        const known = new Set(current.map((e) => e.uid));
        return [...rows.filter((e) => !known.has(e.uid)), ...current.map((e) => fresh.get(e.uid) ?? e)];
      });
      setMore((m) => m && rows.length === 50);
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [crusher, type, lastSeq]);

  const loadMore = async () => {
    if (!events?.length) return;
    const rows = await query(events[events.length - 1].seq);
    setEvents([...events, ...rows]);
    setMore(rows.length === 50);
  };

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">События</h1>
        <p className="text-sm text-muted-foreground">
          {isAdmin ? "Тревоги, остановки и запуски, связь с камерами — с доставкой уведомлений и озвученными фразами" : "Тревоги, остановки и запуски, связь с камерами"}
        </p>
      </div>
      <div className="flex flex-wrap gap-4">
        <Field className="w-auto">
          <FieldLabel htmlFor={ids.crusher}>Дробилка</FieldLabel>
          <NativeSelect id={ids.crusher} value={crusher} onChange={(e) => setCrusher(e.target.value)}>
            <NativeSelectOption value="">Обе дробилки</NativeSelectOption>
            {overview?.crushers.map((c) => (
              <NativeSelectOption key={c.id} value={c.id}>
                {c.name}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </Field>
        <Field className="w-auto">
          <FieldLabel htmlFor={ids.type}>Что показать</FieldLabel>
          <NativeSelect id={ids.type} value={type} onChange={(e) => setType(e.target.value)}>
            {TYPES.map(([id, label]) => (
              <NativeSelectOption key={id} value={id}>
                {label}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </Field>
      </div>
      {events === null ? <Skeleton className="h-64 rounded-xl" /> : <EventsList events={events} crushers={overview?.crushers} canEdit={isAdmin} />}
      {events && more && events.length > 0 && (
        <Button variant="outline" className="self-center" onClick={loadMore}>
          <ChevronsDown /> Показать ещё
        </Button>
      )}
    </div>
  );
}
