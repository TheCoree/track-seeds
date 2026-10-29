"""Пресет «Семена в дробилке»: зоны бункера открылись → семена заканчиваются.

Человек при настройке рисует на кадре зоны (обычно одну — у дна бункера, где семена
кончаются последними, и, если нужно, ещё одну повыше — «осталось мало») и отмечает
эталоны: кадры, где в зоне есть семена и где пусто. Дальше каждый кадр сравнивается
с эталонами, а решение принимается, только если зона устойчиво пуста empty_after_s секунд.

Зона «привод» (цепь, звёздочка) показывает, работает ли дробилка. Пока привод стоит, о семенах
не сообщается: у остановленной дробилки и картинка в бункере другая (поток семян замер, лопасть
остановилась где придётся), и пустой бункер никому не мешает. После запуска наличие семян
подтверждается заново — по кадрам, снятым в работе.
"""

import logging
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from ..camera import Frame
from ..config import SeedsConfig, SeedZone
from ..detection import Detector
from ..events import Event, SeedsLevelChanged
from ..seeds import (Features, Reading, Score, ZoneDebounce, ZoneModel, changed_share, confidence, extract,
                     extract_patch, is_grayscale, motion_patch, order_quad, zone_box)

log = logging.getLogger(__name__)

# Зона закрыта человеком, если его рамка покрывает такую долю описанного прямоугольника зоны.
# Людей этот режим не отслеживает и не рисует: детектор нужен только, чтобы не принять
# загороженную зону за пустую. Он дорогой (~0.5 с процессора на кадр), поэтому запускается,
# только когда кадр может что-то изменить, и не чаще раза в _PEOPLE_REUSE_S: люди движутся медленно.
_OCCLUSION = 0.1
_PEOPLE_REUSE_S = 1.0
_HISTORY_S = 15 * 60
_HISTORY_STEP_S = 1.0  # история для графика — не чаще точки в секунду, как бы часто ни проверяли

# Кадр привода сравнивается с предыдущим, только если тот не старше стольких интервалов проверки
_MOTION_MAX_GAP = 3

_COLORS = {Reading.COVERED: (0, 190, 0), Reading.UNCOVERED: (0, 0, 230), Reading.UNKNOWN: (0, 200, 230)}
# привод: движется — синий, стоит — серый
_DRIVE_COLORS = {Reading.COVERED: (230, 140, 0), Reading.UNCOVERED: (150, 150, 150), Reading.UNKNOWN: (0, 200, 230)}
_LEVEL_TEXT = {"ok": "SEEDS OK", "low": "SEEDS LOW", "empty": "SEEDS EMPTY", "unknown": "SEEDS: ?",
               "stopped": "STOPPED"}

# (путь, mtime, углы зоны) → признаки: при правке настроек эталоны не пересчитываются заново
_FEATURE_CACHE: OrderedDict[tuple, Features] = OrderedDict()
_FEATURE_CACHE_SIZE = 1000


def _sample_features(path: Path, zones: list[SeedZone], wanted: set[str]) -> dict[str, Features]:
    """Признаки зон wanted на сохранённом кадре эталона."""
    stamp = path.stat().st_mtime_ns
    result: dict[str, Features] = {}
    image = None
    for zone in zones:
        if zone.id not in wanted:
            continue
        key = (str(path), stamp, tuple(map(tuple, zone.points)))
        if (cached := _FEATURE_CACHE.get(key)) is not None:
            _FEATURE_CACHE.move_to_end(key)
            result[zone.id] = cached
            continue
        if image is None:
            image = cv2.imread(str(path))
            if image is None:
                raise ValueError(f"не читается {path}")
            gray = is_grayscale(image)
        result[zone.id] = _FEATURE_CACHE[key] = extract(extract_patch(image, zone.points), gray)
        if len(_FEATURE_CACHE) > _FEATURE_CACHE_SIZE:
            _FEATURE_CACHE.popitem(last=False)
    return result


@dataclass
class _Zone:
    config: SeedZone
    model: ZoneModel
    # семена: COVERED — есть, UNCOVERED — пусто; привод: COVERED — движется, UNCOVERED — стоит
    debounce: ZoneDebounce
    reading: Reading = Reading.UNKNOWN
    reason: str = "start"  # почему UNKNOWN: start, no_samples, people, dark, novel, border
    score: Score | None = None
    brightness: float | None = None
    motion: float | None = None  # привод: доля зоны, изменившейся с прошлой проверки
    previous: tuple[float, np.ndarray] | None = None  # привод: прошлый кадр зоны
    history: deque[tuple[float, float | None]] = field(default_factory=deque)

    @property
    def drive(self) -> bool:
        return self.config.kind == "motion"


