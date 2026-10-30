"use client";

import { CircleAlert, CircleCheck, CircleX, Megaphone, Play, Save, Send, Undo2 } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { AdminOnly, useApp } from "@/components/app-shell";
import { SchemaForm, SettingRow, SettingsList, SwitchRow } from "@/components/schema-form";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { InputGroup, InputGroupAddon, InputGroupButton, InputGroupInput } from "@/components/ui/input-group";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { toast } from "@/components/ui/toast";
import { type SpeakerTarget, useSpeakerSay, useVoicePlayer, VoiceStatusLine } from "@/components/voice";
import { api } from "@/lib/api";
import type { Crusher, SettingsData } from "@/lib/types";
import { useAction } from "@/lib/use-action";

export default function SettingsPage() {
  return (
    <AdminOnly>
      <Settings />
    </AdminOnly>
  );
}

function Settings() {
  const { meta, overview } = useApp();
  const [settings, setSettings] = useState<SettingsData | null>(null);

  useEffect(() => {
    api.get<SettingsData>("/settings").then(setSettings);
  }, []);

  if (!settings || !meta) return <Skeleton className="mx-auto h-96 w-full max-w-3xl rounded-xl" />;

  // все сохранённые динамики обеих дробилок — для проверки голоса
  const targets: SpeakerTarget[] = ((overview?.crushers ?? []) as Crusher[]).flatMap((c) => c.speakers.map((s) => ({ crusherId: c.id, id: s.id, name: `${c.name} · ${s.name}` })));

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-5">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Уведомления и голос</h1>
        <p className="text-sm text-muted-foreground">Общие для обеих дробилок. Когда сообщать и динамики — у каждой дробилки на вкладке «Оповещения».</p>
      </div>
      <NotifyCard initial={settings} />
      <VoiceCard initial={settings.voice} targets={targets} />
    </div>
  );
}

function NotifyCard({ initial }: { initial: SettingsData }) {
  const { meta, reloadOverview } = useApp();
  const [saved, setSaved] = useState(initial);
  const [settings, setSettings] = useState(initial);
  const [results, setResults] = useState<Record<string, string | null> | null>(null);
  const { busy, run } = useAction();
  const ntfy = settings.ntfy;
  const channels = settings.notify.channels;
  const ids = { url: useId(), topic: useId(), token: useId(), user: useId() };
  const pick = (s: SettingsData) => JSON.stringify({ notify: s.notify, ntfy: s.ntfy });
  const dirty = pick(settings) !== pick(saved);
  const setNtfy = (patch: Partial<SettingsData["ntfy"]>) => setSettings({ ...settings, ntfy: { ...ntfy, ...patch } });
  const toggle = (name: string, on: boolean) =>
    setSettings({ ...settings, notify: { ...settings.notify, channels: on ? [...channels, name] : channels.filter((c) => c !== name) } });

  const save = () =>
    run(
      "save",
      async () => {
        const next = await api.put<SettingsData>("/settings", { notify: settings.notify, ntfy });
        setSettings(next);
        setSaved(next);
        reloadOverview();
      },
      "Уведомления сохранены",
    );
  const test = () =>
    run("test", async () => {
      const r = await api.post<{ results: Record<string, string | null> }>("/notify/test");
      setResults(r.results);
    });
  const pushOn = channels.includes("ntfy");

  return (
    <Card>
      <CardHeader>
        <CardTitle>Уведомления на телефон</CardTitle>
        <CardDescription>Приложение ntfy: подпишитесь на топик — тревоги придут пушем со снимком.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {saved.notify_error && (
          <Alert variant="destructive">
            <CircleAlert />
            <AlertDescription>{saved.notify_error}</AlertDescription>
          </Alert>
        )}
        <SettingsList>
          {meta?.channels.includes("ntfy") && <SwitchRow label="Пуш на телефон (ntfy)" checked={pushOn} onChange={(on) => toggle("ntfy", on)} />}
          {pushOn && (
            <>
              <SettingRow label="Сервер" hint="https://ntfy.sh или свой" htmlFor={ids.url} wide>
                <Input id={ids.url} value={ntfy.url} onChange={(e) => setNtfy({ url: e.target.value })} />
              </SettingRow>
              <SettingRow label="Топик" hint="На ntfy.sh топик — это пароль: длинный и случайный" htmlFor={ids.topic} wide>
                <Input id={ids.topic} value={ntfy.topic} onChange={(e) => setNtfy({ topic: e.target.value })} />
              </SettingRow>
              <SettingRow label="Токен" hint="Только для своего сервера" htmlFor={ids.token} wide>
                <Input
                  id={ids.token}
                  type="password"
                  autoComplete="new-password"
                  placeholder={ntfy.has_token ? "сохранён" : "tk_…"}
                  value={ntfy.token ?? ""}
                  onChange={(e) => setNtfy({ token: e.target.value || null })}
                />
              </SettingRow>
              <SettingRow label="Или логин и пароль" hint="Тоже для своего сервера" htmlFor={ids.user} wide>
                <div className="flex w-full gap-2">
                  <Input id={ids.user} value={ntfy.username ?? ""} placeholder="логин" onChange={(e) => setNtfy({ username: e.target.value || null })} />
                  <Input
                    type="password"
                    autoComplete="new-password"
                    placeholder={ntfy.has_password ? "сохранён" : "пароль"}
                    value={ntfy.password ?? ""}
                    onChange={(e) => setNtfy({ password: e.target.value || null })}
                  />
                </div>
              </SettingRow>
            </>
          )}
          <SchemaForm schema={meta?.schemas.notify} value={settings.notify} onChange={(notify) => setSettings({ ...settings, notify: notify as SettingsData["notify"] })} level="main" />
        </SettingsList>
        {results && (
          <div className="flex flex-wrap gap-2">
            {Object.entries(results).map(([name, error]) => (
              <Badge key={name} variant={error ? "destructive" : "outline"}>
                {error ? <CircleX /> : <CircleCheck className="text-emerald-600" />}
                {name === "log" ? "журнал" : name}: {error ?? "доставлено"}
              </Badge>
            ))}
          </div>
        )}
      </CardContent>
      <CardFooter className="flex flex-wrap gap-2 border-t">
        <Button variant={dirty ? "default" : "outline"} disabled={!dirty || busy === "save"} onClick={save}>
          {busy === "save" ? <Spinner /> : <Save />} {dirty ? "Сохранить" : "Сохранено"}
        </Button>
        {dirty && (
          <Button variant="ghost" onClick={() => setSettings(saved)}>
            <Undo2 /> Отменить
          </Button>
        )}
        <span className="flex-1" />
        <Button variant="outline" disabled={dirty || busy === "test"} title={dirty ? "Сначала сохраните" : undefined} onClick={test}>
          {busy === "test" ? <Spinner /> : <Send />} Отправить тест
        </Button>
      </CardFooter>
    </Card>
  );
}

