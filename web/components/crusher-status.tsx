"use client";

import {
  Activity,
  CircleCheck,
  CirclePause,
  Cog,
  EyeOff,
  Loader2,
  type LucideIcon,
  OctagonAlert,
  OctagonX,
  PowerOff,
  TriangleAlert,
  Wheat,
  Wifi,
  WifiOff,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { duration, LEVELS, percent, REASONS, since, TONE_DOT, TONE_SOLID, TONE_SOFT, type Tone } from "@/lib/format";
import type { Crusher, Level, SeedsStatus, ZoneStatus } from "@/lib/types";
import { cn } from "@/lib/utils";

export interface CrusherState {
  tone: Tone;
  title: string;
  short: string;
  Icon: LucideIcon;
  spinning?: boolean;
  detail?: string | null;
  dot: string;
}

const LEVEL_ICON: Record<Level, LucideIcon> = {
  ok: CircleCheck,
  low: TriangleAlert,
  empty: OctagonAlert,
  unknown: EyeOff,
  stopped: CirclePause,
};

const LEVEL_SHORT: Record<Level, string> = {
  ok: "семена есть",
  low: "семян мало",
  empty: "семена закончились",
  unknown: "не видно желоб",
  stopped: "стоит",
};

function state(tone: Tone, title: string, short: string, Icon: LucideIcon, extra: Partial<CrusherState> = {}): CrusherState {
  return { tone, title, short, Icon, dot: TONE_DOT[tone], ...extra };
}

/** Что показать о дробилке одной строкой: связь, ошибка или уровень семян. */
export function crusherState(c: Crusher): CrusherState {
  const st = c.status;
  if (!c.enabled) return state("gray", "Наблюдение выключено", "выключена", PowerOff);
  if (st.state === "error") return state("red", "Ошибка", "ошибка", OctagonX, { detail: st.error });
  if (!st.connected) return state("red", st.state === "starting" ? "Подключение к камере…" : "Нет связи с камерой", "нет связи", WifiOff);
  const level = st.seeds?.level;
  if (!level) return state("gray", "Определяется…", "определяется", Loader2, { spinning: true });
  return state(LEVELS[level].tone, LEVELS[level].text, LEVEL_SHORT[level], LEVEL_ICON[level]);
}

/** Главная зона желоба (по ней решается «есть / закончились»). */
export function hopperZone(seeds: SeedsStatus | null | undefined): ZoneStatus | undefined {
  const zones = seeds?.zones ?? [];
  return zones.find((z) => z.kind === "empty") ?? zones.find((z) => z.kind === "low");
}

/** Насколько система уверена в том, что показывает: семена есть — уверенность «есть», пусто — «пусто». */
export function certainty(seeds: SeedsStatus | null | undefined): number | null {
  const zone = hopperZone(seeds);
  if (!seeds || !zone || zone.confidence === null || zone.reading === "unknown") return null;
  if (seeds.level === "ok") return zone.confidence;
  if (seeds.level === "empty" || seeds.level === "low") return 1 - zone.confidence;
  return null;
}

export function LevelHero({ crusher, now }: { crusher: Crusher; now: number }) {
  const s = crusherState(crusher);
  const seeds = crusher.status.seeds;
  const sure = certainty(seeds);
  const hasDrive = seeds?.zones.some((z) => z.kind === "motion");
  const levelSince = seeds?.level ? seeds.level_since : null;
  return (
    <div className={cn("rounded-xl p-4 shadow-sm sm:p-5", TONE_SOLID[s.tone])}>
      <div className="flex items-start gap-3">
        <s.Icon className={cn("size-9 shrink-0 sm:size-10", s.spinning && "animate-spin")} />
        <div className="min-w-0">
          <div className="text-xl font-semibold leading-tight sm:text-2xl">{s.title}</div>
          <div className="mt-0.5 text-sm opacity-90">
            {s.detail ?? (levelSince ? `уже ${since(levelSince, now)}` : seeds?.level === null ? "нужно несколько секунд наблюдения" : "")}
            {seeds?.level === "stopped" && " · о семенах не сообщается, пока не запустят"}
          </div>
        </div>
      </div>
      {sure !== null && (
        <div className="mt-4">
          <div className="mb-1.5 flex items-baseline justify-between text-sm">
            <span className="opacity-90">Уверенность</span>
            <span className="text-lg font-semibold tabular-nums">{percent(sure)}</span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-white/25">
            <div className="h-full rounded-full bg-white transition-[width] duration-500" style={{ width: `${Math.max(2, sure * 100)}%` }} />
          </div>
        </div>
      )}
      {hasDrive && crusher.status.connected && seeds?.level !== "stopped" && (
        <div className="mt-3 flex items-center gap-2 border-t border-white/20 pt-3 text-sm">
          <Activity className={cn("size-4", seeds?.running && "animate-pulse")} />
          {seeds?.running === true
            ? `Дробилка работает${seeds.running_since ? ` · ${duration(now - seeds.running_since)}` : ""}`
            : seeds?.running === false
              ? "Дробилка стоит"
              : "Работает ли дробилка — определяется"}
        </div>
      )}
    </div>
  );
}

/** Строка зоны: желоб — уверенность «семена есть», привод — движение. */
export function ZoneLine({ zone, bandLow = 0.35 }: { zone: ZoneStatus; bandLow?: number }) {
  const drive = zone.kind === "motion";
  const Icon = drive ? Cog : Wheat;
  const value = drive ? zone.motion : zone.confidence;
  const ok = drive ? zone.state === "covered" : (zone.confidence ?? 0) >= 0.5;
  const stateText = drive
    ? zone.state === "covered"
      ? "крутится"
      : zone.state === "uncovered"
        ? "стоит"
        : REASONS[zone.reason] || "определяется"
    : zone.reading === "unknown"
      ? REASONS[zone.reason] || "не определено"
      : zone.state === "covered"
        ? "семена есть"
        : zone.state === "uncovered"
          ? "пусто"
          : "определяется";
  const tone: Tone = drive ? (zone.state === "covered" ? "blue" : "gray") : zone.reading === "unknown" ? "gray" : ok ? "green" : (zone.confidence ?? 1) < bandLow ? "red" : "amber";
  return (
    <div className="flex items-center gap-3 text-sm">
      <Icon className={cn("size-4 shrink-0 text-muted-foreground", drive && zone.state === "covered" && "animate-spin [animation-duration:3s]")} />
      <span className="min-w-0 flex-1 truncate">{zone.name}</span>
      <Badge variant="outline" className={cn("ring-1 ring-inset", TONE_SOFT[tone])}>
        {stateText}
      </Badge>
      <span className="w-16 shrink-0 whitespace-nowrap text-right tabular-nums text-muted-foreground">{value !== null ? percent(value) : "—"}</span>
    </div>
  );
}

export function ConnectionBadge({ crusher }: { crusher: Crusher }) {
  const st = crusher.status;
  if (!crusher.enabled)
    return (
      <Badge variant="secondary">
        <PowerOff /> выключена
      </Badge>
    );
  if (st.state === "error")
    return (
      <Badge variant="destructive">
        <OctagonX /> ошибка
      </Badge>
    );
  if (st.connected)
    return (
      <Badge variant="outline" className="bg-background/90 text-emerald-700 dark:text-emerald-300">
        <Wifi /> в сети{st.fps ? ` · ${st.fps} к/с` : ""}
      </Badge>
    );
  return (
    <Badge variant="destructive">
      <WifiOff /> {st.state === "starting" ? "подключение…" : "нет связи"}
    </Badge>
  );
}
