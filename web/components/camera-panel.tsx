"use client";

import { CircleAlert, CircleCheck, KeyRound, PlugZap, Save } from "lucide-react";
import { useId, useState } from "react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Spinner } from "@/components/ui/spinner";
import { api } from "@/lib/api";
import { crusherBody } from "@/lib/crusher";
import type { CameraConn, Crusher, ProbeResult } from "@/lib/types";
import { useAction } from "@/lib/use-action";
import { SettingRow, SettingsList, SwitchRow } from "./schema-form";

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
    <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
      <Card>
        <CardHeader>
          <CardTitle>Камера</CardTitle>
          <CardDescription>Hikvision у желоба. Адрес вшит в программу — на месте нужен только пароль.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {!crusher.camera.has_password && conn.username && (
            <Alert variant="destructive">
              <KeyRound />
              <AlertTitle>Пароль камеры не задан</AlertTitle>
              <AlertDescription>Без него сервер не подключается — иначе Hikvision заблокирует IP за неверные входы.</AlertDescription>
            </Alert>
          )}
          <SettingsList>
            <SwitchRow checked={enabled} onChange={setEnabled} label="Следить за дробилкой" hint="Выключенная не подключается к камере и не оповещает — например, на время ремонта" />
            <SettingRow label="Логин" htmlFor={ids.user}>
              <Input id={ids.user} value={conn.username ?? ""} onChange={(e) => set({ username: e.target.value || null })} />
            </SettingRow>
            <SettingRow label="Пароль" hint={`Хранится отдельно, только для владельца. Без браузера: track-seeds password ${crusher.id}`} htmlFor={ids.pass}>
              <Input
                id={ids.pass}
                type="password"
                autoComplete="new-password"
                placeholder={conn.has_password ? "сохранён" : "не задан"}
                value={conn.password ?? ""}
                onChange={(e) => set({ password: e.target.value || null })}
              />
            </SettingRow>
            <SettingRow
              label="Адрес потока"
              hint={
                custom ? (
                  <span>
                    Отличается от программы.{" "}
                    <button type="button" className="underline underline-offset-2" onClick={() => set({ url: crusher.builtin_camera_url })}>
                      Вернуть
                    </button>
                  </span>
                ) : (
                  "Как в программе. Меняйте, только если у камеры сменился IP."
                )
              }
              htmlFor={ids.url}
              wide
            >
              <Input id={ids.url} value={conn.url} onChange={(e) => set({ url: e.target.value })} className="font-mono text-xs" />
            </SettingRow>
            <SettingRow label="Транспорт RTSP" htmlFor={ids.transport}>
              <NativeSelect id={ids.transport} className="w-full" value={conn.rtsp_transport} onChange={(e) => set({ rtsp_transport: e.target.value as CameraConn["rtsp_transport"] })}>
                <NativeSelectOption value="tcp">TCP — надёжнее</NativeSelectOption>
                <NativeSelectOption value="udp">UDP</NativeSelectOption>
              </NativeSelect>
            </SettingRow>
          </SettingsList>
        </CardContent>
        <CardFooter className="flex flex-wrap gap-2 border-t">
          <Button variant={dirty ? "default" : "outline"} disabled={!dirty || busy === "save"} onClick={save}>
            {busy === "save" ? <Spinner /> : <Save />}
            {dirty ? "Сохранить" : "Сохранено"}
          </Button>
          <Button variant="outline" disabled={busy === "probe"} onClick={check}>
            {busy === "probe" ? <Spinner /> : <PlugZap />}
            Проверить подключение
          </Button>
        </CardFooter>
      </Card>

      <div className="flex flex-col gap-4">
        {probe ? (
          <Card size="sm" className="pt-0">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            {probe.image && <img src={`data:image/jpeg;base64,${probe.image}`} alt="Кадр проверки" className="w-full rounded-t-xl" />}
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                {probe.ok ? <CircleCheck className="size-4 text-emerald-600" /> : <CircleAlert className="size-4 text-destructive" />}
                {probe.ok ? "Камера отвечает" : "Не подключилась"}
              </CardTitle>
              <CardDescription>
                {probe.ok ? `${probe.width}×${probe.height}, ${probe.codec}, ${probe.measured_fps} к/с, подключение за ${probe.connect_s} с` : probe.error}
              </CardDescription>
            </CardHeader>
          </Card>
        ) : (
          <p className="rounded-xl border border-dashed p-4 text-sm text-muted-foreground">«Проверить подключение» — покажет кадр, разрешение и частоту, не сохраняя настройки.</p>
        )}
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
