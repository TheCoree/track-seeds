from dataclasses import dataclass
from typing import Protocol

import numpy as np


@dataclass(frozen=True, slots=True)
class Frame:
    image: np.ndarray  # BGR
    index: int  # сквозной номер кадра (растёт и после переподключения)
    ts: float  # time.time() момента получения


class FrameSource(Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...

    def wait_frame(self, after_index: int, timeout: float) -> Frame | None:
        """Ждёт кадр новее after_index; None, если за timeout его не было."""
        ...

    @property
    def connected(self) -> bool: ...

    @property
    def status_since(self) -> float:
        """Момент (time.time()) последней смены статуса connected."""
        ...
