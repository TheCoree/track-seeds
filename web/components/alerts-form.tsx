"use client";

import { BellRing, ChevronDown, Play, Save, SlidersHorizontal, Undo2 } from "lucide-react";
import Link from "next/link";
import { useId, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { InputGroup, InputGroupAddon, InputGroupButton, InputGroupInput } from "@/components/ui/input-group";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { api } from "@/lib/api";
import { crusherBody } from "@/lib/crusher";
import type { Crusher, Meta, SeedsConfig, Speaker } from "@/lib/types";
import { useAction } from "@/lib/use-action";
import { SchemaForm, SettingRow, SettingsList, SwitchRow } from "./schema-form";
import { Segmented, type SegmentOption } from "./segmented";
import { SpeakersEditor } from "./speakers-editor";
import { useVoicePlayer, VoiceStatusLine } from "./voice";

const PHRASES: [keyof SeedsConfig, string, string][] = [
  ["say_empty", "Семена закончились", "Внимание! В дробилке нет зерна."],
  ["say_refill", "Семена досыпали", "Семена досыпали, спасибо."],
  ["say_unknown", "Камера не видит желоб", "Камера не видит желоб дробилки, проверьте камеру."],
  ["say_stopped", "Дробилка остановилась", "Внимание! Дробилка остановилась."],
];

const TIMES: SegmentOption<string>[] = [
  { value: "1", label: "1" },
  { value: "2", label: "2" },
  { value: "3", label: "3" },
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
  const phraseId = useId();

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
    <div className="flex max-w-3xl flex-col gap-4">
      <div className="sticky top-16 z-10 flex flex-wrap items-center gap-2 rounded-xl border bg-background/95 p-2 shadow-sm backdrop-blur">
        <Button variant={dirty ? "default" : "outline"} disabled={!dirty || busy === "save"} onClick={save}>
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

      <Card>
        <CardHeader>
          <CardTitle>Когда сообщать</CardTitle>
          <CardDescription>Защита от ложных тревог: состояние должно держаться всё указанное время.</CardDescription>
        </CardHeader>
        <CardContent>
          <SettingsList>
            <SwitchRow checked={notify} onChange={setNotify} label="Уведомления на телефон" hint="Канал ntfy — в «Уведомления и голос». Журнал событий пишется всегда." />
            <SchemaForm schema={meta?.schemas.seeds} value={settings} onChange={(v) => setSettings(v as SeedsConfig)} level="main" />
          </SettingsList>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Что говорить</CardTitle>
          <CardDescription>Пустая фраза — в этом случае молчать.</CardDescription>
          <CardAction>
            <VoiceStatusLine />
          </CardAction>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {PHRASES.map(([key, label, example]) => {
            const text = String(settings[key] ?? "");
            return (
              <div key={key} className="flex flex-col gap-1.5">
                <Label htmlFor={`${phraseId}-${key}`}>{label}</Label>
                <InputGroup>
                  <InputGroupInput id={`${phraseId}-${key}`} value={text} placeholder={example} onChange={(e) => set({ [key]: e.target.value } as Partial<SeedsConfig>)} />
                  <InputGroupAddon align="inline-end">
                    <InputGroupButton
                      size="icon-xs"
                      aria-label="Прослушать"
                      title="Прослушать в браузере"
                      aria-disabled={!text.trim() || player.playing !== null}
                      className="aria-disabled:opacity-40"
                      onClick={() => text.trim() && player.playing === null && player.play(text)}
                    >
                      {player.playing === text.trim() ? <Spinner /> : <Play />}
                    </InputGroupButton>
                  </InputGroupAddon>
                </InputGroup>
              </div>
            );
          })}
          <SettingsList className="mt-2 border-t pt-4">
            <SettingRow label="Сколько раз повторять фразу">
              <Segmented label="Сколько раз повторять" value={String(settings.say_times)} onChange={(v) => set({ say_times: Number(v) })} options={TIMES} />
            </SettingRow>
            <SettingRow label="Часы голосовых оповещений" hint="Например 08:00-20:00; пусто — круглосуточно" htmlFor={hoursId}>
              <Input id={hoursId} value={settings.voice_hours} placeholder="круглосуточно" onChange={(e) => set({ voice_hours: e.target.value })} />
            </SettingRow>
            <SwitchRow checked={settings.say_reminders} onChange={(v) => set({ say_reminders: v })} label="Озвучивать напоминания" hint="«Всё ещё пусто» тоже говорится вслух" />
          </SettingsList>
        </CardContent>
      </Card>

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
              <SettingsList>
                <SchemaForm schema={meta?.schemas.seeds} value={settings} onChange={(v) => setSettings(v as SeedsConfig)} level="advanced" />
              </SettingsList>
            </CardContent>
          </CollapsibleContent>
        </Card>
      </Collapsible>
    </div>
  );
}
