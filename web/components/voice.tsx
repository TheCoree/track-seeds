"use client";

import { AudioLines, CircleAlert, CircleCheck, Loader2 } from "lucide-react";
import { useCallback, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { toast } from "@/components/ui/toast";
import { api, usePoll } from "@/lib/api";
import { TONE_SOFT } from "@/lib/format";
import { cn } from "@/lib/utils";
import { useApp } from "./app-shell";

export interface VoiceStatus {
  engine: "piper" | "espeak" | null;
  voice: string | null;
  configured?: string;
  state: "idle" | "loading" | "ready" | "error";
  error: string | null;
}

/** Название голоса для людей: «Денис (мужской)» вместо ru_RU-denis-medium. */
export function useVoiceTitle() {
  const { meta } = useApp();
  return (voice: string | null | undefined) => (voice ? (meta?.voices[voice] ?? voice) : "—");
}

/**
 * Прослушать фразу в браузере. Звук запрашивается заново каждый раз (без кеша браузера) —
 * после смены голоса тот же текст звучит новым голосом. Первый раз новый голос скачивается.
 */
export function useVoicePlayer() {
  const title = useVoiceTitle();
  const [playing, setPlaying] = useState<string | null>(null);

  const play = useCallback(
    async (text: string) => {
      const phrase = text.trim();
      if (!phrase) return;
      setPlaying(phrase);
      try {
        const res = await fetch(`/api/voice/say.wav?text=${encodeURIComponent(phrase)}`, { cache: "no-store" });
        if (!res.ok) {
          let detail = `${res.status}`;
          try {
            detail = (await res.json()).detail;
          } catch {
            /* не JSON */
          }
          throw new Error(detail);
        }
        const engine = res.headers.get("X-Voice-Engine");
        const voice = res.headers.get("X-Voice");
        const url = URL.createObjectURL(await res.blob());
        const audio = new Audio(url);
        audio.onended = () => URL.revokeObjectURL(url);
        await audio.play();
        if (engine === "espeak")
          toast.add({ title: "Сказано запасным голосом espeak-ng", description: "Голос piper не загрузился — см. «Уведомления и голос»", type: "warning" });
        else toast.add({ title: `Голос: ${title(voice)}` });
      } catch (e) {
        toast.add({ title: "Не удалось прослушать", description: e instanceof Error ? e.message : String(e), type: "error" });
      } finally {
        setPlaying(null);
      }
    },
    [title],
  );

  return { play, playing };
}

export interface SpeakerTarget {
  crusherId: string;
  id: string;
  name: string;
}

/** Сказать фразу на динамиках (сохранённым голосом). Динамик должен быть сохранён. */
export function useSpeakerSay() {
  const [busy, setBusy] = useState(false);
  const say = useCallback(async (text: string, speakers: SpeakerTarget[]) => {
    if (!text.trim() || !speakers.length) return;
    setBusy(true);
    try {
      const results = await Promise.all(
        speakers.map(async (s) => ({
          s,
          r: await api.post<{ ok: boolean; error: string | null }>(`/crushers/${s.crusherId}/speakers/${s.id}/test`, { text }),
        })),
      );
      for (const { s, r } of results)
        toast.add(r.ok ? { title: `${s.name}: сказано`, type: "success" } : { title: s.name, description: r.error ?? "", type: "error" });
    } catch (e) {
      toast.add({ title: "Не получилось", description: e instanceof Error ? e.message : String(e), type: "error" });
    } finally {
      setBusy(false);
    }
  }, []);
  return { say, busy };
}

/** Состояние голоса: скачивается / готов / ошибка. */
export function VoiceStatusLine() {
  const title = useVoiceTitle();
  const { data } = usePoll<VoiceStatus>("/voice/status", 2000);
  if (!data) return null;
  const badge = (tone: keyof typeof TONE_SOFT, Icon: typeof AudioLines, text: string, spin = false) => (
    <Badge variant="outline" className={cn("h-auto whitespace-normal py-1 ring-1 ring-inset", TONE_SOFT[tone])}>
      <Icon className={cn(spin && "animate-spin")} />
      {text}
    </Badge>
  );
  if (data.state === "error" && data.engine === "espeak")
    return badge("amber", CircleAlert, `Голос ${title(data.configured)} не загрузился — говорит запасной espeak-ng. ${data.error ?? ""}`);
  if (data.state === "error") return badge("red", CircleAlert, `Синтез речи не работает: ${data.error}`);
  if (data.state === "loading") return badge("blue", Loader2, `Голос ${title(data.configured)} скачивается (~60 МБ, один раз)…`, true);
  if (data.engine === "espeak") return badge("gray", AudioLines, "Говорит espeak-ng");
  return badge("green", CircleCheck, `Голос ${title(data.voice)}: ${data.state === "ready" ? "готов" : "загрузится при первой фразе"}`);
}
