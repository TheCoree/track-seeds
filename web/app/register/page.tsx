"use client";

import { CircleAlert, UserPlus } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useId, useState } from "react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { api } from "@/lib/api";

interface InviteInfo {
  note: string;
  expires: number;
  reset: boolean;
  username: string | null;
}

export default function RegisterPage() {
  return (
    <Suspense fallback={<Skeleton className="h-96 w-full max-w-sm rounded-xl" />}>
      <RegisterForm />
    </Suspense>
  );
}

/** Регистрация по одноразовой ссылке от разработчика (или новый пароль по ссылке сброса). */
function RegisterForm() {
  const code = useSearchParams().get("code") ?? "";
  const [invite, setInvite] = useState<InviteInfo | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ids = { name: useId(), user: useId(), pass: useId(), repeat: useId() };

  useEffect(() => {
    if (!code) {
      setProblem("В ссылке нет кода приглашения — скопируйте её целиком");
      return;
    }
    api
      .get<InviteInfo>(`/auth/invite?code=${encodeURIComponent(code)}`)
      .then(setInvite)
      .catch((e) => setProblem(e instanceof Error ? e.message : String(e)));
  }, [code]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (password !== repeat) {
      setError("Пароли не совпадают");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.post("/auth/register", { code, username, name, password });
      window.location.assign("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  };

  if (problem)
    return (
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-xl">
            <CircleAlert className="size-5 text-destructive" /> Ссылка не подходит
          </CardTitle>
          <CardDescription>{problem}</CardDescription>
        </CardHeader>
        <CardFooter className="mt-4">
          <Link href="/login/" className="text-sm underline underline-offset-2">
            У меня уже есть аккаунт
          </Link>
        </CardFooter>
      </Card>
    );
  if (!invite) return <Skeleton className="h-96 w-full max-w-sm rounded-xl" />;

  const until = new Date(invite.expires * 1000).toLocaleString("ru-RU", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" });
  return (
    <Card className="w-full max-w-sm">
      <form onSubmit={submit}>
        <CardHeader>
          <CardTitle className="text-xl">{invite.reset ? "Новый пароль" : "Регистрация"}</CardTitle>
          <CardDescription>
            {invite.reset ? `Для входа ${invite.username}.` : "Просмотр дробилок в реальном времени и отчёты о работе."} Ссылка одноразовая, действует до {until}.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4 pt-4">
          {!invite.reset && (
            <>
              <Field>
                <FieldLabel htmlFor={ids.name}>Как вас зовут</FieldLabel>
                <Input id={ids.name} autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Иван Петрович" />
              </Field>
              <Field>
                <FieldLabel htmlFor={ids.user}>Логин</FieldLabel>
                <Input id={ids.user} autoComplete="username" autoCapitalize="none" required minLength={3} maxLength={40} value={username} onChange={(e) => setUsername(e.target.value)} />
                <FieldDescription>Латиница или кириллица, цифры, точка, дефис</FieldDescription>
              </Field>
            </>
          )}
          <Field>
            <FieldLabel htmlFor={ids.pass}>Пароль</FieldLabel>
            <Input id={ids.pass} type="password" autoComplete="new-password" required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} />
            <FieldDescription>Не короче 8 символов</FieldDescription>
          </Field>
          <Field>
            <FieldLabel htmlFor={ids.repeat}>Пароль ещё раз</FieldLabel>
            <Input id={ids.repeat} type="password" autoComplete="new-password" required value={repeat} onChange={(e) => setRepeat(e.target.value)} />
          </Field>
          {error && (
            <Alert variant="destructive">
              <CircleAlert />
              <AlertTitle>Не получилось</AlertTitle>
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
        </CardContent>
        <CardFooter className="mt-4">
          <Button type="submit" size="lg" className="w-full" disabled={busy}>
            {busy ? <Spinner /> : <UserPlus />}
            {invite.reset ? "Сохранить и войти" : "Зарегистрироваться"}
          </Button>
        </CardFooter>
      </form>
    </Card>
  );
}
