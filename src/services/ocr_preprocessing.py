"""保留原图的正交旋转与线性倾斜校正；每步记录精确的反变换。"""

from __future__ import annotations
import math
import hashlib
import numpy as np
from PIL import Image
from src.services.input_geometry import Affine
from src.services.ocr_tiles import png_bytes

PREPROCESSING_VERSION = "reversible-image-v1"


def rotate(image, degrees):
    angle = float(degrees) % 360
    if not angle:
        return image, Affine(), []
    original_sha = hashlib.sha256(png_bytes(image)).hexdigest()
    # 正交方向不做插值；非正交只作可逆几何映射，不进行图像内容修复。
    orthogonal = {
        90: Image.Transpose.ROTATE_90,
        180: Image.Transpose.ROTATE_180,
        270: Image.Transpose.ROTATE_270,
    }
    result = (
        image.transpose(orthogonal[angle])
        if angle in orthogonal
        else image.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True, fillcolor="white")
    )
    theta = math.radians(angle)
    a = math.cos(theta)
    b = -math.sin(theta)
    c = math.sin(theta)
    d = math.cos(theta)
    a, b, c, d = [0.0 if abs(v) < 1e-12 else v for v in (a, b, c, d)]
    cx, cy = image.width / 2, image.height / 2
    nx, ny = result.width / 2, result.height / 2
    forward = Affine(a, b, c, d, nx - a * cx - c * cy, ny - b * cx - d * cy)
    inverse = forward.inverse()
    operation = {
        "operation": "rotate",
        "degrees_counterclockwise": degrees,
        "geometry_inverse_verified": True,
        "raster_interpolation": angle not in orthogonal,
        "input_size": list(image.size),
        "output_size": list(result.size),
        "input_sha256": original_sha,
        "output_sha256": hashlib.sha256(png_bytes(result)).hexdigest(),
        "input_to_output": forward.values(),
        "output_to_input": inverse.values(),
    }
    return result, inverse, [operation]


def estimate_layout_angle(layout):
    """仅用 150 DPI 墨迹投影估计小角度，不以数字识别结果或勾稽差额选择角度。"""
    gray = layout.convert("L")

    def score(image):
        ink = np.asarray(image) < 180
        projection = ink.sum(axis=1).astype(float)
        return float(np.square(projection).sum()) / max(1, int(ink.sum()))

    baseline = score(gray)
    if baseline == 0:
        return 0.0
    coarse = [(-5 + i * 0.5) for i in range(21)]
    scores = [
        (
            score(
                gray.rotate(angle, resample=Image.Resampling.NEAREST, expand=True, fillcolor=255)
            ),
            angle,
        )
        for angle in coarse
    ]
    best, angle = max(scores, key=lambda p: (p[0], -abs(p[1])))
    # 墨迹较稀少或提升不明确时，保留原方向；不改变 OCR 识别阈值。
    return angle if abs(angle) >= 0.5 and best > baseline * 1.15 else 0.0
