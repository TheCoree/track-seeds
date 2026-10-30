#!/usr/bin/env bash
# Первая установка и обновление на сервере с Docker: Lenovo ThinkCentre, Ubuntu Server.
# Запускать из папки проекта от обычного пользователя (не через sudo):  ./deploy/setup.sh
# Обновление — то же самое:  git pull && ./deploy/setup.sh
# Доступ из интернета по HTTPS — DOMAIN=seeds.example.ru в .env (см. README → «Доступ из интернета»).
set -euo pipefail
cd "$(dirname "$0")/.."

if [ "$(id -u)" = 0 ]; then
    echo "✗ Запускайте от обычного пользователя, без sudo: контейнер работает от его имени (владелец config/)"
    exit 1
fi
if ! command -v docker >/dev/null; then
    echo "✗ Нет Docker. Установите и запустите скрипт снова:"
    echo "    sudo apt update && sudo apt install -y docker.io docker-compose-v2"
    exit 1
fi
DOCKER=docker
docker info >/dev/null 2>&1 || DOCKER="sudo docker"  # пользователь ещё не в группе docker
$DOCKER compose version >/dev/null || { echo "✗ Нет docker compose: sudo apt install -y docker-compose-v2"; exit 1; }

# 1. Сам поднимается после перезагрузки и пропадания питания: служба Docker — в автозапуске,
#    у контейнера restart: always (docker-compose.yml).
sudo systemctl enable --now docker containerd >/dev/null
echo "✓ Docker запускается вместе с сервером"

# 2. .env: веб — в локальную сеть, вход разработчика admin по паролю (клиентов он приглашает сам)
[ -f .env ] || cp .env.example .env
set_env() {
    if grep -q "^$1=" .env; then sed -i "s|^$1=.*|$1=$2|" .env; else echo "$1=$2" >> .env; fi
}
set_env WEB_HOST 0.0.0.0
set_env HOST_UID "$(id -u)"
if ! grep -q '^WEB_PASSWORD=.' .env; then
    password=$(python3 -c 'import secrets; print(secrets.token_urlsafe(9))' 2>/dev/null || openssl rand -base64 9)
    set_env WEB_PASSWORD "$password"
    echo "✓ Вход разработчика: логин admin, пароль $password   (сохранён в .env)"
fi
chmod 600 .env
port=$(sed -n 's/^WEB_PORT=//p' .env); port=${port:-8080}

# 2а. Домен: Caddy с сертификатом Let's Encrypt; сайт — для всех, но только через него (по HTTPS)
domain=$(sed -n 's/^DOMAIN=//p' .env | tr -d "[:space:]\"'")
if [ -n "$domain" ]; then
    case "$domain" in
        *://*|*/*|*:*) echo "✗ DOMAIN в .env — только имя, без https:// и пути: seeds.example.ru"; exit 1 ;;
    esac
    set_env COMPOSE_PROFILES domain  # docker compose up поднимает и Caddy
    set_env WEB_PUBLIC_URL "https://$domain"
    allowed=$(sed -n 's/^WEB_ALLOWED_NETWORKS=//p' .env)
    if [ -z "$allowed" ]; then
        set_env WEB_ALLOWED_NETWORKS any
    elif [ "$allowed" != any ]; then
        echo "! WEB_ALLOWED_NETWORKS=$allowed — из интернета пустит только эти сети (для всех — any)"
    fi
    echo "✓ Домен $domain: HTTPS через Caddy, панель разработчика — только из локальной сети"
elif grep -q '^COMPOSE_PROFILES=domain' .env; then
    set_env COMPOSE_PROFILES ""
    $DOCKER compose --profile domain rm -sf caddy >/dev/null 2>&1 || true
    echo "✓ DOMAIN убран из .env — Caddy остановлен (WEB_PUBLIC_URL и WEB_ALLOWED_NETWORKS поправьте сами)"
fi

