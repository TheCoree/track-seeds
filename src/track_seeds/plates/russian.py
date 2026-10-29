"""Российские госномера (ГОСТ Р 50577): исправление ошибок OCR и проверка формата.

OCR-модель обучена на латинице. Буквы российских номеров выбраны так, что совпадают
по начертанию с латинскими (А, В, Е, К, М, Н, О, Р, С, Т, У, Х), поэтому распознаём
латиницей, а показываем кириллицей.
"""

import re
from dataclasses import dataclass

LETTERS = "ABEKMHOPCTYX"
_TO_CYRILLIC = str.maketrans(LETTERS, "АВЕКМНОРСТУХ")

# Частые путаницы OCR: цифра на месте буквы и наоборот
_AS_LETTER = {"0": "O", "8": "B", "4": "A", "7": "T"}
_AS_DIGIT = {"O": "0", "Q": "0", "D": "0", "U": "0", "B": "8", "I": "1", "L": "1", "Z": "2", "S": "5",
             "G": "6", "T": "7"}

# L — буква, D — цифра; после шаблона идёт код региона из 2–3 цифр.
# Порядок важен: при равном числе исправлений выигрывает более частый формат.
_FORMATS = (
    "LDDDLL",  # легковые, грузовые: А123ВС 77
    "LLDDD",  # общественный транспорт, такси: АВ123 77
    "LDDDD",  # полиция: А1234 77
    "LLDDDD",  # прицепы: АВ1234 77
    "DDDDLL",  # мотоциклы: 1234АВ 77
)
MAX_CORRECTIONS = 2


@dataclass(frozen=True, slots=True)
class RuPlate:
    body: str  # латиницей: C484HX
    region: str  # 70
    corrections: int  # сколько символов исправлено по формату

    @property
    def text(self) -> str:
        """Ключ для сравнения прочтений: C484HX70."""
        return self.body + self.region

    @property
    def display(self) -> str:
        """Для людей: С484НХ 70 (кириллицей)."""
        return f"{self.body.translate(_TO_CYRILLIC)} {self.region}"


def to_cyrillic(text: str) -> str:
    """Латинские двойники букв номера → кириллица (для поиска по журналу)."""
    return text.upper().translate(_TO_CYRILLIC)


def clean(raw: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", raw.upper())


def parse_ru(raw: str) -> RuPlate | None:
    """Приводит прочитанный текст к формату российского номера; None, если не похоже."""
    text = clean(raw)
    best: RuPlate | None = None
    for pattern in _FORMATS:
        for region_len in (2, 3):
            if len(text) != len(pattern) + region_len:
                continue
            coerced = _coerce(text, pattern + "D" * region_len)
            if coerced is None:
                continue
            fixed, corrections = coerced
            body, region = fixed[:len(pattern)], fixed[len(pattern):]
            if corrections > MAX_CORRECTIONS or not _valid_region(region):
                continue
            if best is None or corrections < best.corrections:
                best = RuPlate(body, region, corrections)
    return best


def _coerce(text: str, template: str) -> tuple[str, int] | None:
    out, corrections = [], 0
    for char, kind in zip(text, template, strict=True):
        if kind == "L" and char in LETTERS or kind == "D" and char.isdigit():
            out.append(char)
            continue
        fixed = (_AS_LETTER if kind == "L" else _AS_DIGIT).get(char)
        if fixed is None:
            return None
        out.append(fixed)
        corrections += 1
    return "".join(out), corrections


def _valid_region(region: str) -> bool:
    # трёхзначные коды регионов начинаются с 1, 7, 8 или 9 (102, 777, 977…); это же
    # разводит неоднозначные строки вроде AB123477 (такси АВ123 477 или прицеп АВ1234 77)
    return region != "00" and (len(region) == 2 or region[0] in "1789")
