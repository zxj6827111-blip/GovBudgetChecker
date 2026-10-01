"""本地 OCR 服务（ZC-02）：PaddleOCR / PP-Structure CPU 容器的 HTTP 门面。

部署：见 Dockerfile.ocr 与 docker-compose.yml 的 `ocr` 服务（amd64 容器，
本机 arm64 通过 Rosetta/QEMU 兼容运行，必须实测启动与性能）。

纪律（任务包 OCR 实施约定）：
- 启动时加载模型并预热；此后离线运行，**不自动下载或更新模型**
  （PADDLE_HOME 预置模型目录须匹配 MANIFEST.sha256，缺失即启动失败）；
- 只做识别：返回文本块、识别分数与页面坐标，不做任何数值改写；
- 不可准确逆变换的非线性图像矫正保持关闭。
"""

from __future__ import annotations

import json
import hashlib
import logging
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from pydantic import BaseModel

logger = logging.getLogger("ocr_service")

APP_VERSION = "zc02-ocr-1"
ENGINE_NAME = "paddleocr"

_engine_state: Dict[str, Any] = {"ready": False, "predictor": None}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """确保 uvicorn 模块入口与直接运行入口都加载 OCR 引擎。"""
    if not _engine_state["ready"]:
        _load_engine()
    yield


app = FastAPI(title="GovBudgetChecker OCR", version=APP_VERSION, lifespan=lifespan)


class HealthPayload(BaseModel):
    engine: str
    version: str
    models_ready: bool
    model_fingerprint: str | None = None


@app.get("/health", response_model=HealthPayload)
def health() -> HealthPayload:
    return HealthPayload(
        engine=ENGINE_NAME,
        version=APP_VERSION,
        models_ready=bool(_engine_state["ready"]),
        model_fingerprint=_engine_state.get("model_fingerprint"),
    )


def _verify_model_manifest(models_dir: str) -> str:
    """校验离线模型目录的完整 SHA256 清单并返回清单指纹。"""
    root = Path(models_dir).resolve()
    manifest = Path(os.getenv("OCR_MODEL_MANIFEST", str(root / "MANIFEST.sha256"))).resolve()
    if not root.is_dir() or not manifest.is_file() or root not in manifest.parents:
        raise RuntimeError("offline OCR model directory or MANIFEST.sha256 is missing")
    entries: Dict[str, str] = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2 or len(parts[0]) != 64:
            raise RuntimeError("invalid model manifest line; expected '<sha256>  <relative-path>'")
        digest, relative_name = parts[0].lower(), parts[1].lstrip("* ")
        candidate = (root / relative_name).resolve()
        if root not in candidate.parents or not candidate.is_file():
            raise RuntimeError(f"model manifest path is missing or outside model directory: {relative_name}")
        file_digest = hashlib.sha256()
        with candidate.open("rb") as model_file:
            for chunk in iter(lambda: model_file.read(1024 * 1024), b""):
                file_digest.update(chunk)
        actual = file_digest.hexdigest()
        if actual != digest:
            raise RuntimeError(f"model file SHA256 mismatch: {relative_name}")
        entries[str(candidate.relative_to(root))] = digest
    if not entries:
        raise RuntimeError("offline OCR model manifest has no model files")
    actual_files = {
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() and path.resolve() != manifest
    }
    if actual_files != set(entries):
        raise RuntimeError("offline OCR model files do not exactly match MANIFEST.sha256")
    return hashlib.sha256(manifest.read_bytes()).hexdigest()


def _load_engine() -> None:
    """加载 PP-StructureV3；模型文件缺失时启动失败，不切换云端服务。"""
    from paddleocr import PPStructureV3

    models_dir = os.getenv("PADDLE_HOME", "/models")
    model_fingerprint = _verify_model_manifest(models_dir)
    os.environ.setdefault("PADDLE_PDX_CACHE_HOME", models_dir)
    os.environ.setdefault("OMP_NUM_THREADS", os.getenv("OCR_CPU_THREADS", "4"))
    _engine_state["predictor"] = PPStructureV3(
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,  # 不可准确逆变换的非线性矫正：关闭
        use_textline_orientation=True,
    )
    _engine_state["model_fingerprint"] = model_fingerprint
    _engine_state["ready"] = True


