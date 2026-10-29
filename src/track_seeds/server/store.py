"""Дробилки (вшиты в программу, см. crushers.py) и то, что о них меняют на месте через веб-интерфейс.

    config/
    ├── settings.json          уведомления, голос — общие для всех дробилок (права 600: токен ntfy)
    ├── crushers/
    │   ├── drobilka-1/
    │   │   ├── settings.json  что поменяли в интерфейсе: оповещения, фразы, динамики, вкл/выкл — без паролей
    │   │   ├── secrets.json   пароли камеры и динамиков (права 600)
    │   │   └── samples/       эталоны, снятые на месте (вшитые — в программе)
    │   └── drobilka-2/ …
    └── .trash/                прежние папки (например, пресеты до того, как дробилки вшили в программу)

Чего нет в settings.json, берётся из программы: обновили программу — новые значения по умолчанию
применяются сами. Испорченный settings.json дробилку не останавливает: она работает с настройками
из программы, ошибка видна в вебе и в `track-seeds crushers`.
Запись атомарная (временный файл + rename), прежняя версия остаётся в *.bak.
"""

import json
import logging
import os
import shutil
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, SecretStr, ValidationError

from .. import crushers as builtin
from ..config import CameraConfig, DetectorConfig, NotifyConfig, NtfyConfig, SeedSample, SeedsConfig, SpeakerConfig, \
    VoiceConfig
from ..crushers import CrusherSpec

log = logging.getLogger(__name__)

FORMAT = 1


class Crusher(BaseModel):
    """Одна дробилка в работе: вшитое описание + изменения, сделанные на месте."""

    id: str
    name: str
    enabled: bool = True
    notify: bool = True  # уведомления на телефон (журнал пишется всегда, голос — по динамикам)
    camera: CameraConfig
    seeds: SeedsConfig = SeedsConfig()
    speakers: list[SpeakerConfig] = []


class GlobalSettings(BaseModel):
    notify: NotifyConfig = NotifyConfig()
    ntfy: NtfyConfig = NtfyConfig()
    voice: VoiceConfig = VoiceConfig()
    detector: DetectorConfig = DetectorConfig()


def _plain(value: object) -> object:
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"не сериализуется: {type(value).__name__}")


def _dump(model: BaseModel) -> dict[str, Any]:
    return json.loads(json.dumps(model.model_dump(), default=_plain))


def _changed(value: dict[str, Any], default: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in value.items() if default.get(k) != v}


def defaults(spec: CrusherSpec) -> Crusher:
    """Дробилка такой, как она вшита в программу."""
    return Crusher(id=spec.id, name=spec.name,
                   camera=CameraConfig(url=spec.camera_url, username=spec.username, name=spec.name),
                   seeds=SeedsConfig(zones=list(spec.zones), samples=list(spec.samples)))


def split(crusher: Crusher, spec: CrusherSpec) -> tuple[dict, dict]:
    """settings.json (только отличия от программы, без паролей) и secrets.json."""
    base = defaults(spec)
    camera = _dump(crusher.camera)
    secrets = {"camera_password": camera.pop("password", None), "speakers": {}}
    speakers = []
    for speaker in crusher.speakers:
        data = _dump(speaker)
        secrets["speakers"][speaker.id] = data.pop("password", None)
        speakers.append(data)
    seeds = _dump(crusher.seeds)
    seeds.pop("zones")
    seeds["samples"] = [s for s in seeds["samples"] if not s["builtin"]]
    public = {"format": FORMAT, "enabled": crusher.enabled, "notify": crusher.notify,
              "camera": _changed(camera, _dump(base.camera)),
              "seeds": _changed(seeds, {**_dump(base.seeds), "samples": []}) | {"samples": seeds["samples"]},
              "speakers": speakers}
    return public, secrets


