import type { Level } from "./types";

export function duration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h) return `${h} ч ${m} мин`;
  if (m) return `${m} мин ${s} с`;
  return `${s} с`;
}

export function since(ts: number | null | undefined, now = Date.now() / 1000): string {
  return ts ? duration(now - ts) : "—";
}

export function clock(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString("ru-RU");
}

export function dateTime(ts: number): string {
  const d = new Date(ts * 1000);
  const today = new Date();
  const time = d.toLocaleTimeString("ru-RU");
  return d.toDateString() === today.toDateString()
    ? `сегодня ${time}`
    : `${d.toLocaleDateString("ru-RU", { day: "numeric", month: "short" })} ${time}`;
}

/** 0.9994 → «99,9 %», 0.42 → «42 %», 0.9998 → «100 %»: у края шкалы важны десятые. */
export function percent(value: number): string {
  const p = value * 100;
  if (p >= 99 && p < 99.95) return `${p.toFixed(1).replace(".", ",")} %`;
  return `${Math.round(p)} %`;
}

export type Tone = "green" | "amber" | "red" | "gray" | "blue";

export const LEVELS: Record<Level, { text: string; tone: Tone }> = {
  ok: { text: "Семена есть", tone: "green" },
  low: { text: "Семян мало", tone: "amber" },
  empty: { text: "Семена закончились", tone: "red" },
  unknown: { text: "Не видно желоб", tone: "gray" },
  stopped: { text: "Дробилка стоит", tone: "blue" },
};

/** Классы для цветных состояний — одинаковые по всему интерфейсу. */
export const TONE_SOLID: Record<Tone, string> = {
  green: "bg-emerald-600 text-white",
  amber: "bg-amber-500 text-white",
  red: "bg-red-600 text-white",
  gray: "bg-slate-500 text-white",
  blue: "bg-sky-600 text-white",
};

export const TONE_SOFT: Record<Tone, string> = {
  green: "bg-emerald-50 text-emerald-800 ring-emerald-600/20 dark:bg-emerald-950 dark:text-emerald-200",
  amber: "bg-amber-50 text-amber-900 ring-amber-600/20 dark:bg-amber-950 dark:text-amber-200",
  red: "bg-red-50 text-red-800 ring-red-600/20 dark:bg-red-950 dark:text-red-200",
  gray: "bg-muted text-muted-foreground ring-border",
  blue: "bg-sky-50 text-sky-800 ring-sky-600/20 dark:bg-sky-950 dark:text-sky-200",
};

export const TONE_DOT: Record<Tone, string> = {
  green: "bg-emerald-500",
  amber: "bg-amber-500",
  red: "bg-red-500",
  gray: "bg-slate-400",
  blue: "bg-sky-500",
};

export const REASONS: Record<string, string> = {
  start: "набирает данные",
  no_samples: "нет эталонов",
  people: "закрыта человеком",
  covered: "загорожена",
  dark: "слишком темно",
  novel: "не похоже на эталоны",
  border: "на границе",
  "": "",
};

/** 22320 → «6 ч 12 мин», 2700 → «45 мин», 20 → «0 мин» — для отчётов, без секунд. */
export function hoursMinutes(seconds: number): string {
  const minutes = Math.round(Math.max(0, seconds) / 60);
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (h && m) return `${h} ч ${m} мин`;
  if (h) return `${h} ч`;
  return `${m} мин`;
}

export function hhmm(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
}