def _serialise_prediction(result: Any) -> Dict[str, Any]:
    """读取 PaddleOCR 官方结果对象的 JSON 序列化，兼容 dict 结果。"""
    if isinstance(result, dict):
        return result
    save = getattr(result, "save_to_json", None)
    if not callable(save):
        raise TypeError("PP-StructureV3 result does not support save_to_json")
    with tempfile.TemporaryDirectory(prefix="gbc-ocr-") as temp_dir:
        save(save_path=temp_dir)
        json_files = [name for name in os.listdir(temp_dir) if name.endswith(".json")]
        if not json_files:
            raise ValueError("PP-StructureV3 produced no JSON result")
        with open(os.path.join(temp_dir, sorted(json_files)[0]), "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("PP-StructureV3 JSON result is not an object")
    return payload


def _table_rows_from_content(content: Any) -> List[List[str]]:
    """从 PP-StructureV3 的 HTML 或 Markdown 表格块提取二维文本。"""
    from html import unescape
    from html.parser import HTMLParser

    if not isinstance(content, str) or not content.strip():
        return []

    class TableParser(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.rows: List[List[str]] = []
            self.row: List[str] = []
            self.cell: List[str] = []
            self.in_table = False
            self.in_cell = False

        def handle_starttag(self, tag: str, attrs: Any) -> None:
            if tag == "table":
                self.in_table = True
            elif self.in_table and tag == "tr":
                self.row = []
            elif self.in_table and tag in {"td", "th"}:
                self.cell = []
                self.in_cell = True

        def handle_data(self, data: str) -> None:
            if self.in_cell:
                self.cell.append(data)

        def handle_endtag(self, tag: str) -> None:
            if tag in {"td", "th"} and self.in_cell:
                self.row.append(" ".join("".join(self.cell).split()))
                self.cell = []
                self.in_cell = False
            elif tag == "tr" and self.in_table:
                if self.row:
                    self.rows.append(self.row)
                self.row = []
            elif tag == "table":
                self.in_table = False

    parser = TableParser()
    parser.feed(content)
    if parser.rows:
        return parser.rows

    rows: List[List[str]] = []
    for line in content.splitlines():
        line = line.strip()
        if not (line.startswith("|") and line.endswith("|")):
            continue
        cells = [unescape(cell.strip()) for cell in line.strip("|").split("|")]
        if cells and all(set(cell) <= {"-", ":", " "} for cell in cells):
            continue
        rows.append(cells)
    return rows


def _render_prediction(payload: Dict[str, Any]) -> Dict[str, Any]:
    """把 PP-StructureV3 结果归一为文字块及表格块，保留原识别分数。"""
    blocks: List[Dict[str, Any]] = []
    overall = payload.get("overall_ocr_res") or {}
    if isinstance(overall, dict):
        texts = overall.get("rec_texts") or []
        scores = overall.get("rec_scores") or []
        boxes = overall.get("rec_polys") or overall.get("dt_polys") or []
        for index, text in enumerate(texts):
            score = float(scores[index]) if index < len(scores) else 0.0
            bbox: List[float] | None = None
            if index < len(boxes) and len(boxes[index]) >= 4:
                xs = [float(p[0]) for p in boxes[index][:4]]
                ys = [float(p[1]) for p in boxes[index][:4]]
                bbox = [min(xs), min(ys), max(xs), max(ys)]
            blocks.append({"text": str(text), "score": score, "bbox": bbox})

    tables: List[Dict[str, Any]] = []
    parsing = payload.get("parsing_res_list") or []
    for item in parsing:
        if not isinstance(item, dict) or str(item.get("block_label") or "").lower() != "table":
            continue
        raw_bbox = item.get("block_bbox")
        bbox = None
        try:
            if raw_bbox is not None and len(raw_bbox) >= 4:
                bbox = [float(value) for value in raw_bbox[:4]]
        except (TypeError, ValueError):
            bbox = None
        rows = _table_rows_from_content(item.get("block_content"))
        if rows:
            tables.append({"rows": rows, "bbox": bbox, "block_id": item.get("block_id")})
    return {"blocks": blocks, "tables": tables}


@app.post("/ocr")
async def run_ocr(
    image: UploadFile = File(...),
    deskew: bool = Query(default=False),
) -> Dict[str, Any]:
    if deskew:
        raise HTTPException(status_code=422, detail="deskew is disabled; coordinate inverse is not verified")
    if not _engine_state["ready"]:
        raise HTTPException(status_code=503, detail="ocr engine not ready")
    predictor = _engine_state["predictor"]
    try:
        import numpy as np

        data = await image.read()
        decoded = np.frombuffer(data, dtype=np.uint8)
        import cv2

        page_image = cv2.imdecode(decoded, cv2.IMREAD_COLOR)
        if page_image is None:
            raise HTTPException(status_code=400, detail="invalid image payload")
        output = predictor.predict(input=page_image)
        if not output:
            return {"blocks": [], "tables": [], "deskew": False}
        return {**_render_prediction(_serialise_prediction(output[0])), "deskew": False}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("ocr inference failed")
        raise HTTPException(status_code=500, detail=f"ocr_failed: {exc}") from exc


if __name__ == "__main__":
    import uvicorn

    _load_engine()
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("OCR_SERVICE_PORT", "9010")))
