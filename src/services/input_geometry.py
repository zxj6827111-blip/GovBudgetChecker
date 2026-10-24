"""统一可逆仿射坐标；框通过四角变换，适用于旋转及线性倾斜。"""

from __future__ import annotations

import math
from dataclasses import dataclass

GEOMETRY_VERSION = "input-affine-v2-rotated-media-crop"


@dataclass(frozen=True)
class Affine:
    a: float = 1
    b: float = 0
    c: float = 0
    d: float = 1
    e: float = 0
    f: float = 0

    def __post_init__(self):
        if (
            not all(math.isfinite(v) for v in self.values())
            or abs(self.a * self.d - self.b * self.c) < 1e-12
        ):
            raise ValueError("non-invertible affine transform")

    def values(self):
        return [self.a, self.b, self.c, self.d, self.e, self.f]

    def point(self, x, y):
        return self.a * x + self.c * y + self.e, self.b * x + self.d * y + self.f

    def box(self, box):
        x0, y0, x1, y1 = box
        if not all(math.isfinite(v) for v in box) or x0 >= x1 or y0 >= y1:
            raise ValueError("invalid source box")
        points = [self.point(x, y) for x in (x0, x1) for y in (y0, y1)]
        return [
            min(p[0] for p in points),
            min(p[1] for p in points),
            max(p[0] for p in points),
            max(p[1] for p in points),
        ]

    def inverse(self):
        det = self.a * self.d - self.b * self.c
        return Affine(
            self.d / det,
            -self.b / det,
            -self.c / det,
            self.a / det,
            (self.c * self.f - self.d * self.e) / det,
            (self.b * self.e - self.a * self.f) / det,
        )

    def then(self, other):
        return Affine(
            other.a * self.a + other.c * self.b,
            other.b * self.a + other.d * self.b,
            other.a * self.c + other.c * self.d,
            other.b * self.c + other.d * self.d,
            other.a * self.e + other.c * self.f + other.e,
            other.b * self.e + other.d * self.f + other.f,
        )


def page_viewport_to_native(page):
    """PyMuPDF 可见裁剪页 → pdfplumber 旋转后的 MediaBox 顶部坐标。

    渲染已应用 /Rotate，不能再旋转 OCR 框。两种解析器的原点不同；
    非零 MediaBox、CropBox 和 /UserUnit 都必须进入变换，不能只除以 DPI。
    """
    media, crop, rect = page.mediabox, page.cropbox, page.rect
    rotation = int(page.rotation) % 360
    if rotation not in (0, 90, 180, 270):
        raise ValueError('unsupported physical page rotation')
    x, y = crop.x0 - media.x0, crop.y0
    width, height = media.width, media.height
    if rotation == 0:
        offset = (x, y)
    elif rotation == 90:
        offset = (height - y - crop.height, x)
    elif rotation == 180:
        offset = (width - x - crop.width, height - y - crop.height)
    else:
        offset = (y, width - x - crop.width)
    if rotation in (90, 270):
        origin = (media.y0, width - media.x1)
        expected_width, expected_height = crop.height, crop.width
    else:
        origin = (media.x0, height - media.y1)
        expected_width, expected_height = crop.width, crop.height
    if min(width, height, expected_width, expected_height) <= 0:
        raise ValueError('invalid physical page geometry')
    sx, sy = rect.width / expected_width, rect.height / expected_height
    if min(sx, sy) <= 0 or not math.isclose(sx, sy, rel_tol=1e-7):
        raise ValueError('nonuniform physical page scale')
    return Affine(a=1 / sx, d=1 / sy, e=origin[0] + offset[0], f=origin[1] + offset[1])


def rendered_pixels_to_native(page, pixmap, dpi):
    if dpi <= 0 or pixmap.width <= 0 or pixmap.height <= 0:
        raise ValueError('invalid page rendering dimensions')
    scale = 72 / dpi
    pixels_to_viewport = Affine(a=scale, d=scale, e=pixmap.x * scale, f=pixmap.y * scale)
    return pixels_to_viewport.then(page_viewport_to_native(page))
