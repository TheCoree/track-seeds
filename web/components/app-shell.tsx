"use client";

import { BellRing, CircleCheck, Factory, History, LayoutDashboard, Lock, Menu, PanelLeftClose, PanelLeftOpen, Siren, Volume2, VolumeX, Wheat } from "lucide-react";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { createContext, Suspense, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarRail,
  useSidebar,
} from "@/components/ui/sidebar";
import { toast, Toaster } from "@/components/ui/toast";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { api, usePoll } from "@/lib/api";
import { duration } from "@/lib/format";
import type { EventRow, Meta, Overview } from "@/lib/types";
import { cn } from "@/lib/utils";
import { crusherState } from "./crusher-status";

interface AppData {
  overview: Overview | null;
  overviewError: string | null;
  reloadOverview: () => Promise<void>;
  meta: Meta | null;
}

const AppContext = createContext<AppData>({ overview: null, overviewError: null, reloadOverview: async () => {}, meta: null });
export const useApp = () => useContext(AppContext);

const NAV: [string, string, typeof LayoutDashboard][] = [
  ["/", "Обзор", LayoutDashboard],
  ["/events", "События", History],
  ["/settings", "Уведомления и голос", BellRing],
];

const SIDEBAR_KEY = "track-seeds:sidebar";

/** Свёрнута ли боковая панель — запоминается в этом браузере (статический сайт не читает cookie shadcn). */
function useSidebarOpen(): [boolean, (open: boolean) => void] {
  const [open, setOpen] = useState(true);
  useEffect(() => {
    try {
      setOpen(localStorage.getItem(SIDEBAR_KEY) !== "0");
    } catch {
      /* приватный режим */
    }
  }, []);
  const set = (value: boolean) => {
    setOpen(value);
    try {
      localStorage.setItem(SIDEBAR_KEY, value ? "1" : "0");
    } catch {
      /* ignore */
    }
  };
  return [open, set];
}

export function AppShell({ children }: { children: ReactNode }) {
  const { data: overview, error: overviewError, reload } = usePoll<Overview>("/overview", 2000);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [sidebarOpen, setSidebarOpen] = useSidebarOpen();
  useEffect(() => {
    api.get<Meta>("/meta").then(setMeta).catch(() => {});
  }, []);

  return (
    <TooltipProvider>
      <Toaster>
        <AppContext.Provider value={{ overview, overviewError, reloadOverview: reload, meta }}>
          <SidebarProvider open={sidebarOpen} onOpenChange={setSidebarOpen}>
            <AppSidebar />
            <SidebarInset className="min-w-0 bg-muted/40">
              <TopBar />
              {overviewError && !overview && (
                <div className="bg-destructive px-4 py-2 text-sm text-white">Нет связи с сервером: {overviewError}</div>
              )}
              <main className="mx-auto w-full max-w-7xl flex-1 p-4 md:p-6">{children}</main>
            </SidebarInset>
          </SidebarProvider>
          <EventWatcher />
        </AppContext.Provider>
      </Toaster>
    </TooltipProvider>
  );
}

