#!/usr/bin/env bash
# Первая установка (и обновление) на сервере с Docker: Lenovo ThinkCentre, Ubuntu Server.
# Запускать из папки проекта от обычного пользователя (не через sudo):  ./deploy/setup.sh
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

# 2. .env: веб — в локальную сеть, вход по паролю
[ -f .env ] || cp .env.example .env
set_env() {
    if grep -q "^$1=" .env; then sed -i "s|^$1=.*|$1=$2|" .env; else echo "$1=$2" >> .env; fi
}
set_env WEB_HOST 0.0.0.0
set_env HOST_UID "$(id -u)"
if ! grep -q '^WEB_PASSWORD=.' .env; then
    password=$(python3 -c 'import secrets; print(secrets.token_urlsafe(9))' 2>/dev/null || openssl rand -base64 9)
    set_env WEB_PASSWORD "$password"
    echo "✓ Пароль веб-интерфейса: $password   (сохранён в .env; имя пользователя — любое)"
fi
chmod 600 .env
port=$(sed -n 's/^WEB_PORT=//p' .env); port=${port:-8080}

# 3. Файрвол (если включён): порт веба — только для локальной сети
if command -v ufw >/dev/null && sudo ufw status | grep -q "Status: active"; then
    for net in 192.168.0.0/16 10.0.0.0/8 172.16.0.0/12; do
        sudo ufw allow from "$net" to any port "$port" proto tcp >/dev/null
    done
    echo "✓ ufw: порт $port открыт только для локальной сети"
fi

# 4. Настройки с ПК: пароли камер, ntfy, фразы (config/ в git не попадает — копируется отдельно)
mkdir -p config
if ! ls config/crushers/*/secrets.json >/dev/null 2>&1 && ! ls config/presets/*/secrets.json >/dev/null 2>&1; then
    echo "! Нет паролей камер. Скопируйте config/ с ПК (scp -r config user@сервер:$(pwd)/)"
    echo "  или после запуска: $DOCKER compose run --rm -it track-seeds track-seeds password"
fi

# 5. Сборка и запуск (первая сборка на 2 ядрах — минут 10–15)
$DOCKER compose up -d --build

echo -n "Жду запуска"
for _ in $(seq 90); do
    if python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:$port/api/health', timeout=3)" 2>/dev/null; then
        ip=$( (hostname -I 2>/dev/null || true) | awk '{print $1}')
        echo
        echo "✓ Работает: http://${ip:-адрес-сервера}:$port/  — с любого компьютера локальной сети"
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
