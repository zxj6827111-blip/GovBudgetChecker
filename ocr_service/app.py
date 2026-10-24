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
import threading
import resource
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel
from ocr_service.protocol import PROTOCOL_VERSION, map_table_cells, valid_score, valid_bbox

logger = logging.getLogger("ocr_service")

APP_VERSION = "trusted-input-ocr-4.3"
ENGINE_NAME = "paddleocr"

_engine_state: Dict[str, Any] = {"ready": False, "predictor": None, "error": None}
_inference_lock = threading.Lock()


@contextmanager
def detector_tensor_trace(predictor):
    """在锁定版本的检测器张量组批入口记录真实尺寸，不能拿HTTP图片尺寸冒充。"""
    detector = predictor.paddlex_pipeline.general_ocr_pipeline.text_det_model
    transforms = detector.pre_tfs
    original = transforms['ToBatch']
    sizes = []
    def traced(*args, **kwargs):
        result = original(*args, **kwargs)
        for tensor in result:
            shape = list(tensor.shape)
            if len(shape) != 4:
                raise RuntimeError('unexpected OCR detector tensor layout')
            sizes.append([int(shape[-1]),int(shape[-2])])
        return result
    transforms['ToBatch'] = traced
    try:
        yield sizes
    finally:
        transforms['ToBatch'] = original


