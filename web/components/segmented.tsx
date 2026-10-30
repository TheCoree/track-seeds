"use client";

import type { LucideIcon } from "lucide-react";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { cn } from "@/lib/utils";

export type SegmentOption<T extends string> = { value: T; label: string; icon?: LucideIcon; hint?: string };

/**
 * Выбор одного из нескольких (ToggleGroup из shadcn): период отчёта, срок ссылки, вид камеры.
 * Повторное нажатие на выбранный не снимает выбор. tone="dark" — поверх кадра камеры.
 */
export function Segmented<T extends string>({
  value,
  onChange,
  options,
  label,
  tone = "muted",
  className,
  itemClassName,
}: {
  value: T;
  onChange: (value: T) => void;
  options: SegmentOption<T>[];
  label: string;
  tone?: "muted" | "dark";
  className?: string;
  itemClassName?: string;
}) {
  return (
    <ToggleGroup
      aria-label={label}
      value={[value]}
      onValueChange={(next) => next[0] && onChange(next[0] as T)}
      spacing={0}
      className={cn("gap-0.5 rounded-lg p-0.5", tone === "muted" ? "bg-muted" : "bg-black/55 text-white backdrop-blur-sm", className)}
    >
      {options.map(({ value: v, label: text, icon: Icon, hint }) => (
        <ToggleGroupItem
          key={v}
          value={v}
          title={hint}
          className={cn(
            "rounded-md! border-0 px-3",
            tone === "muted"
              ? "text-muted-foreground hover:bg-transparent hover:text-foreground aria-pressed:bg-background aria-pressed:text-foreground aria-pressed:shadow-sm"
              : "h-7 px-2 text-xs text-white hover:bg-white/15 hover:text-white aria-pressed:bg-white aria-pressed:text-slate-900",
            itemClassName,
          )}
        >
          {Icon && <Icon />}
          {text}
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  );
}
