import argparse
import csv
import dataclasses
import logging
import signal
import sys
import threading
import time
from pathlib import Path

import cv2
from pydantic import ValidationError

from .analyzers import build_analyzers
from .bus import EventBus
from .camera import OpenCVSource, mask_url, open_capture, probe_camera
from .config import Settings, load_settings
from .detection import CATALOG, build_detector, download_model, draw_detections
from .journal import COLUMNS, VehicleJournal, recent_visits
from .notify import Notification, NotificationService, build_notifiers
from .notify.messages import format_duration
from .pipeline import Pipeline
from .preview import PreviewServer
from .server.commands import add_commands

log = logging.getLogger("track_seeds")


def cmd_run(settings: Settings, args: argparse.Namespace) -> int:
    if args.preview:
        settings = dataclasses.replace(settings, preview=settings.preview.model_copy(update={"enabled": True}))

    bus = EventBus()
    NotificationService(build_notifiers(settings), settings.notify).attach(bus)
    analyzers = build_analyzers(settings)
    if not analyzers:
        log.error("Не задан ни один пресет в ANALYZERS")
        return 1
    if "plates" in settings.app.analyzers and settings.plates.journal:
        VehicleJournal(settings.app.data_dir).attach(bus)
    preview = PreviewServer(settings.preview) if settings.preview.enabled else None
    pipeline = Pipeline(
        OpenCVSource(settings.camera), analyzers, bus, settings.camera.name,
        offline_alert_s=settings.camera.offline_alert_s, preview=preview, preview_fps=settings.preview.fps,
    )

    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())

    log.info("Старт: камера «%s», анализаторы: %s, уведомления: %s", settings.camera.name,
             ", ".join(a.name for a in analyzers), ", ".join(settings.notify.channels))
    bus.start()
    if preview:
        preview.start()
    try:
        pipeline.run(stop)
    finally:
        if preview:
            preview.stop()
        bus.stop()
    log.info("Остановлено")
    return 0


def cmd_check_camera(settings: Settings, args: argparse.Namespace) -> int:
    print(f"Источник: {mask_url(settings.camera.url)}")
    r = probe_camera(settings.camera, args.seconds)
    if r.width == 0:
        print(f"✗ {r.error}")
        return 1
    print(f"✓ Подключено за {r.connect_s:.1f} с (backend: {r.backend})")
    print(f"  Заявлено: {r.width}x{r.height}, {r.declared_fps:.1f} fps, кодек {r.codec}")
    if r.image is None:
        print(f"✗ {r.error}")
        return 1
    print(f"✓ Получено {r.frames} кадров ({r.measured_fps:.1f} fps), ошибок чтения: {r.read_failures}")
    args.snapshot.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(args.snapshot), r.image)
    print(f"✓ Снимок: {args.snapshot}")
    return 0 if r.ok else 1


def _grab_frame(settings: Settings):
    cap = open_capture(settings.camera)
    try:
        if not cap.isOpened():
            return None
        image = None
        # Первые кадры после подключения бывают серыми/битыми, пока не придёт ключевой кадр
        for _ in range(10):
            ok, frame = cap.read()
            if ok:
                image = frame
        return image
    finally:
        cap.release()


def cmd_detect(settings: Settings, args: argparse.Namespace) -> int:
    image = cv2.imread(str(args.image)) if args.image else _grab_frame(settings)
    if image is None:
        print(f"✗ Не удалось получить изображение из {args.image or mask_url(settings.camera.url)}")
        return 1
    detector = build_detector(settings.detector)
    detector.detect(image)  # прогрев: первый проход заметно медленнее
    started = time.perf_counter()
    detections = detector.detect(image)
    elapsed_ms = (time.perf_counter() - started) * 1000

    print(f"Модель {settings.detector.model}, порог {settings.detector.confidence}: "
          f"{len(detections)} объектов за {elapsed_ms:.0f} мс")
    for d in sorted(detections, key=lambda d: -d.confidence):
        print(f"  {d.label:<12} {d.confidence:.2f}  box={d.box}  площадь={d.area_ratio(image.shape):.2%}")
    draw_detections(image, detections)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(args.out), image)
    print(f"Разметка: {args.out}")
    return 0


