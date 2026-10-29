"use client";

import { WifiOff } from "lucide-react";
import { useEffect, useState } from "react";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { Skeleton } from "@/components/ui/skeleton";
import { percent } from "@/lib/format";
import type { Crusher, SeedZone, ZoneStatus } from "@/lib/types";
import { cn } from "@/lib/utils";

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

/** Кадр камеры с зонами дробилки (зоны вшиты в программу — здесь только показ). */
export function ZoneView({ crusher, tick, className }: { crusher: Crusher; tick: number; className?: string }) {
  const live = crusher.enabled && crusher.status.connected;
  const { shown, failed } = useSmoothImage(live ? `/api/crushers/${crusher.id}/frame.jpg?w=1280&t=${tick}` : null);
  const statuses = crusher.status.seeds?.zones ?? [];
  const zones = crusher.seeds.zones;

  if (!live || (failed && !shown)) {
    return (
      <Empty className={cn("aspect-video rounded-xl border bg-card", className)}>
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <WifiOff />
          </EmptyMedia>
          <EmptyTitle>{crusher.enabled ? "Нет изображения" : "Наблюдение выключено"}</EmptyTitle>
          <EmptyDescription>{crusher.status.error ?? (crusher.enabled ? "Камера не подключена — проверьте вкладку «Камера»" : "Включите его на вкладке «Камера»")}</EmptyDescription>
        </EmptyHeader>
      </Empty>
    );
  }

  return (
    <div className={cn("camera-frame relative aspect-video w-full overflow-hidden rounded-xl", className)}>
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
        const top = zone.points.reduce((a, b) => (b[1] < a[1] ? b : a));
        return (
          <div
            key={zone.id}
            className="pointer-events-none absolute -translate-y-full whitespace-nowrap rounded-md px-1.5 py-0.5 text-[11px] font-semibold text-white shadow sm:text-xs"
            style={{ left: `${top[0] * 100}%`, top: `calc(${top[1] * 100}% - 4px)`, background: zoneColor(zone, status) }}
          >
            {zone.name}
            {zoneValue(zone, status) && <span className="ml-1 tabular-nums opacity-90">· {zoneValue(zone, status)}</span>}
          </div>
        );
      })}
    </div>
  );
}
