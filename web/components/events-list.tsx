"use client";

import { CircleCheck, CircleX, Inbox, MoreHorizontal, Volume2 } from "lucide-react";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuGroup, DropdownMenuItem, DropdownMenuLabel, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Empty, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { Spinner } from "@/components/ui/spinner";
import { api, fileUrl } from "@/lib/api";
import { dateTime } from "@/lib/format";
import type { CrusherView, EventRow, Label } from "@/lib/types";
import { useAction } from "@/lib/use-action";
import { cn } from "@/lib/utils";

const SEVERITY = {
  alert: "before:bg-red-500",
  warning: "before:bg-amber-500",
  info: "before:bg-border",
};

/** Список событий; для событий по семенам — «сделать кадр эталоном» (кадр без разметки сохранён). */
export function EventsList({ events, crushers, compact, canEdit }: { events: EventRow[]; crushers?: CrusherView[]; compact?: boolean; canEdit?: boolean }) {
  const [zoom, setZoom] = useState<string | null>(null);
  const { busy, run } = useAction();

  const makeSample = (e: EventRow, label: Label) => {
    const crusher = crushers?.find((c) => c.id === e.camera_id);
    if (!crusher) return;
    const labels = Object.fromEntries(crusher.seeds.zones.filter((z) => z.kind !== "motion").map((z) => [z.id, label]));
    run(
      `${e.uid}-${label}`,
      () => api.post(`/crushers/${crusher.id}/samples`, { labels, event_uid: e.uid }),
      `Кадр стал эталоном «${label === "full" ? "семена есть" : "пусто"}»`,
    );
  };

  if (!events.length)
    return (
      <Empty className="py-10">
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <Inbox />
          </EmptyMedia>
          <EmptyTitle>Событий пока нет</EmptyTitle>
        </EmptyHeader>
      </Empty>
    );

  return (
    <>
      <ul className="flex flex-col gap-2">
        {events.map((e) => {
          const crusher = crushers?.find((c) => c.id === e.camera_id);
          const canSample = canEdit && !compact && e.frame && e.type === "SeedsLevelChanged" && crusher?.seeds.zones.some((z) => z.kind !== "motion");
          return (
            <li
              key={e.uid}
              className={cn(
                "relative flex gap-3 overflow-hidden rounded-lg border bg-card p-3 pl-4 before:absolute before:inset-y-0 before:left-0 before:w-1",
                SEVERITY[e.severity],
              )}
            >
              {e.snapshot && (
                <button type="button" className="shrink-0 cursor-zoom-in" onClick={() => setZoom(fileUrl(e.snapshot!))}>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={fileUrl(e.snapshot)} alt="" loading="lazy" className={cn("rounded-md object-cover", compact ? "h-11 w-20" : "h-20 w-36")} />
                </button>
              )}
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-baseline gap-x-2">
                  <span className="font-medium">{e.title}</span>
                  <span className="text-xs text-muted-foreground">{dateTime(e.ts)}</span>
                </div>
                <div className="whitespace-pre-line text-sm text-muted-foreground">{e.message}</div>
                {!compact && (e.deliveries.length > 0 || e.speech) && (
                  <div className="mt-2 flex flex-wrap gap-1">
                    {e.deliveries.map((d, i) => (
                      <Badge key={i} variant={d.ok ? "outline" : "destructive"} title={d.error ?? "доставлено"}>
                        {d.ok ? <CircleCheck className="text-emerald-600" /> : <CircleX />}
                        {d.channel}
                        {!d.ok && d.error ? `: ${d.error.slice(0, 60)}` : ""}
                      </Badge>
                    ))}
                    {e.speech && (
                      <Badge variant="secondary">
                        <Volume2 /> «{e.speech}»
                      </Badge>
                    )}
                  </div>
                )}
              </div>
              {canSample && (
                <DropdownMenu>
                  <DropdownMenuTrigger render={<Button variant="ghost" size="icon-sm" className="-mr-1 -mt-1 shrink-0" aria-label="Действия" disabled={busy !== null} />}>
                    {busy?.startsWith(e.uid) ? <Spinner /> : <MoreHorizontal />}
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end" className="w-60">
                    <DropdownMenuGroup>
                      <DropdownMenuLabel>Распознала неверно? Сделать кадр эталоном</DropdownMenuLabel>
                      <DropdownMenuItem onClick={() => makeSample(e, "full")}>
                        <CircleCheck className="text-emerald-600" /> На кадре семена есть
                      </DropdownMenuItem>
                      <DropdownMenuItem onClick={() => makeSample(e, "empty")}>
                        <CircleX className="text-red-600" /> На кадре пусто
                      </DropdownMenuItem>
                    </DropdownMenuGroup>
                  </DropdownMenuContent>
                </DropdownMenu>
              )}
            </li>
          );
        })}
      </ul>
      <Dialog open={zoom !== null} onOpenChange={(open) => !open && setZoom(null)}>
        <DialogContent className="max-w-[min(96vw,1400px)] p-2 sm:max-w-[min(96vw,1400px)]">
          <DialogTitle className="sr-only">Снимок события</DialogTitle>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          {zoom && <img src={zoom} alt="" className="w-full rounded-lg" />}
        </DialogContent>
      </Dialog>
    </>
  );
}
