import { useCallback, useEffect, useRef, useState } from "react";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

function detailText(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    // ошибки валидации FastAPI: [{loc: [...], msg}]
    return detail
      .map((e: { loc?: unknown[]; msg?: string }) => `${(e.loc ?? []).slice(1).join(".")}: ${e.msg ?? ""}`)
      .join("; ");
  }
  return JSON.stringify(detail);
}

// Изменяющие запросы — только с этим заголовком: чужая страница в браузере не сможет их отправить (CSRF)
const CSRF = { "X-Track-Seeds": "1" };

async function request<T>(method: string, path: string, body?: unknown, raw?: Blob | ArrayBuffer): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      method,
      headers: {
        ...(method === "GET" ? {} : CSRF),
        ...(raw !== undefined ? { "Content-Type": "application/octet-stream" } : body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      body: raw !== undefined ? raw : body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, "Нет связи с сервером track-seeds");
  }
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    try {
      message = detailText((await res.json()).detail);
    } catch {
      /* не JSON */
    }
    throw new ApiError(res.status, message);
  }
  return res.json() as Promise<T>;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body ?? {}),
  put: <T>(path: string, body: unknown) => request<T>("PUT", path, body),
  patch: <T>(path: string, body: unknown) => request<T>("PATCH", path, body),
  del: <T>(path: string) => request<T>("DELETE", path),
  upload: <T>(path: string, file: Blob) => request<T>("POST", path, undefined, file),
};

export const fileUrl = (path: string) => `/api/files/${path}`;

/** Данные с сервера с периодическим обновлением; при ошибке показываются последние удачные данные. */
export function usePoll<T>(path: string | null, intervalMs: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pathRef = useRef(path);
  pathRef.current = path;

  const reload = useCallback(async () => {
    const current = pathRef.current;
    if (!current) return;
    try {
      const result = await api.get<T>(current);
      if (pathRef.current === current) {
        setData(result);
        setError(null);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    setData(null);
    if (!path) return;
    reload();
    if (!intervalMs) return;
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") reload();
    }, intervalMs);
    return () => clearInterval(timer);
  }, [path, intervalMs, reload]);

  return { data, error, reload, setData };
}

/** Картинка, которая обновляется раз в intervalMs (снимки камер). */
export function useTick(intervalMs: number) {
  const [tick, setTick] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") setTick(Date.now());
    }, intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs]);
  return tick;
}
