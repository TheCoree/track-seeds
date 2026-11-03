"""Синтез речи без интернета: Vosk (естественный голос с верными ударениями), piper или espeak-ng."""

import hashlib
import io
import logging
import os
import shutil
import subprocess
import threading
import urllib.request
import wave
from pathlib import Path

import numpy as np

from ..config import VoiceConfig
from . import vosk
from .audio import read_wav, wav_bytes

log = logging.getLogger(__name__)

# Голос → название. Vosk — одна модель на все пять голосов (~450 МБ на диске); piper (MIT,
# https://github.com/rhasspy/piper) — по ~60 МБ, ударения от espeak-ng, часто неверные («семЕна»).
VOICES = {name: f"{title} (Vosk)" for name, (_, title) in vosk.VOICES.items()} | {
    "ru_RU-irina-medium": "Ирина (piper, женский)",
    "ru_RU-dmitri-medium": "Дмитрий (piper, мужской)",
    "ru_RU-denis-medium": "Денис (piper, мужской)",
    "ru_RU-ruslan-medium": "Руслан (piper, мужской)",
}
_VOICE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/{lang}/{code}/{name}/{quality}/{voice}{ext}"


def vosk_installed() -> bool:
    return vosk.installed()


def piper_installed() -> bool:
    try:
        import piper  # noqa: F401
    except ImportError:
        return False
    return True


def espeak_installed() -> bool:
    return shutil.which("espeak-ng") is not None


def voices_dir(models_dir: Path, voice: str | None = None) -> Path:
    """Папка голоса: VOICES_DIR или models_dir/voices. В Docker голоса лежат вне тома с моделями: в уже
    существующий том Docker не докладывает новые файлы из образа.

    VOICES_DOWNLOAD_DIR — куда докачивать голос, которого там нет: в Docker папка из образа только для
    чтения, а голос, выбранный уже на сервере, скачивается в том data/ и переживает пересборку образа."""
    folder = Path(os.environ["VOICES_DIR"]) if os.environ.get("VOICES_DIR") else models_dir / "voices"
    extra = os.environ.get("VOICES_DOWNLOAD_DIR")
    if extra and voice is not None and not _has_voice(folder, voice):
        return Path(extra)
    return folder


def _has_voice(folder: Path, voice: str) -> bool:
    if voice in vosk.VOICES:
        return vosk.downloaded(folder)
    return (folder / f"{voice}.onnx").is_file() and (folder / f"{voice}.onnx.json").is_file()


def download_voice(voice: str, models_dir: Path) -> Path:
    """Скачивает голос (если его ещё нет) в voices_dir()."""
    folder = voices_dir(models_dir, voice)
    if voice in vosk.VOICES:
        return vosk.download(folder)
    model = folder / f"{voice}.onnx"
    if _has_voice(folder, voice):
        return model
    code, name, quality = voice.split("-")
    folder.mkdir(parents=True, exist_ok=True)
    for ext in (".onnx.json", ".onnx"):
        url = _VOICE_URL.format(lang=code.split("_")[0], code=code, name=name, quality=quality, voice=voice, ext=ext)
        target = folder / f"{voice}{ext}"
        tmp = target.with_suffix(target.suffix + ".part")
        log.info("Загрузка голоса %s", url)
        with urllib.request.urlopen(url, timeout=120) as resp, tmp.open("wb") as f:
            while chunk := resp.read(1 << 20):
                f.write(chunk)
        tmp.rename(target)
    return model


# Загруженные голоса — общие для всех экземпляров Synthesizer: смена настроек не грузит голос заново
_PIPER_VOICES: dict[Path, object] = {}
_VOSK_VOICES: dict[Path, vosk.VoskVoice] = {}
_VOSK_LOCK = threading.Lock()  # скачивание модели Vosk
_PIPER_LOCK = threading.Lock()  # и загрузка/скачивание, и синтез (espeak внутри piper не потокобезопасен)


