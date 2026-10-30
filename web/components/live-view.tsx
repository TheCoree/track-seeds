"use client";

import { useEffect, useRef, useState, type RefObject } from "react";
import { refreshSession, toLogin } from "@/lib/api";

const CRLF2 = [13, 10, 13, 10];
const STALL_MS = 8000;
// Поток открылся, а кадра за столько нет — значит, прокси по дороге копит бесконечный ответ (облако роутера,
// хостинг): дальше — кадры по одному (live.jpg), каждый ответ конечный и проходит через любой прокси.
const FIRST_FRAME_MS = 5000;
let streamBuffered = false; // уже выяснили на этой странице — сразу по кадру, без 5 с ожидания

function indexOf(buffer: Uint8Array, pattern: number[]): number {
  outer: for (let i = 0; i <= buffer.length - pattern.length; i++) {
    for (let j = 0; j < pattern.length; j++) if (buffer[i + j] !== pattern[j]) continue outer;
    return i;
  }
  return -1;
}

/** Вкладка видна — смотрим; свернули — поток закрывается, чтобы не тратить канал завода. */
function useVisible() {
  const [visible, setVisible] = useState(true);
  useEffect(() => {
    const update = () => setVisible(document.visibilityState === "visible");
    update();
    document.addEventListener("visibilitychange", update);
    return () => document.removeEventListener("visibilitychange", update);
  }, []);
  return visible;
}

export type LiveState = "connecting" | "live" | "error";

/**
 * MJPEG-поток камеры на canvas. Через fetch, а не <img>: видно ответ сервера — истёк токен (обновить и
 * переподключиться), слишком много зрителей, обрыв; кадры, которые не успели нарисовать, пропускаются.
 * Поток не доходит (прокси копит его в буфере) — кадры по одному с того же сервера.
 */
