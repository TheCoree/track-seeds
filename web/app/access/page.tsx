"use client";

import { Ban, CircleCheck, CircleX, Copy, History, KeyRound, LogOut, MoreHorizontal, Share2, Trash2, UserCheck, UserPlus } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { AdminOnly, useApp } from "@/components/app-shell";
import { Segmented, type SegmentOption } from "@/components/segmented";
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
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Field, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Item, ItemActions, ItemContent, ItemDescription, ItemGroup, ItemMedia, ItemTitle } from "@/components/ui/item";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { api } from "@/lib/api";
import { copyText, deviceName } from "@/lib/clipboard";
import { dateTime, duration } from "@/lib/format";
import type { AuthLogRow, Invite, User } from "@/lib/types";
import { useAction } from "@/lib/use-action";
import { cn } from "@/lib/utils";

const HOURS: SegmentOption<string>[] = [
  { value: "1", label: "1 час" },
  { value: "24", label: "Сутки" },
  { value: "72", label: "3 дня" },
  { value: "168", label: "Неделя" },
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
  widget: "добавлен виджет",
  disable: "доступ отключён",
  enable: "доступ включён",
  delete: "удалён",
};

export default function AccessPage() {
  return (
    <AdminOnly>
      <Access />
    </AdminOnly>
  );
}

/** Люди с доступом и ожидающие приглашения — одним списком; приглашение и журнал — в диалогах. */
function Access() {
  const { me } = useApp();
  const [users, setUsers] = useState<User[] | null>(null);
  const [invites, setInvites] = useState<Invite[]>([]);
  const [inviting, setInviting] = useState(false);
  const [journal, setJournal] = useState(false);
  const [link, setLink] = useState<{ url: string; title: string } | null>(null);
  const base = me?.public_url || (typeof window !== "undefined" ? window.location.origin : "");

  const reload = useCallback(() => {
    api.get<User[]>("/admin/users").then(setUsers);
    api.get<{ invites: Invite[] }>("/admin/invites").then((r) => setInvites(r.invites));
  }, []);
  useEffect(() => {
    reload();
  }, [reload]);

  const showLink = (code: string, title: string) => {
    setInviting(false);
    setLink({ url: `${base}/register/?code=${encodeURIComponent(code)}`, title });
    reload();
  };
  const now = Date.now() / 1000;
  const waiting = invites.filter((i) => !i.used && i.expires > now && !i.user_id);

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Клиенты</h1>
          <p className="text-sm text-muted-foreground">Видят дробилки, события и отчёты — без настроек</p>
        </div>
        <Button size="lg" disabled={me?.open} onClick={() => setInviting(true)}>
          <UserPlus /> Пригласить
        </Button>
      </div>
      {me?.open && <p className="text-sm text-muted-foreground">Чтобы приглашать, сначала заведите разработчика в «Мой аккаунт».</p>}

      <Card className="py-0">
        {users === null ? (
          <Skeleton className="m-4 h-24" />
        ) : (
          <ItemGroup className="gap-0 divide-y">
            {waiting.map((i) => (
              <WaitingRow key={`i${i.id}`} invite={i} now={now} onChanged={reload} />
            ))}
            {users.map((u) => (
              <UserRow key={u.id} user={u} me={u.id === me?.user_id} now={now} onChanged={reload} onResetLink={(code) => showLink(code, `Новый пароль для ${u.name}`)} />
            ))}
          </ItemGroup>
        )}
      </Card>

      <Button variant="ghost" className="self-start text-muted-foreground" onClick={() => setJournal(true)}>
        <History /> Журнал входов
      </Button>

      <InviteDialog open={inviting} onClose={() => setInviting(false)} base={base} onCreated={(code) => showLink(code, "Ссылка готова")} />
      <LinkDialog link={link} onClose={() => setLink(null)} />
      <JournalDialog open={journal} onClose={() => setJournal(false)} />
    </div>
  );
}

