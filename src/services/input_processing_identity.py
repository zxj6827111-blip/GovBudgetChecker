"""输入处理语义身份；代码、配置变化不能继续命中旧的财政事实。"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import re
from pathlib import Path
from functools import lru_cache
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_VERSION = 'gbc.parse-artifact.v3'
_FLAGS = ('FACT_DISABLE_CELL_CONF_FLOOR', 'INTAKE_DISABLE_ZERO_GAP_PASS', 'PDF_DISABLE_TOC_GUARD',
          'INTAKE_DISABLE_TOC_HINT_GUARD', 'PDF_DISABLE_TITLE_MARKER',
          'PDF_DISABLE_PLACEHOLDER_ON_TABLE_PAGE', 'OCR_MODEL_FINGERPRINT',
          'OCR_RENDER_DPI', 'OCR_TILE_WIDTH', 'OCR_TILE_HEIGHT', 'OCR_TILE_OVERLAP',
          'OCR_DOCUMENT_SOFT_BUDGET_SEC',
          'OCR_ENABLED', 'OCR_CPU_THREADS', 'PDF_PARSE_MAX_PAGES', 'MAX_UPLOAD_PAGES',
          'PDF_PARSE_MAX_TEXT_CHARS', 'PDF_PARSE_MAX_TABLE_CELLS')


def content_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


@lru_cache(maxsize=1)
def runtime_code_manifest() -> dict:
    # 在进程首次加载时冻结。运行中改磁盘文件不能把未重载的进程冒充新实现。
    paths = [p for d in ('src/services', 'src/engine', 'src/schemas', 'src/qc', 'ocr_service')
             for p in (ROOT / d).rglob('*.py')]
    paths.extend(ROOT / name for name in ('api/main.py', 'api/runtime.py', 'src/db/migrations.py'))
    code = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(paths)}
    packages = {}
    for name in ('pdfplumber', 'pymupdf', 'paddleocr', 'paddlepaddle', 'Pillow', 'numpy', 'opencv-python'):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    # 仅记录摘要，不把可能含服务信息的配置内容写入分析产物。
    configuration_files = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        if (ROOT / name).is_file() else None
        for name in ('config/app.yaml', 'config/providers.yaml', 'rules/v3_3.yaml',
                     'rules/budget_v3_3_draft.yaml')}
    return {'version': 'gbc.input-processing.v2', 'code': code, 'packages': packages,
            'configuration_files': configuration_files}


def processing_manifest() -> dict:
    basis = {**runtime_code_manifest(), 'configuration': {k: os.getenv(k, '') for k in _FLAGS}}
    return {**basis, 'sha256': content_hash(basis)}


def processing_config_fingerprint(value: str, manifest: dict | None = None) -> str:
    basis = re.sub(r':processing-v2:[0-9a-f]{64}', '', str(value or ''))
    return basis + ':processing-v2:' + (manifest or processing_manifest())['sha256']


def artifact_content_hash(artifact) -> str:
    payload = artifact.to_dict() if hasattr(artifact, 'to_dict') else dict(artifact)
    # 运行UID区分执行尝试，不是原件内容；物理格/逻辑格及资格证据均参与比较。
    payload.pop('parse_run_uid', None)
    return content_hash(payload)


def journal_cache_conflict(job_id, supplied, cached):
    import gzip
    if not str(job_id).replace('-', '').replace('_', '').isalnum():
        raise ValueError('invalid job identifier for cache conflict journal')
    root = Path(os.getenv('UPLOAD_DIR', 'uploads')).resolve() / job_id / 'cache_conflicts'
    root.mkdir(parents=True, exist_ok=True)
    data = {'schema_version': 'gbc.parse-cache-conflict.v1',
            'supplied_hash': artifact_content_hash(supplied), 'cached_hash': artifact_content_hash(cached),
            'supplied': supplied.to_dict(), 'cached': cached.to_dict()}
    target = root / (content_hash(data) + '.json.gz')
    if not target.exists():
        with gzip.open(target, 'wt', encoding='utf-8') as handle:
            json.dump(data, handle, ensure_ascii=False)
    return str(target)
