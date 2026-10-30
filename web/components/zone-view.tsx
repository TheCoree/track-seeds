"use client";

import { Loader2, ScanEye, Video, WifiOff } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { Skeleton } from "@/components/ui/skeleton";
import { percent } from "@/lib/format";
import type { CrusherView, SeedZone, ZoneStatus } from "@/lib/types";
import { cn } from "@/lib/utils";
import { LiveCanvas, type LiveState } from "./live-view";
import { Segmented, type SegmentOption } from "./segmented";

const COLORS = {
  covered: "#10b981",
  uncovered: "#ef4444",
  unknown: "#f59e0b",
  moving: "#0ea5e9",
  still: "#64748b",
};

function zoneColor(zone: SeedZone, status: ZoneStatus | undefined) {
  if (zone.kind === "motion") return status?.state === "covered" ? COLORS.moving : status?.state === "uncovered" ? COLORS.still : COLORS.unknown;
  if (!status || status.reading === "unknown") return COLORS.unknown;
  return (status.confidence ?? 0) >= 0.5 ? COLORS.covered : COLORS.uncovered;
}

function zoneValue(zone: SeedZone, status: ZoneStatus | undefined) {
  const value = zone.kind === "motion" ? status?.motion : status?.confidence;
  return value != null ? percent(value) : "";
}

/** Новый кадр подменяет старый только после загрузки — картинка не мигает. */
function useSmoothImage(src: string | null) {
  const [shown, setShown] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!src) return;
    const img = new Image();
    img.onload = () => {
      setShown(src);
      setFailed(false);
    };
    img.onerror = () => setFailed(true);
    img.src = src;
    return () => {
      img.onload = img.onerror = null;
    };
  }, [src]);
  return { shown, failed };
}

export type CameraMode = "zones" | "live";
const MODE_KEY = "track-seeds:camera-mode";
const MODE_EVENT = "track-seeds-camera-mode";

/** Как смотреть камеру дробилки: с зонами (по умолчанию) или живое видео — у каждой дробилки своё,
 *  запоминается в этом браузере; карточка в «Обзоре» и страница той же дробилки показывают одно и то же. */
export function useCameraMode(crusherId: string): [CameraMode, (mode: CameraMode) => void] {
  const [mode, setMode] = useState<CameraMode>("zones");
  const key = `${MODE_KEY}:${crusherId}`;
  useEffect(() => {
    try {
      setMode(localStorage.getItem(key) === "live" ? "live" : "zones");
    } catch {
      /* приватный режим */
    }
    const listener = (e: Event) => {
      const { id, mode: next } = (e as CustomEvent<{ id: string; mode: CameraMode }>).detail;
      if (id === crusherId) setMode(next);
    };
    window.addEventListener(MODE_EVENT, listener);
    return () => window.removeEventListener(MODE_EVENT, listener);
  }, [key, crusherId]);
  const set = (value: CameraMode) => {
    setMode(value);
    try {
      localStorage.setItem(key, value);
    } catch {
      /* ignore */
    }
    window.dispatchEvent(new CustomEvent(MODE_EVENT, { detail: { id: crusherId, mode: value } }));
  };
  return [mode, set];
}

const MODES: SegmentOption<CameraMode>[] = [
  { value: "zones", label: "Зоны", hint: "Кадр раз в секунду с зонами и уверенностью", icon: ScanEye },
  { value: "live", label: "Оригинал", hint: "Видео с камеры как есть, без обработки", icon: Video },
];

/**
 * Камера дробилки: «Зоны» — кадр раз в секунду с зонами и состоянием (зоны вшиты в программу — здесь только
 * показ), «Оригинал» — живое видео с камеры как есть. href — карточка ведёт на страницу дробилки.
 */
