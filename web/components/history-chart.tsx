"use client";

import { Area, AreaChart, CartesianGrid, ReferenceArea, ReferenceLine, XAxis, YAxis } from "recharts";
import { type ChartConfig, ChartContainer, ChartTooltip } from "@/components/ui/chart";
import { clock, percent } from "@/lib/format";
import type { ZoneStatus } from "@/lib/types";

const MINUTES = 15;

/**
 * Последние 15 минут зоны: для желоба — уверенность «семена есть» (линия 50 % — граница,
 * полоса — гистерезис), для привода — доля зоны, изменившейся за секунду (линия — порог).
 * Разрывы — кадры, где зону не удалось определить (темно, закрыта человеком).
 */
export function HistoryChart({ zone, now, band, motionThreshold }: { zone: ZoneStatus; now: number; band: [number, number]; motionThreshold: number }) {
  const drive = zone.kind === "motion";
  const color = drive ? "#0ea5e9" : "#10b981";
  const config = { v: { label: drive ? "Движение" : "Уверенность", color } } satisfies ChartConfig;
  const start = now - MINUTES * 60;
  const data = (zone.history ?? []).filter(([t]) => t >= start).map(([t, v]) => ({ t, v: v === null ? null : v * 100 }));

  return (
    <ChartContainer config={config} className="aspect-auto h-44 w-full">
      <AreaChart data={data} margin={{ left: 4, right: 8, top: 14, bottom: 0 }}>
        <defs>
          <linearGradient id={`fill-${zone.id}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.35} />
            <stop offset="100%" stopColor={color} stopOpacity={0.02} />
          </linearGradient>
        </defs>
        <CartesianGrid vertical={false} />
        <XAxis
          dataKey="t"
          type="number"
          domain={[start, now]}
          tickLine={false}
          axisLine={false}
          ticks={[start, start + 300, start + 600, now]}
          tickFormatter={(t: number) => (now - t < 30 ? "сейчас" : `−${Math.round((now - t) / 60)} мин`)}
        />
        <YAxis domain={[0, 100]} ticks={[0, 50, 100]} tickLine={false} axisLine={false} width={44} tickFormatter={(v: number) => `${v} %`} />
        {drive ? (
          <ReferenceLine y={motionThreshold * 100} stroke="#64748b" strokeDasharray="4 4" />
        ) : (
          <>
            <ReferenceArea y1={band[0] * 100} y2={band[1] * 100} fill="#94a3b8" fillOpacity={0.15} />
            <ReferenceLine y={50} stroke="#64748b" strokeDasharray="4 4" />
          </>
        )}
        <ChartTooltip
          cursor={{ stroke: "#94a3b8" }}
          content={({ active, payload }) => {
            const point = active && payload?.[0]?.payload;
            if (!point) return null;
            return (
              <div className="rounded-lg border bg-background px-2.5 py-1.5 text-xs shadow-md">
                <div className="text-muted-foreground">{clock(point.t)}</div>
                <div className="font-medium tabular-nums">
                  {point.v === null ? "не определено" : `${drive ? "движение" : "уверенность"} ${percent(point.v / 100)}`}
                </div>
              </div>
            );
          }}
        />
        <Area dataKey="v" type="monotone" stroke={color} strokeWidth={2} fill={`url(#fill-${zone.id})`} isAnimationActive={false} connectNulls={false} dot={false} />
      </AreaChart>
    </ChartContainer>
  );
}
