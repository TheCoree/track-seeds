"use client";

import { ChevronRight, Copy, Download, KeyRound, LogOut, MonitorSmartphone, Save, ShieldCheck, Smartphone, Trash2, UserPlus } from "lucide-react";
import { useCallback, useEffect, useId, useState, type ReactNode } from "react";
import { useApp } from "@/components/app-shell";
import { CodeInput } from "@/components/code-input";
import { useInstall } from "@/components/pwa";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Item, ItemActions, ItemContent, ItemDescription, ItemGroup, ItemMedia, ItemTitle } from "@/components/ui/item";
import { Spinner } from "@/components/ui/spinner";
import { api } from "@/lib/api";
import { copyText, deviceName } from "@/lib/clipboard";
import { dateTime } from "@/lib/format";
import { scriptableWidget } from "@/lib/scriptable";
import type { Me, SessionRow, WidgetKey } from "@/lib/types";
import { useAction } from "@/lib/use-action";

type Panel = "password" | "totp" | "devices" | "widget" | "install" | null;

/** «Мой аккаунт»: одна строка на настройку, формы — в диалоге. */
export default function AccountPage() {
  const { me } = useApp();
  const [panel, setPanel] = useState<Panel>(null);
  const [sessions, setSessions] = useState<SessionRow[] | null>(null);
  const [widgets, setWidgets] = useState<WidgetKey[] | null>(null);
  const { installed } = useInstall();

  const reload = useCallback(() => {
    api.get<SessionRow[]>("/auth/sessions").then(setSessions).catch(() => {});
    api.get<WidgetKey[]>("/auth/widgets").then(setWidgets).catch(() => {});
  }, []);
  useEffect(() => {
    if (me && !me.open) reload();
  }, [me, reload]);

  if (!me) return null;
  if (me.open)
    return (
      <Page>
        <SetupCard />
      </Page>
    );

  const close = () => {
    setPanel(null);
    reload();
  };

  return (
    <Page>
      <Item className="px-0">
        <ItemMedia>
          <Avatar className="size-12">
            <AvatarFallback className="bg-primary text-lg font-semibold text-primary-foreground">{me.name.slice(0, 1).toUpperCase()}</AvatarFallback>
          </Avatar>
        </ItemMedia>
        <ItemContent>
          <ItemTitle className="text-lg font-semibold">{me.name}</ItemTitle>
          <ItemDescription>
            {me.username} · {me.role === "admin" ? "разработчик" : "клиент"}
          </ItemDescription>
        </ItemContent>
      </Item>

      <Card className="py-0">
        <ItemGroup className="gap-0 divide-y">
          <Row icon={KeyRound} title="Пароль" hint="Сменить пароль для входа" onClick={() => setPanel("password")} />
          <Row icon={ShieldCheck} title="Вход по коду" hint={me.totp ? "Включён" : "Выключен — код из приложения на телефоне вдобавок к паролю"} onClick={() => setPanel("totp")} />
          <Row icon={MonitorSmartphone} title="Устройства" hint={sessions ? `Вход выполнен на ${sessions.length} ${plural(sessions.length, "устройстве", "устройствах", "устройствах")}` : " "} onClick={() => setPanel("devices")} />
          <Row icon={Smartphone} title="Виджет на iPhone" hint={widgets?.length ? `Добавлено: ${widgets.length}` : "Дробилки на экране «Домой» через Scriptable"} onClick={() => setPanel("widget")} />
          {!installed && <Row icon={Download} title="Приложение на телефон" hint="Значок на экране «Домой»" onClick={() => setPanel("install")} />}
        </ItemGroup>
      </Card>

      <Sheet open={panel === "password"} onClose={close} title="Пароль" description="После смены остальные устройства выйдут, это — останется.">
        <PasswordForm onDone={close} />
      </Sheet>
      <Sheet open={panel === "totp"} onClose={close} title="Вход по коду" description={me.totp ? "При входе, кроме пароля, нужен код из приложения." : "Кроме пароля — код из приложения (Яндекс Ключ, Google Authenticator). Украденного пароля станет мало."}>
        <TwoFactor me={me} />
      </Sheet>
      <Sheet open={panel === "devices"} onClose={close} title="Устройства" description="Незнакомое — выйдите на нём и смените пароль.">
        <Devices sessions={sessions} onChanged={reload} />
      </Sheet>
      <Sheet open={panel === "widget"} onClose={close} title="Виджет на iPhone" description="Состояние дробилок и итоги дня на экране «Домой» и экране блокировки." wide>
        <Widget me={me} keys={widgets} onChanged={reload} />
      </Sheet>
      <Sheet open={panel === "install"} onClose={close} title="Приложение на телефон" description="Открывается без адресной строки, как обычное приложение.">
        <Install />
      </Sheet>
    </Page>
  );
}

function plural(n: number, one: string, few: string, many: string) {
  const d = n % 10;
  const h = n % 100;
  return d === 1 && h !== 11 ? one : d >= 2 && d <= 4 && (h < 12 || h > 14) ? few : many;
}