def cmd_plates(settings: Settings, args: argparse.Namespace) -> int:
    from .analyzers.vehicles import find_vehicles
    from .plates import KIND_NAMES, PlateReader, estimate_color

    image = cv2.imread(str(args.image)) if args.image else _grab_frame(settings)
    if image is None:
        print(f"✗ Не удалось получить изображение из {args.image or mask_url(settings.camera.url)}")
        return 1
    detector = build_detector(settings.detector)
    reader = PlateReader(settings.plates, settings.detector.models_dir)
    vehicles = find_vehicles(detector.detect(image), settings.plates, image.shape)
    print(f"Машин в кадре: {len(vehicles)}")

    found = 0
    for v in sorted(vehicles, key=lambda d: -d.box[2] * d.box[3]):
        started = time.perf_counter()
        read = reader.read(image, v.box)
        elapsed_ms = (time.perf_counter() - started) * 1000
        color = estimate_color(image, v.box)
        kind = KIND_NAMES.get(v.label, v.label)
        if read is None:
            print(f"  {kind}, {color}: номер не найден или нечитаем ({elapsed_ms:.0f} мс)")
        else:
            found += 1
            print(f"  {kind}, {color}: {read.display}  уверенность {read.confidence:.2f} ({elapsed_ms:.0f} мс)")
            px, py, pw, ph = read.box
            cv2.rectangle(image, (px, py), (px + pw, py + ph), (0, 255, 0), 2)
            cv2.putText(image, read.text, (px, py + ph + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
        x, y, w, h = v.box
        cv2.rectangle(image, (x, y), (x + w, y + h), (0, 200, 255), 2)
    if not vehicles:
        # Машину могли не распознать (вплотную к камере, обрезана) — ищем номер на всём кадре
        read = reader.read(image)
        print(f"  на всём кадре: {read.display if read else 'номер не найден'}")
        found += read is not None
    args.out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(args.out), image)
    print(f"Разметка: {args.out}")
    return 0 if found else 1


def cmd_vehicles(settings: Settings, args: argparse.Namespace) -> int:
    rows = recent_visits(settings.app.data_dir, args.limit, args.plate)
    if args.csv:
        with args.csv.open("w", newline="", encoding="utf-8-sig") as f:  # BOM — чтобы Excel понял кириллицу
            writer = csv.writer(f, delimiter=";")
            writer.writerow(COLUMNS)
            writer.writerows(tuple(row) for row in rows)
        print(f"✓ {len(rows)} записей → {args.csv}")
        return 0
    if not rows:
        print("Журнал пуст")
        return 0
    print(f"{'Подъехала':<20} {'Номер':<13} {'Цвет':<12} {'Тип':<10} {'Уехала':<20} Стояла")
    for r in rows:
        plate = (r["plate"] or "—") + ("" if r["plate_verified"] or not r["plate"] else "?")
        stood = format_duration(r["duration_s"]) if r["duration_s"] is not None else "стоит"
        print(f"{r['arrived_at']:<20} {plate:<13} {r['color']:<12} {r['kind']:<10} {r['left_at'] or '—':<20} {stood}")
    print(f"\nСнимки: {settings.app.data_dir / 'vehicles'}   «?» — номер прочитан неуверенно")
    return 0


def cmd_notify_test(settings: Settings, args: argparse.Namespace) -> int:
    image = args.image.read_bytes() if args.image else None
    notification = Notification(
        title=f"{settings.camera.name}: тестовое уведомление",
        message=f"track-seeds работает — {time.strftime('%H:%M:%S')}",
        tags=("test_tube",),
        image=image,
    )
    notifiers = build_notifiers(settings)
    service = NotificationService(notifiers, settings.notify)
    results = {n.name: service.send(n, notification) for n in notifiers}
    for name, ok in results.items():
        print(f"{'✓' if ok else '✗'} {name}")
    return 0 if all(results.values()) else 1


def cmd_download_model(settings: Settings, args: argparse.Namespace) -> int:
    names = args.names or [settings.detector.model]
    for name in names:
        if name not in CATALOG:
            print(f"✗ {name}: нет в каталоге ({', '.join(CATALOG)})")
            return 1
        print(f"✓ {download_model(name, settings.detector.models_dir)}")
    if args.plates or "plates" in settings.app.analyzers:
        from .plates import download_plate_models

        for path in download_plate_models(settings.plates, settings.detector.models_dir):
            print(f"✓ {path}")
    if args.voice:
        from .voice import download_voice

        print(f"✓ {download_voice(args.voice, settings.detector.models_dir)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="track-seeds", description="Мониторинг по IP-камере")
    parser.add_argument("--env-file", default=".env", help="путь к .env (по умолчанию ./.env)")
    parser.add_argument("--camera", metavar="URL", help="переопределить CAMERA_URL (RTSP, файл, индекс вебкамеры)")
    sub = parser.add_subparsers(dest="command", required=True)

    add_commands(sub)

    # --- прежние режимы (люди, номера) — одна камера из .env, без веба ---
    p = sub.add_parser("run", help="[старое] одна камера из .env без веба: люди / номера машин")
    p.add_argument("--preview", action="store_true", help="включить MJPEG-превью в браузере")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("check-camera", help="проверить подключение к камере и сохранить снимок")
    p.add_argument("--seconds", type=float, default=5, help="сколько секунд читать поток")
    p.add_argument("--snapshot", type=Path, default=Path("data/snapshot.jpg"))
    p.set_defaults(func=cmd_check_camera)

    p = sub.add_parser("detect", help="прогнать детектор на кадре с камеры или на картинке")
    p.add_argument("--image", type=Path, help="картинка вместо кадра с камеры")
    p.add_argument("--out", type=Path, default=Path("data/detect.jpg"))
    p.set_defaults(func=cmd_detect)

    p = sub.add_parser("plates", help="найти машины и прочитать номера на кадре с камеры или на картинке")
    p.add_argument("--image", type=Path, help="картинка вместо кадра с камеры")
    p.add_argument("--out", type=Path, default=Path("data/plates.jpg"))
    p.set_defaults(func=cmd_plates)

    p = sub.add_parser("vehicles", help="журнал заездов машин")
    p.add_argument("--limit", type=int, default=30, help="сколько последних записей показать")
    p.add_argument("--plate", help="поиск по номеру, можно часть: 484, С484НХ, C484HX")
    p.add_argument("--csv", type=Path, help="выгрузить в CSV (открывается в Excel)")
    p.set_defaults(func=cmd_vehicles)

    p = sub.add_parser("notify-test", help="отправить тестовое уведомление во все каналы")
    p.add_argument("--image", type=Path, help="приложить картинку")
    p.set_defaults(func=cmd_notify_test)

    p = sub.add_parser("download-model", help="скачать модели детектора")
    p.add_argument("names", nargs="*", help=f"имена из каталога: {', '.join(CATALOG)}")
    p.add_argument("--plates", action="store_true", help="ещё и модели номеров (если пресет plates не включён)")
    p.add_argument("--voice", nargs="?", const="ru_RU-irina-medium", metavar="ГОЛОС",
                   help="ещё и голос piper для оповещений (по умолчанию ru_RU-irina-medium)")
    p.set_defaults(func=cmd_download_model)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        settings = load_settings(args.env_file if Path(args.env_file).is_file() else None)
    except ValidationError as e:
        sys.exit(f"Ошибка конфигурации:\n{e}")
    if args.camera:
        settings = dataclasses.replace(settings, camera=settings.camera.model_copy(update={"url": args.camera}))

    logging.basicConfig(level=settings.app.log_level.upper(),
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
    if settings.app.log_level.upper() != "DEBUG":
        cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)

    try:
        sys.exit(args.func(settings, args))
    except (ValueError, FileNotFoundError) as e:
        sys.exit(f"Ошибка: {e}")
