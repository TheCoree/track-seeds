import { BellRing, CirclePause, CirclePlay, EyeOff, FlaskConical, Info, type LucideIcon, Video, VideoOff, Wheat, WheatOff } from "lucide-react";
import type { Tone } from "./format";

/** Вид события (сервер: eventlog.KINDS) — что случилось. Порядок — порядок кнопок фильтра. */
export type EventKind = "empty" | "reminder" | "low" | "unknown" | "stopped" | "started" | "refill" | "offline" | "online" | "test" | "other";

export const EVENT_KINDS: Record<EventKind, { label: string; icon: LucideIcon; tone: Tone; adminOnly?: boolean }> = {
  empty: { label: "Закончились", icon: WheatOff, tone: "red" },
  reminder: { label: "Всё ещё пусто", icon: BellRing, tone: "red" },
  low: { label: "Заканчиваются", icon: Wheat, tone: "amber" },
  unknown: { label: "Не видно желоб", icon: EyeOff, tone: "gray" },
  stopped: { label: "Остановилась", icon: CirclePause, tone: "blue" },
  started: { label: "Запустилась", icon: CirclePlay, tone: "green" },
  refill: { label: "Досыпали", icon: Wheat, tone: "green" },
  offline: { label: "Нет связи", icon: VideoOff, tone: "gray" },
  online: { label: "Связь есть", icon: Video, tone: "gray" },
  test: { label: "Проверки", icon: FlaskConical, tone: "gray", adminOnly: true },
  other: { label: "Прочее", icon: Info, tone: "gray" },
};

export const KIND_ORDER = Object.keys(EVENT_KINDS) as EventKind[];

/** Цвет иконки вида события — как точки состояний. */
export const KIND_ICON: Record<Tone, string> = {
  green: "text-emerald-600 dark:text-emerald-400",
  amber: "text-amber-600 dark:text-amber-400",
  red: "text-red-600 dark:text-red-400",
  gray: "text-muted-foreground",
  blue: "text-sky-600 dark:text-sky-400",
};
