"use client";

import { Copy, Download, KeyRound, LogOut, MonitorSmartphone, Plus, Save, ShieldCheck, ShieldOff, Smartphone, Trash2, UserPlus } from "lucide-react";
import { useCallback, useEffect, useId, useState } from "react";
import { useApp } from "@/components/app-shell";
import { useInstall } from "@/components/pwa";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { api } from "@/lib/api";
import { copyText, deviceName } from "@/lib/clipboard";
import { dateTime } from "@/lib/format";
import { scriptableWidget } from "@/lib/scriptable";
import type { Me, SessionRow, WidgetKey } from "@/lib/types";
import { useAction } from "@/lib/use-action";

export default function AccountPage() {
  const { me } = useApp();
  if (!me) return null;
  return (
    <div className="flex max-w-3xl flex-col gap-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Мой аккаунт</h1>
        <p className="text-sm text-muted-foreground">Пароль, вход по коду, устройства, приложение на телефон и виджет</p>
      </div>
      {me.open ? (
        <SetupCard />
      ) : (
        <>
          <ProfileCard me={me} />
          <InstallCard />
          <WidgetCard me={me} />
          <PasswordCard />
          <TwoFactorCard me={me} />
          <SessionsCard />
        </>
      )}
    </div>
  );
}

/** Разработчика ещё нет: вход без пароля только с этого компьютера. */
function SetupCard() {
  const [username, setUsername] = useState("admin");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const { busy, run } = useAction();
  const ids = { user: useId(), name: useId(), pass: useId(), repeat: useId() };
  const save = () =>
    run("setup", async () => {
      if (password !== repeat) throw new Error("Пароли не совпадают");
      await api.post("/auth/setup", { username, name, password });
      window.location.reload();
    });
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <UserPlus className="size-4" /> Завести разработчика
        </CardTitle>
        <CardDescription>
          Сейчас вход без пароля — только с этого компьютера. С логином и паролем можно открыть сайт в сеть и приглашать клиентов. На сервере то же самое делает WEB_PASSWORD в .env (логин admin).
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4 sm:grid-cols-2">
        <Field>
          <FieldLabel htmlFor={ids.user}>Логин</FieldLabel>
          <Input id={ids.user} autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} />
        </Field>
        <Field>
          <FieldLabel htmlFor={ids.name}>Имя</FieldLabel>
          <Input id={ids.name} placeholder="Разработчик" value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field>
          <FieldLabel htmlFor={ids.pass}>Пароль</FieldLabel>
          <Input id={ids.pass} type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <Field>
          <FieldLabel htmlFor={ids.repeat}>Ещё раз</FieldLabel>
          <Input id={ids.repeat} type="password" autoComplete="new-password" value={repeat} onChange={(e) => setRepeat(e.target.value)} />
        </Field>
      </CardContent>
      <CardFooter className="border-t">
        <Button disabled={busy === "setup" || password.length < 8} onClick={save}>
          {busy === "setup" ? <Spinner /> : <Save />} Завести и войти
        </Button>
      </CardFooter>
    </Card>
  );
}

function ProfileCard({ me }: { me: Me }) {
  return (
    <Card size="sm">
      <CardHeader>
        <CardTitle>{me.name}</CardTitle>
        <CardDescription>
          Логин <span className="font-medium text-foreground">{me.username}</span> · {me.role === "admin" ? "разработчик: всё, включая настройки и клиентов" : "клиент: просмотр дробилок, события и отчёты"}
        </CardDescription>
        <CardAction>
          {me.totp ? (
            <Badge variant="outline" className="text-emerald-700 dark:text-emerald-300">
              <ShieldCheck /> вход по коду
            </Badge>
          ) : (
            <Badge variant="secondary">
              <ShieldOff /> без кода
            </Badge>
          )}
        </CardAction>
      </CardHeader>
    </Card>
  );
}

