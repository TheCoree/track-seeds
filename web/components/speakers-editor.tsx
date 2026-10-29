"use client";

import { Megaphone, Plus, Radio, Speaker as SpeakerIcon, Trash2 } from "lucide-react";
import { useId, useState } from "react";
import { Button } from "@/components/ui/button";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Spinner } from "@/components/ui/spinner";
import { api } from "@/lib/api";
import type { Speaker } from "@/lib/types";
import { useAction } from "@/lib/use-action";
import { SwitchField } from "./schema-form";
import { useSpeakerSay } from "./voice";

const NEW: Record<Speaker["kind"], Omit<Speaker, "id">> = {
  hikvision: { name: "Комната отдыха", kind: "hikvision", device: "", host: "", port: 80, https: false, username: "admin", password: null, channel: 1, timeout_s: 10 },
  local: { name: "Динамик у сервера", kind: "local", device: "", host: "", port: 80, https: false, username: "", password: null, channel: 1, timeout_s: 10 },
};

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

/** Куда говорить голосом: камеры Hikvision с динамиком и/или динамик, подключённый к серверу. */
export function SpeakersEditor({
  crusherId,
  value,
  saved,
  onChange,
}: {
  crusherId: string;
  value: Speaker[];
  saved: Speaker[];
  onChange: (speakers: Speaker[]) => void;
}) {
  const [phrase, setPhrase] = useState("Проверка связи. Это тестовое оповещение.");
  const [probe, setProbe] = useState<Record<string, string>>({});
  const { busy, run } = useAction();
  const speakerSay = useSpeakerSay();

  const set = (index: number, patch: Partial<Speaker>) => onChange(value.map((s, i) => (i === index ? { ...s, ...patch } : s)));
  // проверять можно только сохранённый динамик — сервер говорит теми настройками, что записаны
  const isSaved = (s: Speaker) => {
    const stored = saved.find((x) => x.id === s.id);
    return Boolean(stored) && JSON.stringify({ ...stored, password: null }) === JSON.stringify({ ...s, password: null }) && !s.password;
  };

  const check = (s: Speaker) =>
    run(`probe-${s.id}`, async () => {
      const r = await api.post<{ ok: boolean; error?: string; info?: { codec?: string; supported?: boolean; player?: string } }>(
        `/crushers/${crusherId}/speakers/${s.id}/probe`,
      );
      const text = !r.ok
        ? `✗ ${r.error}`
        : r.info?.player
          ? `✓ звук через ${r.info.player}`
          : r.info?.supported
            ? `✓ связь есть, кодек ${r.info.codec}`
            : `✗ кодек ${r.info?.codec} не поддерживается — поставьте G.711ulaw в настройках аудио камеры`;
      setProbe((p) => ({ ...p, [s.id]: text }));
    });

  return (
    <div className="flex flex-col gap-4">
      {!value.length && (
        <Empty className="border border-dashed py-8">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <SpeakerIcon />
            </EmptyMedia>
            <EmptyTitle>Динамиков нет</EmptyTitle>
            <EmptyDescription>Эта дробилка оповещает только уведомлениями на телефон и в браузере.</EmptyDescription>
          </EmptyHeader>
        </Empty>
      )}
      {value.map((s, index) => (
        <div key={s.id || index} className="flex flex-col gap-4 rounded-xl border bg-muted/30 p-4">
          <div className="grid items-end gap-4 sm:grid-cols-[1fr_1fr_auto]">
            <TextField label="Название" value={s.name} onChange={(e) => set(index, { name: e.target.value })} />
            <Field>
              <FieldLabel>Что это</FieldLabel>
              <NativeSelect className="w-full" value={s.kind} onChange={(e) => set(index, { kind: e.target.value as Speaker["kind"] })}>
                <NativeSelectOption value="hikvision">Динамик IP-камеры Hikvision</NativeSelectOption>
                <NativeSelectOption value="local">Динамик, подключённый к серверу</NativeSelectOption>
              </NativeSelect>
            </Field>
            <Button variant="ghost" size="icon" aria-label="Убрать динамик" onClick={() => onChange(value.filter((_, i) => i !== index))}>
              <Trash2 />
            </Button>
          </div>
          {s.kind === "hikvision" ? (
            <div className="grid gap-4 sm:grid-cols-3">
              <TextField label="IP камеры" value={s.host} placeholder="192.168.1.50" onChange={(e) => set(index, { host: e.target.value.trim() })} />
              <TextField label="Логин" hint="С правом «Двусторонний звук»" value={s.username} onChange={(e) => set(index, { username: e.target.value })} />
              <TextField
                label="Пароль"
                hint="При ошибке повторов не будет — камера не заблокирует IP"
                type="password"
                autoComplete="new-password"
                placeholder={s.has_password ? "сохранён — пусто = не менять" : ""}
                value={s.password ?? ""}
                onChange={(e) => set(index, { password: e.target.value || null })}
              />
              <TextField label="HTTP-порт" inputMode="numeric" value={String(s.port)} onChange={(e) => set(index, { port: Number(e.target.value) || 80 })} />
              <TextField label="Канал звука" hint="У камеры — 1" inputMode="numeric" value={String(s.channel)} onChange={(e) => set(index, { channel: Number(e.target.value) || 1 })} />
              <div className="sm:pt-6">
                <SwitchField label="HTTPS" checked={s.https} onChange={(v) => set(index, { https: v, port: v && s.port === 80 ? 443 : s.port })} />
              </div>
            </div>
          ) : (
            <TextField
              label="Звуковая карта (ALSA)"
              hint="Пусто — по умолчанию. Если звука нет: на сервере aplay -l покажет карты, впишите, например, plughw:1,0"
              value={s.device}
              placeholder="по умолчанию"
              onChange={(e) => set(index, { device: e.target.value.trim() })}
            />
          )}
          <div className="flex flex-wrap items-center gap-2">
            {isSaved(s) ? (
              <>
                <Button variant="outline" size="sm" disabled={busy === `probe-${s.id}`} onClick={() => check(s)}>
                  {busy === `probe-${s.id}` ? <Spinner /> : <Radio />}
                  Проверить связь
                </Button>
                <Button variant="outline" size="sm" disabled={speakerSay.busy} onClick={() => speakerSay.say(phrase, [{ crusherId, id: s.id, name: s.name }])}>
                  {speakerSay.busy ? <Spinner /> : <Megaphone />}
                  Сказать фразу
                </Button>
                {probe[s.id] && <span className="text-sm">{probe[s.id]}</span>}
              </>
            ) : (
              <span className="text-xs text-muted-foreground">Сохраните, чтобы проверить этот динамик</span>
            )}
          </div>
        </div>
      ))}
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" size="sm" onClick={() => onChange([...value, { ...NEW.hikvision, id: "" }])}>
          <Plus /> Динамик камеры Hikvision
        </Button>
        <Button variant="outline" size="sm" onClick={() => onChange([...value, { ...NEW.local, id: "" }])}>
          <Plus /> Динамик у сервера
        </Button>
      </div>
      {value.some(isSaved) && <TextField label="Фраза для «Сказать фразу»" value={phrase} onChange={(e) => setPhrase(e.target.value)} />}
    </div>
  );
}
