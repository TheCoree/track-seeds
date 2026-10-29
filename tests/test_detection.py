import numpy as np

from track_seeds.detection import Detection, TiledDetector, merge_detections, tile_grid


def person(x, y, w, h, conf=0.9):
    return Detection("person", conf, (x, y, w, h))


def test_nested_box_is_suppressed_as_duplicate():
    body = person(100, 100, 100, 200, 0.6)
    torso = person(110, 110, 60, 80, 0.5)  # IoU мало, но целиком внутри body
    neighbour = person(250, 100, 100, 200, 0.5)
    assert merge_detections([torso, body, neighbour], nms=0.45, containment=0.6) == [body, neighbour]
    assert len(merge_detections([torso, body], nms=0.45, containment=1.0)) == 2


def test_nested_box_of_other_class_is_kept():
    laptop = Detection("laptop", 0.8, (120, 150, 40, 30))
    assert len(merge_detections([person(100, 100, 100, 200), laptop], nms=0.45, containment=0.6)) == 2


def test_tile_grid():
    assert tile_grid(1280, 720) == [(0, 0, 720), (560, 0, 720)]
    assert tile_grid(720, 1280) == [(0, 0, 720), (0, 560, 720)]
    assert tile_grid(1440, 720) == [(0, 0, 720), (360, 0, 720), (720, 0, 720)]  # перекрытие при 2:1
    assert tile_grid(800, 720) == []


class FixedDetector:
    """Всегда «видит» человека в одной и той же точке переданного изображения."""

    def __init__(self):
        self.shapes = []

    def detect(self, image):
        self.shapes.append(image.shape[:2])
        return [person(30, 30, 20, 40)]


def test_tiled_detector_shifts_tile_coordinates_and_merges():
    inner = FixedDetector()
    result = TiledDetector(inner, nms=0.45, containment=0.6).detect(np.zeros((720, 1280, 3), np.uint8))

    assert inner.shapes == [(720, 1280), (720, 720), (720, 720)]
    # целый кадр и первая плитка нашли одно и то же — слились в одну рамку
    assert sorted(d.box for d in result) == [(30, 30, 20, 40), (590, 30, 20, 40)]


def test_class_agnostic_merge_for_vehicles():
    truck = Detection("truck", 0.8, (100, 100, 400, 300))
    car_half = Detection("car", 0.7, (300, 110, 190, 280))
    assert len(merge_detections([truck, car_half], nms=0.5, containment=0.8)) == 2
    assert merge_detections([truck, car_half], nms=0.5, containment=0.8, class_agnostic=True) == [truck]


class TileScripted:
    """Отдаёт заданные рамки для целого кадра (ключ "full") и левой плитки ("left")."""

    def __init__(self, full, left):
        self._full, self._left = full, left

    def detect(self, image):
        if image.shape[1] == 1280:
            return self._full
        return self._left if image[0, 0, 0] == 1 else []  # правая плитка начинается с x=560, там 0


def marked_frame() -> np.ndarray:
    image = np.zeros((720, 1280, 3), np.uint8)
    image[0, 0, 0] = 1
    return image


def test_tile_partial_of_big_object_does_not_replace_full_box():
    full_car = Detection("car", 0.86, (100, 200, 1000, 400))
    half_car = Detection("car", 0.9, (100, 200, 600, 390))  # левая плитка видит половину машины
    inner = TileScripted(full=[full_car], left=[half_car])
    assert TiledDetector(inner, nms=0.45, containment=0.6).detect(marked_frame()) == [full_car]


def test_tile_box_cut_by_inner_edge_is_dropped_but_bigger_better_box_kept():
    torso = Detection("person", 0.45, (500, 120, 60, 80))
    whole = Detection("person", 0.7, (490, 100, 100, 150))  # в плитке человек крупнее, рамка лучше
    cut = Detection("person", 0.9, (680, 300, 40, 100))  # упирается в правый край левой плитки (720)
    inner = TileScripted(full=[torso], left=[whole, cut])
    assert TiledDetector(inner, nms=0.45, containment=0.6).detect(marked_frame()) == [whole]