function InstallCard() {
  const { available, installed, install, secure } = useInstall();
  return (
    <Card size="sm">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Smartphone className="size-4" /> Приложение на телефон
        </CardTitle>
        <CardDescription>
          {installed
            ? "Уже открыто как приложение."
            : "Значок на экране «Домой»: открывается без адресной строки, как обычное приложение."}
        </CardDescription>
        {available && (
          <CardAction>
            <Button size="sm" onClick={install}>
              <Download /> Установить
            </Button>
          </CardAction>
        )}
      </CardHeader>
      {!installed && (
        <CardContent className="grid gap-2 text-sm text-muted-foreground sm:grid-cols-2">
          <p>
            <span className="font-medium text-foreground">iPhone:</span> Safari → кнопка «Поделиться» → «На экран „Домой“».
          </p>
          <p>
            <span className="font-medium text-foreground">Android:</span> Chrome → меню ⋮ → «Установить приложение».
          </p>
          {!secure && <p className="sm:col-span-2">Полностью (с работой без связи) — только по адресу https://…, по локальному адресу — как закладка.</p>}
        </CardContent>
      )}
    </Card>
  );
}

function WidgetCard({ me }: { me: Me }) {
  const [keys, setKeys] = useState<WidgetKey[] | null>(null);
  const [name, setName] = useState("iPhone");
  const [script, setScript] = useState<string | null>(null);
  const { busy, run } = useAction();
  const server = me.public_url || (typeof window !== "undefined" ? window.location.origin : "");
  const local = /^https?:\/\/(localhost|127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(server);
  const reload = useCallback(() => api.get<WidgetKey[]>("/auth/widgets").then(setKeys), []);
  useEffect(() => {
    reload();
  }, [reload]);

  const create = () =>
    run("create", async () => {
      const { token } = await api.post<{ token: string }>("/auth/widgets", { name });
      setScript(scriptableWidget(server, token));
      reload();
    });
  const revoke = (key: WidgetKey) => run(`revoke-${key.id}`, () => api.del(`/auth/widgets/${key.id}`).then(reload), "Ключ отозван — виджет перестанет обновляться");

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <MonitorSmartphone className="size-4" /> Виджет на iPhone
        </CardTitle>
        <CardDescription>Состояние дробилок и итоги дня на экране «Домой» и экране блокировки — через бесплатное приложение Scriptable.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {local && (
          <Alert>
            <AlertTitle>Виджет будет работать только в сети завода</AlertTitle>
            <AlertDescription>Сайт открыт по локальному адресу {server}. Чтобы виджет обновлялся с мобильного интернета, разработчик задаёт внешний адрес (WEB_PUBLIC_URL).</AlertDescription>
          </Alert>
        )}
        <div className="flex flex-wrap items-end gap-2">
          <Field className="w-56">
            <FieldLabel>Название устройства</FieldLabel>
            <Input value={name} maxLength={60} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Button disabled={busy === "create"} onClick={create}>
            {busy === "create" ? <Spinner /> : <Plus />} Создать виджет
          </Button>
        </div>
        {keys && keys.length > 0 && (
          <ul className="divide-y rounded-lg border">
            {keys.map((key) => (
              <li key={key.id} className="flex items-center gap-3 px-3 py-2 text-sm">
                <KeyRound className="size-4 shrink-0 text-muted-foreground" />
                <span className="min-w-0 flex-1 truncate">{key.name}</span>
                <span className="text-xs text-muted-foreground">{key.last_used ? `обновлялся ${dateTime(key.last_used)}` : "ещё не обновлялся"}</span>
                <Button variant="ghost" size="sm" className="text-destructive" disabled={busy === `revoke-${key.id}`} onClick={() => revoke(key)}>
                  <Trash2 /> Отозвать
                </Button>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
      <Dialog open={script !== null} onOpenChange={(open) => !open && setScript(null)}>
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>Скрипт виджета</DialogTitle>
            <DialogDescription>Показывается один раз — в нём ключ доступа. Потеряли — создайте новый, старый отзовите.</DialogDescription>
          </DialogHeader>
          <ol className="list-decimal space-y-1 pl-5 text-sm">
            <li>Установите Scriptable из App Store.</li>
            <li>Скопируйте скрипт → в Scriptable «+» → вставьте → назовите «Дробилки».</li>
            <li>Экран «Домой» → удерживайте → «+» → Scriptable → выберите размер → нажмите на виджет → Script: «Дробилки».</li>
          </ol>
          <Button onClick={() => script && copyText(script, "Скрипт скопирован")}>
            <Copy /> Скопировать скрипт
          </Button>
          <pre className="max-h-64 overflow-auto rounded-lg bg-muted p-3 text-[11px] leading-snug">{script}</pre>
        </DialogContent>
      </Dialog>
    </Card>
  );
}

function PasswordCard() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const { busy, run } = useAction();
  const ids = { current: useId(), next: useId(), repeat: useId() };
  const save = () =>
    run(
      "password",
      async () => {
        if (next !== repeat) throw new Error("Новые пароли не совпадают");
        await api.post("/auth/password", { current, new: next });
        setCurrent("");
        setNext("");
        setRepeat("");
      },
      "Пароль сменён — на остальных устройствах нужно войти заново",
    );
  return (
    <Card>
      <CardHeader>
        <CardTitle>Пароль</CardTitle>
        <CardDescription>После смены остальные устройства выйдут, это — останется.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4 sm:grid-cols-3">
        <Field>
          <FieldLabel htmlFor={ids.current}>Текущий</FieldLabel>
          <Input id={ids.current} type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} />
        </Field>
        <Field>
          <FieldLabel htmlFor={ids.next}>Новый</FieldLabel>
          <Input id={ids.next} type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} />
          <FieldDescription>Не короче 8 символов</FieldDescription>
        </Field>
        <Field>
          <FieldLabel htmlFor={ids.repeat}>Новый ещё раз</FieldLabel>
          <Input id={ids.repeat} type="password" autoComplete="new-password" value={repeat} onChange={(e) => setRepeat(e.target.value)} />
        </Field>
      </CardContent>
      <CardFooter className="border-t">
        <Button disabled={!current || next.length < 8 || busy === "password"} onClick={save}>
          {busy === "password" ? <Spinner /> : <Save />} Сменить пароль
        </Button>
      </CardFooter>
    </Card>
  );
}

function TwoFactorCard({ me }: { me: Me }) {
  const [setup, setSetup] = useState<{ secret: string; uri: string; qr: string } | null>(null);
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const { busy, run } = useAction();
  const begin = () => run("begin", async () => setSetup(await api.post("/auth/totp/setup")));
  const enable = () =>
    run(
      "enable",
      async () => {
        await api.post("/auth/totp/enable", { code });
        window.location.reload();
      },
      "Вход по коду включён",
    );
  const disable = () =>
    run(
      "disable",
      async () => {
        await api.post("/auth/totp/disable", { password });
        window.location.reload();
      },
      "Вход по коду выключен",
    );

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ShieldCheck className="size-4" /> Вход по коду (2FA)
        </CardTitle>
        <CardDescription>
          {me.totp
            ? "Включён: при входе, кроме пароля, нужен код из приложения на телефоне."
            : "Кроме пароля — код из приложения (Яндекс Ключ, Google Authenticator, 1Password). Украденного пароля станет мало."}
          {me.role === "admin" && !me.totp && " Разработчику — рекомендуется."}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {me.totp ? (
          <div className="flex flex-wrap items-end gap-2">
            <Field className="w-56">
              <FieldLabel>Пароль, чтобы выключить</FieldLabel>
              <Input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
            </Field>
            <Button variant="outline" disabled={!password || busy === "disable"} onClick={disable}>
              {busy === "disable" ? <Spinner /> : <ShieldOff />} Выключить
            </Button>
          </div>
        ) : setup ? (
          <div className="grid gap-4 sm:grid-cols-[180px_1fr]">
            {/* QR рисует сервер (OpenCV) — без сторонних сервисов: ключ никуда не уходит */}
            <div className="aspect-square w-[180px] overflow-hidden rounded-lg border bg-white p-1 [&>svg]:size-full" dangerouslySetInnerHTML={{ __html: setup.qr }} />
            <div className="flex flex-col gap-3 text-sm">
              <p>
                Отсканируйте QR в приложении. На этом телефоне —{" "}
                <a href={setup.uri} className="underline underline-offset-2">
                  открыть в приложении
                </a>
                . Или введите ключ вручную:
              </p>
              <code className="break-all rounded bg-muted px-2 py-1 font-mono text-xs">{setup.secret.match(/.{1,4}/g)?.join(" ")}</code>
              <div className="flex flex-wrap items-end gap-2">
                <Field className="w-40">
                  <FieldLabel>Код из приложения</FieldLabel>
                  <Input inputMode="numeric" autoComplete="one-time-code" maxLength={7} value={code} onChange={(e) => setCode(e.target.value)} className="font-mono tracking-widest" />
                </Field>
                <Button disabled={code.replace(/\s/g, "").length !== 6 || busy === "enable"} onClick={enable}>
                  {busy === "enable" ? <Spinner /> : <ShieldCheck />} Включить
                </Button>
              </div>
            </div>
          </div>
        ) : (
          <Button variant="outline" className="self-start" disabled={busy === "begin"} onClick={begin}>
            {busy === "begin" ? <Spinner /> : <KeyRound />} Настроить
          </Button>
        )}
      </CardContent>
    </Card>
  );
}

function SessionsCard() {
  const [sessions, setSessions] = useState<SessionRow[] | null>(null);
  const { busy, run } = useAction();
  const reload = useCallback(() => api.get<SessionRow[]>("/auth/sessions").then(setSessions), []);
  useEffect(() => {
    reload();
  }, [reload]);
  const end = (s: SessionRow) => run(s.id, () => api.del(`/auth/sessions/${s.id}`).then(reload), "Устройство вышло");
  const endOthers = () => run("others", () => api.post("/auth/sessions/logout-others").then(reload), "Остальные устройства вышли");
  const others = (sessions ?? []).filter((s) => !s.current).length;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Где выполнен вход</CardTitle>
        <CardDescription>Незнакомое устройство — завершите его сеанс и смените пароль.</CardDescription>
        {others > 0 && (
          <CardAction>
            <Button variant="outline" size="sm" disabled={busy === "others"} onClick={endOthers}>
              <LogOut /> Выйти на остальных
            </Button>
          </CardAction>
        )}
      </CardHeader>
      <CardContent>
        <ul className="divide-y rounded-lg border">
          {(sessions ?? []).map((s) => (
            <li key={s.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-sm">
              <span className="font-medium">{deviceName(s.agent)}</span>
              {s.current && <Badge variant="secondary">это устройство</Badge>}
              <span className="flex-1 text-xs text-muted-foreground">
                {s.ip || "—"} · активность {dateTime(s.last_seen)} · вход {dateTime(s.created)}
              </span>
              {!s.current && (
                <Button variant="ghost" size="sm" disabled={busy === s.id} onClick={() => end(s)}>
                  <LogOut /> Выйти
                </Button>
              )}
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
