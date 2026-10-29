"use client";

import { BellRing, ChevronDown, Play, Save, SlidersHorizontal, Undo2 } from "lucide-react";
import Link from "next/link";
import { useId, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import { crusherBody } from "@/lib/crusher";
import type { Crusher, Meta, SeedsConfig, Speaker } from "@/lib/types";
import { useAction } from "@/lib/use-action";
import { SchemaForm, SwitchField } from "./schema-form";
import { SpeakersEditor } from "./speakers-editor";
import { useVoicePlayer, VoiceStatusLine } from "./voice";

const PHRASES: [keyof SeedsConfig, string, string][] = [
  ["say_empty", "Когда семена закончились", "Внимание! В дробилке нет зерна."],
  ["say_refill", "Когда семена досыпали", "Семена досыпали, спасибо."],
  ["say_unknown", "Когда камера не видит желоб", "Камера не видит желоб дробилки, проверьте камеру."],
];

/** Вкладка «Оповещения»: когда сообщать, что говорить, куда. */
export function AlertsForm({ crusher, meta, onSaved }: { crusher: Crusher; meta: Meta | null; onSaved: () => void }) {
  const [settings, setSettings] = useState<SeedsConfig>(crusher.seeds);
  const [speakers, setSpeakers] = useState<Speaker[]>(crusher.speakers);
  const [notify, setNotify] = useState(crusher.notify);
  const { busy, run } = useAction();
  const player = useVoicePlayer();
  const set = (patch: Partial<SeedsConfig>) => setSettings({ ...settings, ...patch });
  const hoursId = useId();
  const timesId = useId();

  const strip = (s: SeedsConfig) => ({ ...s, zones: 0, samples: 0 });
  const dirty =
    JSON.stringify(strip(settings)) !== JSON.stringify(strip(crusher.seeds)) || JSON.stringify(speakers) !== JSON.stringify(crusher.speakers) || notify !== crusher.notify;

  const save = () =>
    run(
      "save",
      async () => {
        const saved = await api.put<Crusher>(`/crushers/${crusher.id}`, crusherBody(crusher, { notify, speakers, seeds: settings }));
        setSpeakers(saved.speakers);
        onSaved();
      },
      "Оповещения сохранены",
    );
  const reset = () => {
    setSettings(crusher.seeds);
    setSpeakers(crusher.speakers);
    setNotify(crusher.notify);
  };
  const testAlarm = () =>
    run("alarm", () => api.post(`/crushers/${crusher.id}/test-alarm`), "Проверочная тревога отправлена — смотрите телефон, динамики и журнал событий");

  return (
    <div className="flex flex-col gap-4">
      <div className="sticky top-16 z-10 flex flex-wrap items-center gap-2 rounded-xl border bg-background/95 p-2 shadow-sm backdrop-blur">
        <Button disabled={!dirty || busy === "save"} onClick={save}>
          {busy === "save" ? <Spinner /> : <Save />}
          {dirty ? "Сохранить изменения" : "Всё сохранено"}
        </Button>
        {dirty && (
          <Button variant="ghost" onClick={reset}>
            <Undo2 /> Отменить
          </Button>
        )}
        <span className="flex-1" />
        <Button variant="outline" disabled={dirty || busy === "alarm"} onClick={testAlarm} title={dirty ? "Сначала сохраните изменения" : undefined}>
          {busy === "alarm" ? <Spinner /> : <BellRing />}
          Проверить оповещение
        </Button>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Когда сообщать</CardTitle>
            <CardDescription>Защита от ложных тревог: состояние должно держаться всё указанное время.</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-5">
            <SwitchField
              checked={notify}
              onChange={setNotify}
              label="Уведомления на телефон (ntfy)"
              hint="Сервер и канал — в «Уведомления и голос». Журнал событий пишется всегда."
            />
            <SchemaForm schema={meta?.schemas.seeds} value={settings} onChange={(v) => setSettings(v as SeedsConfig)} level="main" />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Что говорить</CardTitle>
            <CardDescription>Свои фразы. Пустое поле — в этом случае молчать. «Прослушать» — в этом браузере.</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-5">
            <VoiceStatusLine />
            {PHRASES.map(([key, label, example]) => {
              const text = String(settings[key] ?? "");
              return (
                <Field key={key}>
                  <FieldLabel>{label}</FieldLabel>
                  <div className="flex gap-2">
                    <Textarea rows={2} value={text} placeholder={`например: ${example}`} onChange={(e) => set({ [key]: e.target.value } as Partial<SeedsConfig>)} />
                    <Button variant="outline" size="icon" className="shrink-0" aria-label="Прослушать" disabled={!text.trim() || player.playing !== null} onClick={() => player.play(text)}>
                      {player.playing === text.trim() ? <Spinner /> : <Play />}
                    </Button>
                  </div>
                </Field>
              );
            })}
            <div className="grid gap-5 sm:grid-cols-2">
              <Field>
                <FieldLabel htmlFor={timesId}>Сколько раз повторять</FieldLabel>
                <NativeSelect id={timesId} className="w-full" value={String(settings.say_times)} onChange={(e) => set({ say_times: Number(e.target.value) })}>
                  {[1, 2, 3].map((n) => (
                    <NativeSelectOption key={n} value={String(n)}>
                      {n}
                    </NativeSelectOption>
                  ))}
                </NativeSelect>
              </Field>
              <Field>
                <FieldLabel htmlFor={hoursId}>Часы голосовых оповещений</FieldLabel>
                <Input id={hoursId} value={settings.voice_hours} placeholder="круглосуточно" onChange={(e) => set({ voice_hours: e.target.value })} />
                <FieldDescription>Например 08:00-20:00</FieldDescription>
              </Field>
            </div>
            <SwitchField checked={settings.say_reminders} onChange={(v) => set({ say_reminders: v })} label="Повторять при напоминаниях" hint="«Всё ещё пусто» тоже озвучивается" />
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Куда говорить голосом</CardTitle>
          <CardDescription>
            Динамик IP-камеры Hikvision и/или динамик у сервера. Голос и общие уведомления — в{" "}
            <Link href="/settings" className="underline underline-offset-2">
              «Уведомления и голос»
            </Link>
            .
          </CardDescription>
        </CardHeader>
        <CardContent>
          <SpeakersEditor crusherId={crusher.id} value={speakers} saved={crusher.speakers} onChange={setSpeakers} />
        </CardContent>
      </Card>

      <Collapsible>
        <Card>
          <CardHeader>
            <CollapsibleTrigger render={<button type="button" className="group flex w-full items-center gap-2 text-left" />}>
              <SlidersHorizontal className="size-4 text-muted-foreground" />
              <CardTitle className="flex-1">Тонкая настройка распознавания</CardTitle>
              <ChevronDown className="size-4 text-muted-foreground transition-transform group-data-[panel-open]:rotate-180" />
            </CollapsibleTrigger>
            <CardDescription>Пороги оценки, движения привода и строгость без эталона «пусто». Обычно менять не нужно.</CardDescription>
          </CardHeader>
          <CollapsibleContent>
            <CardContent className="pt-2">
              <SchemaForm schema={meta?.schemas.seeds} value={settings} onChange={(v) => setSettings(v as SeedsConfig)} level="advanced" />
            </CardContent>
          </CollapsibleContent>
        </Card>
      </Collapsible>
    </div>
  );
}