function AppSidebar() {
  const { meta } = useApp();
  const pathname = usePathname();
  const { setOpenMobile } = useSidebar();
  const close = () => setOpenMobile(false); // на телефоне меню — шторка: выбрали пункт — закрыть

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader>
        <Link href="/" onClick={close} className="flex items-center gap-2.5 px-2 py-1.5 group-data-[collapsible=icon]:px-0">
          <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground">
            <Wheat className="size-4" />
          </span>
          <span className="flex flex-col leading-tight group-data-[collapsible=icon]:hidden">
            <span className="text-sm font-semibold">track-seeds</span>
            <span className="text-xs text-muted-foreground">семена в дробилках</span>
          </span>
        </Link>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel>Дробилки</SidebarGroupLabel>
          <SidebarGroupContent>
            {/* useSearchParams в статической сборке — только внутри Suspense */}
            <Suspense fallback={<CrusherMenu currentId={null} />}>
              <CrusherMenuFromUrl />
            </Suspense>
          </SidebarGroupContent>
        </SidebarGroup>
        <SidebarGroup>
          <SidebarGroupLabel>Сервис</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {NAV.map(([href, label, Icon]) => (
                <SidebarMenuItem key={href}>
                  <SidebarMenuButton
                    tooltip={label}
                    isActive={href === "/" ? pathname === "/" : pathname.startsWith(href)}
                    onClick={close}
                    render={<Link href={href} />}
                  >
                    <Icon />
                    <span>{label}</span>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
      <SidebarFooter>
        <div className="flex items-center gap-2 px-2 py-1 text-xs text-muted-foreground group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:px-0">
          <Lock className="size-3.5 shrink-0" />
          <span className="group-data-[collapsible=icon]:hidden">{meta?.auth ? "Вход по паролю" : "Доступ только с этого компьютера"}</span>
        </div>
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  );
}

/** Выбранная дробилка — из ?id= в адресе: при переходе между дробилками путь тот же, меняется только он. */
function CrusherMenuFromUrl() {
  const pathname = usePathname();
  const id = useSearchParams().get("id");
  return <CrusherMenu currentId={pathname.startsWith("/crusher") ? id : null} />;
}

function CrusherMenu({ currentId }: { currentId: string | null }) {
  const { overview } = useApp();
  const { setOpenMobile } = useSidebar();
  return (
    <SidebarMenu>
      {(overview?.crushers ?? []).map((c) => {
        const state = crusherState(c);
        return (
          <SidebarMenuItem key={c.id}>
            <SidebarMenuButton
              size="lg"
              tooltip={`${c.name}: ${state.short}`}
              isActive={currentId === c.id}
              className="group-data-[collapsible=icon]:justify-center"
              onClick={() => setOpenMobile(false)}
              render={<Link href={`/crusher?id=${c.id}`} />}
            >
              {/* точка состояния — на иконке: видна и на свёрнутой панели */}
              <span className="relative shrink-0">
                <Factory />
                <span className={cn("absolute -right-1 -top-1 size-2.5 rounded-full ring-2 ring-sidebar", state.dot, state.tone === "red" && "animate-pulse")} />
              </span>
              <span className="flex min-w-0 flex-col leading-tight group-data-[collapsible=icon]:hidden">
                <span className="truncate">{c.name}</span>
                <span className="truncate text-xs text-muted-foreground">{state.short}</span>
              </span>
            </SidebarMenuButton>
          </SidebarMenuItem>
        );
      })}
    </SidebarMenu>
  );
}

const ALARM_TEXT: Record<string, string> = { empty: "закончились семена", low: "семена заканчиваются", unknown: "не видно желоб" };

/** Верхняя строка: идёт ли тревога; звук в браузере. */
function TopBar() {
  const { overview } = useApp();
  const [sound, setSound] = useSound();
  const alarms = (overview?.crushers ?? []).flatMap((c) => {
    const seeds = c.status.seeds;
    return seeds?.level && ALARM_TEXT[seeds.level] ? [{ crusher: c, level: seeds.level, since: seeds.level_since }] : [];
  });
  const worst = alarms.find((a) => a.level === "empty") ?? alarms[0];
  const now = overview?.time ?? Date.now() / 1000;

  return (
    <header
      className={cn(
        "sticky top-0 z-20 flex h-14 shrink-0 items-center gap-2 border-b px-3 md:px-4",
        !worst && "bg-background/95 backdrop-blur",
        worst?.level === "empty" && "alarm-pulse border-transparent text-white",
        worst?.level === "low" && "border-transparent bg-amber-500 text-white",
        worst?.level === "unknown" && "border-transparent bg-slate-600 text-white",
      )}
    >
      <PanelToggle className={cn(worst && "text-white hover:bg-white/15 hover:text-white")} />
      <div className="flex min-w-0 flex-1 items-center gap-2 text-sm font-medium">
        {worst ? (
          <>
            <Siren className="size-4 shrink-0" />
            <div className="flex min-w-0 flex-wrap gap-x-4">
              {alarms.map((a) => (
                <Link key={a.crusher.id} href={`/crusher?id=${a.crusher.id}`} className="truncate underline-offset-2 hover:underline">
                  {a.crusher.name}: {ALARM_TEXT[a.level]}
                  {a.since ? ` · ${duration(now - a.since)}` : ""}
                </Link>
              ))}
            </div>
          </>
        ) : (
          <span className="flex items-center gap-2 text-muted-foreground">
            <CircleCheck className="size-4 text-emerald-600" />
            Тревог нет
          </span>
        )}
      </div>
      <Tooltip>
        <TooltipTrigger
          render={
            <Button
              variant={worst ? "secondary" : "outline"}
              size="sm"
              onClick={() => setSound(!sound)}
              aria-label="Звук тревог в этом браузере"
            />
          }
        >
          {sound ? <Volume2 /> : <VolumeX />}
          <span className="hidden sm:inline">{sound ? "Звук включён" : "Звук выключен"}</span>
        </TooltipTrigger>
        <TooltipContent>Озвучивать тревоги в этом браузере — вкладка должна быть открыта</TooltipContent>
      </Tooltip>
    </header>
  );
}

/** Свернуть / развернуть боковую панель (на телефоне — открыть меню). Иконка показывает, что будет. */
function PanelToggle({ className }: { className?: string }) {
  const { open, isMobile, toggleSidebar } = useSidebar();
  const label = isMobile ? "Меню" : open ? "Свернуть панель" : "Развернуть панель";
  const Icon = isMobile ? Menu : open ? PanelLeftClose : PanelLeftOpen;
  return (
    <Tooltip>
      <TooltipTrigger render={<Button variant="ghost" size="icon" className={className} onClick={toggleSidebar} aria-label={label} data-sidebar="trigger" />}>
        <Icon className="size-5" />
      </TooltipTrigger>
      <TooltipContent side="bottom">
        {label}
        {!isMobile && <span className="ml-2 opacity-70">Ctrl+B</span>}
      </TooltipContent>
    </Tooltip>
  );
}

const SOUND_KEY = "track-seeds:sound";

function useSound(): [boolean, (v: boolean) => void] {
  const [enabled, setEnabled] = useState(false);
  useEffect(() => {
    try {
      setEnabled(localStorage.getItem(SOUND_KEY) === "1");
    } catch {
      /* приватный режим */
    }
    const listener = (e: Event) => setEnabled((e as CustomEvent<boolean>).detail);
    window.addEventListener("track-seeds-sound", listener);
    return () => window.removeEventListener("track-seeds-sound", listener);
  }, []);
  const set = (v: boolean) => {
    setEnabled(v);
    try {
      localStorage.setItem(SOUND_KEY, v ? "1" : "0");
    } catch {
      /* ignore */
    }
    window.dispatchEvent(new CustomEvent("track-seeds-sound", { detail: v }));
    if (v) beep();
  };
  return [enabled, set];
}

/** Новые события: всплывающее сообщение и звук тревоги в браузере. */
function EventWatcher() {
  const { overview } = useApp();
  const [sound] = useSound();
  const lastSeq = useRef<number | null>(null);
  const soundRef = useRef(sound);
  soundRef.current = sound;

  useEffect(() => {
    if (!overview) return;
    if (lastSeq.current === null) {
      lastSeq.current = overview.last_seq;
      return;
    }
    if (overview.last_seq <= lastSeq.current) return;
    const after = lastSeq.current;
    lastSeq.current = overview.last_seq;
    api
      .get<EventRow[]>(`/events?after=${after}&limit=20`)
      .then((events) => {
        for (const e of events) {
          if (e.severity === "info" && !e.speech) continue;
          toast.add({
            title: e.title,
            description: e.message,
            type: e.severity === "alert" ? "error" : e.severity === "warning" ? "warning" : "success",
            timeout: e.severity === "alert" ? 20000 : 6000,
          });
          if (soundRef.current) playAlarm(e);
        }
      })
      .catch(() => {});
  }, [overview]);
  return null;
}

let audioQueue: Promise<void> = Promise.resolve();

function playAlarm(e: EventRow) {
  audioQueue = audioQueue.then(
    () =>
      new Promise<void>((resolve) => {
        if (e.speech) {
          const audio = new Audio(`/api/voice/say.wav?text=${encodeURIComponent(e.speech)}`);
          audio.onended = () => resolve();
          audio.onerror = () => {
            beep();
            resolve();
          };
          audio.play().catch(() => {
            beep();
            resolve();
          });
        } else if (e.severity !== "info") {
          beep();
          setTimeout(resolve, 800);
        } else resolve();
      }),
  );
}

export function beep() {
  try {
    const ctx = new AudioContext();
    [880, 660, 880].forEach((freq, i) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0.25, ctx.currentTime + i * 0.25);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + i * 0.25 + 0.22);
      osc.connect(gain).connect(ctx.destination);
      osc.start(ctx.currentTime + i * 0.25);
      osc.stop(ctx.currentTime + i * 0.25 + 0.23);
    });
  } catch {
    /* нет звука */
  }
}
