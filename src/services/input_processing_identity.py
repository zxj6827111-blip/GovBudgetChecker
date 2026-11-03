"""输入处理语义身份；代码、配置变化不能继续命中旧的财政事实。"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import re
from pathlib import Path
from functools import lru_cache
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_VERSION = 'gbc.parse-artifact.v3'
#: 解析身份方案版本。改变身份定义时必须递增，旧运行不能被新实现冒充。
PARSE_IDENTITY_VERSION = 'gbc.parse-run-identity.v1'
PARSE_IMPLEMENTATION_VERSION = 'gbc.parse-implementation.v1'
_FLAGS = ('GBC_TEMPLATE_SCOPE', 'GBC_BUDGET_TEMPLATE_SCOPE', 'GBC_TEMPLATE_EXPORT_SHA256', 'FACT_DISABLE_CELL_CONF_FLOOR', 'INTAKE_DISABLE_ZERO_GAP_PASS', 'PDF_DISABLE_TOC_GUARD',
          'INTAKE_DISABLE_TOC_HINT_GUARD', 'PDF_DISABLE_TITLE_MARKER',
          'PDF_DISABLE_PLACEHOLDER_ON_TABLE_PAGE', 'GOVBUDGET_PDF_DISABLE_LABEL_VISIBILITY_RECOVERY',
          'OCR_MODEL_FINGERPRINT',
          'OCR_RENDER_DPI', 'OCR_TILE_WIDTH', 'OCR_TILE_HEIGHT', 'OCR_TILE_OVERLAP',
          'OCR_DOCUMENT_SOFT_BUDGET_SEC',
          'OCR_ENABLED', 'OCR_IMAGE_GATE', 'OCR_CPU_THREADS', 'PDF_PARSE_MAX_PAGES', 'MAX_UPLOAD_PAGES',
          'PDF_PARSE_MAX_TEXT_CHARS', 'PDF_PARSE_MAX_TABLE_CELLS')

#: 真正决定解析产物内容的实现文件。规则、诊断、复核、展示和导出代码不在此列：
#: 这些模块的变化只能改变完整运行指纹，不能改变可复用的解析身份。
#: 新增解析模块必须同时登记在这里，并递增 PARSE_IMPLEMENTATION_VERSION。
PARSE_IMPLEMENTATION_FILES = (
    'ocr_service/app.py',
    'ocr_service/protocol.py',
    'src/engine/fiscal_lexeme.py',
    'src/schemas/parse_artifact.py',
    'src/schemas/source_reconstruction.py',
    'src/services/fiscal_column_identity.py',
    'src/services/fiscal_row_roles.py',
    'src/services/fiscal_table_rules.py',
    'src/services/cell_identity.py',
    'src/services/input_geometry.py',
    'src/services/input_processing_identity.py',
    'src/services/native_dimension_recovery.py',
    'src/services/native_label_visibility.py',
    'src/services/native_ocr_fusion.py',
    'src/services/native_structure_recovery.py',
    'src/services/ocr_client.py',
    'src/services/ocr_fact_promotion.py',
    'src/services/ocr_grid_reconstruction.py',
    'src/services/ocr_pipeline.py',
    'src/services/ocr_preprocessing.py',
    'src/services/ocr_qualification.py',
    'src/services/ocr_tiled_pipeline.py',
    'src/services/ocr_tiles.py',
    'src/services/pdf_parser.py',
    'src/services/raster_grid_candidates.py',
    'src/services/source_reconstruction.py',
    'src/services/table_reading_assessment.py',
)

#: 解析依赖。版本变化会改变解析产物，不能继续复用旧缓存。
PARSE_DEPENDENCIES = ('pdfplumber', 'pymupdf', 'paddleocr', 'paddlepaddle',
                      'Pillow', 'numpy', 'opencv-python')


def content_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


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


@lru_cache(maxsize=1)
def parse_code_manifest() -> dict:
    """冻结进程启动时的解析实现；运行中改盘不能把未重载进程冒充新实现。"""
    code = {}
    for name in PARSE_IMPLEMENTATION_FILES:
        path = ROOT / name
        code[name] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    packages = {}
    for name in PARSE_DEPENDENCIES:
        packages[name] = _package_version(name)
    return {'version': PARSE_IMPLEMENTATION_VERSION, 'code': code, 'packages': packages,
            'python': platform.python_version()}


def parse_implementation_manifest() -> dict:
    """只含解析实现、解析依赖与解析配置的指纹。"""
    basis = {**parse_code_manifest(), 'configuration': {k: os.getenv(k, '') for k in _FLAGS}}
    return {**basis, 'sha256': content_hash(basis)}


def processing_manifest() -> dict:
    """完整运行清单：包含规则、诊断、展示代码，只作审计与完整运行指纹。"""
    basis = {**runtime_code_manifest(), 'configuration': {k: os.getenv(k, '') for k in _FLAGS},
             'parse_implementation': parse_implementation_manifest()}
    return {**basis, 'sha256': content_hash(basis)}


def processing_config_fingerprint(value: str, manifest: dict | None = None) -> str:
    """解析配置指纹只跟随解析实现与解析配置，不随诊断/展示代码变化。"""
    basis = re.sub(r':processing-v2:[0-9a-f]{64}', '', str(value or ''))
    return basis + ':processing-v2:' + (manifest or parse_implementation_manifest())['sha256']


def parse_run_identity(artifact) -> str:
    """可复用解析身份：原件、解析实现、配置、OCR、schema 与身份方案版本。

    诊断/展示字段不参与；它们属于完整运行指纹。身份方案版本参与，
    保证旧运行不会被新实现按同一输入冒充。
    """
    basis = {
        'version': PARSE_IDENTITY_VERSION,
        'source_file_hash': str(getattr(artifact, 'source_file_hash', '') or ''),
        'parser_name': str(getattr(artifact, 'parser_name', '') or ''),
        'parser_fingerprint': str(getattr(artifact, 'parser_fingerprint', '') or ''),
        'config_fingerprint': str(getattr(artifact, 'config_fingerprint', '') or ''),
        'ocr_model_fingerprint': str(getattr(artifact, 'ocr_model_fingerprint', '') or ''),
        'schema_version': str(getattr(artifact, 'schema_version', '') or ''),
    }
    return hashlib.sha256(json.dumps(basis, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()[:32]


def refresh_parse_identity(artifact) -> str:
    """解析、OCR 提升、归属绑定或人工映射变化后统一重算运行身份。"""
    artifact.parse_run_uid = parse_run_identity(artifact)
    return artifact.parse_run_uid


def full_run_fingerprint(artifact) -> str:
    """完整运行指纹：artifact 全字段 + 当前完整实现清单。

    诊断、展示、规则代码或展示字段变化必须改变该指纹；它不能用于复用
    解析缓存或冒充可复用的解析身份。
    """
    return content_hash({'artifact_content_hash': artifact_content_hash(artifact),
                         'implementation': processing_manifest()})


def artifact_content_hash(artifact) -> str:
    payload = artifact.to_dict() if hasattr(artifact, 'to_dict') else dict(artifact)
    # 运行UID区分执行尝试，不是原件内容；物理格/逻辑格及资格证据均参与比较。
    payload.pop('parse_run_uid', None)
    return content_hash(payload)


def bind_artifact_document_identity(artifact, *, organization_id=None, organization_name=None,
                                    fiscal_year=None, report_kind=None, doc_type=None,
                                    check_subject_name=None, scope_confirmation=None) -> None:
    """同一 PDF 改归属后建立独立运行，不能复用旧主体的不可变原始事实。"""
    binding = {key: str(value or '').strip() for key, value in {
        'organization_id': organization_id, 'organization_name': organization_name,
        'fiscal_year': fiscal_year, 'report_kind': report_kind, 'doc_type': doc_type,
        'check_subject_name': check_subject_name,
    }.items()}
    if scope_confirmation:
        binding['scope_confirmation'] = {key: scope_confirmation[key] for key in
            ('request_id', 'file_hash', 'material_scope', 'revision', 'actor', 'note', 'evidence_reference')}
    if artifact.processing_manifest.get('document_binding') == binding:
        return
    artifact.processing_manifest = {**artifact.processing_manifest, 'document_binding': binding}
    base = re.sub(r':document-binding-v1:[0-9a-f]{64}', '', artifact.config_fingerprint or '')
    artifact.config_fingerprint = base + ':document-binding-v1:' + content_hash(binding)
    # 来源格、原始文字、金额及文件哈希不改；仅让包含主体解释的运行身份与归属一起隔离。
    refresh_parse_identity(artifact)


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
