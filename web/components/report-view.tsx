"use client";

import { useState } from "react";
import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { type ChartConfig, ChartContainer, ChartTooltip } from "@/components/ui/chart";
import { hhmm, hoursMinutes } from "@/lib/format";
import type { CrusherReport, Period, Report, Totals, WorkState } from "@/lib/types";
import { cn } from "@/lib/utils";

/** Что показываем в отчёте: «не определено» — вместе с «нет связи» (оба — работа неизвестна). */
export type Shown = "running" | "idle" | "stopped" | "offline";

export const STATES: { key: Shown; label: string; color: string }[] = [
  { key: "running", label: "Работала", color: "var(--state-running)" },
  { key: "idle", label: "Без зерна", color: "var(--state-idle)" },
  { key: "stopped", label: "Стояла", color: "var(--state-stopped)" },
  { key: "offline", label: "Нет связи", color: "var(--state-offline)" },
];

const SHOWN: Record<WorkState, Shown> = { running: "running", idle: "idle", stopped: "stopped", offline: "offline", unknown: "offline" };

export function shownTotals(t: Totals): Record<Shown, number> & { nodata: number } {
  return { running: t.running, idle: t.idle, stopped: t.stopped, offline: t.offline + t.unknown, nodata: t.nodata };
}

export function Legend() {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-muted-foreground">
      {STATES.map((s) => (
        <span key={s.key} className="flex items-center gap-1.5">
          <span className="size-3 rounded-sm" style={{ background: s.color }} />
          {s.label}
        </span>
      ))}
      <span className="flex items-center gap-1.5">
        <span className="size-3 rounded-sm border border-dashed border-muted-foreground/50" />
        нет данных
      </span>
    </div>
  );
}

export function bucketLabel(period: Period, start: number, short = false): string {
  const d = new Date(start * 1000);
  if (period === "day") return `${String(d.getHours()).padStart(2, "0")}:00`;
  if (period === "week")
    return short ? d.toLocaleDateString("ru-RU", { weekday: "short" }) : d.toLocaleDateString("ru-RU", { weekday: "short", day: "numeric", month: "short" });
  return short ? String(d.getDate()) : d.toLocaleDateString("ru-RU", { day: "numeric", month: "long", weekday: "short" });
}

function when(period: Period, ts: number): string {
  return period === "day" ? hhmm(ts) : new Date(ts * 1000).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

/** Дробилка за период: три главных числа, одна строка подробностей, график (или таблица). */
export function CrusherReportCard({ report, crusher, table }: { report: Report; crusher: CrusherReport; table: boolean }) {
  const t = shownTotals(crusher.totals);
  const observed = t.running + t.idle + t.stopped + t.offline;
  const work = t.running + t.idle;
  const details = [
    t.idle >= 60 && `без зерна ${hoursMinutes(t.idle)}`,
    t.offline + t.nodata >= 60 && `нет связи или данных ${hoursMinutes(t.offline + t.nodata)}`,
    crusher.first_work && `первый запуск ${when(report.period, crusher.first_work)}`,
    crusher.last_work && `последняя остановка ${when(report.period, crusher.last_work)}`,
  ].filter(Boolean);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-lg">{crusher.name}</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        <div className="flex flex-col gap-2">
          <div className="grid grid-cols-[1fr_1fr_auto] gap-3 sm:grid-cols-3 sm:gap-4">
            <Stat value={total(work)} label={observed ? `работала · ${Math.round((work / observed) * 100)} %` : "работала"} color="var(--state-running)" />
            <Stat value={total(t.stopped)} label="стояла" color="var(--state-stopped)" />
            <Stat value={String(crusher.stops)} label={plural(crusher.stops, "остановка", "остановки", "остановок")} />
          </div>
          {details.length > 0 && <div className="text-sm text-muted-foreground">{details.join(" · ")}</div>}
          {!crusher.first_work && <div className="text-sm text-muted-foreground">За этот период не работала{observed === 0 ? " — данных нет" : ""}</div>}
        </div>
        {table ? <BucketTable report={report} crusher={crusher} /> : report.period === "day" ? <DayTimeline report={report} crusher={crusher} /> : <DaysChart report={report} crusher={crusher} />}
      </CardContent>
    </Card>
  );
}

/** Итог за неделю или месяц — целыми часами: «335 ч» читается, «335 ч 5 мин» — нет. */
function total(seconds: number): string {
  return seconds >= 86400 ? `${Math.round(seconds / 3600)} ч` : hoursMinutes(seconds);
}