function Page({ children }: { children: ReactNode }) {
  return (
    <div className="mx-auto flex w-full max-w-xl flex-col gap-5">
      <h1 className="text-2xl font-semibold tracking-tight">Мой аккаунт</h1>
      {children}
    </div>
  );
}

function Row({ icon: Icon, title, hint, onClick }: { icon: typeof KeyRound; title: string; hint: string; onClick: () => void }) {
  return (
    <Item render={<button type="button" onClick={onClick} />} className="cursor-pointer rounded-none px-4 py-3.5 text-left not-first:border-t-border hover:bg-muted/60">
      <ItemMedia variant="icon">
        <Icon className="size-5 text-muted-foreground" />
      </ItemMedia>
      <ItemContent className="min-w-0">
        <ItemTitle className="text-base">{title}</ItemTitle>
        <ItemDescription className="truncate">{hint}</ItemDescription>
      </ItemContent>
      <ItemActions>
        <ChevronRight className="size-4 text-muted-foreground" />
      </ItemActions>
    </Item>
  );
}

function Sheet({ open, onClose, title, description, wide, children }: { open: boolean; onClose: () => void; title: string; description: string; wide?: boolean; children: ReactNode }) {
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className={wide ? "sm:max-w-2xl" : "sm:max-w-md"}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        {open && children}
      </DialogContent>
    </Dialog>
  );
}

/** Разработчика ещё нет: вход без пароля только с этого компьютера. */
function SetupCard() {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const { busy, run } = useAction();
  const ids = { user: useId(), pass: useId(), repeat: useId() };
  const save = () =>
    run("setup", async () => {
      if (password !== repeat) throw new Error("Пароли не совпадают");
      await api.post("/auth/setup", { username, name: "Разработчик", password });
      window.location.reload();
    });
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <UserPlus className="size-4" /> Завести разработчика
        </CardTitle>
        <CardDescription>Сейчас вход без пароля — только с этого компьютера. С логином и паролем можно открыть сайт в сеть и приглашать клиентов.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <Field>
          <FieldLabel htmlFor={ids.user}>Логин</FieldLabel>
          <Input id={ids.user} autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} />
        </Field>
        <Field>
          <FieldLabel htmlFor={ids.pass}>Пароль</FieldLabel>
          <Input id={ids.pass} type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <Field>
          <FieldLabel htmlFor={ids.repeat}>Пароль ещё раз</FieldLabel>
          <Input id={ids.repeat} type="password" autoComplete="new-password" value={repeat} onChange={(e) => setRepeat(e.target.value)} />
        </Field>
      </CardContent>
      <CardFooter>
        <Button className="w-full" disabled={busy === "setup" || password.length < 8} onClick={save}>
          {busy === "setup" ? <Spinner /> : <Save />} Завести и войти
        </Button>
      </CardFooter>
    </Card>
  );
}

function PasswordForm({ onDone }: { onDone: () => void }) {
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
        onDone();
      },
      "Пароль сменён",
    );
  return (
    <div className="flex flex-col gap-4">
      <Field>
        <FieldLabel htmlFor={ids.current}>Текущий пароль</FieldLabel>
        <Input id={ids.current} type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} />
      </Field>
      <Field>
        <FieldLabel htmlFor={ids.next}>Новый пароль</FieldLabel>
        <Input id={ids.next} type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} />
        <FieldDescription>Не короче 8 символов</FieldDescription>
      </Field>
      <Field>
        <FieldLabel htmlFor={ids.repeat}>Новый ещё раз</FieldLabel>
        <Input id={ids.repeat} type="password" autoComplete="new-password" value={repeat} onChange={(e) => setRepeat(e.target.value)} />
      </Field>
      <Button disabled={!current || next.length < 8 || busy === "password"} onClick={save}>
        {busy === "password" ? <Spinner /> : <Save />} Сменить пароль
      </Button>
    </div>
  );
}

function TwoFactor({ me }: { me: Me }) {
  const [setup, setSetup] = useState<{ secret: string; uri: string; qr: string } | null>(null);
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const { busy, run } = useAction();

  useEffect(() => {
    if (!me.totp) api.post<{ secret: string; uri: string; qr: string }>("/auth/totp/setup").then(setSetup).catch(() => {});
  }, [me.totp]);

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

  if (me.totp)
    return (
      <div className="flex flex-col gap-3">
        <Field>
          <FieldLabel>Пароль, чтобы выключить</FieldLabel>
          <Input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <Button variant="outline" disabled={!password || busy === "disable"} onClick={disable}>
          {busy === "disable" ? <Spinner /> : null} Выключить вход по коду
        </Button>
      </div>
    );
  if (!setup) return <Spinner className="mx-auto" />;
  return (
    <div className="flex flex-col items-center gap-4 text-center">
      {/* QR рисует сервер (OpenCV) — ключ никуда не уходит */}
      <div className="size-44 overflow-hidden rounded-lg border bg-white p-1 [&>svg]:size-full" dangerouslySetInnerHTML={{ __html: setup.qr }} />
      <p className="text-sm text-muted-foreground">
        Отсканируйте в приложении или{" "}
        <a href={setup.uri} className="underline underline-offset-2">
          откройте на этом телефоне
        </a>
        . Ключ вручную: <code className="break-all font-mono text-xs text-foreground">{setup.secret.match(/.{1,4}/g)?.join(" ")}</code>
      </p>
      <CodeInput value={code} onChange={setCode} />
      <Button className="w-full" disabled={code.length !== 6 || busy === "enable"} onClick={enable}>
        {busy === "enable" ? <Spinner /> : <ShieldCheck />} Включить
      </Button>
    </div>
  );
}

