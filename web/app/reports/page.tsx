"use client";

import { BarChart3, ChevronLeft, ChevronRight, Download, Table2 } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { CrusherReportCard, Legend, reportCsv } from "@/components/report-view";
import { Segmented, type SegmentOption } from "@/components/segmented";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { usePoll } from "@/lib/api";
import type { Period, Report } from "@/lib/types";

const PERIODS: SegmentOption<Period>[] = [
  { value: "day", label: "День" },
  { value: "week", label: "Неделя" },
  { value: "month", label: "Месяц" },
];

export default function ReportsPage() {
  return (
    <Suspense fallback={<Skeleton className="h-96 rounded-xl" />}>
      <Reports />
    </Suspense>
  );
}

function title(report: Report): string {
  const start = new Date(report.start * 1000);
  const last = new Date((report.end - 1) * 1000);
  if (report.period === "day") return start.toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
  if (report.period === "week") {
    const from = start.toLocaleDateString("ru-RU", { day: "numeric", month: start.getMonth() === last.getMonth() ? undefined : "long" });
    return `${from} – ${last.toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: "numeric" })}`;
  }
  const month = start.toLocaleDateString("ru-RU", { month: "long", year: "numeric" });
  return month.charAt(0).toUpperCase() + month.slice(1);
}

/** Отчёты: сколько дробилки работали, стояли, крутились без зерна — за день, неделю, месяц. */
function Reports() {
  const params = useSearchParams();
  const router = useRouter();
  const period = (PERIODS.some((p) => p.value === params.get("period")) ? params.get("period") : "day") as Period;
  const date = params.get("date");
  const query = `/reports?period=${period}${date ? `&date=${date}` : ""}`;
  const { data: report, error } = usePoll<Report>(query, 60000);
  const [table, setTable] = useState(false);

  const go = (next: { period?: Period; date?: string | null }) => {
    const p = new URLSearchParams({ period: next.period ?? period });
    const d = next.date === undefined ? date : next.date;
    if (d) p.set("date", d);
    router.replace(`/reports?${p}`, { scroll: false });
  };

  const download = () => {
    if (!report) return;
    const url = URL.createObjectURL(new Blob([reportCsv(report)], { type: "text/csv;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `дробилки-${report.period === "day" ? "день" : report.period === "week" ? "неделя" : "месяц"}-${report.date}.csv`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">Отчёты</h1>
        <Segmented label="Период" value={period} onChange={(value) => go({ period: value, date: null })} options={PERIODS} itemClassName="px-4" />
      </div>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <div className="flex w-full items-center gap-1 sm:w-auto">
          <Button variant="ghost" size="icon" aria-label="Раньше" disabled={!report} onClick={() => report && go({ date: report.prev })}>
            <ChevronLeft />
          </Button>
          <span className="min-w-0 text-lg font-medium first-letter:uppercase">{report ? title(report) : "…"}</span>
          <Button variant="ghost" size="icon" aria-label="Позже" disabled={!report?.next} onClick={() => report?.next && go({ date: report.next })}>
            <ChevronRight />
          </Button>
          {date && (
            <Button variant="link" size="sm" onClick={() => go({ date: null })}>
              {period === "day" ? "сегодня" : period === "week" ? "эта неделя" : "этот месяц"}
            </Button>
          )}
        </div>
        <div className="flex-1" />
        <Button variant="ghost" size="sm" onClick={() => setTable(!table)}>
          {table ? <BarChart3 /> : <Table2 />} {table ? "График" : "Таблица"}
        </Button>
        <Button variant="ghost" size="sm" disabled={!report} onClick={download}>
          <Download /> CSV
        </Button>
      </div>

      {!table && <Legend />}

      {!report ? (
        error ? (
          <p className="text-sm text-destructive">{error}</p>
        ) : (
          <Skeleton className="h-96 rounded-xl" />
        )
      ) : (
        report.crushers.map((c) => <CrusherReportCard key={c.id} report={report} crusher={c} table={table} />)
      )}
      <p className="text-xs text-muted-foreground">«Работала» — привод крутился, включая время без зерна. Время — по часам сервера.</p>
    </div>
  );
}
