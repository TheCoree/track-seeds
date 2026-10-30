"use client";

import { CircleAlert, CircleCheck, CircleX, Lock, Megaphone, Play, Save, Send } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { AdminOnly, useApp } from "@/components/app-shell";
import { SchemaForm, SwitchField } from "@/components/schema-form";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
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

  if (!settings || !meta) return <Skeleton className="h-96 max-w-4xl rounded-xl" />;

  // все сохранённые динамики обеих дробилок — для проверки голоса
  const targets: SpeakerTarget[] = ((overview?.crushers ?? []) as Crusher[]).flatMap((c) => c.speakers.map((s) => ({ crusherId: c.id, id: s.id, name: `${c.name} → ${s.name}` })));

  return (
    <div className="flex max-w-4xl flex-col gap-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Уведомления и голос</h1>
        <p className="text-sm text-muted-foreground">Общие для обеих дробилок. Динамики — у каждой дробилки на вкладке «Оповещения».</p>
      </div>
      <NotifyCard settings={settings} setSettings={setSettings} />
      <VoiceCard initial={settings.voice} targets={targets} />
      <Alert>
        <Lock />
        <AlertTitle>{meta.auth ? "Вход по логину и паролю" : "Вход без пароля — только с этого компьютера"}</AlertTitle>
        <AlertDescription>
          {meta.auth
            ? "Клиенты, приглашения и журнал входов — в «Клиенты». Забыли пароль admin — смените WEB_PASSWORD в .env и перезапустите сервер."
            : "Заведите разработчика в «Мой аккаунт» — тогда можно открыть сайт в сеть и приглашать клиентов."}
        </AlertDescription>
      </Alert>
    </div>
  );
}

function TextField({ label, hint, ...props }: { label: string; hint?: string } & React.ComponentProps<typeof Input>) {
  const id = useId();
  return (
    <Field>
      <FieldLabel htmlFor={id}>{label}</FieldLabel>
      <Input id={id} {...props} />
      {hint && <FieldDescription>{hint}</FieldDescription>}
    </Field>
  );
}