class Synthesizer:
    """Текст → звук. Готовые фразы кешируются на диске: тревога звучит без задержки на синтез.

    state — для веб-интерфейса: idle (ещё не загружался), loading (скачивается/загружается
    голос), ready, error (голос не загрузился — error; при engine=auto говорит espeak-ng).
    """

    def __init__(self, config: VoiceConfig, models_dir: Path, cache_dir: Path) -> None:
        self._config = config
        self._models_dir = models_dir
        self._cache_dir = cache_dir
        self.engine = self._pick_engine()
        self.state = "ready" if self.engine == "espeak" else "idle"
        self.error: str | None = None

    @property
    def voice(self) -> str:
        return "espeak-ng" if self.engine == "espeak" else self._config.voice

    def _pick_engine(self) -> str:
        """Движок — по выбранному голосу; нет нужного пакета — espeak-ng."""
        if self._config.engine != "espeak":
            if self._config.voice in vosk.VOICES and vosk_installed():
                return "vosk"
            if self._config.voice not in vosk.VOICES and piper_installed():
                return "piper"
        if espeak_installed():
            return "espeak"
        raise RuntimeError("Нет синтеза речи: установите vosk-tts или piper-tts (pip) либо espeak-ng")

    def prepare(self) -> None:
        """Скачать голос заранее (первый раз — до ~930 МБ), чтобы тревога не ждала."""
        if self.engine == "piper":
            self._run_piper(lambda voice: None)
        elif self.engine == "vosk":
            self._run_vosk(lambda voice: None)

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        text = " ".join(text.split())
        if not text:
            raise ValueError("Пустая фраза")
        path = self._cache_path(text)
        if path.is_file():
            return read_wav(path.read_bytes())
        if self.engine == "espeak":
            data = self._espeak_wav(text)
        elif self.engine == "vosk":
            data = self._run_vosk(lambda voice: self._vosk_wav(voice, text))
        else:
            data = self._run_piper(lambda voice: self._piper_wav(voice, text))
        path = self._cache_path(text)  # движок мог смениться на запасной
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{threading.get_ident()}.part")
        tmp.write_bytes(data)
        tmp.replace(path)
        return read_wav(data)

    def _cache_path(self, text: str) -> Path:
        key = hashlib.sha1(f"{self.engine}|{self.voice}|{self._config.speed}|{text}".encode()).hexdigest()
        return self._cache_dir / f"{key}.wav"

    def _run_vosk(self, action):
        """Скачать модель Vosk (если ещё нет) и выполнить action(voice); при ошибке — espeak-ng."""
        try:
            with _VOSK_LOCK:
                folder = voices_dir(self._models_dir, self._config.voice)
                if not vosk.downloaded(folder):
                    self.state = "loading"
                root = vosk.download(folder)
                voice = _VOSK_VOICES.setdefault(root, vosk.VoskVoice(root))
            result = action(voice)
            self.state, self.error = "ready", None
            return result
        except ValueError:
            raise
        except Exception as e:
            return self._fallback(e, action)

    def _vosk_wav(self, voice: vosk.VoskVoice | None, text: str) -> bytes:
        if voice is None:  # запасной путь после ошибки
            return self._espeak_wav(text)
        samples, rate = voice.synthesize(text, vosk.VOICES[self._config.voice][0], self._config.speed)
        return wav_bytes(samples, rate)

    def _fallback(self, error: Exception, action):
        self.state, self.error = "error", f"{type(error).__name__}: {error}"
        # голос не скачался / не загрузился: лучше роботизированный голос, чем молчание
        if self._config.engine != "auto" or not espeak_installed():
            raise error
        log.warning("Голос %s недоступен (%s) — говорю через espeak-ng", self._config.voice, error)
        self.engine = "espeak"
        return action(None)

    def _run_piper(self, action):
        """Загрузить голос piper (если ещё нет) и выполнить action(voice); при ошибке в режиме auto — espeak-ng."""
        try:
            from piper import PiperVoice

            with _PIPER_LOCK:
                path = voices_dir(self._models_dir, self._config.voice) / f"{self._config.voice}.onnx"
                voice = _PIPER_VOICES.get(path)
                if voice is None:
                    self.state = "loading"
                    voice = _PIPER_VOICES[path] = PiperVoice.load(download_voice(self._config.voice,
                                                                                 self._models_dir))
                self.state, self.error = "ready", None
                return action(voice)
        except Exception as e:
            return self._fallback(e, action)

    def _piper_wav(self, voice, text: str) -> bytes:
        from piper import SynthesisConfig

        if voice is None:  # запасной путь после ошибки piper
            return self._espeak_wav(text)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            voice.synthesize_wav(text, wav, syn_config=SynthesisConfig(length_scale=1 / self._config.speed))
        return buf.getvalue()

    def _espeak_wav(self, text: str) -> bytes:
        if not espeak_installed():
            raise RuntimeError("espeak-ng не установлен")
        # текст через stdin: фраза, начинающаяся с «-», не должна читаться как ключ
        result = subprocess.run(["espeak-ng", "-v", "ru", "-s", str(round(160 * self._config.speed)), "--stdout"],
                                input=text.encode(), capture_output=True, timeout=60, check=True)
        return result.stdout
