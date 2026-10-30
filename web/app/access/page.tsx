"use client";

import { Ban, CircleCheck, CircleX, Copy, KeyRound, Link2, LogOut, MoreHorizontal, Plus, Share2, ShieldCheck, Trash2, UserCheck, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { AdminOnly, useApp } from "@/components/app-shell";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Field, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { api } from "@/lib/api";
import { copyText, deviceName } from "@/lib/clipboard";
import { dateTime, duration } from "@/lib/format";
import type { AuthLogRow, Invite, User } from "@/lib/types";
import { useAction } from "@/lib/use-action";
import { cn } from "@/lib/utils";

const HOURS: [number, string][] = [
  [1, "1 час"],
  [24, "сутки"],
  [72, "3 дня"],
  [168, "неделя"],
];

const ACTIONS: Record<string, string> = {
  login: "вход",
  refresh: "обновление сессии",
  register: "регистрация по ссылке",
  reset: "новый пароль по ссылке",
  setup: "разработчик заведён",
  bootstrap: "пароль из .env",
  password: "смена пароля",
  totp_on: "включён вход по коду",
  totp_off: "выключен вход по коду",
  invite: "создана ссылка",
  widget: "создан виджет",
  disable: "отключён",
  enable: "включён",
  delete: "удалён",
};

export default function AccessPage() {
  return (
    <AdminOnly>
      <Access />
    </AdminOnly>
  );
}

function Access() {
  const { me } = useApp();
  const [users, setUsers] = useState<User[] | null>(null);
  const [invites, setInvites] = useState<Invite[] | null>(null);
  const [log, setLog] = useState<AuthLogRow[] | null>(null);
  const [link, setLink] = useState<{ url: string; title: string } | null>(null);
  const base = me?.public_url || (typeof window !== "undefined" ? window.location.origin : "");
  const local = /^https?:\/\/(localhost|127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(base);

  const reload = useCallback(() => {
    api.get<User[]>("/admin/users").then(setUsers);
    api.get<{ invites: Invite[] }>("/admin/invites").then((r) => setInvites(r.invites));
    api.get<AuthLogRow[]>("/admin/log?limit=60").then(setLog);
  }, []);
  useEffect(() => {
    reload();
  }, [reload]);

  const showLink = (code: string, title: string) => {
    setLink({ url: `${base}/register/?code=${encodeURIComponent(code)}`, title });
    reload();
  };

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Клиенты и доступ</h1>
        <p className="text-sm text-muted-foreground">Клиенты видят дробилки в реальном времени, события и отчёты — без настроек. Регистрация — только по вашей одноразовой ссылке.</p>
      </div>
      {me?.open && (
        <Alert variant="destructive">
          <AlertTitle>Сначала заведите разработчика</AlertTitle>
          <AlertDescription>Сейчас вход без пароля с этого компьютера. «Мой аккаунт» → логин и пароль — потом приглашайте клиентов.</AlertDescription>
        </Alert>
      )}
      {local && (
        <Alert>
          <Link2 />
          <AlertTitle>Ссылки ведут на адрес в локальной сети</AlertTitle>
          <AlertDescription>
            {base} откроется только в сети завода. Чтобы клиенты заходили с телефона откуда угодно, откройте сайт в интернет по HTTPS и задайте WEB_PUBLIC_URL (см. README → «Доступ для клиентов»).
          </AlertDescription>
        </Alert>
      )}
      <div className="grid gap-4 lg:grid-cols-5">
        <div className="flex flex-col gap-4 lg:col-span-2">
          <InviteCard onCreated={(code) => showLink(code, "Ссылка для регистрации")} />
          <InvitesCard invites={invites} onChanged={reload} />
        </div>
        <div className="flex flex-col gap-4 lg:col-span-3">
          <UsersCard users={users} myId={me?.user_id ?? null} onChanged={reload} onResetLink={(code, login) => showLink(code, `Новый пароль для ${login}`)} />
          <LogCard log={log} />
        </div>
      </div>
      <Dialog open={link !== null} onOpenChange={(open) => !open && setLink(null)}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{link?.title}</DialogTitle>
            <DialogDescription>Ссылка показывается один раз и работает для одного человека. Отправьте её в мессенджер.</DialogDescription>
          </DialogHeader>
          <code className="break-all rounded-lg bg-muted p-3 text-xs">{link?.url}</code>
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => link && copyText(link.url, "Ссылка скопирована")}>
              <Copy /> Скопировать
            </Button>
            {typeof navigator !== "undefined" && "share" in navigator && (
              <Button variant="outline" onClick={() => link && navigator.share({ title: "track-seeds", text: "Регистрация: дробилки в реальном времени", url: link.url }).catch(() => {})}>
                <Share2 /> Отправить
              </Button>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function InviteCard({ onCreated }: { onCreated: (code: string) => void }) {
  const [note, setNote] = useState("");
  const [hours, setHours] = useState(24);
  const { busy, run } = useAction();
  const create = () =>
    run("invite", async () => {
      const { code } = await api.post<{ code: string }>("/admin/invites", { note, hours });
      setNote("");
      onCreated(code);
    });
  return (
    <Card>
      <CardHeader>
        <CardTitle>Пригласить клиента</CardTitle>
        <CardDescription>Одноразовая ссылка: по ней зарегистрируется один человек, потом она гаснет.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <Field>
          <FieldLabel>Для кого (видно только вам)</FieldLabel>
          <Input placeholder="Иван, мастер смены" maxLength={120} value={note} onChange={(e) => setNote(e.target.value)} />
        </Field>
        <Field>
          <FieldLabel>Ссылка действует</FieldLabel>
          <NativeSelect value={hours} onChange={(e) => setHours(Number(e.target.value))}>
            {HOURS.map(([value, label]) => (
              <NativeSelectOption key={value} value={value}>
                {label}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </Field>
        <Button className="self-start" disabled={busy === "invite"} onClick={create}>
          {busy === "invite" ? <Spinner /> : <Plus />} Создать ссылку
        </Button>
      </CardContent>
    </Card>
  );
}

function InvitesCard({ invites, onChanged }: { invites: Invite[] | null; onChanged: () => void }) {
  const { busy, run } = useAction();
  const now = Date.now() / 1000;
  const revoke = (i: Invite) => run(`invite-${i.id}`, () => api.del(`/admin/invites/${i.id}`).then(onChanged), "Ссылка отозвана");
  return (
    <Card size="sm">
      <CardHeader>
        <CardTitle>Ссылки</CardTitle>
        <CardDescription>Действующие и использованные за неделю</CardDescription>
      </CardHeader>
      <CardContent>
        {invites === null ? (
          <Skeleton className="h-20" />
        ) : invites.length === 0 ? (
          <p className="text-sm text-muted-foreground">Ссылок нет</p>
        ) : (
          <ul className="divide-y rounded-lg border">
            {invites.map((i) => {
              const expired = !i.used && i.expires < now;
              return (
                <li key={i.id} className="flex items-center gap-2 px-3 py-2 text-sm">
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-medium">{i.note || (i.user_id ? `новый пароль: ${i.for_user}` : "без подписи")}</div>
                    <div className="text-xs text-muted-foreground">
                      {i.used ? `использована ${dateTime(i.used)}${i.used_login ? ` · ${i.used_login}` : ""}` : expired ? "истекла" : `ещё ${duration(i.expires - now)}`}
                    </div>
                  </div>
                  {i.used ? (
                    <CircleCheck className="size-4 text-emerald-600" />
                  ) : expired ? (
                    <CircleX className="size-4 text-muted-foreground" />
                  ) : (
                    <Button variant="ghost" size="sm" disabled={busy === `invite-${i.id}`} onClick={() => revoke(i)}>
                      <X /> Отозвать
                    </Button>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function UsersCard({ users, myId, onChanged, onResetLink }: { users: User[] | null; myId: number | null; onChanged: () => void; onResetLink: (code: string, login: string) => void }) {
  const { busy, run } = useAction();
  const [removing, setRemoving] = useState<User | null>(null);
  const now = Date.now() / 1000;

  const toggle = (u: User) =>
    run(`user-${u.id}`, () => api.patch(`/admin/users/${u.id}`, { disabled: !u.disabled }).then(onChanged), u.disabled ? `${u.name}: доступ включён` : `${u.name}: доступ отключён, сессии закрыты`);
  const logout = (u: User) => run(`user-${u.id}`, () => api.post(`/admin/users/${u.id}/logout`).then(onChanged), `${u.name}: все устройства вышли`);
  const reset = (u: User) =>
    run(`user-${u.id}`, async () => {
      const { code } = await api.post<{ code: string }>(`/admin/users/${u.id}/reset`, { hours: 24 });
      onResetLink(code, u.username);
    });
  const remove = (u: User) => run(`user-${u.id}`, () => api.del(`/admin/users/${u.id}`).then(onChanged), `${u.name} удалён`);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Аккаунты</CardTitle>
        <CardDescription>Отключённый аккаунт теряет доступ сразу, вместе с открытым видео и виджетом</CardDescription>
      </CardHeader>
      <CardContent>
        {users === null ? (
          <Skeleton className="h-32" />
        ) : (
          <ul className="divide-y rounded-lg border">
            {users.map((u) => {
              const online = u.last_seen && now - u.last_seen < 20 * 60;
              return (
                <li key={u.id} className={cn("flex items-center gap-3 px-3 py-2.5", u.disabled && "opacity-60")}>
                  <span className={cn("size-2 shrink-0 rounded-full", online ? "bg-emerald-500" : "bg-slate-300 dark:bg-slate-600")} title={online ? "заходил(а) последние 20 минут" : ""} />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                      <span className="font-medium">{u.name}</span>
                      <span className="text-sm text-muted-foreground">{u.username}</span>
                      <Badge variant={u.role === "admin" ? "default" : "secondary"}>{u.role === "admin" ? "разработчик" : "клиент"}</Badge>
                      {u.totp && (
                        <Badge variant="outline">
                          <ShieldCheck /> 2FA
                        </Badge>
                      )}
                      {u.disabled && <Badge variant="destructive">отключён</Badge>}
                    </div>
                    <div className="text-xs text-muted-foreground">
                      {u.last_seen ? `был(а) ${dateTime(u.last_seen)}` : "не входил(а)"} · устройств {u.sessions}
                      {u.widgets ? ` · виджетов ${u.widgets}` : ""}
                    </div>
                  </div>
                  {u.id !== myId && (
                    <DropdownMenu>
                      <DropdownMenuTrigger render={<Button variant="ghost" size="icon-sm" aria-label="Действия" disabled={busy === `user-${u.id}`} />}>
                        {busy === `user-${u.id}` ? <Spinner /> : <MoreHorizontal />}
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem onClick={() => toggle(u)}>
                          {u.disabled ? <UserCheck /> : <Ban />} {u.disabled ? "Включить доступ" : "Отключить доступ"}
                        </DropdownMenuItem>
                        <DropdownMenuItem onClick={() => logout(u)}>
                          <LogOut /> Выйти на всех устройствах
                        </DropdownMenuItem>
                        <DropdownMenuItem onClick={() => reset(u)}>
                          <KeyRound /> Ссылка для нового пароля
                        </DropdownMenuItem>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem variant="destructive" onClick={() => setRemoving(u)}>
                          <Trash2 /> Удалить
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
      <AlertDialog open={removing !== null} onOpenChange={(open) => !open && setRemoving(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Удалить {removing?.name}?</AlertDialogTitle>
            <AlertDialogDescription>Аккаунт, его сессии и ключи виджета удалятся. Вернуть доступ — только новой ссылкой.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Отмена</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={() => removing && remove(removing)}>
              Удалить
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  );
}

function LogCard({ log }: { log: AuthLogRow[] | null }) {
  return (
    <Card size="sm">
      <CardHeader>
        <CardTitle>Журнал входов</CardTitle>
        <CardDescription>Входы, отказы, ссылки и смена паролей — 180 дней</CardDescription>
      </CardHeader>
      <CardContent>
        {log === null ? (
          <Skeleton className="h-32" />
        ) : (
          <ul className="max-h-96 divide-y overflow-y-auto rounded-lg border text-sm">
            {log.map((row) => (
              <li key={row.id} className="flex items-start gap-2 px-3 py-1.5">
                {row.ok ? <CircleCheck className="mt-0.5 size-4 shrink-0 text-emerald-600" /> : <CircleX className="mt-0.5 size-4 shrink-0 text-destructive" />}
                <div className="min-w-0 flex-1">
                  <span className="font-medium">{row.username ?? "—"}</span> · {ACTIONS[row.action] ?? row.action}
                  {row.detail && <span className="text-muted-foreground"> · {row.detail}</span>}
                  <div className="text-xs text-muted-foreground">
                    {dateTime(row.ts)}
                    {row.ip ? ` · ${row.ip}` : ""}
                    {row.agent ? ` · ${deviceName(row.agent)}` : ""}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
