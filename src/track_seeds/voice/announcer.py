"""Голосовые оповещения: события по семенам → фраза → динамики (в отдельном потоке)."""

import logging
import queue
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

import numpy as np

from ..bus import EventBus
from ..config import SeedsConfig, VoiceConfig, parse_hours
from ..events import Event, SeedsLevelChanged
from .audio import chime, normalize, resample
from .speakers import Speaker, SpeakerError
from .tts import Synthesizer

log = logging.getLogger(__name__)

_RATE = 22050


def phrase_for(event: Event, rules: SeedsConfig) -> str:
    """Что сказать вслух по событию; пустая строка — молчать."""
    if not isinstance(event, SeedsLevelChanged):
        return ""
    if event.test:
        return f"Проверка оповещения. {rules.say_empty}" if rules.say_empty else "Проверка оповещения."
    match event.level:
        case "empty" if event.reminder:
            return rules.say_empty if rules.say_reminders else ""
        case "empty":
            return rules.say_empty
        case "low":
            return rules.say_low
        case "unknown":
            return rules.say_unknown
        case "stopped":
            return rules.say_stopped
        case "ok" if event.previous in ("empty", "low"):
            return rules.say_refill
    return ""


def in_hours(hours: str, now: datetime | None = None) -> bool:
    """Попадает ли текущее время в интервал «08:00-20:00» (может переходить через полночь)."""
    if not hours:
        return True
    start, end = parse_hours(hours)
    now = now or datetime.now()
    minute = now.hour * 60 + now.minute
    return start <= minute < end if start <= end else minute >= start or minute < end


def compose(speech: np.ndarray, rate: int, voice: VoiceConfig, times: int) -> tuple[np.ndarray, int]:
    """Сигнал + фраза, повторённая times раз, с паузами; громкость по настройке."""
    speech = resample(speech, rate, _RATE)
    pause = np.zeros(int(voice.pause_s * _RATE), np.float32)
    parts: list[np.ndarray] = [chime(_RATE)] if voice.chime else []
    for i in range(times):
        parts += [speech] if i == 0 else [pause, speech]
    return normalize(np.concatenate(parts), 0.9 * voice.volume), _RATE


@dataclass(frozen=True)
class _Job:
    text: str
    speakers: tuple[Speaker, ...]
    times: int
    event_id: str | None


# (id события или None, канал, успех, ошибка)
type ResultCallback = Callable[[str | None, str, bool, str | None], None]
# id камеры → (правила семян, её динамики); None — камеры нет
type Targets = Callable[[str], tuple[SeedsConfig, list[Speaker]] | None]


class Announcer:
    """Подписчик шины. Синтез и воспроизведение — в своём потоке, чтобы не задерживать уведомления."""

    def __init__(self, synthesizer: Callable[[], Synthesizer], voice: Callable[[], VoiceConfig], targets: Targets,
                 on_result: ResultCallback | None = None) -> None:
        self._synthesizer = synthesizer
        self._voice = voice
        self._targets = targets
        self._on_result = on_result
        self._queue: queue.Queue[_Job | None] = queue.Queue(maxsize=50)
        self._thread: threading.Thread | None = None

    def attach(self, bus: EventBus) -> None:
        bus.subscribe(SeedsLevelChanged, self.handle)

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="announcer", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5) -> None:
        if self._thread is not None:
            self._queue.put(None)
            self._thread.join(timeout)
            self._thread = None

    def handle(self, event: SeedsLevelChanged) -> None:
        targets = self._targets(event.camera_id)
        if targets is None or event.quiet:
            return
        rules, speakers = targets
        text = phrase_for(event, rules)
        if not speakers or not text:
            return
        if not event.test and not in_hours(rules.voice_hours):  # проверку с кнопки — в любое время
            log.info("Голосовое оповещение «%s» пропущено: вне часов %s", text, rules.voice_hours)
            return
        try:
            self._queue.put_nowait(_Job(text, tuple(speakers), rules.say_times, event.id))
        except queue.Full:
            log.error("Очередь голосовых оповещений переполнена — «%s» пропущено", text)

    def say(self, text: str, speakers: Sequence[Speaker], times: int = 1) -> dict[str, str | None]:
        """Сказать сразу (кнопка «Проверить»): имя динамика → None при успехе или текст ошибки."""
        return self._play(_Job(text, tuple(speakers), times, None))

    def _run(self) -> None:
        while (job := self._queue.get()) is not None:
            try:
                self._play(job)
            except Exception:
                log.exception("Голосовое оповещение «%s» не удалось", job.text)

    def _play(self, job: _Job) -> dict[str, str | None]:
        results: dict[str, str | None] = {}
        try:
            speech, rate = self._synthesizer().synthesize(job.text)
            audio, audio_rate = compose(speech, rate, self._voice(), job.times)
        except Exception as e:
            log.error("Синтез речи «%s» не удался: %s", job.text, e)
            for speaker in job.speakers:
                self._report(job, speaker, f"синтез речи: {e}", results)
            return results

        def play(speaker: Speaker) -> None:
            try:
                speaker.play(audio, audio_rate)
            except SpeakerError as e:
                self._report(job, speaker, str(e), results)
            except Exception as e:
                log.exception("Динамик %s", speaker.name)
                self._report(job, speaker, f"{type(e).__name__}: {e}", results)
            else:
                self._report(job, speaker, None, results)

        threads = [threading.Thread(target=play, args=(s,), daemon=True) for s in job.speakers]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return results

    def _report(self, job: _Job, speaker: Speaker, error: str | None, results: dict[str, str | None]) -> None:
        results[speaker.name] = error
        if error:
            log.error("Динамик «%s»: %s", speaker.name, error)
        else:
            log.info("Динамик «%s»: сказано «%s»", speaker.name, job.text)
        if self._on_result and job.event_id:
            self._on_result(job.event_id, f"динамик «{speaker.name}»", error is None, error)
