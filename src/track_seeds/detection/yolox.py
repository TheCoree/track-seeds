"""YOLOX (Apache-2.0, https://github.com/Megvii-BaseDetection/YOLOX) через OpenCV DNN."""

from pathlib import Path

import cv2
import numpy as np

from .base import Detection, merge_detections

COCO_CLASSES = (
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
    "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat", "dog",
    "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella",
    "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite",
    "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket", "bottle",
    "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch", "potted plant",
    "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote", "keyboard", "cell phone",
    "microwave", "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase", "scissors",
    "teddy bear", "hair drier", "toothbrush",
)

_STRIDES = (8, 16, 32)
_PAD_VALUE = 114


def _make_grid(size: int) -> tuple[np.ndarray, np.ndarray]:
    grids, strides = [], []
    for stride in _STRIDES:
        n = size // stride
        xv, yv = np.meshgrid(np.arange(n), np.arange(n))
        grids.append(np.stack((xv, yv), 2).reshape(-1, 2))
        strides.append(np.full((n * n, 1), stride))
    return np.concatenate(grids).astype(np.float32), np.concatenate(strides).astype(np.float32)


class YoloxDetector:
    def __init__(self, model_path: Path, input_size: int, confidence: float, nms: float,
                 containment: float = 1.0, classes: tuple[str, ...] = COCO_CLASSES) -> None:
        self._net = cv2.dnn.readNet(str(model_path))
        self._size = input_size
        self._confidence = confidence
        self._nms = nms
        self._containment = containment
        self._classes = classes
        self._grid, self._strides = _make_grid(input_size)

    def detect(self, image: np.ndarray) -> list[Detection]:
        h, w = image.shape[:2]
        ratio = min(self._size / h, self._size / w)
        rw, rh = int(w * ratio), int(h * ratio)
        canvas = np.full((self._size, self._size, 3), _PAD_VALUE, np.uint8)
        canvas[:rh, :rw] = cv2.resize(image, (rw, rh), interpolation=cv2.INTER_LINEAR)

        # YOLOX >= 0.1.1rc0 обучен на BGR 0..255 без нормализации
        self._net.setInput(cv2.dnn.blobFromImage(canvas))
        out = self._net.forward().reshape(-1, 5 + len(self._classes))
        if out.shape[0] != self._grid.shape[0]:
            raise ValueError(
                f"Выход модели ({out.shape[0]} якорей) не совпадает с input_size={self._size}; "
                "проверьте DETECTOR_INPUT_SIZE"
            )

        scores = out[:, 4:5] * out[:, 5:]
        class_ids = scores.argmax(1)
        confidences = scores[np.arange(len(scores)), class_ids]
        mask = confidences >= self._confidence
        if not mask.any():
            return []

        out, class_ids, confidences = out[mask], class_ids[mask], confidences[mask]
        centers = (out[:, :2] + self._grid[mask]) * self._strides[mask]
        sizes = np.exp(out[:, 2:4]) * self._strides[mask]
        boxes = np.concatenate([centers - sizes / 2, centers + sizes / 2], 1) / ratio

        candidates = []
        for (x0, y0, x1, y1), class_id, confidence in zip(boxes, class_ids, confidences):
            x0, y0 = max(int(x0), 0), max(int(y0), 0)
            x1, y1 = min(int(x1), w), min(int(y1), h)
            candidates.append(Detection(self._classes[class_id], float(confidence), (x0, y0, x1 - x0, y1 - y0)))
        return merge_detections(candidates, self._nms, self._containment)