export function useMjpeg(url: string | null, canvas: RefObject<HTMLCanvasElement | null>) {
  const visible = useVisible();
  const [state, setState] = useState<LiveState>("connecting");
  const [error, setError] = useState<string | null>(null);
  const [size, setSize] = useState<[number, number] | null>(null);

  useEffect(() => {
    if (!url || !visible) return;
    let stopped = false;
    let controller: AbortController | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let stallTimer: ReturnType<typeof setTimeout> | undefined;
    let firstFrameTimer: ReturnType<typeof setTimeout> | undefined;
    let delay = 1000;
    let pending: Uint8Array | null = null;
    let drawing = false;
    setState("connecting");
    setError(null);

    const draw = async () => {
      if (drawing) return;
      drawing = true;
      while (pending && !stopped) {
        const jpeg = pending;
        pending = null;
        try {
          const bitmap = await createImageBitmap(new Blob([jpeg as BlobPart], { type: "image/jpeg" }));
          const el = canvas.current;
          if (el && !stopped) {
            if (el.width !== bitmap.width || el.height !== bitmap.height) {
              el.width = bitmap.width;
              el.height = bitmap.height;
              setSize([bitmap.width, bitmap.height]);
            }
            el.getContext("2d")?.drawImage(bitmap, 0, 0);
          }
          bitmap.close();
        } catch {
          /* битый кадр — следующий */
        }
      }
      drawing = false;
    };

    const resetStall = () => {
      clearTimeout(stallTimer);
      stallTimer = setTimeout(() => controller?.abort(), STALL_MS);
    };

    const retry = (ms: number) => {
      if (stopped) return;
      retryTimer = setTimeout(connect, ms);
    };

    const show = (jpeg: Uint8Array) => {
      pending = jpeg;
      delay = 1000;
      resetStall();
      setState("live");
      setError(null);
      draw();
    };

    // Кадры по одному: сервер отвечает кадром новее прошлого (или ждёт его до 3 с), темп — как у камеры
    const frameUrl = url.replace(/live\.mjpg$/, "live.jpg");
    const pollFrames = async () => {
      let seq = 0;
      let last = 0;
      while (!stopped) {
        // не чаще 12 кадров в секунду — как сам поток, даже если прокси выкинул номер кадра (X-Frame-Seq)
        const wait = last + 80 - performance.now();
        if (wait > 0) await new Promise((done) => (retryTimer = setTimeout(done, wait)));
        last = performance.now();
        controller = new AbortController();
        resetStall();
        try {
          let res = await fetch(`${frameUrl}?after=${seq}`, { signal: controller.signal, cache: "no-store" });
          if (res.status === 401) {
            if (!(await refreshSession())) return toLogin();
            res = await fetch(`${frameUrl}?after=${seq}`, { signal: controller.signal, cache: "no-store" });
          }
          if (res.status === 204) continue;
          if (!res.ok) {
            let message = `Ошибка ${res.status}`;
            try {
              message = (await res.json()).detail ?? message;
            } catch {
              /* не JSON */
            }
            setState("error");
            setError(message);
            await new Promise((done) => (retryTimer = setTimeout(done, 5000)));
            continue;
          }
          seq = Number(res.headers.get("X-Frame-Seq")) || seq;
          show(new Uint8Array(await res.arrayBuffer()));
        } catch {
          if (stopped) return;
          setState("connecting");
          await new Promise((done) => (retryTimer = setTimeout(done, delay)));
          delay = Math.min(delay * 2, 10000);
        }
      }
    };

    const connect = async () => {
      if (streamBuffered) return pollFrames();
      controller = new AbortController();
      resetStall();
      let gotFrame = false;
      const firstFrame = (firstFrameTimer = setTimeout(() => {
        if (gotFrame || stopped) return;
        streamBuffered = true; // поток открылся, а кадров нет — прокси копит его; дальше по кадру
        controller?.abort();
      }, FIRST_FRAME_MS));
      try {
        let res = await fetch(url, { signal: controller.signal, cache: "no-store" });
        if (res.status === 401) {
          if (!(await refreshSession())) {
            clearTimeout(firstFrame);
            return toLogin();
          }
          res = await fetch(url, { signal: controller.signal, cache: "no-store" });
        }
        if (!res.ok || !res.body) {
          clearTimeout(firstFrame); // сервер ответил отказом — это не прокси, режим не меняем
          let message = `Ошибка ${res.status}`;
          try {
            message = (await res.json()).detail ?? message;
          } catch {
            /* не JSON */
          }
          setState("error");
          setError(message);
          return retry(res.status === 429 ? 15000 : 5000);
        }
        const reader = res.body.getReader();
        let buffer = new Uint8Array(0);
        for (;;) {
          const { value, done } = await reader.read();
          if (done || stopped) break;
          const joined = new Uint8Array(buffer.length + value.length);
          joined.set(buffer);
          joined.set(value, buffer.length);
          buffer = joined;
          for (;;) {
            const headerEnd = indexOf(buffer, CRLF2);
            if (headerEnd < 0) break;
            const header = new TextDecoder().decode(buffer.subarray(0, headerEnd));
            const length = Number(/content-length:\s*(\d+)/i.exec(header)?.[1] ?? -1);
            const start = headerEnd + 4;
            if (length < 0) {
              buffer = buffer.subarray(start);
              continue;
            }
            if (buffer.length < start + length) break;
            gotFrame = true;
            show(buffer.slice(start, start + length));
            buffer = buffer.subarray(start + length);
          }
        }
      } catch {
        /* обрыв или зависание — переподключаемся */
      }
      clearTimeout(firstFrame);
      if (streamBuffered && !stopped) return pollFrames();
      if (!stopped) {
        setState("connecting");
        retry(delay);
        delay = Math.min(delay * 2, 10000);
      }
    };

    connect();
    return () => {
      stopped = true;
      clearTimeout(retryTimer);
      clearTimeout(firstFrameTimer);
      clearTimeout(stallTimer);
      controller?.abort();
    };
  }, [url, visible, canvas]);

  return { state, error, size };
}

/** Живое видео с камеры как есть, без зон. */
export function LiveCanvas({ url, label, onState }: { url: string; label: string; onState?: (state: LiveState, error: string | null) => void }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const { state, error } = useMjpeg(url, canvas);
  const callback = useRef(onState);
  callback.current = onState;
  useEffect(() => {
    callback.current?.(state, error);
  }, [state, error]);
  return <canvas ref={canvas} role="img" aria-label={label} className="absolute inset-0 size-full object-cover" />;
}
