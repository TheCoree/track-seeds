"use client";

import { type ReactNode, useState } from "react";
import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from "recharts";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
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
/** Итог за неделю или месяц — целыми часами: «335 ч» читается, «335 ч 5 мин» — нет. */
export function total(seconds: number): string {
  return seconds >= 86400 ? `${Math.round(seconds / 3600)} ч` : hoursMinutes(seconds);
}

export function plural(n: number, one: string, few: string, many: string) {
  const d = n % 10;
  const h = n % 100;
  return d === 1 && h !== 11 ? one : d >= 2 && d <= 4 && (h < 12 || h > 14) ? few : many;
}

function sum(crushers: CrusherReport[], pick: (t: ReturnType<typeof shownTotals>) => number, bucket?: number) {
  return crushers.reduce((acc, c) => acc + pick(shownTotals(bucket === undefined ? c.totals : c.buckets[bucket])), 0);
}

/** Мини-график в плитке: столбик на час (день) или на день (неделя, месяц). */
function Spark({ report, values, color }: { report: Report; values: number[]; color: string }) {
  const max = Math.max(...values, 1);
  return (
    <div className="flex h-9 items-end gap-px" aria-hidden>
      {values.map((v, i) => (
        <div
          key={i}
          title={`${bucketLabel(report.period, report.edges[i])}: ${hoursMinutes(v)}`}
          className="min-w-0 flex-1 rounded-t-[2px]"
          style={{ height: report.edges[i] > report.now ? 2 : `${Math.max((v / max) * 100, v > 0 ? 8 : 4)}%`, background: report.edges[i] > report.now || v === 0 ? "var(--muted)" : color }}
        />
      ))}
    </div>
  );
}

function Tile({ label, value, note, color, spark }: { label: string; value: string; note: string; color?: string; spark?: ReactNode }) {
  return (
    <Card size="sm" className="gap-2">
      <CardHeader className="gap-0.5">
        <CardDescription className="flex items-center gap-1.5">
          {color && <span className="size-2.5 rounded-full" style={{ background: color }} />}
          {label}
        </CardDescription>
        <div className="whitespace-nowrap text-xl font-semibold leading-tight tabular-nums sm:text-3xl">{value}</div>
        <div className="text-xs text-muted-foreground">{note}</div>
      </CardHeader>
      {spark && <CardContent>{spark}</CardContent>}
    </Card>
  );
}

/** Сводка по обеим дробилкам. */
export function SummaryTiles({ report }: { report: Report }) {
  const cs = report.crushers;
  const buckets = report.edges.slice(0, -1).map((_, i) => i);
  const work = sum(cs, (t) => t.running + t.idle);
  const stopped = sum(cs, (t) => t.stopped);
  const idle = sum(cs, (t) => t.idle);
  const observed = sum(cs, (t) => t.running + t.idle + t.stopped + t.offline);
  const stops = cs.reduce((a, c) => a + c.stops, 0);
  const runouts = cs.reduce((a, c) => a + c.runouts, 0);
  const each = (f: (c: CrusherReport) => string) => cs.map((c) => `${c.name.replace("Дробилка ", "Д")}: ${f(c)}`).join(" · ");
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <Tile
        label="Работали"
        color="var(--state-running)"
        value={total(work)}
        note={observed ? `${Math.round((work / observed) * 100)} % времени наблюдения` : "нет данных"}
        spark={<Spark report={report} values={buckets.map((i) => sum(cs, (t) => t.running + t.idle, i))} color="var(--state-running)" />}
      />
      <Tile
        label="Стояли"
        color="var(--state-stopped)"
        value={total(stopped)}
        note={observed ? `${Math.round((stopped / observed) * 100)} % времени` : "нет данных"}
        spark={<Spark report={report} values={buckets.map((i) => sum(cs, (t) => t.stopped, i))} color="var(--state-stopped)" />}
      />
      <Tile label="Остановок" value={String(stops)} note={each((c) => String(c.stops))} />
      <Tile
        label="Без зерна"
        color="var(--state-idle)"
        value={total(idle)}
        note={runouts ? `${runouts} ${plural(runouts, "раз", "раза", "раз")} закончилось зерно` : "зерно не заканчивалось"}
        spark={<Spark report={report} values={buckets.map((i) => sum(cs, (t) => t.idle, i))} color="var(--state-idle)" />}
      />
    </div>
  );
}

