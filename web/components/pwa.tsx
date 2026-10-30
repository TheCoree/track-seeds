"use client";

import { useEffect, useState } from "react";

interface InstallPrompt extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

let deferred: InstallPrompt | null = null;
const listeners = new Set<() => void>();

/**
 * Приложение на телефоне (PWA): service worker держит оболочку сайта в кеше — значок с экрана «Домой»
 * открывается сразу, без связи показывает «нет связи», а не пустой экран. Работает только по HTTPS
 * (и на localhost): по http://192.168… браузер service worker не запустит.
 */
export function PwaRegister() {
  useEffect(() => {
    if ("serviceWorker" in navigator && window.isSecureContext) {
      navigator.serviceWorker.register("/sw.js").catch(() => {});
    }
    const onPrompt = (e: Event) => {
      e.preventDefault(); // своя кнопка «Установить» в «Мой аккаунт»
      deferred = e as InstallPrompt;
      listeners.forEach((l) => l());
    };
    window.addEventListener("beforeinstallprompt", onPrompt);
    return () => window.removeEventListener("beforeinstallprompt", onPrompt);
  }, []);
  return null;
}

/** Можно ли поставить приложение кнопкой (Chrome, Edge, Android); на iPhone — только «Поделиться → На экран „Домой“». */
export function useInstall() {
  const [available, setAvailable] = useState(false);
  const [installed, setInstalled] = useState(false);
  useEffect(() => {
    const update = () => setAvailable(deferred !== null);
    listeners.add(update);
    update();
    setInstalled(matchMedia("(display-mode: standalone)").matches || (navigator as { standalone?: boolean }).standalone === true);
    return () => {
      listeners.delete(update);
    };
  }, []);
  const install = async () => {
    if (!deferred) return;
    await deferred.prompt();
    await deferred.userChoice;
    deferred = null;
    listeners.forEach((l) => l());
  };
  return { available, installed, install, secure: typeof window !== "undefined" && window.isSecureContext };
}
