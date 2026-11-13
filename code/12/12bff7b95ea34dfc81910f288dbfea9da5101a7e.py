"""统一可逆仿射坐标；框通过四角变换，适用于旋转及线性倾斜。"""

from __future__ import annotations

import math
from dataclasses import dataclass

GEOMETRY_VERSION = "input-affine-v2-raster-resolution"


def raster_dpi(context):
    """实际渲染像素密度；插值不会增加原件自身的信息。"""
    a, b, c, d, _, _ = context["pixel_to_pdf"]
    determinant = abs(a * d - b * c)
    return 72 / math.sqrt(determinant) if determinant else None


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
