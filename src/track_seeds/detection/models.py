"""Каталог готовых моделей и их загрузка."""

import hashlib
import logging
import urllib.request
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_YOLOX_RELEASE = "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0"


@dataclass(frozen=True, slots=True)
class ModelInfo:
    url: str
    sha256: str
    input_size: int


CATALOG: dict[str, ModelInfo] = {
    # ~3.6 МБ, самая быстрая, хуже видит мелких/далёких людей
    "yolox_nano": ModelInfo(f"{_YOLOX_RELEASE}/yolox_nano.onnx",
                            "c789161ed43c8269fcd4e67c67eeeb4e80c622da2eb296a20bc6007bd18a0b7d", 416),
    # ~20 МБ, хороший баланс для CPU
    "yolox_tiny": ModelInfo(f"{_YOLOX_RELEASE}/yolox_tiny.onnx",
                            "427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7", 416),
    # ~36 МБ, точнее, но в ~4 раза медленнее tiny
    "yolox_s": ModelInfo(f"{_YOLOX_RELEASE}/yolox_s.onnx",
                         "c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063", 640),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def download_model(name: str, models_dir: Path) -> Path:
    """Скачивает модель из каталога (если её ещё нет) и проверяет контрольную сумму."""
    info = CATALOG[name]
    path = models_dir / f"{name}.onnx"
    if path.is_file():
        return path

    models_dir.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    log.info("Загрузка модели %s из %s", name, info.url)
    with urllib.request.urlopen(info.url, timeout=60) as resp, tmp.open("wb") as f:
        while chunk := resp.read(1 << 20):
            f.write(chunk)
    if (actual := _sha256(tmp)) != info.sha256:
        tmp.unlink()
        raise RuntimeError(f"Контрольная сумма {name} не совпала: {actual}")
    tmp.rename(path)
    log.info("Модель сохранена: %s", path)
    return path