function plural(n: number, one: string, few: string, many: string) {
  const d = n % 10;
  const h = n % 100;
  return d === 1 && h !== 11 ? one : d >= 2 && d <= 4 && (h < 12 || h > 14) ? few : many;
}

function Stat({ value, label, color }: { value: string; label: string; color?: string }) {
  return (
    <div className="min-w-0">
      <div className="whitespace-nowrap text-xl font-semibold tabular-nums sm:text-3xl">{value}</div>
      <div className="flex items-center gap-1.5 text-xs text-muted-foreground sm:text-sm">
        {color && <span className="size-2 shrink-0 rounded-full sm:size-2.5" style={{ background: color }} />}
        {label}
      </div>
    </div>
  );
}

const STATE_TEXT: Record<Shown, string> = { running: "работала", idle: "крутилась без зерна", stopped: "стояла", offline: "нет связи" };

/** Лента суток: каждый отрезок — состояние; пробелы — сервер не работал; правее «сейчас» — ещё не наступило. */
function DayTimeline({ report, crusher }: { report: Report; crusher: CrusherReport }) {
  const [hover, setHover] = useState<{ x: number; text: string } | null>(null);
  const span = report.end - report.start;
  const pct = (ts: number) => ((Math.min(Math.max(ts, report.start), report.end) - report.start) / span) * 100;
  const segments = crusher.timeline ?? [];
  const nowPct = report.now < report.end ? pct(report.now) : null;

  const onMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    const x = Math.min(Math.max(e.clientX - box.left, 0), box.width);
    const ts = report.start + (x / box.width) * span;
    const seg = segments.find(([, s, en]) => ts >= s && ts <= en);
    const text = seg
      ? `${hhmm(seg[1])}–${hhmm(seg[2])} · ${STATE_TEXT[SHOWN[seg[0]]]} ${hoursMinutes(seg[2] - seg[1])}`
      : ts > report.now
        ? `${hhmm(ts)} · ещё не наступило`
        : `${hhmm(ts)} · нет данных`;
    setHover({ x: (x / box.width) * 100, text });
  };

  return (
    <div className="flex flex-col gap-1">
      <div className="relative h-10 touch-none select-none rounded-md border border-dashed border-muted-foreground/30 bg-muted/40" onPointerMove={onMove} onPointerLeave={() => setHover(null)} role="img" aria-label={`Лента работы за сутки: ${crusher.name}`}>
        {segments.map(([state, s, e], i) => (
          <div
            key={i}
            className="absolute inset-y-0 border-x border-card first:rounded-l-md last:rounded-r-md"
            style={{ left: `${pct(s)}%`, width: `${Math.max(pct(e) - pct(s), 0.15)}%`, background: STATES.find((x) => x.key === SHOWN[state])?.color }}
          />
        ))}
        {nowPct !== null && <div className="absolute inset-y-[-4px] w-0.5 rounded bg-foreground" style={{ left: `${nowPct}%` }} title="сейчас" />}
        {hover && (
          <div className="pointer-events-none absolute -top-2 z-10 -translate-x-1/2 -translate-y-full whitespace-nowrap rounded-md border bg-popover px-2 py-1 text-xs shadow-md" style={{ left: `${hover.x}%` }}>
            {hover.text}
          </div>
        )}
      </div>
      <div className="relative h-4 text-[11px] tabular-nums text-muted-foreground">
        {[0, 3, 6, 9, 12, 15, 18, 21, 24].map((h) => (
          <span
            key={h}
            className={cn("absolute -translate-x-1/2", h === 0 && "translate-x-0", h === 24 && "-translate-x-full", h % 6 !== 0 && "hidden sm:block")}
            style={{ left: `${(h / 24) * 100}%` }}
          >
            {String(h).padStart(2, "0")}:00
          </span>
        ))}
      </div>
    </div>
  );
}

const CHART_CONFIG = Object.fromEntries(STATES.map((s) => [s.key, { label: s.label, color: s.color }])) satisfies ChartConfig;

