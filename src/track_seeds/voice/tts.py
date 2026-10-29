"""Синтез речи: piper (нейросетевой голос, работает без интернета) или espeak-ng."""

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
from .audio import read_wav

log = logging.getLogger(__name__)

# Русские голоса piper (MIT, https://github.com/rhasspy/piper); ~60 МБ каждый
VOICES = {
    "ru_RU-irina-medium": "Ирина (женский)",
    "ru_RU-dmitri-medium": "Дмитрий (мужской)",
    "ru_RU-denis-medium": "Денис (мужской)",
    "ru_RU-ruslan-medium": "Руслан (мужской)",
}
_VOICE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/{lang}/{code}/{name}/{quality}/{voice}{ext}"


def piper_installed() -> bool:
    try:
        import piper  # noqa: F401
    except ImportError:
        return False
    return True


def espeak_installed() -> bool:
    return shutil.which("espeak-ng") is not None


def voices_dir(models_dir: Path) -> Path:
    """VOICES_DIR или models_dir/voices. В Docker голоса лежат вне тома с моделями: в уже
    существующий том Docker не докладывает новые файлы из образа."""
    return Path(os.environ["VOICES_DIR"]) if os.environ.get("VOICES_DIR") else models_dir / "voices"


def download_voice(voice: str, models_dir: Path) -> Path:
    """Скачивает голос piper (если его ещё нет) в voices_dir()."""
    folder = voices_dir(models_dir)
    model = folder / f"{voice}.onnx"
    if model.is_file() and model.with_suffix(".onnx.json").is_file():
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


# Загруженные голоса piper — общие для всех экземпляров Synthesizer: смена настроек не грузит голос заново
_PIPER_VOICES: dict[Path, object] = {}
_PIPER_LOCK = threading.Lock()  # и загрузка/скачивание, и синтез (espeak внутри piper не потокобезопасен)


class Synthesizer:
    """Текст → звук. Готовые фразы кешируются на диске: тревога звучит без задержки на синтез.

    state — для веб-интерфейса: idle (ещё не загружался), loading (скачивается/загружается
    голос), ready, error (piper не загрузился — error; при engine=auto говорит espeak-ng).
    """

    def __init__(self, config: VoiceConfig, models_dir: Path, cache_dir: Path) -> None:
        self._config = config
        self._models_dir = models_dir
        self._cache_dir = cache_dir
        self.engine = self._pick_engine()
        self.state = "idle" if self.engine == "piper" else "ready"
        self.error: str | None = None

    @property
    def voice(self) -> str:
        return self._config.voice if self.engine == "piper" else "espeak-ng"

    def _pick_engine(self) -> str:
        match self._config.engine:
            case "piper" | "espeak" as engine:
                return engine
        if piper_installed():
            return "piper"
        if espeak_installed():
            return "espeak"
        raise RuntimeError("Нет синтеза речи: установите piper-tts (pip) или espeak-ng")

    def prepare(self) -> None:
        """Скачать и загрузить голос заранее (первый раз — ~60 МБ), чтобы тревога не ждала."""
        if self.engine == "piper":
            self._run_piper(lambda voice: None)

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        text = " ".join(text.split())
        if not text:
            raise ValueError("Пустая фраза")
        path = self._cache_path(text)
        if path.is_file():
            return read_wav(path.read_bytes())
        if self.engine == "espeak":
            data = self._espeak_wav(text)
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

    def _run_piper(self, action):
        """Загрузить голос piper (если ещё нет) и выполнить action(voice); при ошибке в режиме auto — espeak-ng."""
        try:
            from piper import PiperVoice

            with _PIPER_LOCK:
                path = voices_dir(self._models_dir) / f"{self._config.voice}.onnx"
                voice = _PIPER_VOICES.get(path)
                if voice is None:
                    self.state = "loading"
                    voice = _PIPER_VOICES[path] = PiperVoice.load(download_voice(self._config.voice,
                                                                                 self._models_dir))
                self.state, self.error = "ready", None
                return action(voice)
        except Exception as e:
            self.state, self.error = "error", f"{type(e).__name__}: {e}"
            # голос не скачался / не загрузился: лучше роботизированный голос, чем молчание
            if self._config.engine != "auto" or not espeak_installed():
                raise
            log.warning("Голос piper %s недоступен (%s) — говорю через espeak-ng", self._config.voice, e)
            self.engine = "espeak"
            return action(None)

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