/** Загрузка: доля времени каждой дробилки по состояниям — одной полосой. */
export function Utilization({ report }: { report: Report }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Загрузка</CardTitle>
        <CardDescription>Доля времени за период — на каждой дробилке</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {report.crushers.map((c) => {
          const t = shownTotals(c.totals);
          const all = t.running + t.idle + t.stopped + t.offline + t.nodata;
          const parts = [...STATES.map((s) => ({ key: s.key, label: s.label, color: s.color, v: t[s.key] })), { key: "nodata", label: "нет данных", color: "transparent", v: t.nodata }];
          const work = t.running + t.idle;
          return (
            <div key={c.id} className="grid grid-cols-[6.5rem_1fr_3.5rem] items-center gap-3 sm:grid-cols-[8rem_1fr_4rem]">
              <span className="truncate text-sm font-medium">{c.name}</span>
              <div className="flex h-4 overflow-hidden rounded-full border border-dashed border-muted-foreground/30">
                {all > 0 &&
                  parts.map((p) =>
                    p.v > 0 ? <div key={p.key} title={`${p.label}: ${hoursMinutes(p.v)} (${Math.round((p.v / all) * 100)} %)`} className="h-full border-r-2 border-card last:border-r-0" style={{ width: `${(p.v / all) * 100}%`, background: p.color }} /> : null,
                  )}
              </div>
              <span className="text-right text-lg font-semibold tabular-nums">{all ? Math.round((work / all) * 100) : 0} %</span>
            </div>
          );
        })}
        <Legend />
      </CardContent>
    </Card>
  );
}

/** Дробилка: главные числа, график (или таблица), подробности. */
export function CrusherReportCard({ report, crusher, table }: { report: Report; crusher: CrusherReport; table: boolean }) {
  const t = shownTotals(crusher.totals);
  const work = t.running + t.idle;
  const details = [
    crusher.first_work && `первый запуск ${when(report.period, crusher.first_work)}`,
    crusher.last_work && `последняя остановка ${when(report.period, crusher.last_work)}`,
    crusher.longest_work >= 60 && `дольше всего без остановки ${hoursMinutes(crusher.longest_work)}`,
    t.offline + t.nodata >= 60 && `нет связи или данных ${hoursMinutes(t.offline + t.nodata)}`,
  ].filter(Boolean);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{crusher.name}</CardTitle>
        <CardDescription className="tabular-nums">
          работала {total(work)} · стояла {total(t.stopped)} · {crusher.stops} {plural(crusher.stops, "остановка", "остановки", "остановок")}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {table ? <BucketTable report={report} crusher={crusher} /> : report.period === "day" ? <DayTimeline report={report} crusher={crusher} /> : <DaysChart report={report} crusher={crusher} />}
        <div className="text-xs text-muted-foreground">{details.length ? details.join(" · ") : "За этот период не работала"}</div>
      </CardContent>
    </Card>
  );
}

/** Простои за день по обеим дробилкам — по времени. */
export function StopsCard({ report }: { report: Report }) {
  const rows = report.crushers.flatMap((c) => (c.stop_list ?? []).map(([s, e]) => ({ name: c.name, s, e }))).sort((a, b) => a.s - b.s);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Простои</CardTitle>
        <CardDescription>Дробилка стояла дольше минуты</CardDescription>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <p className="text-sm text-muted-foreground">Простоев не было</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Дробилка</TableHead>
                <TableHead>Когда</TableHead>
                <TableHead className="text-right">Сколько</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((r) => (
                <TableRow key={`${r.name}-${r.s}`}>
                  <TableCell>{r.name}</TableCell>
                  <TableCell className="tabular-nums">
                    {hhmm(r.s)}–{report.now - r.e < 30 ? "сейчас" : hhmm(r.e)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{hoursMinutes(r.e - r.s)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
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
    <div className="max-h-80 overflow-y-auto rounded-lg border">
      <Table className="tabular-nums">
        <TableHeader className="sticky top-0 bg-card">
          <TableRow>
            <TableHead>{report.period === "day" ? "Час" : "День"}</TableHead>
            {STATES.map((s) => (
              <TableHead key={s.key} className="text-right">
                {s.label}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {crusher.buckets.map((b, i) => {
            if (report.edges[i] > report.now) return null;
            const t = shownTotals(b);
            return (
              <TableRow key={i}>
                <TableCell>{bucketLabel(report.period, report.edges[i])}</TableCell>
                {STATES.map((s) => (
                  <TableCell key={s.key} className="text-right">
                    {t[s.key] >= 60 ? hoursMinutes(t[s.key]) : "—"}
                  </TableCell>
                ))}
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
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
