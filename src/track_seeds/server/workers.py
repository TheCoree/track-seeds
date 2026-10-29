"""Дробилки в работе: по конвейеру на дробилку, общая шина, уведомления, голос, журнал событий."""

import dataclasses
import logging
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from ..analyzers.seeds import SeedsAnalyzer
from ..bus import EventBus
from ..camera import OpenCVSource
from ..config import (AppConfig, CameraConfig, PlatesConfig, PresenceConfig, PreviewConfig, SeedSample, SeedsConfig,
                      Settings)
from ..detection import FrameCachedDetector, build_detector
from ..events import Event
from ..notify import LogNotifier, Notification, NotificationService, build_notifiers
from ..pipeline import Pipeline
from ..voice import Announcer, Speaker, Synthesizer, build_speaker, phrase_for
from .eventlog import EventLog
from .store import ConfigStore, Crusher, GlobalSettings

log = logging.getLogger(__name__)

_PREVIEW_MAX_WIDTH = 1280
_VIEWER_TTL_S = 5  # снимок запросили — рисуем разметку ещё столько секунд
_BUILD_RETRY_S = 30


def notify_settings(settings: GlobalSettings, data_dir: Path) -> Settings:
    """Общие настройки в том виде, в каком их ждут каналы уведомлений."""
    return Settings(app=AppConfig(analyzers=[], data_dir=data_dir), camera=CameraConfig(), detector=settings.detector,
                    presence=PresenceConfig(), plates=PlatesConfig(), notify=settings.notify, ntfy=settings.ntfy,
                    preview=PreviewConfig())


class FrameHub:
    """Последний кадр с разметкой для веб-интерфейса. Конвейер рисует разметку, только пока его кто-то смотрит."""

    def __init__(self, quality: int = 80) -> None:
        self._quality = quality
        self._lock = threading.Lock()
        self._jpeg: bytes | None = None
        self._seq = 0
        self._ts = 0.0
        self._streams = 0
        self._wanted_until = 0.0

    @property
    def has_clients(self) -> bool:
        return self._streams > 0 or time.monotonic() < self._wanted_until

    def touch(self) -> None:
        self._wanted_until = time.monotonic() + _VIEWER_TTL_S

    def open_stream(self) -> None:
        with self._lock:
            self._streams += 1

    def close_stream(self) -> None:
        with self._lock:
            self._streams -= 1

    def update(self, image: np.ndarray) -> None:
        if image.shape[1] > _PREVIEW_MAX_WIDTH:
            scale = _PREVIEW_MAX_WIDTH / image.shape[1]
            image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, self._quality])
        if ok:
            with self._lock:
                self._jpeg, self._ts = buf.tobytes(), time.time()
                self._seq += 1

    def latest(self) -> tuple[int, bytes | None, float]:
        with self._lock:
            return self._seq, self._jpeg, self._ts


class CameraPublisher:
    """Проставляет id дробилки в события, чтобы журнал и голос знали, откуда они."""

    def __init__(self, bus: EventBus, camera_id: str) -> None:
        self._bus = bus
        self._camera_id = camera_id

    def publish(self, event: Event) -> None:
        self._bus.publish(dataclasses.replace(event, camera_id=self._camera_id))