function Person({ name, dim }: { name: string; dim?: boolean }) {
  return (
    <ItemMedia>
      <Avatar size="lg">
        <AvatarFallback className={cn("font-semibold", dim && "border border-dashed bg-transparent")}>{name.slice(0, 1).toUpperCase() || "?"}</AvatarFallback>
      </Avatar>
    </ItemMedia>
  );
}

function UserRow({ user: u, me, now, onChanged, onResetLink }: { user: User; me: boolean; now: number; onChanged: () => void; onResetLink: (code: string) => void }) {
  const { busy, run } = useAction();
  const [removing, setRemoving] = useState(false);
  const online = u.last_seen && now - u.last_seen < 20 * 60;
  const toggle = () =>
    run("u", () => api.patch(`/admin/users/${u.id}`, { disabled: !u.disabled }).then(onChanged), u.disabled ? "Доступ включён" : "Доступ отключён");
  const logout = () => run("u", () => api.post(`/admin/users/${u.id}/logout`).then(onChanged), "Вышел на всех устройствах");
  const reset = () =>
    run("u", async () => {
      const { code } = await api.post<{ code: string }>(`/admin/users/${u.id}/reset`, { hours: 24 });
      onResetLink(code);
    });
  const remove = () => run("u", () => api.del(`/admin/users/${u.id}`).then(onChanged), `${u.name} удалён`);

  return (
    <Item role="listitem" className={cn("rounded-none px-4 py-3 not-first:border-t-border", u.disabled && "opacity-55")}>
      <Person name={u.name} />
      <ItemContent className="min-w-0">
        <ItemTitle className="text-base">
          {u.name}
          {me && <span className="text-sm font-normal text-muted-foreground">вы</span>}
        </ItemTitle>
        <ItemDescription className="truncate">
          {u.disabled ? "доступ отключён" : u.role === "admin" ? `${u.username} · разработчик` : online ? "сейчас на сайте" : u.last_seen ? `был(а) ${dateTime(u.last_seen)}` : "ещё не заходил(а)"}
        </ItemDescription>
      </ItemContent>
      {!me && (
        <ItemActions>
        <DropdownMenu>
          <DropdownMenuTrigger render={<Button variant="ghost" size="icon" aria-label="Действия" disabled={busy !== null} />}>{busy ? <Spinner /> : <MoreHorizontal />}</DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onClick={toggle}>
              {u.disabled ? <UserCheck /> : <Ban />} {u.disabled ? "Вернуть доступ" : "Отключить доступ"}
            </DropdownMenuItem>
            <DropdownMenuItem onClick={logout}>
              <LogOut /> Выйти на всех устройствах
            </DropdownMenuItem>
            <DropdownMenuItem onClick={reset}>
              <KeyRound /> Ссылка для нового пароля
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem variant="destructive" onClick={() => setRemoving(true)}>
              <Trash2 /> Удалить
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        </ItemActions>
      )}
      <AlertDialog open={removing} onOpenChange={setRemoving}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Удалить {u.name}?</AlertDialogTitle>
            <AlertDialogDescription>Доступ пропадёт сразу. Вернуть — только новой ссылкой.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Отмена</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={remove}>
              Удалить
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Item>
  );
}

function WaitingRow({ invite, now, onChanged }: { invite: Invite; now: number; onChanged: () => void }) {
  const { busy, run } = useAction();
  return (
    <Item role="listitem" className="rounded-none px-4 py-3 not-first:border-t-border">
      <Person name={invite.note} dim />
      <ItemContent className="min-w-0">
        <ItemTitle className="text-base">{invite.note || "Приглашение"}</ItemTitle>
        <ItemDescription>ждём регистрации · ссылка действует ещё {duration(invite.expires - now).replace(/ \d+ с$/, "")}</ItemDescription>
      </ItemContent>
      <ItemActions>
        <Button variant="ghost" size="sm" disabled={busy !== null} onClick={() => run("x", () => api.del(`/admin/invites/${invite.id}`).then(onChanged), "Приглашение отменено")}>
          Отменить
        </Button>
      </ItemActions>
    </Item>
  );
}

