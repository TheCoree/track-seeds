"use client";

import { BellRing, Camera, CircleAlert, Eye, Images } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { AlertsForm } from "@/components/alerts-form";
import { useApp } from "@/components/app-shell";
import { CameraPanel } from "@/components/camera-panel";
import { CrusherMonitor } from "@/components/crusher-monitor";
import { ConnectionBadge } from "@/components/crusher-status";
import { SamplesPanel } from "@/components/samples-panel";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { usePoll } from "@/lib/api";
import type { Crusher } from "@/lib/types";

const TABS = [
  ["monitor", "Наблюдение", Eye],
  ["samples", "Эталоны", Images],
  ["alerts", "Оповещения", BellRing],
  ["camera", "Камера", Camera],
] as const;
type Tab = (typeof TABS)[number][0];

export default function CrusherPage() {
  return (
    <Suspense fallback={<Skeleton className="h-96 rounded-xl" />}>
      <CrusherPageInner />
    </Suspense>
  );
}

function CrusherPageInner() {
  const params = useSearchParams();
  const router = useRouter();
  const id = params.get("id");
  const { meta, reloadOverview, overview } = useApp();
  const { data: crusher, error, reload } = usePoll<Crusher>(id ? `/crushers/${id}` : null, 1500);

  if (!id) return <Problem text="Не указана дробилка" />;
  if (!crusher) return error ? <Problem text={error} /> : <Skeleton className="h-96 rounded-xl" />;

  const requested = params.get("tab") as Tab;
  const tab: Tab = TABS.some(([t]) => t === requested) ? requested : "monitor";
  const reloadAll = () => {
    reload();
    reloadOverview();
  };
  const st = crusher.status;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">{crusher.name}</h1>
        <ConnectionBadge crusher={crusher} />
        {st.width ? (
          <span className="text-sm text-muted-foreground">
            {st.width}×{st.height}
            {st.timing_ms ? ` · анализ ${Math.round(st.timing_ms)} мс` : ""}
          </span>
        ) : null}
      </div>
      {st.error && <Problem text={st.error} />}
      {st.analyzer_error && <Problem text={`Анализ падает: ${st.analyzer_error}`} />}

      <Tabs value={tab} onValueChange={(t) => router.replace(`/crusher?id=${id}&tab=${t}`, { scroll: false })}>
        <TabsList className="w-full justify-start overflow-x-auto sm:w-fit">
          {TABS.map(([value, label, Icon]) => (
            <TabsTrigger key={value} value={value}>
              <Icon /> {label}
            </TabsTrigger>
          ))}
        </TabsList>
        <TabsContent value="monitor" className="pt-2">
          <CrusherMonitor crusher={crusher} now={overview?.time ?? Date.now() / 1000} />
        </TabsContent>
        <TabsContent value="samples" className="pt-2">
          <SamplesPanel crusher={crusher} onSaved={reloadAll} />
        </TabsContent>
        <TabsContent value="alerts" className="pt-2">
          <AlertsForm key={crusher.id} crusher={crusher} meta={meta} onSaved={reloadAll} />
        </TabsContent>
        <TabsContent value="camera" className="pt-2">
          <CameraPanel key={crusher.id} crusher={crusher} onSaved={reloadAll} />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function Problem({ text }: { text: string }) {
  return (
    <Alert variant="destructive">
      <CircleAlert />
      <AlertTitle>Проблема</AlertTitle>
      <AlertDescription>{text}</AlertDescription>
    </Alert>
  );
}