def join(spec: CrusherSpec, public: dict | None, secrets: dict | None) -> Crusher:
    """Вшитое описание + settings.json + secrets.json. Имя, зоны и вшитые эталоны — всегда из программы."""
    public, secrets = public or {}, secrets or {}
    if public.get("format", FORMAT) > FORMAT:
        raise ValueError("Настройки сохранены более новой версией track-seeds — обновите программу")
    base = _dump(defaults(spec))
    camera = base["camera"] | (public.get("camera") or {})
    camera["password"] = secrets.get("camera_password") or None
    seeds = base["seeds"] | {k: v for k, v in (public.get("seeds") or {}).items() if k not in ("zones", "samples")}
    builtin_ids = {s.id for s in spec.samples}
    seeds["samples"] = base["seeds"]["samples"] + [
        dict(s, builtin=False) for s in (public.get("seeds") or {}).get("samples", []) if s.get("id") not in builtin_ids]
    passwords = secrets.get("speakers") or {}
    speakers = [dict(s, password=passwords.get(s.get("id")) or s.get("password")) for s in public.get("speakers") or []]
    return Crusher.model_validate({"id": spec.id, "name": spec.name, "enabled": public.get("enabled", True),
                                   "notify": public.get("notify", True), "camera": camera, "seeds": seeds,
                                   "speakers": speakers})


