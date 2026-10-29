from typing import Protocol

import numpy as np

from ..camera import Frame
from ..events import Event


class Analyzer(Protocol):
    """Независимый обработчик кадров: присутствие людей, уровень зерна и т.п.

    Конвейер вызывает process() не чаще, чем раз в interval_s секунд,
    и публикует возвращённые события в шину.
    """

    name: str
    interval_s: float

    def process(self, frame: Frame) -> list[Event]: ...

    def annotate(self, image: np.ndarray) -> None:
        """Рисует последний результат на кадре для превью."""
        ...