/** Голос: сохраняется сразу при изменении — нечего «забыть сохранить». */
function VoiceCard({ initial, targets }: { initial: Record<string, unknown>; targets: SpeakerTarget[] }) {
  const { meta } = useApp();
  const [voice, setVoice] = useState(initial);
  const [saving, setSaving] = useState<"saved" | "saving" | "error">("saved");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [text, setText] = useState("");
  const [chosen, setChosen] = useState<string[]>([]);
  const player = useVoicePlayer();
  const speakerSay = useSpeakerSay();
  const textId = useId();

  const change = (next: Record<string, unknown>) => {
    setVoice(next);
    setSaving("saving");
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(async () => {
      try {
        await api.put("/settings", { voice: next });
        setSaving("saved");
      } catch (e) {
        setSaving("error");
        toast.add({ title: "Голос не сохранён", description: e instanceof Error ? e.message : String(e), type: "error" });
      }
    }, 500);
  };

  if (!meta) return null;
  const ready = saving === "saved";
  const key = (t: SpeakerTarget) => `${t.crusherId}/${t.id}`;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Голос</CardTitle>
        <CardDescription>Сохраняется сразу. Новый голос скачивается один раз (~60 МБ).</CardDescription>
        <CardAction>
          <span className="flex items-center gap-1.5 text-sm text-muted-foreground">
            {saving === "saving" ? <Spinner /> : saving === "saved" ? <CircleCheck className="size-4 text-emerald-600" /> : <CircleX className="size-4 text-destructive" />}
            {saving === "saving" ? "сохраняю…" : saving === "saved" ? "сохранено" : "не сохранено"}
          </span>
        </CardAction>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <VoiceStatusLine />
        {!meta.engines.piper && !meta.engines.espeak && (
          <Alert variant="destructive">
            <CircleAlert />
            <AlertDescription>Нет синтеза речи: установите piper-tts (pip) или espeak-ng.</AlertDescription>
          </Alert>
        )}
        <SettingsList>
          <SchemaForm
            schema={meta.schemas.voice}
            value={voice}
            onChange={change}
            level="main"
            only={["voice", "speed", "volume", "chime", "engine"]}
            options={{
              engine: [
                ["auto", "Автоматически"],
                ["piper", "piper (естественный)"],
                ["espeak", "espeak-ng (роботизированный)"],
              ],
              voice: Object.entries(meta.voices),
            }}
          />
        </SettingsList>
      </CardContent>
      <CardFooter className="flex flex-col items-stretch gap-3 border-t">
        <Label htmlFor={textId}>Проверить, как звучит</Label>
        <InputGroup>
          <InputGroupInput
            id={textId}
            value={text}
            placeholder="Внимание! В дробилке нет зерна."
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && text.trim() && ready && player.play(text)}
          />
          <InputGroupAddon align="inline-end">
            <InputGroupButton
              variant="secondary"
              aria-disabled={!text.trim() || !ready || player.playing !== null}
              className="aria-disabled:opacity-50"
              onClick={() => text.trim() && ready && player.playing === null && player.play(text)}
            >
              {player.playing === text.trim() ? <Spinner /> : <Play />} Прослушать
            </InputGroupButton>
          </InputGroupAddon>
        </InputGroup>
        {targets.length > 0 && (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            {targets.map((t) => (
              <Label key={key(t)} className="font-normal">
                <Checkbox checked={chosen.includes(key(t))} onCheckedChange={(on) => setChosen(on ? [...chosen, key(t)] : chosen.filter((x) => x !== key(t)))} />
                {t.name}
              </Label>
            ))}
            <Button
              variant="outline"
              size="sm"
              disabled={!text.trim() || !chosen.length || !ready || speakerSay.busy}
              onClick={() => speakerSay.say(text, targets.filter((t) => chosen.includes(key(t))))}
            >
              {speakerSay.busy ? <Spinner /> : <Megaphone />} Сказать на динамиках
            </Button>
          </div>
        )}
      </CardFooter>
    </Card>
  );
}
