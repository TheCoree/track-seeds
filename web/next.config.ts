import type { NextConfig } from "next";
import { PHASE_DEVELOPMENT_SERVER } from "next/constants";

// Прод: статический экспорт в out/, его раздаёт `track-seeds serve` вместе с API (один порт).
// Разработка: `next dev` с горячей перезагрузкой, /api проксируется на запущенный `track-seeds serve`.
export default function config(phase: string): NextConfig {
  if (phase === PHASE_DEVELOPMENT_SERVER) {
    const api = process.env.API_URL ?? "http://127.0.0.1:8080";
    return {
      async rewrites() {
        return [{ source: "/api/:path*", destination: `${api}/api/:path*` }];
      },
    };
  }
  return { output: "export", trailingSlash: true, images: { unoptimized: true } };
}
