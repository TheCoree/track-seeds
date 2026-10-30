import { useCallback, useEffect, useRef, useState } from "react";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    /** уточнение от сервера, например «totp» — нужен код 2FA */
    public code: string | null = null,
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
export const CSRF = { "X-Track-Seeds": "1" };

/** Страницы, открытые без входа: на них 401 — не повод уводить на вход. */
export const PUBLIC_PAGES = ["/login", "/register"];

/** Здесь 401 — ответ по существу (неверный пароль, ссылка), а не истёкший токен. */
const NO_REFRESH = ["/auth/login", "/auth/refresh", "/auth/logout", "/auth/register", "/auth/invite", "/auth/setup"];

let refreshing: Promise<boolean> | null = null;

/**
 * Access-токен живёт 15 минут: на 401 меняем его по refresh-токену (оба — в HttpOnly-cookie) и повторяем запрос.
 * Обновление одно на все запросы вкладки — иначе сервер увидел бы старый refresh-токен дважды.
 */
export function refreshSession(): Promise<boolean> {
  refreshing ??= fetch("/api/auth/refresh", { method: "POST", headers: CSRF, cache: "no-store" })
    .then((res) => res.ok)
    .catch(() => false)
    .finally(() => setTimeout(() => (refreshing = null), 1000));
  return refreshing;
}

/** Сессия закончилась — на страницу входа, вернуться туда же. */
export function toLogin() {
  if (typeof window === "undefined" || PUBLIC_PAGES.some((p) => window.location.pathname.startsWith(p))) return;
  const back = window.location.pathname + window.location.search;
  window.location.assign(`/login/?next=${encodeURIComponent(back)}`);
}

async function request<T>(method: string, path: string, body?: unknown, raw?: Blob | ArrayBuffer, retry = true): Promise<T> {
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
  if (res.status === 401 && retry && !NO_REFRESH.some((p) => path.startsWith(p))) {
    if (await refreshSession()) return request<T>(method, path, body, raw, false);
    toLogin();
  }
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    let code: string | null = null;
    try {
      const data = await res.json();
      message = detailText(data.detail);
      code = data.code ?? null;
    } catch {
      /* не JSON */
    }
    throw new ApiError(res.status, message, code);
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
