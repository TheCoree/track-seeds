"use client";

import { ChevronLeft, ChevronRight, Download } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { CrusherReportCard, Legend, reportCsv } from "@/components/report-view";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { usePoll } from "@/lib/api";
import type { Period, Report } from "@/lib/types";
import { cn } from "@/lib/utils";

const PERIODS: [Period, string][] = [
  ["day", "День"],
  ["week", "Неделя"],
  ["month", "Месяц"],
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
  const period = (PERIODS.some(([p]) => p === params.get("period")) ? params.get("period") : "day") as Period;
  const date = params.get("date");
  const query = `/reports?period=${period}${date ? `&date=${date}` : ""}`;
  const { data: report, error } = usePoll<Report>(query, 60000);

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
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Отчёты</h1>
          <p className="text-sm text-muted-foreground">Сколько дробилки работали, стояли и крутились без зерна</p>
        </div>
        <div role="radiogroup" aria-label="Период" className="flex rounded-lg border bg-background p-0.5">
          {PERIODS.map(([value, label]) => (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={period === value}
              onClick={() => go({ period: value })}
              className={cn("rounded-md px-3 py-1 text-sm font-medium transition-colors", period === value ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground")}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button variant="outline" size="icon" aria-label="Раньше" disabled={!report} onClick={() => report && go({ date: report.prev })}>
          <ChevronLeft />
        </Button>
        <Button variant="outline" size="icon" aria-label="Позже" disabled={!report?.next} onClick={() => report?.next && go({ date: report.next })}>
          <ChevronRight />
        </Button>
        <span className="min-w-0 text-lg font-medium first-letter:uppercase">{report ? title(report) : "…"}</span>
        {date && (
          <Button variant="ghost" size="sm" onClick={() => go({ date: null })}>
            {period === "day" ? "Сегодня" : period === "week" ? "Эта неделя" : "Этот месяц"}
          </Button>
        )}
        <div className="flex-1" />
        <Button variant="outline" size="sm" disabled={!report} onClick={download}>
          <Download /> Скачать CSV
        </Button>
      </div>

      <Legend />

      {!report ? (
        error ? (
          <p className="text-sm text-destructive">{error}</p>
        ) : (
          <Skeleton className="h-96 rounded-xl" />
        )
      ) : (
        report.crushers.map((c) => <CrusherReportCard key={c.id} report={report} crusher={c} />)
      )}
      <p className="text-xs text-muted-foreground">
        «Работала» — привод крутился (камера видит движение цепи), включая время без зерна. «Стояла» — привод неподвижен. «Нет данных» — сервер был выключен.
        Время — по часам сервера.
      </p>
    </div>
  );
}