function Devices({ sessions, onChanged }: { sessions: SessionRow[] | null; onChanged: () => void }) {
  const { busy, run } = useAction();
  const end = (s: SessionRow) => run(s.id, () => api.del(`/auth/sessions/${s.id}`).then(onChanged));
  const endOthers = () => run("others", () => api.post("/auth/sessions/logout-others").then(onChanged), "Остальные устройства вышли");
  const others = (sessions ?? []).filter((s) => !s.current).length;
  return (
    <div className="flex flex-col gap-3">
      <ul className="divide-y">
        {(sessions ?? []).map((s) => (
          <li key={s.id} className="flex items-center gap-3 py-2.5">
            <div className="min-w-0 flex-1">
              <div className="font-medium">
                {deviceName(s.agent)}
                {s.current && <span className="ml-2 text-sm font-normal text-muted-foreground">это устройство</span>}
              </div>
              <div className="text-xs text-muted-foreground">
                {s.ip || "—"} · {dateTime(s.last_seen)}
              </div>
            </div>
            {!s.current && (
              <Button variant="ghost" size="sm" disabled={busy === s.id} onClick={() => end(s)}>
                Выйти
              </Button>
            )}
          </li>
        ))}
      </ul>
      {others > 0 && (
        <Button variant="outline" disabled={busy === "others"} onClick={endOthers}>
          <LogOut /> Выйти на всех, кроме этого
        </Button>
      )}
    </div>
  );
}

function Widget({ me, keys, onChanged }: { me: Me; keys: WidgetKey[] | null; onChanged: () => void }) {
  const [script, setScript] = useState<string | null>(null);
  const { busy, run } = useAction();
  const server = me.public_url || window.location.origin;
  const local = /^https?:\/\/(localhost|127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(server);
  const create = () =>
    run("create", async () => {
      const { token } = await api.post<{ token: string }>("/auth/widgets", { name: deviceName(navigator.userAgent) });
      setScript(scriptableWidget(server, token));
      onChanged();
    });
  const revoke = (key: WidgetKey) => run(`revoke-${key.id}`, () => api.del(`/auth/widgets/${key.id}`).then(onChanged), "Виджет отключён");

  if (script)
    return (
      <div className="flex flex-col gap-4">
        <ol className="list-decimal space-y-1.5 pl-5 text-sm">
          <li>Установите Scriptable из App Store.</li>
          <li>Скопируйте скрипт → в Scriptable нажмите «+» → вставьте.</li>
          <li>Экран «Домой» → удерживайте → «+» → Scriptable → нажмите на виджет → выберите скрипт.</li>
        </ol>
        <Button onClick={() => copyText(script, "Скрипт скопирован")}>
          <Copy /> Скопировать скрипт
        </Button>
        <p className="text-xs text-muted-foreground">Скрипт показывается один раз — в нём ключ доступа. Потеряли — добавьте виджет заново.</p>
      </div>
    );

  return (
    <div className="flex flex-col gap-4">
      {local && <p className="text-sm text-muted-foreground">Сайт открыт по адресу в локальной сети — виджет будет обновляться только в сети завода.</p>}
      {keys && keys.length > 0 && (
        <ul className="divide-y">
          {keys.map((key) => (
            <li key={key.id} className="flex items-center gap-3 py-2">
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium">{key.name}</div>
                <div className="text-xs text-muted-foreground">{key.last_used ? `обновлялся ${dateTime(key.last_used)}` : "ещё не обновлялся"}</div>
              </div>
              <Button variant="ghost" size="icon-sm" aria-label="Отключить" disabled={busy === `revoke-${key.id}`} onClick={() => revoke(key)}>
                <Trash2 />
              </Button>
            </li>
          ))}
        </ul>
      )}
      <Button disabled={busy === "create"} onClick={create}>
        {busy === "create" ? <Spinner /> : <Smartphone />} Добавить виджет
      </Button>
    </div>
  );
}

function Install() {
  const { available, install, secure } = useInstall();
  return (
    <div className="flex flex-col gap-3 text-sm">
      {available && (
        <Button onClick={install}>
          <Download /> Установить
        </Button>
      )}
      <p>
        <span className="font-medium">iPhone:</span> Safari → «Поделиться» → «На экран „Домой“».
      </p>
      <p>
        <span className="font-medium">Android:</span> Chrome → меню ⋮ → «Установить приложение».
      </p>
      {!secure && <p className="text-muted-foreground">По локальному адресу — как закладка; полноценно — по адресу https://…</p>}
    </div>
  );
}
