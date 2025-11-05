"""视觉与几何工具（占位）。"""

from typing import Tuple


def is_visible(bbox, viewport=None) -> bool:
    # 简单判断：有面积即可，必要时结合 viewport
    if not bbox:
        return False
    return bbox.w > 0 and bbox.h > 0


def center(bbox) -> Tuple[float, float]:
    return bbox.x + bbox.w / 2.0, bbox.y + bbox.h / 2.0