export function ZoneView({ crusher, tick, className, href }: { crusher: CrusherView; tick: number; className?: string; href?: string }) {
  const [mode, setMode] = useCameraMode(crusher.id);
  const [live, setLive] = useState<{ state: LiveState; error: string | null }>({ state: "connecting", error: null });
  const online = crusher.enabled && crusher.status.connected;
  const zonesMode = mode === "zones";
  const { shown, failed } = useSmoothImage(online && zonesMode ? `/api/crushers/${crusher.id}/frame.jpg?w=1280&t=${tick}` : null);
  const statuses = crusher.status.seeds?.zones ?? [];
  const zones = crusher.seeds.zones;

  if (!online || (zonesMode && failed && !shown)) {
    return (
      <Empty className={cn("aspect-video rounded-xl border bg-card", className)}>
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <WifiOff />
          </EmptyMedia>
          <EmptyTitle>{crusher.enabled ? "Нет изображения" : "Наблюдение выключено"}</EmptyTitle>
          <EmptyDescription>{crusher.status.error ?? (crusher.enabled ? "Камера не подключена" : "Дробилка временно не наблюдается")}</EmptyDescription>
        </EmptyHeader>
      </Empty>
    );
  }

  return (
    <div className={cn("camera-frame relative isolate aspect-video w-full overflow-hidden rounded-xl", className)}>
      {href && <Link href={href} className="absolute inset-0 z-10" aria-label={`${crusher.name}: подробнее`} />}
      <Segmented label="Как смотреть камеру" tone="dark" value={mode} onChange={setMode} options={MODES} className="absolute left-2 top-2 z-20" />
      {!zonesMode ? (
        <>
          <LiveCanvas url={`/api/crushers/${crusher.id}/live.mjpg`} label={`Камера: ${crusher.name}, видео`} onState={(state, error) => setLive({ state, error })} />
          {live.state !== "live" && (
            <div className="absolute inset-0 flex items-center justify-center gap-2 text-sm text-white/90">
              {live.state === "error" ? live.error : <><Loader2 className="size-4 animate-spin" /> подключение к видео…</>}
            </div>
          )}
          {live.state === "live" && (
            <span className="pointer-events-none absolute bottom-2 left-2 z-20 flex items-center gap-1.5 rounded-md bg-black/55 px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-white">
              <span className="size-1.5 animate-pulse rounded-full bg-red-500" /> live
            </span>
          )}
        </>
      ) : (
        <>
          {shown ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={shown} alt={`Камера: ${crusher.name}`} className="absolute inset-0 size-full object-cover" draggable={false} />
          ) : (
            <Skeleton className="absolute inset-0 rounded-none" />
          )}
          <svg className="absolute inset-0 size-full" viewBox="0 0 1000 1000" preserveAspectRatio="none">
            {zones.map((zone) => {
              const color = zoneColor(zone, statuses.find((s) => s.id === zone.id));
              return (
                <polygon
                  key={zone.id}
                  points={zone.points.map(([x, y]) => `${x * 1000},${y * 1000}`).join(" ")}
                  fill={color}
                  fillOpacity={0.18}
                  stroke={color}
                  strokeWidth={2.5}
                  vectorEffect="non-scaling-stroke"
                />
              );
            })}
          </svg>
          {zones.map((zone) => {
            const status = statuses.find((s) => s.id === zone.id);
            // подпись желоба — над зоной, привода — под ней: зоны рядом, подписи не налезают на соседнюю
            const below = zone.kind === "motion";
            const anchor = zone.points.reduce((a, b) => ((below ? b[1] > a[1] : b[1] < a[1]) ? b : a));
            return (
              <div
                key={zone.id}
                className={cn(
                  "pointer-events-none absolute -translate-x-1/2 whitespace-nowrap rounded-md px-1.5 py-0.5 text-[11px] font-semibold text-white shadow sm:text-xs",
                  !below && "-translate-y-full",
                )}
                style={{
                  left: `${anchor[0] * 100}%`,
                  top: below ? `calc(${anchor[1] * 100}% + 4px)` : `calc(${anchor[1] * 100}% - 4px)`,
                  background: zoneColor(zone, status),
                }}
              >
                {zone.name}
                {zoneValue(zone, status) && <span className="ml-1 tabular-nums opacity-90">· {zoneValue(zone, status)}</span>}
              </div>
            );
          })}
        </>
      )}
    </div>
  );
}
