BIN := .venv/bin

.PHONY: install serve web web-dev crushers check run preview check-camera detect plates vehicles notify-test test docker-up docker-logs docker-down

.venv:
	python3.14 -m venv .venv
	$(BIN)/pip install -U pip

install: .venv
	$(BIN)/pip install -e '.[dev]'
	@test -f .env || cp .env.example .env
	cd web && npm install

# Веб-интерфейс + все камеры: http://127.0.0.1:8080/ (сначала make web)
serve:
	$(BIN)/track-seeds serve

# Собрать фронтенд в web/out — его раздаёт `serve`
web:
	cd web && npm run build

# Дробилки и проверка без браузера (камеры, зоны, привод, динамики, голос)
crushers:
	$(BIN)/track-seeds crushers

check:
	$(BIN)/track-seeds check

# Разработка фронтенда с горячей перезагрузкой: http://127.0.0.1:3000/ (рядом должен работать make serve)
web-dev:
	cd web && npm run dev

run:
	$(BIN)/track-seeds run

preview:
	$(BIN)/track-seeds run --preview

check-camera:
	$(BIN)/track-seeds check-camera

detect:
	$(BIN)/track-seeds detect

plates:
	$(BIN)/track-seeds plates

vehicles:
	$(BIN)/track-seeds vehicles

notify-test:
	$(BIN)/track-seeds notify-test

test:
	$(BIN)/pytest -q
	cd web && npm run typecheck

docker-up:
	docker compose up -d --build

docker-logs:
	docker compose logs -f

docker-down:
	docker compose down