function NotifyCard({ settings, setSettings }: { settings: SettingsData; setSettings: (s: SettingsData) => void }) {
  const { meta, reloadOverview } = useApp();
  const [results, setResults] = useState<Record<string, string | null> | null>(null);
  const { busy, run } = useAction();
  const ntfy = settings.ntfy;
  const channels = settings.notify.channels;
  const setNtfy = (patch: Partial<SettingsData["ntfy"]>) => setSettings({ ...settings, ntfy: { ...ntfy, ...patch } });
  const toggle = (name: string, on: boolean) =>
    setSettings({ ...settings, notify: { ...settings.notify, channels: on ? [...channels, name] : channels.filter((c) => c !== name) } });

  const save = () =>
    run(
      "save",
      async () => {
        setSettings(await api.put<SettingsData>("/settings", { notify: settings.notify, ntfy }));
        reloadOverview();
      },
      "Уведомления сохранены",
    );
  const test = () =>
    run("test", async () => {
      const r = await api.post<{ results: Record<string, string | null> }>("/notify/test");
      setResults(r.results);
    });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Уведомления на телефон</CardTitle>
        <CardDescription>Приложение ntfy: подпишитесь на топик — и тревоги придут пушем со снимком.</CardDescription>
        <CardAction className="flex gap-2">
          <Button variant="outline" disabled={busy === "test"} onClick={test}>
            {busy === "test" ? <Spinner /> : <Send />} Тест
          </Button>
          <Button disabled={busy === "save"} onClick={save}>
            {busy === "save" ? <Spinner /> : <Save />} Сохранить
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        {settings.notify_error && (
          <Alert variant="destructive">
            <CircleAlert />
            <AlertDescription>{settings.notify_error}</AlertDescription>
          </Alert>
        )}
        {results && (
          <div className="flex flex-wrap gap-2">
            {Object.entries(results).map(([name, error]) => (
              <Badge key={name} variant={error ? "destructive" : "outline"}>
                {error ? <CircleX /> : <CircleCheck className="text-emerald-600" />}
                {error ? `${name}: ${error}` : `${name}: доставлено`}
              </Badge>
            ))}
          </div>
        )}
        <div className="grid gap-4 sm:grid-cols-2">
          {meta?.channels.map((name) => (
            <SwitchField
              key={name}
              checked={channels.includes(name)}
              onChange={(on) => toggle(name, on)}
              label={name === "ntfy" ? "ntfy — пуш на телефон" : name === "log" ? "Журнал сервера (log)" : name}
            />
          ))}
        </div>
        {channels.includes("ntfy") && (
          <div className="grid gap-5 sm:grid-cols-2">
            <TextField label="Сервер ntfy" hint="https://ntfy.sh или свой сервер" value={ntfy.url} onChange={(e) => setNtfy({ url: e.target.value })} />
            <TextField
              label="Топик"
              hint="На ntfy.sh топик — это пароль: длинный и случайный"
              value={ntfy.topic}
              onChange={(e) => setNtfy({ topic: e.target.value })}
            />
            <TextField
              label="Токен (для своего сервера)"
              type="password"
              autoComplete="new-password"
              placeholder={ntfy.has_token ? "сохранён" : "tk_…"}
              value={ntfy.token ?? ""}
              onChange={(e) => setNtfy({ token: e.target.value || null })}
            />
            <Field>
              <FieldLabel>Или логин / пароль</FieldLabel>
              <div className="flex gap-2">
                <Input value={ntfy.username ?? ""} placeholder="логин" onChange={(e) => setNtfy({ username: e.target.value || null })} />
                <Input
                  type="password"
                  autoComplete="new-password"
                  placeholder={ntfy.has_password ? "сохранён" : "пароль"}
                  value={ntfy.password ?? ""}
                  onChange={(e) => setNtfy({ password: e.target.value || null })}
                />
              </div>
            </Field>
          </div>
        )}
        <SchemaForm schema={meta?.schemas.notify} value={settings.notify} onChange={(notify) => setSettings({ ...settings, notify: notify as SettingsData["notify"] })} level="main" />
      </CardContent>
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
    <>
      <Card>
        <CardHeader>
          <CardTitle>Голос</CardTitle>
          <CardDescription>Сохраняется сразу. Новый голос скачивается один раз (~60 МБ); на сервер попадает вместе с Docker-образом.</CardDescription>
          <CardAction>
            <span className="flex items-center gap-1.5 text-sm text-muted-foreground">
              {saving === "saving" ? <Spinner /> : saving === "saved" ? <CircleCheck className="size-4 text-emerald-600" /> : <CircleX className="size-4 text-destructive" />}
              {saving === "saving" ? "сохраняю…" : saving === "saved" ? "сохранено" : "не сохранено"}
            </span>
          </CardAction>
        </CardHeader>
        <CardContent className="flex flex-col gap-5">
          <VoiceStatusLine />
          {!meta.engines.piper && !meta.engines.espeak && (
            <Alert variant="destructive">
              <CircleAlert />
              <AlertDescription>Нет синтеза речи: установите piper-tts (pip) или espeak-ng.</AlertDescription>
            </Alert>
          )}
          <SchemaForm
            schema={meta.schemas.voice}
            value={voice}
            onChange={change}
            level="main"
            options={{
              engine: [
                ["auto", "Автоматически"],
                ["piper", "piper (естественный)"],
                ["espeak", "espeak-ng (роботизированный)"],
              ],
              voice: Object.entries(meta.voices),
            }}
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Проверка голоса</CardTitle>
          <CardDescription>Напишите что угодно — так это прозвучит.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <Field>
            <FieldLabel htmlFor={textId}>Текст</FieldLabel>
            <Textarea id={textId} rows={3} value={text} placeholder="Например: Внимание! В дробилке нет зерна." onChange={(e) => setText(e.target.value)} />
          </Field>
          <div className="flex flex-wrap items-center gap-2">
            <Button disabled={!text.trim() || !ready || player.playing !== null} onClick={() => player.play(text)}>
              {player.playing === text.trim() ? <Spinner /> : <Play />} Прослушать здесь
            </Button>
            {!ready && <span className="text-xs text-muted-foreground">дождитесь сохранения голоса</span>}
          </div>
          {targets.length > 0 && (
            <div className="flex flex-col gap-3 border-t pt-4">
              <div className="text-sm font-medium">Сказать на динамиках</div>
              <div className="grid gap-3 sm:grid-cols-2">
                {targets.map((t) => (
                  <SwitchField
                    key={key(t)}
                    checked={chosen.includes(key(t))}
                    onChange={(on) => setChosen(on ? [...chosen, key(t)] : chosen.filter((x) => x !== key(t)))}
                    label={t.name}
                  />
                ))}
              </div>
              <Button
                variant="outline"
                className="self-start"
                disabled={!text.trim() || !chosen.length || !ready || speakerSay.busy}
                onClick={() => speakerSay.say(text, targets.filter((t) => chosen.includes(key(t))))}
              >
                {speakerSay.busy ? <Spinner /> : <Megaphone />} Сказать на выбранных
              </Button>
            </div>
          )}
        </CardContent>
      </Card>
    </>
  );
}
