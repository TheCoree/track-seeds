"use client";

import { useId, useState } from "react";
import { Field, FieldContent, FieldDescription, FieldError, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Switch } from "@/components/ui/switch";
import type { JsonSchema, SchemaProperty } from "@/lib/types";

/** Поля настроек по JSON-схеме pydantic-модели: подписи и подсказки — из config.py (title/description). */
export function SchemaForm({
  schema,
  value,
  onChange,
  level,
  options,
  only,
}: {
  schema: JsonSchema | undefined;
  value: Record<string, unknown>;
  onChange: (v: Record<string, unknown>) => void;
  level: "main" | "advanced";
  options?: Record<string, [string, string][]>;
  /** показать только эти поля (в этом порядке) */
  only?: string[];
}) {
  if (!schema) return null;
  const fields = only
    ? only.flatMap((name) => (schema.properties[name] ? [[name, schema.properties[name]] as const] : []))
    : Object.entries(schema.properties).filter(([, p]) => p.ui === level);
  if (!fields.length) return null;
  return (
    <div className="grid gap-x-6 gap-y-5 sm:grid-cols-2">
      {fields.map(([name, prop]) => (
        <SchemaField key={name} prop={prop} value={value[name]} options={options?.[name]} onChange={(v) => onChange({ ...value, [name]: v })} />
      ))}
    </div>
  );
}

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

function SchemaField({ prop, value, onChange, options }: { prop: SchemaProperty; value: unknown; onChange: (v: unknown) => void; options?: [string, string][] }) {
  const id = useId();
  if (prop.type === "boolean") {
    return <SwitchField checked={Boolean(value)} onChange={onChange} label={prop.title} hint={prop.description} />;
  }
  if (options || prop.enum) {
    const opts = options ?? prop.enum!.map((v) => [v, v] as [string, string]);
    return (
      <Field>
        <FieldLabel htmlFor={id}>{prop.title}</FieldLabel>
        <NativeSelect id={id} className="w-full" value={String(value ?? "")} onChange={(e) => onChange(e.target.value)}>
          {opts.map(([v, label]) => (
            <NativeSelectOption key={v} value={v}>
              {label}
            </NativeSelectOption>
          ))}
        </NativeSelect>
        {prop.description && <FieldDescription>{prop.description}</FieldDescription>}
      </Field>
    );
  }
  if (prop.type === "number" || prop.type === "integer") {
    return <NumberField id={id} prop={prop} value={value as number} onChange={onChange} />;
  }
  return (
    <Field>
      <FieldLabel htmlFor={id}>{prop.title}</FieldLabel>
      <Input id={id} value={String(value ?? "")} onChange={(e) => onChange(e.target.value)} />
      {prop.description && <FieldDescription>{prop.description}</FieldDescription>}
    </Field>
  );
}

function NumberField({ id, prop, value, onChange }: { id: string; prop: SchemaProperty; value: number; onChange: (v: unknown) => void }) {
  // пока человек печатает («0.» или пусто) — держим строку, наверх отдаём только число
  const [text, setText] = useState<string | null>(null);
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
    <Field data-invalid={invalid || undefined}>
      <FieldLabel htmlFor={id}>{prop.title}</FieldLabel>
      <Input
        id={id}
        inputMode="decimal"
        value={shown}
        aria-invalid={invalid || undefined}
        className="max-w-40"
        onChange={(e) => {
          setText(e.target.value);
          const n = Number(e.target.value.replace(",", "."));
          if (e.target.value.trim() !== "" && !Number.isNaN(n)) onChange(n);
        }}
        onBlur={() => setText(null)}
      />
      {invalid ? <FieldError>Нужно число{range}</FieldError> : prop.description && <FieldDescription>{prop.description}</FieldDescription>}
    </Field>
  );
}
