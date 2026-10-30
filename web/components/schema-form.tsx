"use client";

import { useId, useState, type ReactNode } from "react";
import { Field, FieldContent, FieldDescription, FieldLabel } from "@/components/ui/field";
import { InputGroup, InputGroupAddon, InputGroupInput, InputGroupText } from "@/components/ui/input-group";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import type { JsonSchema, SchemaProperty } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * Строка настройки: слева — название и короткая подсказка, справа — поле. Строки идут списком
 * с разделителями (SettingsList); на телефоне поле переносится под название.
 */
export function SettingRow({ label, hint, htmlFor, children, wide, invalid }: { label: ReactNode; hint?: ReactNode; htmlFor?: string; children: ReactNode; wide?: boolean; invalid?: string | null }) {
  return (
    <div className="flex flex-col gap-2 py-3.5 first:pt-0 last:pb-0 sm:flex-row sm:items-center sm:gap-6">
      <div className="min-w-0 flex-1">
        <label htmlFor={htmlFor} className="text-sm font-medium leading-snug">
          {label}
        </label>
        {invalid ? <p className="mt-0.5 text-xs text-destructive">{invalid}</p> : hint && <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">{hint}</p>}
      </div>
      <div className={cn("shrink-0", wide ? "sm:w-80" : "sm:w-56", "flex sm:justify-end")}>{children}</div>
    </div>
  );
}

export function SettingsList({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("flex flex-col divide-y", className)}>{children}</div>;
}

/** Поля настроек по JSON-схеме pydantic-модели: подписи и подсказки — из config.py (title/description). */
export function SchemaForm({
  schema,
  value,
  onChange,
  level,
  options,
  only,
  skip,
}: {
  schema: JsonSchema | undefined;
  value: Record<string, unknown>;
  onChange: (v: Record<string, unknown>) => void;
  level: "main" | "advanced";
  options?: Record<string, [string, string][]>;
  /** показать только эти поля (в этом порядке) */
  only?: string[];
  /** эти поля нарисованы отдельно */
  skip?: string[];
}) {
  if (!schema) return null;
  const fields = only
    ? only.flatMap((name) => (schema.properties[name] ? [[name, schema.properties[name]] as const] : []))
    : Object.entries(schema.properties).filter(([name, p]) => p.ui === level && !skip?.includes(name));
  if (!fields.length) return null;
  return (
    <>
      {fields.map(([name, prop]) => (
        <SchemaField key={name} prop={prop} value={value[name]} options={options?.[name]} onChange={(v) => onChange({ ...value, [name]: v })} />
      ))}
    </>
  );
}

/** Переключатель-строка (для мест вне SettingsList — с подписью справа от переключателя). */
export function SwitchField({ label, hint, checked, onChange }: { label?: string; hint?: string; checked: boolean; onChange: (v: boolean) => void }) {
  const id = useId();
  return (
    <Field orientation="horizontal">
      <Switch id={id} checked={checked} onCheckedChange={(v) => onChange(Boolean(v))} />
      <FieldContent>
        <FieldLabel htmlFor={id}>{label}</FieldLabel>
        {hint && <FieldDescription>{hint}</FieldDescription>}
      </FieldContent>
    </Field>
  );
}

export function SwitchRow({ label, hint, checked, onChange }: { label: ReactNode; hint?: ReactNode; checked: boolean; onChange: (v: boolean) => void }) {
  const id = useId();
  return (
    <SettingRow label={label} hint={hint} htmlFor={id}>
      <Switch id={id} checked={checked} onCheckedChange={(v) => onChange(Boolean(v))} />
    </SettingRow>
  );
}

/** «Сообщить через, с» → название «Сообщить через» и единица «с» внутри поля. */
function splitUnit(title = ""): [string, string | null] {
  const match = /^(.*),\s*(с|мин|px|%)$/.exec(title);
  return match ? [match[1], match[2]] : [title, null];
}

function SchemaField({ prop, value, onChange, options }: { prop: SchemaProperty; value: unknown; onChange: (v: unknown) => void; options?: [string, string][] }) {
  const id = useId();
  if (prop.type === "boolean") {
    return <SwitchRow checked={Boolean(value)} onChange={onChange} label={prop.title} hint={prop.description} />;
  }
  if (options || prop.enum) {
    const opts = options ?? prop.enum!.map((v) => [v, v] as [string, string]);
    return (
      <SettingRow label={prop.title} hint={prop.description} htmlFor={id}>
        <NativeSelect id={id} className="w-full" value={String(value ?? "")} onChange={(e) => onChange(e.target.value)}>
          {opts.map(([v, label]) => (
            <NativeSelectOption key={v} value={v}>
              {label}
            </NativeSelectOption>
          ))}
        </NativeSelect>
      </SettingRow>
    );
  }
  if (prop.type === "number" || prop.type === "integer") {
    const min = prop.minimum ?? prop.exclusiveMinimum;
    const max = prop.maximum ?? prop.exclusiveMaximum;
    // доли и шкалы с обеими границами — ползунком; секунды без верхней границы — полем
    if (min !== undefined && max !== undefined && max <= 255 && !splitUnit(prop.title)[1]) return <SliderRow prop={prop} value={Number(value ?? min)} min={min} max={max} onChange={onChange} />;
    return <NumberRow prop={prop} value={value as number} onChange={onChange} />;
  }
  return (
    <SettingRow label={prop.title} hint={prop.description} htmlFor={id} wide>
      <InputGroup>
        <InputGroupInput id={id} value={String(value ?? "")} onChange={(e) => onChange(e.target.value)} />
      </InputGroup>
    </SettingRow>
  );
}

function SliderRow({ prop, value, min, max, onChange }: { prop: SchemaProperty; value: number; min: number; max: number; onChange: (v: unknown) => void }) {
  const integer = prop.type === "integer" || max - min >= 50;
  const step = integer ? 1 : max - min > 1 ? 0.05 : 0.01;
  const lo = prop.exclusiveMinimum !== undefined ? min + step : min;
  const hi = prop.exclusiveMaximum !== undefined ? max - step : max;
  const shown = integer ? String(Math.round(value)) : String(Math.round(value * 100) / 100).replace(".", ",");
  return (
    <SettingRow label={prop.title} hint={prop.description} wide>
      <div className="flex w-full items-center gap-3">
        <Slider
          min={lo}
          max={hi}
          step={step}
          value={[Math.min(Math.max(value, lo), hi)]}
          onValueChange={(v) => onChange(Math.round((Array.isArray(v) ? v[0] : v) / step) * step)}
          aria-label={prop.title}
        />
        <span className="w-10 shrink-0 text-right text-sm tabular-nums">{shown}</span>
      </div>
    </SettingRow>
  );
}

function NumberRow({ prop, value, onChange }: { prop: SchemaProperty; value: number; onChange: (v: unknown) => void }) {
  const id = useId();
  // пока человек печатает («0.» или пусто) — держим строку, наверх отдаём только число
  const [text, setText] = useState<string | null>(null);
  const [label, unit] = splitUnit(prop.title);
  const min = prop.minimum ?? prop.exclusiveMinimum;
  const max = prop.maximum ?? prop.exclusiveMaximum;
  const shown = text ?? String(value ?? "");
  const number = Number(shown.replace(",", "."));
  const invalid =
    shown.trim() === "" ||
    Number.isNaN(number) ||
    (prop.minimum !== undefined && number < prop.minimum) ||
    (prop.exclusiveMinimum !== undefined && number <= prop.exclusiveMinimum) ||
    (prop.maximum !== undefined && number > prop.maximum) ||
    (prop.exclusiveMaximum !== undefined && number >= prop.exclusiveMaximum) ||
    (prop.type === "integer" && !Number.isInteger(number));
  const range = min !== undefined || max !== undefined ? ` (${min ?? "…"} – ${max ?? "…"})` : "";
  return (
    <SettingRow label={label} hint={prop.description} htmlFor={id} invalid={invalid ? `Нужно число${range}` : null}>
      <InputGroup className="w-32" data-invalid={invalid || undefined}>
        <InputGroupInput
          id={id}
          inputMode="decimal"
          value={shown}
          aria-invalid={invalid || undefined}
          className="text-right tabular-nums"
          onChange={(e) => {
            setText(e.target.value);
            const n = Number(e.target.value.replace(",", "."));
            if (e.target.value.trim() !== "" && !Number.isNaN(n)) onChange(n);
          }}
          onBlur={() => setText(null)}
        />
        {unit && (
          <InputGroupAddon align="inline-end">
            <InputGroupText>{unit}</InputGroupText>
          </InputGroupAddon>
        )}
      </InputGroup>
    </SettingRow>
  );
}
