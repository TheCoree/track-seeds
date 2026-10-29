"use client";

import { ArrowRight, CircleAlert } from "lucide-react";
import Link from "next/link";
import { useApp } from "@/components/app-shell";
import { ConnectionBadge, LevelHero, ZoneLine } from "@/components/crusher-status";
import { EventsList } from "@/components/events-list";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { buttonVariants } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ZoneView } from "@/components/zone-view";
import { useTick } from "@/lib/api";
import type { Crusher } from "@/lib/types";

export default function Dashboard() {
  const { overview } = useApp();
  const tick = useTick(2000);

  if (!overview)
    return (
      <div className="grid gap-4 md:grid-cols-2">
        <Skeleton className="h-96 rounded-xl" />
        <Skeleton className="h-96 rounded-xl" />
      </div>
    );

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Дробилки</h1>
        <p className="text-sm text-muted-foreground">Семена в желобе и работа привода — обновляется каждые 2 секунды</p>
      </div>

      {overview.notify_error && (
        <Alert variant="destructive">
          <CircleAlert />
          <AlertTitle>Уведомления на телефон не настроены</AlertTitle>
          <AlertDescription>
            {overview.notify_error}.{" "}
            <Link href="/settings" className="underline underline-offset-2">
              Исправить
            </Link>
          </AlertDescription>
        </Alert>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        {overview.crushers.map((c) => (
          <CrusherCard key={c.id} crusher={c} tick={tick} now={overview.time} />
        ))}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Последние события</CardTitle>
          <CardAction>
            <Link href="/events" className={buttonVariants({ variant: "ghost", size: "sm" })}>
              Все события <ArrowRight />
            </Link>
          </CardAction>
        </CardHeader>
        <CardContent>
          <EventsList events={overview.events} crushers={overview.crushers} compact />
        </CardContent>
      </Card>
    </div>
  );
}

function CrusherCard({ crusher, tick, now }: { crusher: Crusher; tick: number; now: number }) {
  const zones = crusher.status.seeds?.zones ?? [];
  return (
    <Card className="pt-0">
      <Link href={`/crusher?id=${crusher.id}`} className="relative block">
        <ZoneView crusher={crusher} tick={tick} className="rounded-none rounded-t-xl border-0" />
        <div className="absolute right-2 top-2">
          <ConnectionBadge crusher={crusher} />
        </div>
      </Link>
      <CardHeader>
        <CardTitle className="text-lg">{crusher.name}</CardTitle>
        <CardAction>
          <Link href={`/crusher?id=${crusher.id}`} className={buttonVariants({ variant: "outline", size: "sm" })}>
            Подробнее <ArrowRight />
          </Link>
        </CardAction>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <LevelHero crusher={crusher} now={now} />
        {zones.length > 0 && (
          <div className="flex flex-col gap-2.5">
            {zones.map((z) => (
              <ZoneLine key={z.id} zone={z} bandLow={crusher.status.seeds?.confidence_band[0]} />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