/** Неделя и месяц: часы по дням, столбики разбиты по состояниям. */
function DaysChart({ report, crusher }: { report: Report; crusher: CrusherReport }) {
  const data = crusher.buckets.map((b, i) => {
    const t = shownTotals(b);
    return {
      label: bucketLabel(report.period, report.edges[i], true),
      full: bucketLabel(report.period, report.edges[i]),
      future: report.edges[i] > report.now,
      ...Object.fromEntries(STATES.map((s) => [s.key, Math.round((t[s.key] / 3600) * 10) / 10])),
      seconds: t,
    };
  });
  return (
    <ChartContainer config={CHART_CONFIG} className="aspect-auto h-56 w-full">
      <BarChart data={data} margin={{ left: 0, right: 4, top: 8, bottom: 0 }} barCategoryGap={report.period === "week" ? "28%" : "18%"}>
        <CartesianGrid vertical={false} />
        <XAxis dataKey="label" tickLine={false} axisLine={false} interval={report.period === "month" ? "preserveStartEnd" : 0} minTickGap={4} />
        <YAxis domain={[0, 24]} ticks={[0, 6, 12, 18, 24]} tickLine={false} axisLine={false} width={36} tickFormatter={(v: number) => `${v} ч`} />
        <ChartTooltip
          cursor={{ fill: "var(--muted)", opacity: 0.6 }}
          content={({ active, payload }) => {
            const point = active && payload?.[0]?.payload;
            if (!point) return null;
            const t = point.seconds as ReturnType<typeof shownTotals>;
            return (
              <div className="min-w-44 rounded-lg border bg-background px-2.5 py-1.5 text-xs shadow-md">
                <div className="mb-1 font-medium">{point.full}</div>
                {point.future ? (
                  <div className="text-muted-foreground">ещё не наступило</div>
                ) : (
                  <>
                    {STATES.map((s) => (
                      <div key={s.key} className="flex items-center gap-2">
                        <span className="size-2.5 rounded-sm" style={{ background: s.color }} />
                        <span className="flex-1 text-muted-foreground">{s.label}</span>
                        <span className="tabular-nums">{hoursMinutes(t[s.key])}</span>
                      </div>
                    ))}
                    {t.nodata >= 60 && <div className="mt-1 text-muted-foreground">нет данных {hoursMinutes(t.nodata)}</div>}
                  </>
                )}
              </div>
            );
          }}
        />
        {STATES.map((s, i) => (
          <Bar key={s.key} dataKey={s.key} stackId="day" fill={s.color} stroke="var(--card)" strokeWidth={1} radius={i === STATES.length - 1 ? [3, 3, 0, 0] : 0} isAnimationActive={false} />
        ))}
      </BarChart>
    </ChartContainer>
  );
}

function BucketTable({ report, crusher }: { report: Report; crusher: CrusherReport }) {
  return (
    <div className="overflow-x-auto rounded-lg border">
      <table className="w-full text-sm tabular-nums">
        <thead className="bg-muted/50 text-left text-xs text-muted-foreground">
          <tr>
            <th className="px-3 py-1.5 font-medium">{report.period === "day" ? "Час" : "День"}</th>
            {STATES.map((s) => (
              <th key={s.key} className="px-3 py-1.5 text-right font-medium">
                {s.label}
              </th>
            ))}
            <th className="px-3 py-1.5 text-right font-medium">Нет данных</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {crusher.buckets.map((b, i) => {
            if (report.edges[i] > report.now) return null;
            const t = shownTotals(b);
            return (
              <tr key={i}>
                <td className="px-3 py-1">{bucketLabel(report.period, report.edges[i])}</td>
                {STATES.map((s) => (
                  <td key={s.key} className="px-3 py-1 text-right">
                    {t[s.key] >= 60 ? hoursMinutes(t[s.key]) : "—"}
                  </td>
                ))}
                <td className="px-3 py-1 text-right text-muted-foreground">{t.nodata >= 60 ? hoursMinutes(t.nodata) : "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** CSV для Excel: «;» и BOM — иначе кириллица и столбцы разъедутся. */
export function reportCsv(report: Report): string {
  const head = ["Дробилка", report.period === "day" ? "Час" : "День", ...STATES.map((s) => `${s.label}, ч`), "Нет данных, ч"];
  const rows = report.crushers.flatMap((c) =>
    c.buckets.flatMap((b, i) => {
      if (report.edges[i] > report.now) return [];
      const t = shownTotals(b);
      const h = (v: number) => (v / 3600).toFixed(2).replace(".", ",");
      return [[c.name, bucketLabel(report.period, report.edges[i]), ...STATES.map((s) => h(t[s.key])), h(t.nodata)]];
    }),
  );
  return "﻿" + [head, ...rows].map((r) => r.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(";")).join("\r\n");
}
