// Типы ответов API (src/track_seeds/server/app.py)

export type Level = "ok" | "low" | "empty" | "unknown" | "stopped";
export type ZoneKind = "empty" | "low" | "motion";
export type Reading = "covered" | "uncovered" | "unknown";
export type Label = "full" | "empty";
export type Point = [number, number];

export interface ZoneStatus {
  id: string;
  name: string;
  kind: ZoneKind;
  /** для привода: covered — крутится, uncovered — стоит */
  state: "covered" | "uncovered" | null;
  state_since: number | null;
  reading: Reading;
  reason: string;
  score: number | null;
  /** уверенность «семена есть» 0..1 (граница — 0.5) */
  confidence: number | null;
  d_full: number | null;
  d_empty: number | null;
  novelty: number | null;
  brightness: number | null;
  /** привод: доля зоны, изменившейся за секунду */
  motion: number | null;
  blind_s: number;
  samples: { full: number; empty: number };
  separation: number | null;
  radius: number | null;
  /** семена — уверенность, привод — движение */
  history?: [number, number | null][];
}

export interface SeedsStatus {
  level: Level | null;
  level_since: number | null;
  last_known: Level | null;
  /** работает ли дробилка (по зоне привода); null — ещё не определено */
  running: boolean | null;
  running_since: number | null;
  /** гистерезис в единицах уверенности */
  confidence_band: [number, number];
  zones: ZoneStatus[];
}

export interface CrusherStatus {
  state: "disabled" | "stopped" | "starting" | "running" | "error";
  error: string | null;
  connected: boolean;
  since: number | null;
  fps?: number;
  width?: number;
  height?: number;
  last_frame_ts?: number | null;
  timing_ms?: number;
  analyzer_error?: string | null;
  seeds?: SeedsStatus | null;
}

export interface SeedZone {
  id: string;
  name: string;
  kind: ZoneKind;
  points: Point[];
}

export interface SeedSample {
  id: string;
  image: string;
  ts: number;
  labels: Record<string, Label>;
  /** вшит в программу — не удаляется и не переотмечается */
  builtin: boolean;
}

export interface SeedsConfig {
  zones: SeedZone[];
  samples: SeedSample[];
  say_empty: string;
  say_low: string;
  say_refill: string;
  say_unknown: string;
  say_reminders: boolean;
  say_times: number;
  voice_hours: string;
  [key: string]: unknown;
}

export interface CameraConn {
  url: string;
  username: string | null;
  password: string | null;
  has_password?: boolean;
  name: string;
  rtsp_transport: "tcp" | "udp";
  offline_alert_s: number;
  [key: string]: unknown;
}

/** Дробилка: камера, зоны и вшитые эталоны — из программы, остальное — настройки на месте. */
export interface Crusher {
  id: string;
  name: string;
  enabled: boolean;
  notify: boolean;
  camera: CameraConn;
  seeds: SeedsConfig;
  speakers: Speaker[];
  folder: string;
  builtin_camera_url: string;
  config_error: string | null;
  status: CrusherStatus;
}

export interface Delivery {
  channel: string;
  ok: boolean;
  error: string | null;
  ts: number;
}

export interface EventRow {
  seq: number;
  uid: string;
  ts: number;
  camera_id: string;
  camera: string;
  type: string;
  severity: "info" | "warning" | "alert";
  title: string;
  message: string;
  speech: string | null;
  snapshot: string | null;
  frame: string | null;
  data: Record<string, unknown>;
  deliveries: Delivery[];
}

export interface Overview {
  crushers: Crusher[];
  errors: Record<string, string>;
  events: EventRow[];
  last_seq: number;
  notify_error: string | null;
  time: number;
}

export interface SchemaProperty {
  type?: string;
  title?: string;
  description?: string;
  default?: unknown;
  enum?: string[];
  minimum?: number;
  maximum?: number;
  exclusiveMinimum?: number;
  exclusiveMaximum?: number;
  ui?: "main" | "advanced";
}

export interface JsonSchema {
  properties: Record<string, SchemaProperty>;
}

export interface Meta {
  schemas: Record<string, JsonSchema>;
  voices: Record<string, string>;
  engines: { piper: boolean; espeak: boolean };
  channels: string[];
  auth: boolean;
  config_dir: string;
}

export interface Speaker {
  id: string;
  name: string;
  kind: "hikvision" | "local";
  device: string;
  host: string;
  port: number;
  https: boolean;
  username: string;
  password: string | null;
  has_password?: boolean;
  channel: number;
  timeout_s: number;
}

export interface SettingsData {
  notify: { channels: string[]; [key: string]: unknown };
  ntfy: {
    url: string;
    topic: string;
    token: string | null;
    has_token?: boolean;
    username: string | null;
    password: string | null;
    has_password?: boolean;
    timeout_s: number;
  };
  voice: Record<string, unknown>;
  notify_error: string | null;
}

export interface ProbeResult {
  ok: boolean;
  error: string;
  connect_s: number;
  width: number;
  height: number;
  codec: string;
  measured_fps: number;
  read_failures: number;
  image: string | null;
}