def _diagnostic_phase(phase: str, context: dict) -> None:
    if os.getenv('OCR_DIAGNOSTICS') == '1':
        # 只记请求身份和内存，不写识别文字，便于区分推理与序列化的资源故障。
        logger.warning('ocr_phase=%s tile=%s size=%s mode=%s peak_rss_kib=%s', phase,
            context['tile_id'], context['size_px'], context['inference_mode'],
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """确保 uvicorn 模块入口与直接运行入口都加载 OCR 引擎。"""
    if not _engine_state["ready"]:
        try:
            _load_engine()
        except Exception as exc:
            # 模型包是独立部署件。缺失时服务仍启动并明确报告 not-ready，
            # 让默认 API/Worker 栈可用；绝不下载模型或伪报 OCR 已就绪。
            _engine_state["error"] = type(exc).__name__
            logger.exception("offline OCR model bundle is unavailable")
    yield


app = FastAPI(title="GovBudgetChecker OCR", version=APP_VERSION, lifespan=lifespan)


class HealthPayload(BaseModel):
    engine: str
    version: str
    models_ready: bool
    model_fingerprint: str | None = None
    pipeline_fingerprint: str | None = None
    error: str | None = None


@app.get("/health", response_model=HealthPayload)
def health() -> HealthPayload:
    return HealthPayload(
        engine=ENGINE_NAME,
        version=APP_VERSION,
        models_ready=bool(_engine_state["ready"]),
        model_fingerprint=_engine_state.get("model_fingerprint"),
        pipeline_fingerprint=_engine_state.get("pipeline_fingerprint"),
        error=_engine_state.get("error"),
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
            raise RuntimeError(
                f"model manifest path is missing or outside model directory: {relative_name}"
            )
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


def _mkldnn_enabled() -> bool:
    """x86 部署可选的 MKLDNN；进入流水线配置指纹，开关前后的识别结果不混用。"""
    return os.getenv("OCR_ENABLE_MKLDNN", "false").strip().lower() in {"1", "true", "yes", "on"}


def _load_engine() -> None:
    """加载 PP-StructureV3；模型文件缺失时启动失败，不切换云端服务。"""
    os.environ.setdefault("PADDLE_PDX_CACHE_HOME", "/tmp/gbc-paddlex-cache")
    os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
    os.environ.setdefault("OMP_NUM_THREADS", os.getenv("OCR_CPU_THREADS", "4"))
    from paddleocr import PPStructureV3

    _engine_state["ready"] = False
    models_dir = os.getenv("PADDLE_HOME", "/models")
    model_fingerprint = _verify_model_manifest(models_dir)
    bindings = _model_bindings(models_dir)
    predictor = PPStructureV3(
        **bindings,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,  # 不可准确逆变换的非线性矫正：关闭
        use_textline_orientation=False,
        use_seal_recognition=False,
        use_formula_recognition=False,
        use_chart_recognition=False,
        use_region_detection=False,
        cpu_threads=int(os.getenv("OCR_CPU_THREADS", "4")),
        enable_mkldnn=_mkldnn_enabled(),
        text_det_limit_side_len=4096,
        text_det_limit_type="max",
        text_recognition_batch_size=1,
    )
    import numpy as np

    # Readiness requires a completed real prediction, not just construction.
    warmup = list(
        predictor.predict(
            input=np.full((256, 256, 3), 255, dtype=np.uint8), use_table_orientation_classify=False
        )
    )
    if not warmup:
        raise RuntimeError("OCR warmup produced no result")
    _serialise_prediction(warmup[0])
    if _verify_model_manifest(models_dir) != model_fingerprint:
        raise RuntimeError("OCR models changed during warmup")
    _engine_state["predictor"] = predictor
    _engine_state["models_dir"] = models_dir
    _engine_state["model_fingerprint"] = model_fingerprint
    config = {
        "version": APP_VERSION,
        "cpu_threads": int(os.getenv("OCR_CPU_THREADS", "4")),
        "mkldnn": _mkldnn_enabled(),
        "det_limit_side_len": "per_request_full_resolution",
        "det_limit_type": "max",
        "rec_batch_size": 1,
        "orientation": False,
        "unwarp": False,
        "input_max_side": 8192,
        "input_resize": False,
        "text_mode": "shared_general_ocr_pipeline_without_layout",
        "detector_input_policy": "right_bottom_white_pad_to_32_and_trace_actual_nchw_tensor",
        "enabled_model_bindings": bindings,
    }
    _engine_state["pipeline_fingerprint"] = hashlib.sha256(
        json.dumps(config, sort_keys=True).encode()
    ).hexdigest()
    _engine_state["error"] = None
    _engine_state["ready"] = True


def _model_bindings(models_dir: str) -> Dict[str, str]:
    """Every enabled submodel must have an explicit verified local directory."""
    root = Path(models_dir).resolve()
    configuration = json.loads((root / "MODELS.json").read_text(encoding="utf-8"))
    roles = {
        "layout_detection",
        "text_detection",
        "text_recognition",
        "table_classification",
        "wired_table_structure_recognition",
        "wireless_table_structure_recognition",
        "wired_table_cells_detection",
        "wireless_table_cells_detection",
    }
    models = configuration.get("models", {})
    if configuration.get("schema_version") != "gbc.ocr.models.v1" or set(models) != roles:
        raise RuntimeError("offline model bindings must cover exactly the enabled submodels")
    result = {}
    for role in sorted(roles):
        entry = models[role]
        directory = (root / entry["path"]).resolve()
        if root not in directory.parents or not directory.is_dir() or not entry.get("name"):
            raise RuntimeError(f"invalid local model binding: {role}")
        if (
            not (directory / "inference.pdiparams").is_file()
            or not (directory / "inference.yml").is_file()
        ):
            raise RuntimeError(f"incomplete local model: {role}")
        result[f"{role}_model_name"] = str(entry["name"])
        result[f"{role}_model_dir"] = str(directory)
    return result


def _serialise_prediction(result: Any) -> Dict[str, Any]:
    """读取 PaddleOCR 官方结果对象的 JSON 序列化，兼容 dict 结果。"""
    if isinstance(result, dict) and not callable(getattr(result, "save_to_json", None)):

        def scalar(value):
            if callable(getattr(value, "tolist", None)):
                return value.tolist()
            if callable(getattr(value, "item", None)):
                return value.item()
            raise TypeError(f"unsupported prediction value: {type(value).__name__}")

        return json.loads(json.dumps(result, default=scalar, ensure_ascii=False, allow_nan=False))
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


def _render_prediction(
    payload: Dict[str, Any], coordinate_scale: List[float] | None = None
) -> Dict[str, Any]:
    """把 PP-StructureV3 结果归一为文字块及表格块，保留原识别分数。"""
    payload = payload.get("res", payload)
    blocks: List[Dict[str, Any]] = []
    overall = payload.get("overall_ocr_res") or (payload if 'rec_texts' in payload else {})
    if isinstance(overall, dict):
        texts = overall.get("rec_texts") or []
        scores = overall.get("rec_scores") or []
        boxes = overall.get("rec_polys") or overall.get("dt_polys") or []
        for index, text in enumerate(texts):
            score = valid_score(scores[index]) if index < len(scores) else None
            bbox: List[float] | None = None
            if index < len(boxes) and len(boxes[index]) >= 4:
                xs = [float(p[0]) for p in boxes[index][:4]]
                ys = [float(p[1]) for p in boxes[index][:4]]
                bbox = [min(xs), min(ys), max(xs), max(ys)]
            blocks.append({"text": str(text), "score": score, "bbox": valid_bbox(bbox)})

    tables: List[Dict[str, Any]] = []
    structured_tables = payload.get("table_res_list") or []
    for item in structured_tables:
        if isinstance(item, dict):
            mapped = map_table_cells(item)
            rows = mapped.get("rows") or _table_rows_from_content(item.get("pred_html"))
            boxes = [valid_bbox(raw) for raw in item.get("cell_box_list", [])]
            boxes = [box for box in boxes if box is not None]
            bbox = (
                [
                    min(b[0] for b in boxes),
                    min(b[1] for b in boxes),
                    max(b[2] for b in boxes),
                    max(b[3] for b in boxes),
                ]
                if boxes
                else None
            )
            tables.append({**mapped, "rows": rows, "bbox": bbox})
    parsing = payload.get("parsing_res_list") or []
    for item in [] if tables else parsing:
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
            tables.append(
                {
                    "rows": rows,
                    "bbox": bbox,
                    "block_id": item.get("block_id"),
                    "mapping_status": "unavailable",
                    "mapping_reasons": ["cell_geometry_missing"],
                }
            )
    sx, sy = coordinate_scale or [1.0, 1.0]

    def scaled(box):
        return [box[0] * sx, box[1] * sy, box[2] * sx, box[3] * sy] if box else None

    for block in blocks:
        block["bbox"] = scaled(block["bbox"])
    for table in tables:
        table["bbox"] = scaled(table.get("bbox"))
        for cell in table.get("cells", []):
            cell["render_bbox"] = scaled(cell["render_bbox"])
            for fragment in cell["fragments"]:
                fragment["render_bbox"] = scaled(fragment["render_bbox"])
    return {"blocks": blocks, "tables": tables}


@app.post("/ocr")
async def run_ocr(
    image: UploadFile = File(...),
    deskew: bool = Query(default=False),
    region_only: bool = Query(default=False),
    tile_context: str | None = Form(default=None),
) -> Dict[str, Any]:
    if deskew:
        raise HTTPException(
            status_code=422, detail="deskew is disabled; coordinate inverse is not verified"
        )
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
        height, width = page_image.shape[:2]
        from ocr_service.protocol import validate_tile_context
        try:
            context = validate_tile_context(json.loads(tile_context) if tile_context else None, data, (width,height))
            if (context['inference_mode'] == 'text') != region_only:
                raise ValueError('OCR inference mode differs from request context')
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if max(height, width) > 8192 or height * width > 32_000_000:
            raise HTTPException(status_code=413, detail="OCR input exceeds full-resolution tile limit")
        coordinate_scale = [width / page_image.shape[1], height / page_image.shape[0]]
        padding = [0, 0, (-width) % 32, (-height) % 32]
        # 只加白边，不插值、不缩小原图；原件坐标的原点和每一个像素保持不变。
        inference_image = cv2.copyMakeBorder(page_image, 0,padding[3],0,padding[2],
                                              cv2.BORDER_CONSTANT,value=(255,255,255))
        model_height, model_width = inference_image.shape[:2]
        with _inference_lock:
            fingerprint = _engine_state.get("model_fingerprint")
            if fingerprint and _verify_model_manifest(_engine_state["models_dir"]) != fingerprint:
                _engine_state["ready"] = False
                raise RuntimeError("OCR models changed before inference")
            prediction_options = {"use_table_orientation_classify": False,
                                  "text_det_limit_side_len": max(model_width,model_height),
                                  "text_det_limit_type": "max"}
            _diagnostic_phase('predict_start', context)
            with detector_tensor_trace(predictor) as detector_sizes:
                if region_only:
                    # 直接复用已加载的同一文字模型，不额外运行版面模型。
                    output = list(predictor.paddlex_pipeline.general_ocr_pipeline.predict(
                        input=inference_image, use_doc_orientation_classify=False,
                        use_doc_unwarping=False, use_textline_orientation=False,
                        text_det_limit_side_len=max(model_width,model_height), text_det_limit_type='max'))
                else:
                    output = list(predictor.predict(input=inference_image, **prediction_options))
            if not detector_sizes or detector_sizes[0] != [model_width,model_height]:
                raise RuntimeError('actual OCR detector input fidelity is unconfirmed')
            _diagnostic_phase('predict_complete', context)
            raw = _serialise_prediction(output[0]) if output else {}
            _diagnostic_phase('serialise_complete', context)
            if fingerprint and _verify_model_manifest(_engine_state["models_dir"]) != fingerprint:
                _engine_state["ready"] = False
                raise RuntimeError("OCR models changed during inference")
        raw_sha = hashlib.sha256(
            json.dumps(
                raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
        ).hexdigest()
        return {
            **_render_prediction(raw, coordinate_scale),
            "deskew": False,
            "schema_version": PROTOCOL_VERSION,
            "tile_context": context,
            "completeness": {"request_processed": True, "scope": "tile", "page_complete": False},
            "input_sha256": hashlib.sha256(data).hexdigest(),
            "model_fingerprint": fingerprint,
            "pipeline_fingerprint": _engine_state.get("pipeline_fingerprint"),
            "prediction_coordinate_scale": coordinate_scale,
            "source_size_px": [width, height],
            "model_input_size_px": detector_sizes[0],
            "detector_tensor_sizes_px": detector_sizes,
            "detector_input_verified": True,
            "inference_padding_px": padding,
            "detector_requested_limit_px": max(model_width,model_height),
            "input_resize_applied": False,
            "raw_prediction": raw,
            "raw_prediction_sha256": raw_sha,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("ocr inference failed")
        raise HTTPException(status_code=500, detail=f"ocr_failed: {exc}") from exc


if __name__ == "__main__":
    import uvicorn

    _load_engine()
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("OCR_SERVICE_PORT", "9010")))
