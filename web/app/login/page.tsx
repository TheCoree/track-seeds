"use client";

import { KeyRound, LogIn, ShieldAlert } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useId, useRef, useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { CodeInput } from "@/components/code-input";
import { api, ApiError } from "@/lib/api";
import type { AuthInfo } from "@/lib/types";

/** Куда вернуть после входа: только свой путь, не чужой сайт. */
/** Куда вернуться после входа — только на этот же сайт. Адрес разбирается так же, как его разберёт браузер:
 *  «/\evil.ru» и «/<Tab>/evil.ru» он превращает в «//evil.ru» — проверка по первым символам их пропускала. */
function safeNext(next: string | null): string {
  if (!next || typeof window === "undefined") return "/";
  try {
    const url = new URL(next, window.location.origin);
    if (url.origin !== window.location.origin || url.pathname.startsWith("/login")) return "/";
    return url.pathname + url.search + url.hash;
  } catch {
    return "/";
  }
}

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginForm />
    </Suspense>
  );
}

function LoginForm() {
  const next = safeNext(useSearchParams().get("next"));
  const [info, setInfo] = useState<AuthInfo | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [needCode, setNeedCode] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ids = { user: useId(), pass: useId(), code: useId() };
  const form = useRef<HTMLFormElement>(null);

  useEffect(() => {
    api
      .get<AuthInfo>("/auth/info")
      .then((i) => {
        setInfo(i);
        if (i.open_here) window.location.replace(next); // разработчика ещё нет — с этого компьютера без входа
      })
      .catch(() => {});
  }, [next]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post("/auth/login", { username, password, code: needCode ? code : "" });
      window.location.assign(next);
    } catch (err) {
      if (err instanceof ApiError && err.code === "totp") {
        if (needCode) setError(err.message);
        setNeedCode(true);
        setCode("");
      } else setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  };

  return (
    <Card className="w-full max-w-sm">
      <form ref={form} onSubmit={submit}>
        <CardHeader>
          <CardTitle className="text-xl">Вход</CardTitle>
          <CardDescription>Дробилки в реальном времени и отчёты о работе</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4 pt-4">
          {needCode ? (
            <Field>
              <FieldLabel htmlFor={ids.code} className="justify-center">
                Код из приложения-аутентификатора
              </FieldLabel>
              <CodeInput autoFocus value={code} onChange={setCode} onComplete={() => setTimeout(() => form.current?.requestSubmit())} />
            </Field>
          ) : (
            <>
              <Field>
                <FieldLabel htmlFor={ids.user}>Логин</FieldLabel>
                <Input id={ids.user} autoComplete="username" autoCapitalize="none" autoFocus required value={username} onChange={(e) => setUsername(e.target.value)} />
              </Field>
              <Field>
                <FieldLabel htmlFor={ids.pass}>Пароль</FieldLabel>
                <Input id={ids.pass} type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
              </Field>
            </>
          )}
          {error && (
            <Alert variant="destructive">
              <ShieldAlert />
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
        </CardContent>
        <CardFooter className="mt-4 flex flex-col items-stretch gap-3">
          <Button type="submit" size="lg" disabled={busy || (needCode && code.length !== 6)}>
            {busy ? <Spinner /> : needCode ? <KeyRound /> : <LogIn />}
            {needCode ? "Подтвердить" : "Войти"}
          </Button>
          {info && !info.admin_here && <p className="text-center text-xs text-muted-foreground">Вход разработчика — только из локальной сети завода</p>}
          {!needCode && <p className="text-center text-xs text-muted-foreground">Нет аккаунта — попросите ссылку для регистрации у разработчика</p>}
        </CardFooter>
      </form>
    </Card>
  );
}
