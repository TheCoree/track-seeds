"use client";

import { CircleAlert, CircleCheck, KeyRound, PlugZap, RotateCcw, Save } from "lucide-react";
import { useId, useState } from "react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Spinner } from "@/components/ui/spinner";
import { api } from "@/lib/api";
import { crusherBody } from "@/lib/crusher";
import type { CameraConn, Crusher, ProbeResult } from "@/lib/types";
import { useAction } from "@/lib/use-action";
import { SwitchField } from "./schema-form";

/** Вкладка «Камера»: адрес вшит в программу, на месте задаётся пароль (и при нужде — другой адрес). */
export function CameraPanel({ crusher, onSaved }: { crusher: Crusher; onSaved: () => void }) {
  const [conn, setConn] = useState<CameraConn>({ ...crusher.camera, password: null });
  const [enabled, setEnabled] = useState(crusher.enabled);
  const [probe, setProbe] = useState<ProbeResult | null>(null);
  const { busy, run } = useAction();
  const ids = { url: useId(), user: useId(), pass: useId(), transport: useId() };
  const set = (patch: Partial<CameraConn>) => setConn({ ...conn, ...patch });
  const dirty = Boolean(conn.password) || enabled !== crusher.enabled || JSON.stringify({ ...conn, password: null }) !== JSON.stringify({ ...crusher.camera, password: null });
  const custom = conn.url !== crusher.builtin_camera_url;

  const save = () =>
    run(
      "save",
      async () => {
        await api.put(`/crushers/${crusher.id}`, crusherBody(crusher, { camera: conn, enabled }));
        setConn((c) => ({ ...c, password: null, has_password: c.has_password || Boolean(c.password) }));
        onSaved();
      },
      "Сохранено — камера переподключится",
    );

  const check = () =>
    run("probe", async () => {
      setProbe(null);
      setProbe(await api.post<ProbeResult>("/crushers/probe", { camera: conn, crusher_id: crusher.id, seconds: 3 }));
    });

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle>Подключение</CardTitle>
          <CardDescription>Камера Hikvision у желоба. Адрес вшит в программу — здесь нужен только пароль.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-5">
          {!crusher.camera.has_password && conn.username && (
            <Alert variant="destructive">
              <KeyRound />
              <AlertTitle>Пароль камеры не задан</AlertTitle>
              <AlertDescription>Без него сервер не подключается к камере — иначе Hikvision заблокирует IP за неверные входы.</AlertDescription>
            </Alert>
          )}
          <Field>
            <FieldLabel htmlFor={ids.url}>Адрес потока (RTSP)</FieldLabel>
            <Input id={ids.url} value={conn.url} onChange={(e) => set({ url: e.target.value })} className="font-mono text-xs" />
            <FieldDescription>
              {custom ? (
                <span className="flex flex-wrap items-center gap-2">
                  Отличается от программы ({crusher.builtin_camera_url}).
                  <Button variant="link" size="xs" className="h-auto p-0" onClick={() => set({ url: crusher.builtin_camera_url })}>
                    <RotateCcw /> Вернуть
                  </Button>
                </span>
              ) : (
                "Как в программе. Меняйте, только если у камеры сменился IP."
              )}
            </FieldDescription>
          </Field>
          <div className="grid gap-5 sm:grid-cols-2">
            <Field>
              <FieldLabel htmlFor={ids.user}>Логин</FieldLabel>
              <Input id={ids.user} value={conn.username ?? ""} onChange={(e) => set({ username: e.target.value || null })} />
            </Field>
            <Field>
              <FieldLabel htmlFor={ids.pass}>Пароль</FieldLabel>
              <Input
                id={ids.pass}
                type="password"
                autoComplete="new-password"
                placeholder={conn.has_password ? "сохранён — пусто = не менять" : "не задан"}
                value={conn.password ?? ""}
                onChange={(e) => set({ password: e.target.value || null })}
              />
            </Field>
          </div>
          <Field>
            <FieldLabel htmlFor={ids.transport}>Транспорт RTSP</FieldLabel>
            <NativeSelect id={ids.transport} value={conn.rtsp_transport} onChange={(e) => set({ rtsp_transport: e.target.value as CameraConn["rtsp_transport"] })}>
              <NativeSelectOption value="tcp">TCP — надёжнее</NativeSelectOption>
              <NativeSelectOption value="udp">UDP</NativeSelectOption>
            </NativeSelect>
          </Field>
        </CardContent>
        <CardFooter className="flex flex-wrap gap-2 border-t">
          <Button disabled={!dirty || busy === "save"} onClick={save}>
            {busy === "save" ? <Spinner /> : <Save />}
            Сохранить
          </Button>
          <Button variant="outline" disabled={busy === "probe"} onClick={check}>
            {busy === "probe" ? <Spinner /> : <PlugZap />}
            Проверить подключение
          </Button>
        </CardFooter>
      </Card>

      <div className="flex flex-col gap-4">
        {probe && (
          <Card size="sm">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                {probe.ok ? <CircleCheck className="size-4 text-emerald-600" /> : <CircleAlert className="size-4 text-destructive" />}
                {probe.ok ? "Камера отвечает" : "Не подключилась"}
              </CardTitle>
              <CardDescription>
                {probe.ok ? `${probe.width}×${probe.height}, ${probe.codec}, ${probe.measured_fps} к/с, подключение за ${probe.connect_s} с` : probe.error}
              </CardDescription>
            </CardHeader>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            {probe.image && <img src={`data:image/jpeg;base64,${probe.image}`} alt="Кадр проверки" className="w-full" />}
          </Card>
        )}
        <Card>
          <CardHeader>
            <CardTitle>Наблюдение</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <SwitchField checked={enabled} onChange={setEnabled} label="Следить за этой дробилкой" hint="Выключенная не подключается к камере и не оповещает — например, на время ремонта" />
            <p className="text-xs text-muted-foreground">
              Пароли хранятся отдельно, только для владельца: <code className="rounded bg-muted px-1">{crusher.folder}/secrets.json</code>. Без браузера:{" "}
              <code className="rounded bg-muted px-1">track-seeds password {crusher.id}</code>
            </p>
          </CardContent>
        </Card>
        {crusher.config_error && (
          <Alert variant="destructive">
            <CircleAlert />
            <AlertTitle>Настройки этой дробилки не прочитались</AlertTitle>
            <AlertDescription>Она работает с настройками из программы. {crusher.config_error}</AlertDescription>
          </Alert>
        )}
      </div>
    </div>
  );
}
