"""本地 OCR 服务客户端（ZCode 整改 ZC-02 / SYS-10）。

约定（任务包 §三 ZC-02「OCR 实施约定」）：

- 独立 Python 3.11 CPU 容器，PP-StructureV3 / PaddleOCR 推理；
  服务端见 `Dockerfile.ocr` 与 `docker-compose.yml` 的 `ocr` 服务。
- 初始化模型后离线运行，不自动下载或更新模型；
- 单页预算 120 秒，区域复识别 30 秒；总预算由调用方按页数控制；
- 关键数字进入确定性判定前要求识别分数 ≥ 0.98（分数原样保留，
  不做业务校准、不生成"问题正确概率"）；
- 不可准确逆变换的非线性图像矫正关闭（`deskew: false`）。

客户端只做：HTTP 调用、超时控制、结果归一。识别质量由服务端返回的
score 表达，调用方不得改写。
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

import httpx

#: 关键数字进入确定性数值判定的最低识别分数（任务包 OCR 约定）
KEY_NUMBER_MIN_SCORE = 0.98
#: 单页 OCR 预算（秒）
DEFAULT_PAGE_TIMEOUT_SECONDS = 120.0
#: 区域复识别预算（秒）
DEFAULT_REGION_TIMEOUT_SECONDS = 30.0
#: 单页最大返回块数（防御异常服务端）
MAX_BLOCKS_PER_PAGE = 2000


@dataclass
class OcrBlock:
    """OCR 单块结果：文本 + 识别分数 + 页面坐标。"""

    text: str
    score: float
    bbox: Optional[List[float]] = None  # [x0, y0, x1, y1] 页面坐标
    page_number: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "score": self.score,
            "bbox": self.bbox,
            "page_number": self.page_number,
        }


@dataclass
class OcrPageResult:
    page_number: int
    blocks: List[OcrBlock] = field(default_factory=list)
    tables: List[Dict[str, Any]] = field(default_factory=list)
    elapsed_ms: int = 0
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def page_text(self) -> str:
        """按阅读顺序（y, x）拼接整页文本，供统一解析对象消费。"""
        ordered = sorted(
            (b for b in self.blocks if b.text.strip()),
            key=lambda b: (b.bbox[1] if b.bbox else 0.0, b.bbox[0] if b.bbox else 0.0),
        )
        return "\n".join(b.text.strip() for b in ordered)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "page_number": self.page_number,
            "blocks": [b.to_dict() for b in self.blocks],
            "tables": self.tables,
            "elapsed_ms": self.elapsed_ms,
            "error": self.error,
        }


def ocr_enabled() -> bool:
    """OCR 总开关：默认关闭（本地 Docker 服务由显式配置启用）。"""
    return os.getenv("OCR_ENABLED", "false").strip().lower() in {"1", "true", "yes"}


def ocr_base_url() -> str:
    return os.getenv("OCR_SERVICE_URL", "http://127.0.0.1:9010").rstrip("/")


def _normalise_blocks(raw: Any, page_number: int) -> List[OcrBlock]:
    blocks: List[OcrBlock] = []
    if not isinstance(raw, list):
        return blocks
    for item in raw[:MAX_BLOCKS_PER_PAGE]:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "")
        try:
            score = float(item.get("score", 0.0))
        except (TypeError, ValueError):
            score = 0.0
        bbox = item.get("bbox")
        blocks.append(
            OcrBlock(
                text=text,
                score=score,
                bbox=[float(v) for v in bbox] if isinstance(bbox, (list, tuple)) and len(bbox) == 4 else None,
                page_number=page_number,
            )
        )
    return blocks


async def ocr_page(
    client: httpx.AsyncClient,
    *,
    image_png: bytes,
    page_number: int,
    timeout_seconds: float = DEFAULT_PAGE_TIMEOUT_SECONDS,
) -> OcrPageResult:
    """识别单页图像（PNG 字节流）。失败返回带 error 的结果，不抛异常。"""
    import time

    started = time.monotonic()
    try:
        response = await client.post(
            f"{ocr_base_url()}/ocr",
            files={"image": (f"page-{page_number}.png", image_png, "image/png")},
            params={"deskew": "false"},  # 非线性矫正关闭（任务包约定）
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        blocks = _normalise_blocks(payload.get("blocks"), page_number)
        tables = payload.get("tables") if isinstance(payload.get("tables"), list) else []
        return OcrPageResult(
            page_number=page_number,
            blocks=blocks,
            tables=[table for table in tables if isinstance(table, dict)],
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
    except Exception as exc:
        return OcrPageResult(
            page_number=page_number,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            error=f"{type(exc).__name__}: {exc}",
        )


async def ocr_pages(
    images: Sequence[bytes],
    page_numbers: Sequence[int],
    *,
    concurrency: int = 1,
    page_timeout_seconds: float = DEFAULT_PAGE_TIMEOUT_SECONDS,
) -> List[OcrPageResult]:
    """逐页识别。默认单并发（任务包：默认单并发，逐页释放图像内存）。"""
    if not images:
        return []
    semaphore = asyncio.Semaphore(max(1, concurrency))
    timeout = httpx.Timeout(page_timeout_seconds)

    async def _one(client: httpx.AsyncClient, image: bytes, page_number: int) -> OcrPageResult:
        async with semaphore:
            return await ocr_page(
                client,
                image_png=image,
                page_number=page_number,
                timeout_seconds=page_timeout_seconds,
            )

    async with httpx.AsyncClient(timeout=timeout) as client:
        results = await asyncio.gather(
            *[_one(client, image, page) for image, page in zip(images, page_numbers, strict=True)]
        )
    return list(results)


async def check_ocr_health(timeout_seconds: float = 5.0) -> Dict[str, Any]:
    """服务健康检查：初始化后离线可用，不触发模型下载。"""
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.get(f"{ocr_base_url()}/health")
            response.raise_for_status()
            payload = response.json()
            model_fingerprint = payload.get("model_fingerprint")
            models_ready = bool(payload.get("models_ready"))
            return {
                "ok": models_ready and isinstance(model_fingerprint, str),
                "engine": payload.get("engine"),
                "models_ready": models_ready,
                "model_fingerprint": model_fingerprint,
            }
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
