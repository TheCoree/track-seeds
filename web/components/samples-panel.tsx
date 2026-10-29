"use client";

import { Camera, CircleCheck, CircleX, ImageUp, Lock, Repeat, Trash2 } from "lucide-react";
import { useRef, useState } from "react";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Spinner } from "@/components/ui/spinner";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { api } from "@/lib/api";
import { sampleUrl } from "@/lib/crusher";
import { dateTime } from "@/lib/format";
import type { Crusher, Label, SeedSample } from "@/lib/types";
import { useAction } from "@/lib/use-action";
import { cn } from "@/lib/utils";

const LABEL_TEXT: Record<Label, string> = { full: "семена есть", empty: "пусто" };

/** Вкладка «Эталоны»: вшитые кадры (только просмотр) и снятые на месте. */
export function SamplesPanel({ crusher, onSaved }: { crusher: Crusher; onSaved: () => void }) {
  const [uploadLabel, setUploadLabel] = useState<Label>("empty");
  const [zoom, setZoom] = useState<SeedSample | null>(null);
  const file = useRef<HTMLInputElement>(null);
  const { busy, run } = useAction();
  const hopper = crusher.seeds.zones.filter((z) => z.kind !== "motion");
  const zoneName = (id: string) => crusher.seeds.zones.find((z) => z.id === id)?.name ?? "?";
  const samples = [...crusher.seeds.samples].sort((a, b) => Number(a.builtin) - Number(b.builtin) || b.ts - a.ts);
  const live = crusher.enabled && crusher.status.connected;
  const running = crusher.status.seeds?.running;

  const capture = (label: Label) =>
    run(
      `capture-${label}`,
      async () => {
        await api.post(`/crushers/${crusher.id}/samples`, { labels: Object.fromEntries(hopper.map((z) => [z.id, label])) });
        onSaved();
      },
      `Эталон «${LABEL_TEXT[label]}» сохранён`,
    );

  const upload = (f: File) =>
    run(
      "upload",
      async () => {
        await api.upload(`/crushers/${crusher.id}/samples/upload?label=${uploadLabel}`, f);
        onSaved();
      },
      `Кадр добавлен как эталон «${LABEL_TEXT[uploadLabel]}»`,
    );

  const relabel = (sample: SeedSample) =>
    run("relabel", async () => {
      const labels = Object.fromEntries(Object.entries(sample.labels).map(([z, l]) => [z, l === "full" ? "empty" : "full"]));
      await api.patch(`/crushers/${crusher.id}/samples/${sample.id}`, { labels });
      onSaved();
    });

  const remove = (sample: SeedSample) =>
    run(
      `remove-${sample.id}`,
      async () => {
        await api.del(`/crushers/${crusher.id}/samples/${sample.id}`);
        onSaved();
      },
      "Эталон удалён",
    );

  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Снять эталон сейчас</CardTitle>
            <CardDescription>Текущий кадр с камеры: что сейчас в желобе? Снимайте, когда дробилка работает — пока стоит, семена не проверяются.</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <div className="grid grid-cols-2 gap-2">
              <Button size="lg" className="bg-emerald-600 text-white hover:bg-emerald-700" disabled={!live || busy !== null} onClick={() => capture("full")}>
                {busy === "capture-full" ? <Spinner /> : <CircleCheck />}
                Семена есть
              </Button>
              <Button size="lg" variant="destructive" disabled={!live || busy !== null} onClick={() => capture("empty")}>
                {busy === "capture-empty" ? <Spinner /> : <CircleX />}
                Сейчас пусто
              </Button>
            </div>
            {!live && <p className="text-xs text-destructive">Камера не подключена — снимать нечего.</p>}
            {live && running === false && <p className="text-xs text-amber-700">Дробилка сейчас стоит — такой кадр может не походить на работу.</p>}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Кадр из архива</CardTitle>
            <CardDescription>Например, пустой желоб из записи регистратора: та же камера, тот же ракурс.</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            <NativeSelect value={uploadLabel} onChange={(e) => setUploadLabel(e.target.value as Label)}>
              <NativeSelectOption value="empty">на кадре — пусто</NativeSelectOption>
              <NativeSelectOption value="full">на кадре — семена есть</NativeSelectOption>
            </NativeSelect>
            <Button variant="outline" disabled={busy === "upload"} onClick={() => file.current?.click()}>
              {busy === "upload" ? <Spinner /> : <ImageUp />}
              Загрузить файл…
            </Button>
            <input
              ref={file}
              type="file"
              accept="image/jpeg,image/png"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                e.target.value = "";
                if (f) upload(f);
              }}
            />
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Эталоны ({samples.length})</CardTitle>
          <CardDescription>
            <Lock className="mr-1 inline size-3.5" />— вшиты в программу (сняты при полном желобе и работающей дробилке). Свои можно переотметить и удалить.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {samples.map((sample) => (
              <div key={sample.id} className="flex flex-col overflow-hidden rounded-xl border bg-card">
                <button type="button" className="relative cursor-zoom-in" onClick={() => setZoom(sample)}>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={sampleUrl(crusher, sample.id)} alt="" loading="lazy" className="aspect-video w-full object-cover" />
                  {sample.builtin && (
                    <Badge variant="secondary" className="absolute left-2 top-2 bg-background/90">
                      <Lock /> из программы
                    </Badge>
                  )}
                </button>
                <div className="flex flex-1 flex-col gap-2 p-3">
                  <div className="text-xs text-muted-foreground">{dateTime(sample.ts)}</div>
                  <div className="flex flex-wrap gap-1">
                    {Object.entries(sample.labels).map(([zoneId, label]) => (
                      <Badge key={zoneId} variant="outline" className={cn(label === "full" ? "text-emerald-700 dark:text-emerald-300" : "text-red-700 dark:text-red-300")}>
                        {label === "full" ? <CircleCheck /> : <CircleX />}
                        {zoneName(zoneId)}: {LABEL_TEXT[label]}
                      </Badge>
                    ))}
                  </div>
                  {!sample.builtin && (
                    <div className="mt-auto flex gap-1">
                      <Tooltip>
                        <TooltipTrigger render={<Button variant="ghost" size="sm" disabled={busy === "relabel"} onClick={() => relabel(sample)} />}>
                          <Repeat /> Поменять
                        </TooltipTrigger>
                        <TooltipContent>«есть» ↔ «пусто»</TooltipContent>
                      </Tooltip>
                      <AlertDialog>
                        <AlertDialogTrigger render={<Button variant="ghost" size="sm" className="text-destructive" />}>
                          <Trash2 /> Удалить
                        </AlertDialogTrigger>
                        <AlertDialogContent>
                          <AlertDialogHeader>
                            <AlertDialogTitle>Удалить эталон?</AlertDialogTitle>
                            <AlertDialogDescription>Кадр от {dateTime(sample.ts)} больше не будет участвовать в сравнении.</AlertDialogDescription>
                          </AlertDialogHeader>
                          <AlertDialogFooter>
                            <AlertDialogCancel>Отмена</AlertDialogCancel>
                            <AlertDialogAction variant="destructive" onClick={() => remove(sample)}>
                              Удалить
                            </AlertDialogAction>
                          </AlertDialogFooter>
                        </AlertDialogContent>
                      </AlertDialog>
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      <Dialog open={zoom !== null} onOpenChange={(open) => !open && setZoom(null)}>
        <DialogContent className="max-w-[min(96vw,1400px)] p-2 sm:max-w-[min(96vw,1400px)]">
          <DialogTitle className="sr-only">Эталон</DialogTitle>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          {zoom && <img src={sampleUrl(crusher, zoom.id)} alt="" className="w-full rounded-lg" />}
        </DialogContent>
      </Dialog>
    </div>
  );
}