def _covered_share(box: tuple[int, int, int, int], people: list[tuple[int, int, int, int]]) -> float:
    x, y, w, h = box
    area = max(w * h, 1)
    best = 0.0
    for px, py, pw, ph in people:
        ix = max(0, min(x + w, px + pw) - max(x, px))
        iy = max(0, min(y + h, py + ph) - max(y, py))
        best = max(best, ix * iy / area)
    return best


class SeedsAnalyzer:
    def __init__(self, settings: SeedsConfig, camera_name: str, samples_dir: Path, detector: Detector | None = None,
                 snapshot_quality: int = 85, builtin_dir: Path | None = None) -> None:
        """samples_dir — папка, относительно которой записаны пути эталонов (папка дробилки);
        builtin_dir — то же для эталонов, вшитых в программу."""
        self.name = "seeds"
        self.interval_s = settings.interval_s
        self._settings = settings
        self._camera = camera_name
        self._detector = detector if settings.ignore_people else None
        self._quality = snapshot_quality
        self._builtin_dir = builtin_dir
        self._zones = [self._build_zone(zone, samples_dir) for zone in settings.zones]
        self._drives = [z for z in self._zones if z.drive]
        self._seeds = [z for z in self._zones if not z.drive]
        self.level: str | None = None  # None — ещё не определён после запуска
        self.level_since: float | None = None
        self.running: bool | None = None  # None — не определено или зоны привода нет
        self.running_since: float | None = None
        self._last_known: str | None = None
        self._last_alert = 0.0
        self._reminders = 0
        self._people: list[tuple[int, int, int, int]] = []
        self._people_at = float("-inf")
        self._lock = threading.Lock()  # process() — в потоке конвейера, status() — из веб-сервера

    def _build_zone(self, zone: SeedZone, samples_dir: Path) -> _Zone:
        s = self._settings
        if zone.kind == "motion":
            return _Zone(config=zone, model=ZoneModel([], []),
                         debounce=ZoneDebounce(s.stopped_after_s, s.running_after_s, s.agreement))
        full: list[Features] = []
        empty: list[Features] = []
        for sample in self._settings.samples:
            label = sample.labels.get(zone.id)
            if label is None:
                continue
            folder = self._builtin_dir if sample.builtin and self._builtin_dir is not None else samples_dir
            try:
                features = _sample_features(folder / sample.image, self._settings.zones, {zone.id})[zone.id]
            except (OSError, ValueError) as e:
                log.warning("Эталон %s пропущен: %s", sample.id, e)
                continue
            (full if label == "full" else empty).append(features)
        return _Zone(config=zone, model=ZoneModel(full, empty, self._settings.one_class_tolerance),
                     debounce=ZoneDebounce(s.empty_after_s, s.refill_after_s, s.agreement),
                     reason="start" if full or empty else "no_samples")

    def inherit(self, old: "SeedsAnalyzer") -> None:
        """Продолжить с состоянием прежнего анализатора (настройки поменяли — тревога не повторяется)."""
        self.level, self.level_since, self._last_known = old.level, old.level_since, old._last_known
        self._last_alert, self._reminders = old._last_alert, old._reminders
        if self._drives and old._drives:
            self.running, self.running_since = old.running, old.running_since
        previous = {z.config.id: z for z in old._zones}
        for zone in self._zones:
            if (prev := previous.get(zone.config.id)) is not None and prev.drive == zone.drive:
                zone.debounce.inherit(prev.debounce)
                zone.history = prev.history
                if zone.config.points == prev.config.points:
                    zone.previous = prev.previous

    # --- кадр → события ---

    def process(self, frame: Frame) -> list[Event]:
        with self._lock:
            return self._process(frame)

    def _process(self, frame: Frame) -> list[Event]:
        image, ts = frame.image, frame.ts
        grayscale = is_grayscale(image)
        for zone in self._drives:
            zone.reading, zone.reason = self._read_motion(zone, image, ts)
        for zone in self._seeds:
            zone.reading, zone.reason = self._read(zone, image, grayscale)
        self._mask_people(image, ts)
        for zone in self._drives:
            zone.debounce.update(ts, zone.reading)
            self._remember(zone, ts, zone.motion)
        self._update_running(ts)
        # Привод в эту секунду не движется — кадр желоба в решение не идёт, ещё до подтверждения «стоит»:
        # иначе при короткой задержке «закончились» лопасть, замершая перед зоной, успела бы поднять тревогу.
        still_now = bool(self._drives) and all(z.reading is Reading.UNCOVERED for z in self._drives)
        for zone in self._seeds:
            if self.running is not False and not still_now:  # кадры остановленной дробилки не в счёт
                zone.debounce.update(ts, zone.reading)
            self._remember(zone, ts, self._confidence(zone))
        return self._update_level(frame)

    def _mask_people(self, image: np.ndarray, ts: float) -> None:
        """Зона, которую загородил человек, — «не определено». Проверяется, только если кадр может что-то
        изменить: желоб «пусто» или привод не в своём состоянии. Семена есть и привод крутится — детектор не нужен."""
        if self._detector is None:
            return
        suspicious = [z for z in self._seeds if z.reading is Reading.UNCOVERED]
        suspicious += [z for z in self._drives if z.reading is not Reading.UNKNOWN and z.reading is not z.debounce.stable]
        if not suspicious:
            return
        if ts - self._people_at >= _PEOPLE_REUSE_S:
            self._people = [d.box for d in self._detector.detect(image) if d.label == "person"]
            self._people_at = ts
        for zone in suspicious:
            if self._people and _covered_share(zone_box(zone.config.points, image.shape), self._people) > _OCCLUSION:
                zone.reading, zone.reason = Reading.UNKNOWN, "people"
                if zone.drive:  # человек двигался в зоне — со следующим кадром его не сравнивать
                    zone.previous, zone.motion = None, None

    def _confidence(self, zone: _Zone) -> float | None:
        return confidence(zone.score.value, self._settings.threshold) if zone.score else None

    @staticmethod
    def _remember(zone: _Zone, ts: float, value: float | None) -> None:
        if zone.history and ts - zone.history[-1][0] < _HISTORY_STEP_S:
            return
        zone.history.append((ts, value if zone.reading is not Reading.UNKNOWN else None))
        while zone.history[0][0] < ts - _HISTORY_S:
            zone.history.popleft()

    def _update_running(self, ts: float) -> None:
        """Работает, если движется хоть один привод; стоит — если стоят все."""
        states = [z.debounce.stable for z in self._drives]
        if Reading.COVERED in states:
            running = True
        elif states and all(state is Reading.UNCOVERED for state in states):
            running = False
        else:
            return
        if running == self.running:
            return
        if running and self.running is False:
            # запустили: что было в бункере, пока стояла, не в счёт — семена подтверждаются заново
            s = self._settings
            for zone in self._seeds:
                zone.debounce = ZoneDebounce(s.empty_after_s, s.refill_after_s, s.agreement)
        log.info("Дробилка «%s»: %s", self._camera, "работает" if running else "стоит")
        self.running, self.running_since = running, ts

    def _read_motion(self, zone: _Zone, image: np.ndarray, ts: float) -> tuple[Reading, str]:
        s = self._settings
        zone.motion = None
        patch = motion_patch(image, zone.config.points)
        zone.brightness = float(patch.mean())
        previous, zone.previous = zone.previous, (ts, patch)
        if zone.brightness < s.min_brightness:
            return Reading.UNKNOWN, "dark"
        if previous is None or ts - previous[0] > _MOTION_MAX_GAP * s.interval_s or previous[1].shape != patch.shape:
            return Reading.UNKNOWN, "start"
        zone.motion = changed_share(patch, previous[1])
        return (Reading.COVERED if zone.motion >= s.motion_threshold else Reading.UNCOVERED), ""

    def _read(self, zone: _Zone, image: np.ndarray, grayscale: bool) -> tuple[Reading, str]:
        s = self._settings
        zone.score = None
        if not zone.model.ready:
            return Reading.UNKNOWN, "no_samples"
        features = extract(extract_patch(image, zone.config.points), grayscale)
        zone.brightness = features.brightness
        if features.brightness < s.min_brightness:
            return Reading.UNKNOWN, "dark"
        zone.score = score = zone.model.score(features)
        if score.novelty is not None and score.novelty > s.novelty:
            return Reading.UNKNOWN, "novel"
        if score.value >= s.threshold + s.hysteresis / 2:
            return Reading.COVERED, ""
        if score.value <= s.threshold - s.hysteresis / 2:
            return Reading.UNCOVERED, ""
        # у самой границы — держим прежнее показание
        if zone.reading is not Reading.UNKNOWN:
            return zone.reading, ""
        return Reading.UNKNOWN, "border"

    def _active(self) -> tuple[list[_Zone], list[_Zone]]:
        ready = [z for z in self._seeds if z.model.ready]
        return [z for z in ready if z.config.kind == "empty"], [z for z in ready if z.config.kind == "low"]

    def _compute_level(self, ts: float) -> tuple[str | None, list[_Zone]]:
        """Уровень и зоны, которые его определили."""
        empty_zones, low_zones = self._active()
        main = empty_zones or low_zones
        if not main:
            return None, []
        if self._drives and not self.running:
            # стоит — «stopped»; ещё не ясно, крутится ли привод, — ничего не решаем
            return ("stopped", []) if self.running is False else (None, [])
        alert_s = self._settings.unknown_alert_s
        if alert_s and (blind := [z for z in main if z.debounce.blind_for(ts) >= alert_s]):
            return "unknown", blind
        if opened := [z for z in empty_zones if z.debounce.stable is Reading.UNCOVERED]:
            return "empty", opened
        if opened := [z for z in low_zones if z.debounce.stable is Reading.UNCOVERED]:
            return "low", opened
        if all(z.debounce.stable is Reading.COVERED for z in main):
            return "ok", []
        return None, []

    def _update_level(self, frame: Frame) -> list[Event]:
        ts = frame.ts
        level, zones = self._compute_level(ts)
        if level is None or level == self.level:
            if (self.level == "empty" and self._settings.remind_every_s
                    and ts - self._last_alert >= self._settings.remind_every_s):
                self._reminders += 1
                self._last_alert = ts
                return [self._event(frame, "empty", "empty", ts - (self.level_since or ts), zones,
                                    reminder=self._reminders)]
            return []

        previous, since = self.level, self.level_since
        self.level, self.level_since = level, ts
        self._last_alert, self._reminders = ts, 0
        last_known = self._last_known
        if level not in ("unknown", "stopped"):
            self._last_known = level
        if level == "unknown":
            duration = max(z.debounce.blind_for(ts) for z in zones)
        else:
            duration = ts - since if since is not None else 0.0
        # Только в журнал: остановка, первое «семена есть» после запуска сервера или дробилки,
        # «досыпали» при notify_refill=false
        quiet = level == "stopped" or level == "ok" and (
            previous in (None, "stopped") or not self._settings.notify_refill and previous != "unknown")
        log.info("Семена (%s): %s → %s", self._camera, previous or "старт", level)
        return [self._event(frame, level, previous, duration, zones, last_known=last_known, quiet=quiet)]

    def _event(self, frame: Frame, level: str, previous: str | None, duration: float, zones: list[_Zone],
               reminder: int = 0, last_known: str | None = None, quiet: bool = False) -> SeedsLevelChanged:
        annotated = frame.image.copy()
        self.annotate(annotated)
        return SeedsLevelChanged(
            camera=self._camera, ts=frame.ts, level=level, previous=previous, duration_s=duration,
            reminder=reminder, zones=tuple(z.config.name for z in zones), last_known=last_known, quiet=quiet,
            snapshot=_jpeg(annotated, self._quality), frame=_jpeg(frame.image, 92),
        )

    def evaluate(self, image: np.ndarray) -> list[dict]:
        """Оценка зон на одном кадре без изменения состояния — для `track-seeds check`."""
        grayscale = is_grayscale(image)
        result = []
        with self._lock:
            for zone in self._seeds:
                previous = (zone.reading, zone.score, zone.brightness)
                zone.reading = Reading.UNKNOWN  # без учёта прошлых кадров
                reading, reason = self._read(zone, image, grayscale)
                result.append({"name": zone.config.name, "kind": zone.config.kind, "reading": reading.value,
                               "reason": reason, "score": zone.score.value if zone.score else None,
                               "full": len(zone.model.full), "empty": len(zone.model.empty)})
                zone.reading, zone.score, zone.brightness = previous
        return result

    def evaluate_drives(self, before: np.ndarray, after: np.ndarray) -> list[dict]:
        """Движение в зонах привода между двумя кадрами — для `track-seeds check`."""
        result = []
        for zone in self._drives:
            a, b = motion_patch(before, zone.config.points), motion_patch(after, zone.config.points)
            dark = float(b.mean()) < self._settings.min_brightness
            motion = None if dark or a.shape != b.shape else changed_share(b, a)
            result.append({"name": zone.config.name, "motion": motion,
                           "moving": motion is not None and motion >= self._settings.motion_threshold})
        return result

    # --- превью и состояние для веб-интерфейса ---

    def annotate(self, image: np.ndarray) -> None:
        h, w = image.shape[:2]
        thickness = max(2, round(w / 640))
        for number, zone in enumerate(self._zones, 1):
            stable = zone.debounce.stable
            if zone.drive:
                color = _DRIVE_COLORS[stable or Reading.UNKNOWN]
            else:
                color = _COLORS[stable] if stable is not None and zone.model.ready else _COLORS[Reading.UNKNOWN]
            quad = np.array([(x * w, y * h) for x, y in order_quad(zone.config.points)], np.int32)
            cv2.polylines(image, [quad], True, color, thickness, cv2.LINE_AA)
            if zone.drive:
                label = f"{number}: {zone.motion:.0%}" if zone.motion is not None else f"{number}: {zone.reason or '?'}"
            else:
                conf = self._confidence(zone)
                label = f"{number}: {conf:.0%}" if conf is not None else f"{number}: {zone.reason or '?'}"
            x, y = int(quad[:, 0].min()), int(quad[:, 1].min())
            cv2.putText(image, label, (x + 4, max(y - 8, 16)), cv2.FONT_HERSHEY_SIMPLEX, 0.5 * thickness / 2 + 0.2,
                        color, thickness, cv2.LINE_AA)
        if self.level is not None:
            # внизу слева на подложке: вверху слева камеры обычно рисуют дату и время
            color = {"ok": (0, 190, 0), "low": (0, 160, 255), "empty": (0, 0, 230),
                     "stopped": (230, 140, 0)}.get(self.level, (0, 200, 230))
            text, scale = _LEVEL_TEXT[self.level], 0.8 * thickness / 2
            (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)
            cv2.rectangle(image, (6, h - th - 22), (tw + 18, h - 6), (0, 0, 0), -1)
            cv2.putText(image, text, (12, h - 14), cv2.FONT_HERSHEY_SIMPLEX, scale, color, thickness, cv2.LINE_AA)

    def status(self) -> dict:
        with self._lock:
            return self._status()

    def _status(self) -> dict:
        now = time.time()
        zones = []
        for zone in self._zones:
            score = zone.score
            zones.append({
                "id": zone.config.id,
                "name": zone.config.name,
                "kind": zone.config.kind,
                "state": zone.debounce.stable.value if zone.debounce.stable else None,
                "state_since": zone.debounce.stable_since,
                "reading": zone.reading.value,
                "reason": zone.reason,
                "score": round(score.value, 3) if score else None,
                "confidence": round(conf, 4) if (conf := self._confidence(zone)) is not None else None,
                "d_full": round(score.d_full, 3) if score and score.d_full is not None else None,
                "d_empty": round(score.d_empty, 3) if score and score.d_empty is not None else None,
                "novelty": round(score.novelty, 2) if score and score.novelty is not None else None,
                "brightness": round(zone.brightness) if zone.brightness is not None else None,
                "motion": round(zone.motion, 3) if zone.motion is not None else None,
                "blind_s": round(zone.debounce.blind_for(now)),
                "samples": {"full": len(zone.model.full), "empty": len(zone.model.empty)},
                "separation": round(zone.model.separation, 3) if zone.model.separation else None,
                "radius": round(zone.model.radius, 3) if zone.model.one_class else None,
                "history": [(round(t, 1), None if v is None else round(v, 3)) for t, v in zone.history],
            })
        s = self._settings
        band = (confidence(s.threshold - s.hysteresis / 2, s.threshold),
                confidence(s.threshold + s.hysteresis / 2, s.threshold))
        return {"level": self.level, "level_since": self.level_since, "last_known": self._last_known,
                "running": self.running, "running_since": self.running_since,
                "confidence_band": [round(band[0], 3), round(band[1], 3)], "zones": zones}


def _jpeg(image: np.ndarray, quality: int) -> bytes:
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes() if ok else b""
