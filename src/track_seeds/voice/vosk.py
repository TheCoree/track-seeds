"""Vosk TTS (https://github.com/alphacep/vosk-tts, Apache-2.0): русский нейросетевой голос без интернета.

Ударения — из словаря модели (2 млн словоформ: «семена́», «зерна́»), интонация — от BERT по смыслу всей
фразы. Неверное ударение можно поправить в тексте фразы: «сем+ена» (плюс перед ударной гласной).

Чтобы уложиться в память сервера (2 ядра, 4 ГБ, контейнер до 1,5 ГБ):
- словарь (100 МБ текста) не загружается: слова фразы ищутся в файле при синтезе (~0,5 с);
- BERT при скачивании сжимается в int8: 650 → 160 МБ, выход почти тот же;
- модель загружается на время синтеза (~650 МБ) и выгружается через минуту простоя: готовые фразы
  лежат в кеше на диске, тревога модель не ждёт.
"""

import hashlib
import logging
import mmap
import re
import threading
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import numpy as np

log = logging.getLogger(__name__)

MODEL = "vosk-model-tts-ru-0.9-multi"
# Копия официального alphacephei.com/vosk/models/vosk-model-tts-ru-0.9-multi.zip на HuggingFace: с alphacephei.com
# качается сотни байт в секунду. Файлы сверены с CRC32 из оглавления официального архива; ревизия закреплена.
_URL = "https://huggingface.co/drakulavich/vosk-tts-ru-0.9-multi/resolve/37c7b45a32b3fa62f3a2bbce89677080dcd2107f/{}"
_FILES = {
    "config.json": "e155fb266a730e1858a2420442b465acf08a3236dffad7d1a507bf155b213d50",
    "bert/vocab.txt": "bbe5063cc3d7a314effd90e9c5099cf493b81f2b9552c155264e16eeab074237",
    "dictionary": "2939e72c170bb41ac8e256828cca1c5fac4db1e36717f9f53fde843b00a220ba",
    "model.onnx": "0fa5a36b22a8bf7fe7179a3882c6371d2c01e5317019e717516f892d329c24b9",
    "bert/model.onnx": "2e2f1740eaae5e29c2b4844625cbb01ff644b2b5fb0560bd34374c35d8a092c1",
}
_BERT = "bert/model.int8.onnx"  # сжатый при скачивании bert/model.onnx (исходный удаляется)
_READY = ("config.json", "bert/vocab.txt", "dictionary", "model.onnx", _BERT)

# голос в настройках → (диктор модели, название)
VOICES = {
    "vosk-male-1": ("male_0", "Мужской 1"),
    "vosk-male-2": ("male_1", "Мужской 2"),
    "vosk-female-1": ("female_0", "Женский 1"),
    "vosk-female-2": ("female_1", "Женский 2"),
    "vosk-female-3": ("female_2", "Женский 3"),
}
IDLE_UNLOAD_S = 60.0


def installed() -> bool:
    try:
        import vosk_tts  # noqa: F401
    except ImportError:
        return False
    return True


def downloaded(folder: Path) -> bool:
    return all((folder / MODEL / name).is_file() for name in _READY)


def download(folder: Path) -> Path:
    """Скачивает модель (≈930 МБ, один раз) в folder/vosk-model-tts-ru-0.9-multi и сжимает BERT."""
    root = folder / MODEL
    if downloaded(folder):
        return root
    for name, sha256 in _FILES.items():
        target = root / name
        if target.is_file() or (name == "bert/model.onnx" and (root / _BERT).is_file()):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".part")
        log.info("Загрузка голоса Vosk: %s", name)
        digest = hashlib.sha256()
        with urllib.request.urlopen(_URL.format(name), timeout=120) as resp, tmp.open("wb") as f:
            while chunk := resp.read(1 << 20):
                digest.update(chunk)
                f.write(chunk)
        if digest.hexdigest() != sha256:
            tmp.unlink()
            raise RuntimeError(f"Голос Vosk: {name} скачался повреждённым (не та контрольная сумма)")
        tmp.rename(target)
    if not (root / _BERT).is_file():
        _quantize(root / "bert/model.onnx", root / _BERT)
    (root / "bert/model.onnx").unlink(missing_ok=True)
    return root


def _quantize(source: Path, target: Path) -> None:
    from onnxruntime.quantization import QuantType, quantize_dynamic

    log.info("Голос Vosk: сжимаю BERT в int8")
    tmp = target.with_suffix(".part.onnx")
    logging.disable(logging.INFO)  # quantize_dynamic пишет в корневой логгер сотни строк INFO
    try:
        quantize_dynamic(source, tmp, weight_type=QuantType.QInt8)
    finally:
        logging.disable(logging.NOTSET)
    tmp.rename(target)


def _release_memory() -> None:
    """Вернуть системе освобождённую память: без этого glibc держит ~400 МБ после выгрузки модели."""
    import ctypes
    import ctypes.util
    import gc

    gc.collect()
    try:
        ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):  # не glibc (macOS, musl) — пусть остаётся как есть
        pass


# --- текст → то, что модель умеет прочитать ---

_ONES = ["ноль", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять", "десять",
         "одиннадцать", "двенадцать", "тринадцать", "четырнадцать", "пятнадцать", "шестнадцать", "семнадцать",
         "восемнадцать", "девятнадцать"]