def write_atomic(path: Path, text: str, private: bool = False, backup: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 if private else 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    if backup and path.is_file():
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    os.replace(tmp, path)


def _dumps(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=_plain) + "\n"


def _read_json(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


class ConfigStore:
    def __init__(self, root: Path, crushers: Sequence[CrusherSpec] | None = None) -> None:
        """crushers — какие дробилки есть (по умолчанию вшитые в программу; в тестах — свои)."""
        self.root = root
        self.crushers_dir = root / "crushers"
        self.trash_dir = root / ".trash"
        self.settings_path = root / "settings.json"
        self.specs = {spec.id: spec for spec in (builtin.CRUSHERS if crushers is None else crushers)}
        self._lock = threading.RLock()
        self.crushers: dict[str, Crusher] = {}
        self.errors: dict[str, str] = {}  # дробилка → почему не прочитались её настройки
        self.settings = GlobalSettings()

    def exists(self) -> bool:
        return self.settings_path.is_file()

    # --- чтение ---

    def load(self) -> list[str]:
        """Общие настройки обязательны (битый settings.json — ошибка, не тихий сброс); дробилки — по одной.
        Возвращает, что перенесено из прежних пресетов (для лога)."""
        with self._lock:
            self.settings = GlobalSettings.model_validate_json(self.settings_path.read_text(encoding="utf-8"))
            moved = self._migrate_presets()
            self.crushers, self.errors = {}, {}
            for spec in self.specs.values():
                self._load(spec)
            return moved

    def _load(self, spec: CrusherSpec) -> None:
        folder = self.crusher_dir(spec.id)
        secrets: dict | None = None
        try:
            secrets = _read_json(folder / "secrets.json")
            self.crushers[spec.id] = join(spec, _read_json(folder / "settings.json"), secrets)
        except (OSError, ValueError, ValidationError) as e:
            self.errors[spec.id] = f"{type(e).__name__}: {e}"
            log.error("Настройки дробилки %s не прочитались (%s) — работает с настройками из программы", spec.id, e)
            try:
                self.crushers[spec.id] = join(spec, None, secrets)
            except ValidationError:
                self.crushers[spec.id] = defaults(spec)

    def crusher(self, crusher_id: str) -> Crusher | None:
        return self.crushers.get(crusher_id)

    def crusher_dir(self, crusher_id: str) -> Path:
        if crusher_id not in self.specs:
            raise KeyError(crusher_id)
        return self.crushers_dir / crusher_id

    def sample_path(self, crusher_id: str, sample: SeedSample) -> Path | None:
        """Файл эталона; None — путь ведёт за пределы папки эталонов."""
        folder = (self.specs[crusher_id].builtin_dir if sample.builtin else self.crusher_dir(crusher_id)).resolve()
        path = (folder / sample.image).resolve()
        return path if path.is_relative_to(folder / "samples") else None

    def ordered(self) -> list[Crusher]:
        return [self.crushers[i] for i in self.specs if i in self.crushers]

    # --- запись ---

    def save_settings(self, settings: GlobalSettings) -> GlobalSettings:
        with self._lock:
            settings = GlobalSettings.model_validate(settings.model_dump())
            write_atomic(self.settings_path, _dumps(settings.model_dump()), private=True)
            self.settings = settings
            return settings

    def update_settings(self, change: Callable[[GlobalSettings], None]) -> GlobalSettings:
        with self._lock:
            draft = self.settings.model_copy(deep=True)
            change(draft)
            return self.save_settings(draft)

    def update(self, crusher_id: str, change: Callable[[Crusher], None]) -> Crusher:
        """Изменить и сохранить. Имя и зоны не меняются — они вшиты в программу."""
        with self._lock:
            spec = self.specs[crusher_id]
            draft = self.crushers[crusher_id].model_copy(deep=True)
            change(draft)
            draft.id, draft.name, draft.seeds.zones = spec.id, spec.name, list(spec.zones)
            draft = Crusher.model_validate(draft.model_dump())
            public, secrets = split(draft, spec)
            folder = self.crusher_dir(crusher_id)
            (folder / "samples").mkdir(parents=True, exist_ok=True)
            write_atomic(folder / "settings.json", _dumps(public))
            write_atomic(folder / "secrets.json", _dumps(secrets), private=True, backup=False)
            self.crushers[crusher_id] = join(spec, public, secrets)
            self.errors.pop(crusher_id, None)
            return self.crushers[crusher_id]

    # --- переход с пресетов ---

    def _migrate_presets(self) -> list[str]:
        """Прежние пресеты config/presets/<id>/ → crushers/<id>/: пароли, оповещения, динамики, свои эталоны.
        Камера и зоны берутся из программы. Папка presets/ уходит в .trash/."""
        presets = self.root / "presets"
        if not presets.is_dir():
            return []
        moved = []
        for folder in sorted(p for p in presets.iterdir() if p.is_dir() and not p.name.startswith(".")):
            spec = self.specs.get(folder.name)
            if spec is None:
                moved.append(f"пресет {folder.name} не перенесён: такой дробилки в программе нет")
                continue
            if self.crusher_dir(spec.id).exists():
                continue
            try:
                old = _read_json(folder / "preset.json") or {}
                secrets = _read_json(folder / "secrets.json")
                old_camera = old.get("camera") or {}
                camera = {k: v for k, v in old_camera.items() if k not in ("url", "username", "name")}
                public = {"enabled": old.get("enabled", True), "notify": old.get("notify", True), "camera": camera,
                          "seeds": {k: v for k, v in (old.get("seeds") or {}).items() if k != "zones"},
                          "speakers": old.get("speakers") or []}
                crusher = join(spec, public, secrets)
            except (OSError, ValueError, ValidationError) as e:
                moved.append(f"пресет {folder.name} не перенесён: {e}")
                continue
            for sample in crusher.seeds.samples:
                if not sample.builtin and (folder / sample.image).is_file():
                    target = self.crusher_dir(spec.id) / sample.image
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(folder / sample.image, target)
            crusher.seeds.samples = [s for s in crusher.seeds.samples
                                     if s.builtin or (self.crusher_dir(spec.id) / s.image).is_file()]
            public, secrets = split(crusher, spec)
            write_atomic(self.crusher_dir(spec.id) / "settings.json", _dumps(public))
            write_atomic(self.crusher_dir(spec.id) / "secrets.json", _dumps(secrets), private=True, backup=False)
            own = len(public["seeds"]["samples"])
            moved.append(f"пресет {folder.name} → crushers/{spec.id}: пароль, оповещения, динамики"
                         f"{f', своих эталонов {own}' if own else ''}")
        self.trash_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(presets, self.trash_dir / f"presets-{time.strftime('%Y%m%d-%H%M%S')}")
        return moved
