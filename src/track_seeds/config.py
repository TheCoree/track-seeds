"""Настройки.

Каждый модуль имеет свою группу. У группы две формы:

- XConfig — чистая модель: её хранит веб-интерфейс (data/config.json), окружение не читается;
- XSettings — та же модель, но поля читаются из переменных окружения / .env с префиксом
  группы (CAMERA_, DETECTOR_, …). Используется в режиме `track-seeds run` без веба.
  Переменные окружения процесса имеют приоритет над .env (удобно для Docker).

Поля с json_schema_extra=UI/ADVANCED показываются в формах веб-интерфейса (title — подпись,
description — подсказка): UI — основные, ADVANCED — в «тонкой настройке».
"""

import ipaddress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

type Priority = Literal["min", "low", "default", "high", "urgent"]

UI = {"ui": "main"}
ADVANCED = {"ui": "advanced"}


class _Base(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", env_ignore_empty=True, extra="ignore")


def _split_csv(value: object) -> object:
    """«a, B,c» → ["a", "b", "c"] для списков в .env."""
    if isinstance(value, str):
        return [item.strip().lower() for item in value.split(",") if item.strip()]
    return value


class AppConfig(BaseModel):
    log_level: str = "INFO"
    # пресеты анализа через запятую: presence (люди), plates (номера машин), seeds (семена в дробилке)
    analyzers: Annotated[list[str], NoDecode] = ["presence"]
    # журнал событий, снимки, кеш голоса — у каждого компьютера свои
    data_dir: Path = Path("data")
    # что меняли на месте: пароли камер, оповещения, свои эталоны (сами дробилки вшиты в программу)
    config_dir: Path = Path("config")

    _split = field_validator("analyzers", mode="before")(_split_csv)


class CameraConfig(BaseModel):
    # rtsp://user:pass@host:554/path, http://host/video.mjpg, индекс вебкамеры (0) или путь к видеофайлу
    url: str = "0"
    # подставляются в url с экранированием спецсимволов, если в самом url логина нет
    username: str | None = None
    password: SecretStr | None = None
    name: str = "Камера"
    rtsp_transport: Literal["tcp", "udp"] = "tcp"
    open_timeout_s: float = Field(10, gt=0)
    read_timeout_s: float = Field(10, gt=0)
    reconnect_delay_s: float = Field(2, gt=0)
    reconnect_max_delay_s: float = Field(60, gt=0)
    # через сколько секунд без связи слать уведомление; 0 — не слать
    offline_alert_s: float = Field(60, ge=0)


class DetectorConfig(BaseModel):
    backend: Literal["yolox"] = "yolox"
    # имя модели из каталога (yolox_nano, yolox_tiny, yolox_s) или путь к своему .onnx
    model: str = Field("yolox_tiny", title="Модель детектора",
                       description="yolox_nano — быстрее, yolox_tiny — баланс, yolox_s — точнее, но в 4 раза медленнее",
                       json_schema_extra=UI)
    models_dir: Path = Path("models")
    # размер входа сети; 0 — взять из каталога
    input_size: int = Field(0, ge=0)
    confidence: float = Field(0.4, ge=0, le=1, title="Порог уверенности детектора",
                              description="Выше — меньше ложных срабатываний, ниже — меньше пропусков",
                              json_schema_extra=UI)
    nms: float = Field(0.45, ge=0, le=1)
    # рамка, на эту долю лежащая внутри более уверенной рамки того же класса, — дубль; 1 — не подавлять
    containment: float = Field(0.6, ge=0, le=1)
    # дополнительно прогонять детектор по частям кадра: лучше видит мелких/сидящих людей, но в ~3 раза дольше
    tiling: bool = Field(True, title="Нарезка кадра",
                         description="Лучше видит далёких и сидящих людей, но в ~3 раза дольше",
                         json_schema_extra=UI)


class PresenceConfig(BaseModel):
    label: str = "person"
    interval_s: float = Field(0.5, gt=0, title="Проверять раз в, с", json_schema_extra=ADVANCED)
    enter_hits: int = Field(2, ge=1, title="«Человек появился» после N детекций подряд", json_schema_extra=ADVANCED)
    leave_after_s: float = Field(10, gt=0, title="«Человек ушёл», если никого нет, с", json_schema_extra=UI)
    min_box_area: float = Field(0.0, ge=0, le=1, title="Мин. размер человека, доля кадра",
                                description="Например 0.002 — игнорировать совсем мелкие объекты",
                                json_schema_extra=ADVANCED)
    count_changes: bool = Field(True, title="Сообщать, когда меняется число людей",
                                description="«Кто-то пришёл / ушёл», когда в кадре ещё есть люди",
                                json_schema_extra=UI)
    count_window_s: float = Field(5, gt=0, title="Окно сглаживания числа людей, с",
                                  description="Детектор то теряет, то находит частично закрытого человека",
                                  json_schema_extra=ADVANCED)
    count_change_after_s: float = Field(10, ge=0, title="Новое число людей должно держаться, с",
                                        json_schema_extra=ADVANCED)


class PlatesConfig(BaseModel):
    interval_s: float = Field(0.5, gt=0, title="Проверять раз в, с", json_schema_extra=ADVANCED)
    vehicle_labels: Annotated[list[str], NoDecode] = ["car", "truck", "bus", "motorcycle"]
    min_vehicle_area: float = Field(0.005, ge=0, le=1, title="Мин. размер машины, доля кадра",
                                    description="Далёкие машины игнорируются — номер всё равно не прочитать",
                                    json_schema_extra=ADVANCED)
    stop_after_s: float = Field(3, gt=0, title="«Остановилась», если стоит, с", json_schema_extra=UI)
    stop_tolerance: float = Field(0.05, gt=0, title="Допуск сдвига стоящей машины, доля диагонали",
                                  json_schema_extra=ADVANCED)
    leave_after_s: float = Field(30, gt=0, title="«Уехала», если не видно, с", json_schema_extra=UI)
    read_timeout_s: float = Field(15, gt=0, title="Сколько пытаться прочитать номер, с", json_schema_extra=ADVANCED)
    confirm_reads: int = Field(3, ge=1, title="Одинаковых прочтений для уверенного номера", json_schema_extra=ADVANCED)
    format: Literal["ru", "any"] = Field("ru", title="Формат номеров",
                                         description="ru — проверять и исправлять по формату РФ; any — как прочитано",
                                         json_schema_extra=UI)
    detector_model: str = "yolo-v9-t-640-license-plate-end2end"
    ocr_model: str = "cct-xs-v2-global-model"
    plate_confidence: float = Field(0.4, ge=0, le=1)
    min_plate_width: int = Field(80, ge=1, title="Мин. ширина номера, px",
                                 description="Номер уже этого OCR читает как правдоподобный мусор",
                                 json_schema_extra=ADVANCED)
    min_char_confidence: float = Field(0.7, ge=0, le=1, title="Мин. уверенность в каждом символе",
                                       json_schema_extra=ADVANCED)
    # зона кадра «x,y,ширина,высота» в долях (0.4,0,0.6,1 — правые 60%); пусто — весь кадр
    roi: tuple[float, float, float, float] | None = None
    notify_unreadable: bool = Field(False, title="Сообщать о машинах с нечитаемым номером",
                                    description="Обычно это дальние стоящие машины — шум",
                                    json_schema_extra=UI)
    journal: bool = Field(True, title="Вести журнал заездов", json_schema_extra=UI)

    _split = field_validator("vehicle_labels", mode="before")(_split_csv)

    @field_validator("roi", mode="before")
    @classmethod
    def _parse_roi(cls, value: object) -> object:
        if isinstance(value, str):
            parts = [float(x) for x in value.split(",")]
            if len(parts) != 4 or not all(0 <= x <= 1 for x in parts):
                raise ValueError("PLATES_ROI: четыре числа от 0 до 1 через запятую: x,y,ширина,высота")
            return tuple(parts)
        return value


class SeedZone(BaseModel):
    """Участок кадра, где видно семена (когда он открывается — семена кончаются), или привод дробилки."""

    id: str
    name: str = "Зона"
    # empty — у дна: открылась → «закончились»; low — выше: открылась → «заканчиваются»;
    # motion — привод (цепь, звёздочка): неподвижен → дробилка стоит, о семенах не сообщать. Эталоны не нужны.
    kind: Literal["empty", "low", "motion"] = "empty"
    # четыре угла в долях кадра (x, y)
    points: list[tuple[float, float]] = Field(min_length=4, max_length=4)

    @field_validator("points")
    @classmethod
    def _in_frame(cls, points: list[tuple[float, float]]) -> list[tuple[float, float]]:
        if not all(0 <= x <= 1 and 0 <= y <= 1 for x, y in points):
            raise ValueError("углы зоны должны быть в пределах кадра (0..1)")
        return points


class SeedSample(BaseModel):
    """Эталон: кадр, на котором человек отметил, где есть семена, а где пусто."""

    id: str
    image: str  # путь к кадру относительно папки дробилки (вшитые — относительно builtin/<id>/)
    ts: float
    labels: dict[str, Literal["full", "empty"]]  # id зоны → есть семена / пусто
    builtin: bool = False  # вшит в программу: не удаляется и не переотмечается из интерфейса


class SeedsConfig(BaseModel):
    interval_s: float = Field(0.5, gt=0, title="Проверять раз в, с", json_schema_extra=ADVANCED)
    empty_after_s: float = Field(5, gt=0, title="«Закончились», если пусто дольше, с",
                                 description="Защита от ложных тревог: зона должна быть пустой всё это время",
                                 json_schema_extra=UI)
    refill_after_s: float = Field(5, gt=0, title="«Досыпали», если семена видны дольше, с", json_schema_extra=UI)
    agreement: float = Field(0.7, ge=0.5, le=1, title="Доля согласных кадров",
                             description="Сколько кадров за это время должны показывать одно и то же",
                             json_schema_extra=ADVANCED)
    threshold: float = Field(0.5, gt=0, lt=1, title="Граница оценки",
                             description="Оценка зоны 0..1: ниже границы — пусто, выше — семена есть",
                             json_schema_extra=ADVANCED)
    hysteresis: float = Field(0.1, ge=0, lt=0.5, title="Гистерезис оценки",
                              description="Чтобы оценка у самой границы не прыгала туда-сюда",
                              json_schema_extra=ADVANCED)
    novelty: float = Field(1.0, gt=0, title="Порог «непохоже на эталоны»",
                           description="Если кадр дальше от всех эталонов, чем «есть» от «пусто» в N раз — "
                                       "считать неопределённым (крышка, сдвиг камеры)",
                           json_schema_extra=ADVANCED)
    min_brightness: float = Field(15, ge=0, le=255, title="Мин. яркость зоны",
                                  description="Темнее — «не видно» (выключен свет)", json_schema_extra=ADVANCED)
    # Выключено: камеры смотрят сверху в желоб — руку или лопату в нём детектор людей всё равно не узнаёт,
    # а человек у желоба обычно обслуживает остановленную дробилку. От «загородили» защищает эталон «пусто»:
    # кадр, непохожий ни на семена, ни на пустой желоб, не считается.
    ignore_people: bool = Field(False, title="Не считать пустой зону, которую загородил человек",
                                description="Детектор людей (+~110 МБ памяти). Обычно не нужен: от «загородили» "
                                            "защищает эталон «пусто»",
                                json_schema_extra=ADVANCED)
    unknown_alert_s: float = Field(300, ge=0, title="Сообщить «не видно желоб» через, с",
                                   description="0 — не сообщать", json_schema_extra=UI)
    remind_every_s: float = Field(600, ge=0, title="Напоминать «всё ещё пусто» каждые, с",
                                  description="0 — не напоминать", json_schema_extra=UI)
    notify_refill: bool = Field(True, title="Сообщать, когда досыпали", json_schema_extra=UI)
    stopped_after_s: float = Field(10, gt=0, title="«Дробилка стоит», если привод неподвижен дольше, с",
                                   description="Нужна зона «привод». Пока дробилка стоит, о пустом желобе не "
                                               "сообщается", json_schema_extra=UI)
    running_after_s: float = Field(5, gt=0, title="«Работает», если привод движется дольше, с",
                                   description="После запуска наличие семян подтверждается заново — по кадрам "
                                               "в работе", json_schema_extra=ADVANCED)
    motion_threshold: float = Field(0.05, gt=0, lt=1, title="Порог движения привода",
                                    description="Доля зоны «привод», которая должна измениться за проверку, чтобы "
                                                "считать, что он крутится (0.05 = 5%)", json_schema_extra=ADVANCED)

    one_class_tolerance: float = Field(
        1.0, ge=0.3, le=4, title="Допуск, пока нет эталона «пусто»",
        description="Насколько картинка может отличаться от эталона «есть», прежде чем считать зону пустой. "
                    "Больше — меньше ложных тревог, но позже заметит пустоту", json_schema_extra=ADVANCED)

    # голосовые фразы (динамики — в настройках дробилки)
    say_empty: str = "Внимание! В дробилке нет зерна."
    say_low: str = ""
    say_refill: str = ""
    say_unknown: str = ""
    say_reminders: bool = True
    say_times: int = Field(2, ge=1, le=5)
    voice_hours: str = ""  # «08:00-20:00»; пусто — всегда

    zones: list[SeedZone] = []
    samples: list[SeedSample] = []

    @field_validator("voice_hours")
    @classmethod
    def _hours(cls, value: str) -> str:
        value = value.strip()
        if value:
            parse_hours(value)
        return value


def parse_hours(value: str) -> tuple[int, int]:
    """«08:00-20:00» → (480, 1200) в минутах от полуночи; интервал может переходить через полночь."""
    try:
        start, end = (part.strip() for part in value.split("-"))
        minutes = []
        for part in (start, end):
            h, m = part.split(":")
            if not (0 <= int(h) <= 24 and 0 <= int(m) < 60):
                raise ValueError
            minutes.append(int(h) * 60 + int(m))
    except ValueError:
        raise ValueError("часы в формате 08:00-20:00") from None
    return minutes[0], minutes[1]


class NotifyConfig(BaseModel):
    channels: Annotated[list[str], NoDecode] = ["log"]
    retries: int = Field(3, ge=1, title="Попыток отправки", json_schema_extra=ADVANCED)
    retry_delay_s: float = Field(3, ge=0, title="Пауза между попытками, с", json_schema_extra=ADVANCED)
    attach_snapshot: bool = Field(True, title="Прикладывать снимок", json_schema_extra=UI)

    _split = field_validator("channels", mode="before")(_split_csv)


class NtfyConfig(BaseModel):
    url: str = "https://ntfy.sh"
    topic: str = ""
    token: SecretStr | None = None
    username: str | None = None
    password: SecretStr | None = None
    timeout_s: float = Field(15, gt=0)


class VoiceConfig(BaseModel):
    engine: Literal["auto", "piper", "espeak"] = Field(
        "auto", title="Синтез речи",
        description="piper — естественный голос (нейросеть, без интернета); espeak — роботизированный, "
                    "но всегда есть; auto — piper, если установлен", json_schema_extra=UI)
    voice: str = Field("ru_RU-irina-medium", title="Голос", json_schema_extra=UI)
    speed: float = Field(0.9, ge=0.5, le=2, title="Скорость речи", description="1 — обычная, меньше — медленнее",
                         json_schema_extra=UI)
    volume: float = Field(1.0, gt=0, le=1, title="Громкость (доля от максимума)", json_schema_extra=UI)
    chime: bool = Field(True, title="Сигнал перед фразой", description="Короткий «динь-дон», чтобы обратили внимание",
                        json_schema_extra=UI)
    pause_s: float = Field(1.0, ge=0, le=5, title="Пауза между повторами фразы, с", json_schema_extra=ADVANCED)


class SpeakerConfig(BaseModel):
    """Динамик для голосовых оповещений: IP-камера Hikvision с выходом на динамик или
    физический динамик, подключённый к компьютеру с сервером (local)."""

    id: str
    name: str = "Динамик"
    kind: Literal["hikvision", "local"] = "hikvision"
    # local: устройство ALSA (aplay -D), например plughw:1,0; пусто — по умолчанию
    device: str = ""
    host: str = ""
    port: int = Field(80, ge=1, le=65535)
    https: bool = False
    username: str = "admin"
    password: SecretStr | None = None
    channel: int = Field(1, ge=1)  # канал двустороннего звука (у камеры — 1)
    timeout_s: float = Field(10, gt=0)


class PreviewConfig(BaseModel):
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8090
    fps: float = Field(10, gt=0)
    jpeg_quality: int = Field(80, ge=10, le=100)


# --- те же группы, читаемые из окружения / .env (режим `track-seeds run`) ---

class AppSettings(_Base, AppConfig):
    pass


class CameraSettings(_Base, CameraConfig):
    model_config = SettingsConfigDict(env_prefix="CAMERA_")


class DetectorSettings(_Base, DetectorConfig):
    model_config = SettingsConfigDict(env_prefix="DETECTOR_")


class PresenceSettings(_Base, PresenceConfig):
    model_config = SettingsConfigDict(env_prefix="PRESENCE_")


class PlatesSettings(_Base, PlatesConfig):
    model_config = SettingsConfigDict(env_prefix="PLATES_")


class SeedsSettings(_Base, SeedsConfig):
    model_config = SettingsConfigDict(env_prefix="SEEDS_")


class NotifySettings(_Base, NotifyConfig):
    model_config = SettingsConfigDict(env_prefix="NOTIFY_")


class NtfySettings(_Base, NtfyConfig):
    model_config = SettingsConfigDict(env_prefix="NTFY_")


class PreviewSettings(_Base, PreviewConfig):
    model_config = SettingsConfigDict(env_prefix="PREVIEW_")


class ServerSettings(_Base):
    """Веб-интерфейс (`track-seeds serve`)."""

    model_config = SettingsConfigDict(env_prefix="WEB_")

    host: str = "127.0.0.1"
    port: int = 8080
    # пароль на вход в веб-интерфейс (имя пользователя любое); обязателен, если host — не 127.0.0.1
    password: SecretStr | None = None
    # разрешить доступ из сети без пароля (не рекомендуется)
    allow_no_password: bool = False
    # откуда пускать — через запятую; по умолчанию только локальная сеть и сам сервер:
    # даже если порт случайно пробросят в интернет, чужие адреса получат отказ
    allowed_networks: Annotated[list[str], NoDecode] = ["127.0.0.0/8", "::1/128", "10.0.0.0/8", "172.16.0.0/12",
                                                         "192.168.0.0/16", "fc00::/7", "fe80::/10"]
    # собранный фронтенд (web/out); пусто — искать рядом с проектом
    static_dir: Path | None = None
    # сколько дней хранить журнал событий со снимками
    events_keep_days: int = Field(30, ge=1)

    @field_validator("allowed_networks", mode="before")
    @classmethod
    def _networks(cls, value: object) -> object:
        networks = _split_csv(value)
        for item in networks if isinstance(networks, list) else []:
            try:
                ipaddress.ip_network(item, strict=False)
            except ValueError:
                raise ValueError(f"WEB_ALLOWED_NETWORKS: «{item}» — не сеть (пример: 192.168.1.0/24)") from None
        return networks


@dataclass(frozen=True, slots=True)
class Settings:
    app: AppConfig
    camera: CameraConfig
    detector: DetectorConfig
    presence: PresenceConfig
    plates: PlatesConfig
    notify: NotifyConfig
    ntfy: NtfyConfig
    preview: PreviewConfig
    seeds: SeedsConfig = field(default_factory=SeedsConfig)


def load_settings(env_file: str | Path | None = ".env") -> Settings:
    kw = {"_env_file": env_file}
    return Settings(
        app=AppSettings(**kw),
        camera=CameraSettings(**kw),
        detector=DetectorSettings(**kw),
        presence=PresenceSettings(**kw),
        plates=PlatesSettings(**kw),
        notify=NotifySettings(**kw),
        ntfy=NtfySettings(**kw),
        preview=PreviewSettings(**kw),
        seeds=SeedsSettings(**kw),
    )