_TENS = ["", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят", "семьдесят", "восемьдесят", "девяносто"]
_HUNDREDS = ["", "сто", "двести", "триста", "четыреста", "пятьсот", "шестьсот", "семьсот", "восемьсот", "девятьсот"]


def _below_thousand(n: int, feminine: bool = False) -> list[str]:
    words = [_HUNDREDS[n // 100]] if n >= 100 else []
    n %= 100
    if n >= 20:
        words.append(_TENS[n // 10])
        n %= 10
        if n == 0:
            return words
    if n or not words:
        words.append({1: "одна", 2: "две"}.get(n, _ONES[n]) if feminine else _ONES[n])
    return words


def number_words(n: int) -> str:
    """Число словами (как читают вслух: «двадцать две тысячи сто пять»), до 999 999."""
    if n >= 1_000_000:
        return " ".join(_ONES[int(d)] for d in str(n))
    thousands, rest = divmod(n, 1000)
    words: list[str] = []
    if thousands:
        last2, last = thousands % 100, thousands % 10
        form = "тысяч" if 11 <= last2 <= 14 or last not in (1, 2, 3, 4) else "тысяча" if last == 1 else "тысячи"
        words += _below_thousand(thousands, feminine=True) + [form]
    if rest or not words:
        words += _below_thousand(rest)
    return " ".join(words)


_SPLIT = re.compile(r"(\.\.\.|- |[ ,.?!;:\"()])")  # как режет фразу vosk_tts (Synth.g2p_multistream)
_VOWELS = "аеёиоуыэюя"


def prepare_text(text: str) -> str:
    """Числа — словами; латиница и прочие знаки, которых нет в модели, — убрать."""
    text = re.sub(r"\d+", lambda m: f" {number_words(int(m.group()))} ", text)
    text = re.sub(r"[^А-Яа-яЁё+\s,.?!;:\"()\-—]", " ", text)
    return re.sub(r" ([,.?!;:)])", r"\1", " ".join(text.split()))


def lookup(dictionary: Path, text: str) -> dict[str, str]:
    """Слова фразы → фонемы с ударением (из вариантов — самый частый). Слова нет в словаре: ударение
    на «ё» или на единственную гласную, иначе модель прочитает слово совсем без ударения."""
    words = {w for w in _SPLIT.split(text.lower()) if w and not _SPLIT.fullmatch(w) and "+" not in w}
    found: dict[str, tuple[float, str]] = {}
    if words:
        pattern = re.compile(rb"^(" + b"|".join(re.escape(w.encode()) for w in sorted(words)) + rb") (\S+) (.*?)\r?$",
                             re.MULTILINE)
        with dictionary.open("rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as data:
            for word, prob, phones in pattern.findall(data):
                key, p = word.decode(), float(prob)
                if key not in found or found[key][0] < p:
                    found[key] = (p, phones.decode())
    result = {word: phones for word, (_, phones) in found.items()}
    for word in words - result.keys():
        vowels = [i for i, ch in enumerate(word) if ch in _VOWELS]
        stress = word.find("ё") if "ё" in word else vowels[0] if len(vowels) == 1 else -1
        if stress >= 0:
            from vosk_tts.g2p import convert

            result[word] = convert(word[:stress] + "+" + word[stress:])
    return result


class VoskVoice:
    """Модель в памяти только на время синтеза: выгружается через IDLE_UNLOAD_S простоя."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = threading.Lock()
        self._model: SimpleNamespace | None = None
        self._timer: threading.Timer | None = None

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def synthesize(self, text: str, speaker: str, speed: float) -> tuple[np.ndarray, int]:
        """→ моно float32 в [-1, 1] и частота."""
        from vosk_tts import Synth

        text = prepare_text(text)
        if not re.search(r"[А-Яа-яЁё]", text):
            raise ValueError("Во фразе нет русских слов")
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            model = self._load()
            model.dic = lookup(self.root / "dictionary", text)
            sid = model.config["speaker_id_map"][speaker]
            audio = Synth(model).synth_audio(text, speaker_id=sid, speech_rate=speed)
            self._timer = threading.Timer(IDLE_UNLOAD_S, self.unload)
            self._timer.daemon = True
            self._timer.start()
        return audio.astype(np.float32) / 32768, model.config["audio"]["sample_rate"]

    def unload(self) -> None:
        with self._lock:
            if self._model is None:
                return
            self._model = None
            _release_memory()
            log.info("Голос Vosk выгружен из памяти")

    def _load(self) -> SimpleNamespace:
        if self._model is None:
            import json

            import onnxruntime
            from tokenizers.implementations import BertWordPieceTokenizer

            options = onnxruntime.SessionOptions()
            # с повторным использованием памяти onnxruntime планирует её для этой модели ~50 с, без — 3 с
            options.enable_mem_reuse = False
            options.enable_mem_pattern = False

            def session(path: Path):
                return onnxruntime.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])

            self._model = SimpleNamespace(
                onnx=session(self.root / "model.onnx"), bert_onnx=session(self.root / _BERT),
                tokenizer=BertWordPieceTokenizer(vocab=str(self.root / "bert/vocab.txt"), unk_token="[UNK]",
                                                 lowercase=False),
                config=json.loads((self.root / "config.json").read_text()), dic={})
        return self._model
