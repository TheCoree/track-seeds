# Прод-образ для сервера: обе мельницы с зонами и эталонами вшиты в программу.

# 1. Фронтенд: статический экспорт Next.js
FROM node:24-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web ./
RUN npm run build

# 2. Сервер
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# tzdata — время в уведомлениях по TZ; espeak-ng — запасной синтез речи;
# alsa-utils (aplay) — физический динамик, подключённый к серверу;
# caddy — HTTPS для доступа из интернета (сервис caddy в docker-compose.yml, если задан DOMAIN):
# из Debian, а не образ с Docker Hub — Docker Hub с сервера бывает недоступен.
# UID пользователя = UID владельца папки config/ на хосте, иначе не будет прав на запись.
ARG UID=1000
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata espeak-ng alsa-utils caddy \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid ${UID} --create-home --home-dir /home/app --groups audio app

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .

# Голос и модель детектора людей — в образе: сервер работает без интернета. В образе — все голоса vosk-*
# (одна модель, ~450 МБ после сжатия BERT); голос piper (ru_RU-*, ~60 МБ), выбранный в «Уведомления и голос»,
# сервер докачивает сам в том data/ (VOICES_DOWNLOAD_DIR ниже) — папка образа только для чтения.
# Детектор людей — чтобы рука рабочего в желобе не считалась пустым желобом.
# Голос — вне томов, чтобы обновлялся вместе с образом.
ARG DETECTOR_MODEL=yolox_tiny
ARG VOICE=vosk-male-1
ENV VOICES_DIR=/app/voices
RUN track-seeds --env-file /dev/null download-model ${DETECTOR_MODEL} --voice ${VOICE} \
    && mkdir -p /app/config /app/data \
    && chown -R app:app /app/config /app/data

COPY --from=web /web/out /app/web

USER app
# VOICES_DOWNLOAD_DIR — после download-model: при сборке голос кладётся в образ, а не в том
ENV CONFIG_DIR=/app/config \
    DATA_DIR=/app/data \
    VOICES_DOWNLOAD_DIR=/app/data/voices \
    WEB_STATIC_DIR=/app/web
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=5s --start-period=60s \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('WEB_PORT', '8080'), timeout=4)" || exit 1
CMD ["track-seeds", "serve"]