# 3. Файрвол (если включён): порт веба — только для локальной сети; с доменом — 80 и 443 для всех
if command -v ufw >/dev/null && sudo ufw status | grep -q "Status: active"; then
    for net in 192.168.0.0/16 10.0.0.0/8 172.16.0.0/12; do
        sudo ufw allow from "$net" to any port "$port" proto tcp >/dev/null
    done
    echo "✓ ufw: порт $port открыт только для локальной сети"
    if [ -n "$domain" ]; then
        sudo ufw allow 80/tcp >/dev/null && sudo ufw allow 443/tcp >/dev/null && sudo ufw allow 443/udp >/dev/null
        echo "✓ ufw: 80 и 443 открыты (HTTPS и получение сертификата)"
    fi
elif [ -n "$domain" ]; then
    echo "! ufw выключен. Сайт и так пускает из интернета только через HTTPS, но лучше закрыть лишнее:"
    echo "    sudo ufw allow OpenSSH && sudo ufw allow 80/tcp && sudo ufw allow 443 && ./deploy/setup.sh && sudo ufw enable"
fi

# 4. Настройки с ПК: пароли камер, ntfy, фразы (config/ в git не попадает — копируется отдельно)
mkdir -p config
if ! ls config/crushers/*/secrets.json >/dev/null 2>&1 && ! ls config/presets/*/secrets.json >/dev/null 2>&1; then
    echo "! Нет паролей камер. Скопируйте config/ с ПК (scp -r config user@сервер:$(pwd)/)"
    echo "  или после запуска: $DOCKER compose run --rm -it track-seeds track-seeds password"
fi

# 5. Сборка и запуск (первая сборка на 2 ядрах — минут 10–15). Пока собирается, работает прежний
#    контейнер; перерыв — только на пересоздание (секунды), состояние дробилок продолжается (data/resume.json).
# Caddy — в том же образе (из Debian): отдельно с Docker Hub ничего не скачивается.
$DOCKER compose up -d --build
if [ -n "$domain" ]; then
    $DOCKER compose restart caddy >/dev/null  # Caddyfile подключён файлом: изменения — только после перезапуска
fi

# HTTPS проверяется здесь же, через Caddy на этом сервере: роутер может не пускать на свой внешний
# адрес изнутри сети, а сертификат и прокси так проверяются честно.
check_domain() {
    if ! command -v curl >/dev/null; then
        echo "  Сайт из интернета: https://$domain/  (проверить отсюда нечем: нет curl)"
        return
    fi
    echo -n "  Жду HTTPS (первый раз Caddy получает сертификат)"
    for _ in $(seq 45); do
        if curl -fsS --max-time 5 --resolve "$domain:443:127.0.0.1" "https://$domain/api/health" >/dev/null 2>&1; then
            echo
            echo "✓ Из интернета: https://$domain/  (клиенты и виджет)"
            echo "  Панель разработчика снаружи не открывается — из дома: ssh -L 8080:127.0.0.1:$port $(whoami)@сервер"
            echo "  и в браузере http://127.0.0.1:8080/"
            return
        fi
        echo -n "."
        sleep 2
    done
    echo
    echo "✗ HTTPS для $domain не заработал за 90 с. Проверьте:"
    echo "  • A-запись $domain указывает на внешний IP (сейчас: $(getent hosts "$domain" | awk '{print $1}' | head -1))"
    echo "  • на роутере порты 80 и 443 проброшены на этот сервер"
    echo "  • что пишет Caddy:  $DOCKER compose logs --tail 30 caddy"
    echo "  Сайт в локальной сети работает. После исправления — снова ./deploy/setup.sh"
}

echo -n "Жду запуска"
for _ in $(seq 90); do
    if python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:$port/api/health', timeout=3)" 2>/dev/null; then
        ip=$( (hostname -I 2>/dev/null || true) | awk '{print $1}')
        echo
        echo "✓ Работает: http://${ip:-адрес-сервера}:$port/  — с любого компьютера локальной сети"
        [ -z "$domain" ] || check_domain
        echo "  Проверка камер и голоса:  $DOCKER compose run --rm track-seeds track-seeds check"
        echo "  Чтобы сервер сам включался при подаче питания — в BIOS: Power → After Power Loss → Power On"
        exit 0
    fi
    echo -n "."
    sleep 2
done
echo
echo "✗ Не запустился за 3 минуты. Логи: $DOCKER compose logs --tail 50"
exit 1