class CameraWorker:
    """Одна дробилка: камера → анализ семян → события."""

    def __init__(self, crusher: Crusher, settings: GlobalSettings, store: ConfigStore, bus: EventBus) -> None:
        self.crusher = crusher
        self._settings = settings
        self._store = store
        self._bus = bus
        self.hub = FrameHub()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._pipeline: Pipeline | None = None
        self._source: OpenCVSource | None = None
        self.state = "stopped"  # stopped, disabled, starting, running, error
        self.error: str | None = None
        self._build_lock = threading.Lock()

    @property
    def id(self) -> str:
        return self.crusher.id

    def start(self) -> None:
        if not self.crusher.enabled:
            self.state = "disabled"
            return
        if missing_password(self.crusher):
            # без пароля камера отвергала бы каждое переподключение — Hikvision заблокирует IP, и видео пропадёт
            self.state, self.error = "error", ("Не задан пароль камеры — укажите его на вкладке «Камера» "
                                               "или в терминале: track-seeds password " + self.crusher.id)
            return
        self._stop.clear()
        self.state, self.error = "starting", None
        self._thread = threading.Thread(target=self._run, name=f"camera-{self.id}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(self.crusher.camera.read_timeout_s + 5)
            self._thread = None
        self._pipeline = self._source = None
        self.state = "stopped" if self.crusher.enabled else "disabled"

    def _analyzer(self) -> SeedsAnalyzer:
        crusher = self.crusher
        # детектор людей — только чтобы не принять загороженную человеком зону за пустую; без плиток:
        # человек, загородивший желоб или цепь, стоит у камеры и в кадре крупный (плитки — втрое дороже)
        detector = (FrameCachedDetector(build_detector(self._settings.detector.model_copy(update={"tiling": False})))
                    if crusher.seeds.ignore_people else None)
        return SeedsAnalyzer(crusher.seeds, crusher.name, self._store.crusher_dir(crusher.id), detector,
                             builtin_dir=self._store.specs[crusher.id].builtin_dir)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                analyzer = self._analyzer()
            except Exception as e:
                self.state, self.error = "error", f"Анализ не запустился: {e}"
                log.exception("Дробилка «%s»: анализ не запустился", self.crusher.name)
                self._stop.wait(_BUILD_RETRY_S)
                continue
            camera = self.crusher.camera.model_copy(update={"name": self.crusher.name})
            self._source = OpenCVSource(camera)
            self._pipeline = Pipeline(self._source, [analyzer], CameraPublisher(self._bus, self.id), self.crusher.name,
                                      offline_alert_s=camera.offline_alert_s, preview=self.hub, preview_fps=10)
            self.state, self.error = "running", None
            try:
                self._pipeline.run(self._stop)
            except Exception as e:
                self.state, self.error = "error", f"Обработка упала: {e}"
                log.exception("Дробилка «%s»: обработка упала, перезапуск", self.crusher.name)
                self._stop.wait(5)

    def apply(self, crusher: Crusher, settings: GlobalSettings) -> None:
        """Новые настройки: подключение/детектор — перезапуск, остальное — замена анализа на ходу."""
        restart = (crusher.enabled != self.crusher.enabled or crusher.camera != self.crusher.camera
                   or settings.detector != self._settings.detector)
        self.crusher, self._settings = crusher, settings
        if restart or self._pipeline is None:
            self.stop()
            self.start()
            return
        pipeline = self._pipeline

        def rebuild() -> None:
            with self._build_lock:
                try:
                    pipeline.set_analyzers([self._analyzer()])
                    self.error = None
                except Exception as e:
                    self.error = f"Новые настройки не применились: {e}"
                    log.exception("Дробилка «%s»: новые настройки не применились", crusher.name)

        threading.Thread(target=rebuild, name=f"rebuild-{self.id}", daemon=True).start()

    def raw_frame(self) -> np.ndarray | None:
        source = self._source
        frame = source.wait_frame(-1, 0) if source is not None else None
        return frame.image if frame is not None else None

    def status(self) -> dict:
        pipeline, source = self._pipeline, self._source
        result: dict = {"state": self.state, "error": self.error, "connected": False, "since": None}
        if source is not None:
            result["connected"], result["since"] = source.connected, source.status_since
        if pipeline is not None:
            stats = pipeline.stats
            result |= {"fps": round(stats.fps, 1), "width": stats.width, "height": stats.height,
                       "last_frame_ts": stats.last_frame_ts or None,
                       "timing_ms": round(sum(stats.timings_ms.values()), 1),
                       "analyzer_error": next((v[1] for v in stats.errors.values()), None)}
            analyzer = next(iter(pipeline.analyzers), None)
            status = getattr(analyzer, "status", None)
            result["seeds"] = status() if status else None
        return result


class Supervisor:
    """Всё приложение в режиме сервера."""

    def __init__(self, store: ConfigStore, data_dir: Path, keep_days: int = 30) -> None:
        self.store = store
        self.data_dir = data_dir
        self.bus = EventBus()
        self.events = EventLog(data_dir, keep_days, speech=self._speech)
        self._workers: dict[str, CameraWorker] = {}
        self._speakers: dict[str, list[Speaker]] = {}
        self._lock = threading.RLock()
        self._notifications = NotificationService([LogNotifier()], store.settings.notify)
        self.notify_error: str | None = None
        self._synth: Synthesizer | None = None
        self.announcer = Announcer(self.synthesizer, lambda: self.settings.voice, self.targets,
                                   on_result=self.events.delivery)

    @property
    def settings(self) -> GlobalSettings:
        return self.store.settings

    # --- запуск ---

    def start(self) -> None:
        self.events.attach(self.bus)  # сначала журнал, потом голос (ставится в очередь) и рассылка
        self.announcer.attach(self.bus)
        self.bus.subscribe(Event, self._notify)
        self._rebuild_notifiers()
        self.bus.start()
        self.announcer.start()
        with self._lock:
            for crusher in self.store.ordered():
                self._start_worker(crusher)
        self.warm_voice()

    def stop(self) -> None:
        """Камеры — параллельно (каждая ждёт свой поток чтения); затем дослать события и договорить."""
        with self._lock:
            workers = list(self._workers.values())
        threads = [threading.Thread(target=w.stop, daemon=True) for w in workers]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.bus.stop()
        self.announcer.stop(timeout=30)
        self.events.close()
        log.info("Остановлено")

    def _start_worker(self, crusher: Crusher) -> None:
        self._speakers[crusher.id] = [build_speaker(s) for s in crusher.speakers]
        worker = CameraWorker(crusher, self.settings, self.store, self.bus)
        self._workers[crusher.id] = worker
        worker.start()

    def _rebuild_notifiers(self) -> None:
        try:
            notifiers = build_notifiers(notify_settings(self.settings, self.data_dir))
            self.notify_error = None
        except ValueError as e:
            log.error("Каналы уведомлений: %s", e)
            notifiers, self.notify_error = [LogNotifier()], str(e)
        self._notifications = NotificationService(notifiers, self.settings.notify, on_result=self.events.delivery)

    # --- подписчики шины ---

    def _notify(self, event: Event) -> None:
        crusher = self.store.crusher(event.camera_id)
        if crusher is not None and not crusher.notify:
            return
        self._notifications.handle(event)

    def targets(self, camera_id: str) -> tuple[SeedsConfig, list[Speaker]] | None:
        crusher = self.store.crusher(camera_id)
        return (crusher.seeds, self._speakers.get(camera_id, [])) if crusher is not None else None

    def _speech(self, event: Event) -> str:
        crusher = self.store.crusher(event.camera_id)
        return phrase_for(event, crusher.seeds) if crusher is not None and not event.quiet else ""

    def test_notify(self, notification: Notification) -> dict[str, str | None]:
        """Отправить во все каналы: имя канала → None (доставлено) или текст ошибки."""
        service = self._notifications
        return {n.name: service.deliver(n, notification) for n in service.notifiers}

    # --- голос ---

    def warm_voice(self) -> None:
        """Скачать/загрузить голос и заранее синтезировать фразы тревог — тревога не ждёт синтеза."""
        phrases = {text for p in self.store.crushers.values()
                   for text in (p.seeds.say_empty, p.seeds.say_low, p.seeds.say_refill, p.seeds.say_unknown) if text}

        def warm() -> None:
            try:
                synth = self.synthesizer()
                synth.prepare()
                for text in phrases:
                    synth.synthesize(text)
            except Exception as e:
                log.warning("Голос не подготовлен: %s", e)

        threading.Thread(target=warm, name="voice-warmup", daemon=True).start()

    def voice_status(self) -> dict:
        try:
            synth = self.synthesizer()
        except Exception as e:
            return {"engine": None, "voice": None, "state": "error", "error": str(e)}
        return {"engine": synth.engine, "voice": synth.voice, "state": synth.state, "error": synth.error,
                "configured": self.settings.voice.voice}

    def synthesizer(self) -> Synthesizer:
        if self._synth is None:
            self._synth = Synthesizer(self.settings.voice, self.settings.detector.models_dir, self.data_dir / "voice")
        return self._synth

    def speakers(self, crusher_id: str) -> list[Speaker]:
        return self._speakers.get(crusher_id, [])

    # --- дробилки ---

    def worker(self, crusher_id: str) -> CameraWorker | None:
        return self._workers.get(crusher_id)

    def _apply(self, crusher: Crusher) -> None:
        self._speakers[crusher.id] = [build_speaker(s) for s in crusher.speakers]
        if (worker := self._workers.get(crusher.id)) is not None:
            worker.apply(crusher, self.settings)
        else:
            self._start_worker(crusher)

    def update_crusher(self, crusher: Crusher) -> Crusher:
        """Эталоны правятся только своими методами: из формы приходит устаревший список."""
        with self._lock:
            def change(current: Crusher) -> None:
                for name in ("enabled", "notify", "camera", "speakers"):
                    setattr(current, name, getattr(crusher, name))
                current.seeds = crusher.seeds.model_copy(update={"zones": current.seeds.zones,
                                                                 "samples": current.seeds.samples})

            saved = self.store.update(crusher.id, change)
            self._apply(saved)
            self.warm_voice()
            return saved

    def update_settings(self, change) -> GlobalSettings:
        """Общие настройки (уведомления, голос, детектор)."""
        with self._lock:
            detector = self.settings.detector
            settings = self.store.update_settings(change)
            self._rebuild_notifiers()
            self._synth = None
            if settings.detector != detector:
                for worker in self._workers.values():
                    worker.apply(worker.crusher, settings)
            self.warm_voice()
            return settings

    # --- эталоны ---

    def add_sample(self, crusher_id: str, labels: dict[str, str], image: np.ndarray | None = None) -> SeedSample:
        with self._lock:
            crusher = self.store.crusher(crusher_id)
            if crusher is None:
                raise KeyError(crusher_id)
            if image is None:
                worker = self._workers.get(crusher_id)
                image = worker.raw_frame() if worker else None
                if image is None:
                    raise RuntimeError("Нет кадра с камеры — она не подключена")
            zone_ids = sample_zone_ids(crusher)
            labels = {z: v for z, v in labels.items() if z in zone_ids}
            if not labels:
                raise ValueError("Отметьте хотя бы одну зону семян (зоне привода эталоны не нужны)")
            sample_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() // 1000 % 1000000:06d}"
            relative = f"samples/{sample_id}.jpg"
            path = self.store.crusher_dir(crusher_id) / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if not cv2.imwrite(str(path), image, [cv2.IMWRITE_JPEG_QUALITY, 95]):
                raise RuntimeError(f"Не удалось сохранить {path}")
            sample = SeedSample(id=sample_id, image=relative, ts=time.time(), labels=labels)  # type: ignore[arg-type]
            self._change_samples(crusher_id, lambda samples: samples + [sample])
            return sample

    def update_sample(self, crusher_id: str, sample_id: str, labels: dict[str, str]) -> None:
        with self._lock:
            self._own_sample(crusher_id, sample_id)

            def change(samples: list[SeedSample]) -> list[SeedSample]:
                result = []
                for s in samples:
                    if s.id == sample_id:
                        s = s.model_copy(update={"labels": labels})
                    if s.labels:
                        result.append(s)
                    else:
                        self._delete_sample_file(crusher_id, s)
                return result

            self._change_samples(crusher_id, change)

    def remove_sample(self, crusher_id: str, sample_id: str) -> None:
        with self._lock:
            self._delete_sample_file(crusher_id, self._own_sample(crusher_id, sample_id))
            self._change_samples(crusher_id, lambda samples: [s for s in samples if s.id != sample_id])

    def _own_sample(self, crusher_id: str, sample_id: str) -> SeedSample:
        crusher = self.store.crusher(crusher_id)
        sample = next((s for s in crusher.seeds.samples if s.id == sample_id), None) if crusher else None
        if sample is None:
            raise KeyError(sample_id)
        if sample.builtin:
            raise ValueError("Это эталон из программы — его не удалить и не переотметить")
        return sample

    def _change_samples(self, crusher_id: str, change) -> None:
        def apply(crusher: Crusher) -> None:
            crusher.seeds.samples = change(list(crusher.seeds.samples))

        self._apply(self.store.update(crusher_id, apply))

    def _delete_sample_file(self, crusher_id: str, sample: SeedSample) -> None:
        path = None if sample.builtin else self.store.sample_path(crusher_id, sample)
        if path is not None:
            path.unlink(missing_ok=True)


def sample_zone_ids(crusher: Crusher) -> set[str]:
    """Зоны, для которых снимают эталоны: все, кроме привода."""
    return {z.id for z in crusher.seeds.zones if z.kind != "motion"}


def missing_password(crusher: Crusher) -> bool:
    """Логин есть, пароля нет — камера отвергла бы вход."""
    return bool(crusher.camera.username) and crusher.camera.password is None and "@" not in crusher.camera.url
