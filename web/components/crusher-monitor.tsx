"use client";

import { Cog, Info, Wheat } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useTick } from "@/lib/api";
import { REASONS, since } from "@/lib/format";
import type { Crusher, ZoneStatus } from "@/lib/types";
import { HistoryChart } from "./history-chart";
import { LevelHero, ZoneLine } from "./crusher-status";
import { ZoneView } from "./zone-view";

/** Вкладка «Наблюдение»: живой кадр с зонами, состояние и история за 15 минут. */
export function CrusherMonitor({ crusher, now }: { crusher: Crusher; now: number }) {
  const tick = useTick(1000);
  const seeds = crusher.status.seeds;
  const statuses = seeds?.zones ?? [];
  const band = seeds?.confidence_band ?? [0.35, 0.65];
  const threshold = Number(crusher.seeds.motion_threshold ?? 0.05);
  const hopper = statuses.filter((z) => z.kind !== "motion");
  const oneClass = hopper.some((z) => z.samples.full > 0 && z.samples.empty === 0);

  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <div className="flex flex-col gap-4 lg:col-span-2">
        <ZoneView crusher={crusher} tick={tick} />
        {statuses.map((zone) => (
          <Card key={zone.id} size="sm">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                {zone.kind === "motion" ? <Cog className="size-4 text-sky-600" /> : <Wheat className="size-4 text-emerald-600" />}
                {zone.kind === "motion" ? `${zone.name}: движение за 15 минут` : `${zone.name}: уверенность «семена есть» за 15 минут`}
              </CardTitle>
              <CardDescription>
                {zone.kind === "motion"
                  ? `Доля зоны, изменившейся за секунду. Выше пунктира (${Math.round(threshold * 100)} %) — привод крутится.`
                  : "Выше 50 % — семена есть, ниже — пусто. «Закончились» — только если пусто держится всё время, заданное в «Оповещениях»."}
              </CardDescription>
            </CardHeader>
            <CardContent>
              <HistoryChart zone={zone} now={now} band={band} motionThreshold={threshold} />
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="flex flex-col gap-4">
        <LevelHero crusher={crusher} now={now} />
        <Card size="sm">
          <CardHeader>
            <CardTitle>Зоны</CardTitle>
            <CardDescription>Вшиты в программу — подобраны по записи с этой камеры</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            {statuses.map((zone) => (
              <div key={zone.id} className="flex flex-col gap-1">
                <ZoneLine zone={zone} bandLow={band[0]} />
                <ZoneDetails zone={zone} now={now} />
              </div>
            ))}
          </CardContent>
        </Card>
        {oneClass && (
          <Alert>
            <Info />
            <AlertTitle>Эталона «пусто» пока нет</AlertTitle>
            <AlertDescription>
              Желоб сравнивается с кадрами «семена есть»: сильно другая картинка — значит пусто. Когда желоб впервые опустеет, нажмите в журнале событий «сделать
              кадр эталоном → пусто» — сравнение станет ещё точнее.
            </AlertDescription>
          </Alert>
        )}
      </div>
    </div>
  );
}

function ZoneDetails({ zone, now }: { zone: ZoneStatus; now: number }) {
  const parts: string[] = [];
  if (zone.state_since) parts.push(`${zone.kind === "motion" ? (zone.state === "covered" ? "крутится" : "стоит") : zone.state === "covered" ? "есть" : "пусто"} ${since(zone.state_since, now)}`);
  if (zone.kind !== "motion") parts.push(`эталонов: ${zone.samples.full} «есть» / ${zone.samples.empty} «пусто»`);
  if (zone.reason && zone.reason !== "start") parts.push(REASONS[zone.reason] ?? zone.reason);
  if (zone.reason === "dark" && zone.brightness !== null) parts.push(`яркость ${zone.brightness}`);
  return <div className="pl-7 text-xs text-muted-foreground">{parts.join(" · ")}</div>;
}