function InviteDialog({ open, onClose, base, onCreated }: { open: boolean; onClose: () => void; base: string; onCreated: (code: string) => void }) {
  const [note, setNote] = useState("");
  const [hours, setHours] = useState(24);
  const { busy, run } = useAction();
  const local = /^https?:\/\/(localhost|127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(base);
  const create = () =>
    run("invite", async () => {
      const { code } = await api.post<{ code: string }>("/admin/invites", { note, hours });
      setNote("");
      onCreated(code);
    });
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Пригласить клиента</DialogTitle>
          <DialogDescription>По ссылке зарегистрируется один человек, потом она перестанет работать.</DialogDescription>
        </DialogHeader>
        <Field>
          <FieldLabel>Кого приглашаете</FieldLabel>
          <Input autoFocus placeholder="Иван, мастер смены" maxLength={120} value={note} onChange={(e) => setNote(e.target.value)} />
        </Field>
        <Field>
          <FieldLabel>Ссылка действует</FieldLabel>
          <Segmented label="Ссылка действует" value={String(hours)} onChange={(v) => setHours(Number(v))} options={HOURS} className="w-full" itemClassName="flex-1" />
        </Field>
        {local && <p className="text-xs text-muted-foreground">Ссылка откроется только в сети завода: сайт не открыт в интернет (WEB_PUBLIC_URL, см. README).</p>}
        <Button disabled={busy === "invite"} onClick={create}>
          {busy === "invite" ? <Spinner /> : null} Создать ссылку
        </Button>
      </DialogContent>
    </Dialog>
  );
}

function LinkDialog({ link, onClose }: { link: { url: string; title: string } | null; onClose: () => void }) {
  const canShare = typeof navigator !== "undefined" && "share" in navigator;
  return (
    <Dialog open={link !== null} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{link?.title}</DialogTitle>
          <DialogDescription>Отправьте её в мессенджер. Больше она не покажется.</DialogDescription>
        </DialogHeader>
        <code className="break-all rounded-lg bg-muted p-3 text-xs">{link?.url}</code>
        <div className="grid gap-2 sm:grid-cols-2">
          <Button onClick={() => link && copyText(link.url, "Ссылка скопирована")} className={canShare ? "" : "sm:col-span-2"}>
            <Copy /> Скопировать
          </Button>
          {canShare && (
            <Button variant="outline" onClick={() => link && navigator.share({ title: "track-seeds", url: link.url }).catch(() => {})}>
              <Share2 /> Отправить
            </Button>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

function JournalDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [log, setLog] = useState<AuthLogRow[] | null>(null);
  useEffect(() => {
    if (open) api.get<AuthLogRow[]>("/admin/log?limit=100").then(setLog);
  }, [open]);
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Журнал входов</DialogTitle>
          <DialogDescription>Входы, отказы, ссылки, смена паролей — за 180 дней</DialogDescription>
        </DialogHeader>
        {log === null ? (
          <Skeleton className="h-40" />
        ) : (
          <ul className="-mx-1 max-h-[60vh] divide-y overflow-y-auto text-sm">
            {log.map((row) => (
              <li key={row.id} className="flex items-start gap-2 px-1 py-2">
                {row.ok ? <CircleCheck className="mt-0.5 size-4 shrink-0 text-emerald-600" /> : <CircleX className="mt-0.5 size-4 shrink-0 text-destructive" />}
                <div className="min-w-0 flex-1">
                  <div>
                    <span className="font-medium">{row.username ?? "—"}</span> · {ACTIONS[row.action] ?? row.action}
                    {row.detail && <span className="text-muted-foreground"> · {row.detail}</span>}
                  </div>
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
      </DialogContent>
    </Dialog>
  );
}
