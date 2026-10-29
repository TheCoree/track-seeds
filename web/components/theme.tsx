"use client";

import { Monitor, Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

type Theme = "light" | "dark" | "system";
const KEY = "track-seeds:theme";
const NEXT: Record<Theme, Theme> = { system: "light", light: "dark", dark: "system" };
const LABEL: Record<Theme, string> = { system: "Тема как в системе", light: "Светлая тема", dark: "Тёмная тема" };
const ICON = { system: Monitor, light: Sun, dark: Moon };

/** Ставит класс .dark на <html> до отрисовки — без вспышки светлой темы. Вставляется в <head>. */
export const THEME_SCRIPT = `(function(){try{var t=localStorage.getItem("${KEY}")||"system";var d=t==="dark"||(t==="system"&&matchMedia("(prefers-color-scheme: dark)").matches);document.documentElement.classList.toggle("dark",d)}catch(e){}})()`;

function apply(theme: Theme) {
  const dark = theme === "dark" || (theme === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
}

/** Кнопка темы: система → светлая → тёмная. */
export function ThemeToggle({ className }: { className?: string }) {
  const [theme, setTheme] = useState<Theme>("system");

  useEffect(() => {
    try {
      setTheme((localStorage.getItem(KEY) as Theme) || "system");
    } catch {
      /* приватный режим */
    }
  }, []);

  useEffect(() => {
    apply(theme);
    if (theme !== "system") return;
    const media = matchMedia("(prefers-color-scheme: dark)");
    const listener = () => apply("system");
    media.addEventListener("change", listener);
    return () => media.removeEventListener("change", listener);
  }, [theme]);

  const change = () => {
    const next = NEXT[theme];
    setTheme(next);
    try {
      localStorage.setItem(KEY, next);
    } catch {
      /* ignore */
    }
  };

  const Icon = ICON[theme];
  return (
    <Tooltip>
      <TooltipTrigger render={<Button variant="ghost" size="icon" className={className} onClick={change} aria-label={LABEL[theme]} />}>
        <Icon className="size-5" />
      </TooltipTrigger>
      <TooltipContent side="bottom">{LABEL[theme]} · нажмите, чтобы сменить</TooltipContent>
    </Tooltip>
  );
}
