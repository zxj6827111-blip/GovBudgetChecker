"""输入处理语义身份；代码、配置变化不能继续命中旧的财政事实。"""
from __future__ import annotations

import ast
import hashlib
import importlib.metadata
import json
import os
import platform
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from functools import lru_cache
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_VERSION = 'gbc.parse-artifact.v3'
#: 解析身份方案版本。改变身份定义时必须递增，旧运行不能被新实现冒充。
PARSE_IDENTITY_VERSION = 'gbc.parse-run-identity.v2'
PARSE_IMPLEMENTATION_VERSION = 'gbc.parse-implementation.v4'
_FLAGS = ('GBC_TEMPLATE_SCOPE', 'GBC_BUDGET_TEMPLATE_SCOPE', 'GBC_TEMPLATE_EXPORT_SHA256', 'FACT_DISABLE_CELL_CONF_FLOOR', 'INTAKE_DISABLE_ZERO_GAP_PASS', 'PDF_DISABLE_TOC_GUARD',
          'INTAKE_DISABLE_TOC_HINT_GUARD', 'PDF_DISABLE_TITLE_MARKER',
          'PDF_DISABLE_PLACEHOLDER_ON_TABLE_PAGE', 'GOVBUDGET_PDF_DISABLE_LABEL_VISIBILITY_RECOVERY',
          'OCR_MODEL_FINGERPRINT',
          'OCR_RENDER_DPI', 'OCR_TILE_WIDTH', 'OCR_TILE_HEIGHT', 'OCR_TILE_OVERLAP',
          'OCR_DOCUMENT_SOFT_BUDGET_SEC',
          'OCR_ENABLED', 'OCR_IMAGE_GATE', 'OCR_CPU_THREADS', 'PDF_PARSE_MAX_PAGES', 'MAX_UPLOAD_PAGES',
          'PDF_PARSE_MAX_TEXT_CHARS', 'PDF_PARSE_MAX_TABLE_CELLS')

#: 分析运行实际执行的本地代码根。执行清单要覆盖规则使用的辅助模块
#: （如 src/utils）、配置/规则加载和 OCR 服务代码，而不只扫解析目录。
_EXECUTION_CODE_ROOTS = ('src', 'api', 'config', 'rules', 'ocr_service', 'gbc_yaml')
_EXECUTION_CONFIG_PATTERNS = (
    'config/*.yaml',
    'config/*.yml',
    'rules/*.yaml',
    'rules/*.yml',
    'rules/**/*.yaml',
    'rules/**/*.yml',
    'requirements.txt',
    'requirements-dev.txt',
    'pyproject.toml',
)
_EXECUTION_DEPENDENCIES = (
    'fastapi', 'pydantic', 'PyYAML', 'httpx', 'asyncpg', 'openai', 'cryptography',
    'rapidfuzz', 'pdfminer.six', 'pdfplumber', 'pymupdf', 'numpy',
    'paddleocr', 'paddlepaddle', 'Pillow', 'opencv-python',
)

#: 真正决定解析产物内容的实现文件。规则、诊断、复核、展示和导出代码不在此列：
#: 这些模块的变化只能改变完整运行指纹，不能改变可复用的解析身份。
#: 新增解析模块必须同时登记在这里，并递增 PARSE_IMPLEMENTATION_VERSION。
PARSE_IMPLEMENTATION_FILES = (
    'api/__init__.py',
    'api/config.py',
    'ocr_service/app.py',
    'ocr_service/protocol.py',
    'src/__init__.py',
    'src/engine/__init__.py',
    'src/engine/fiscal_lexeme.py',
    'src/schemas/__init__.py',
    'src/schemas/parse_artifact.py',
    'src/schemas/source_reconstruction.py',
    'src/services/__init__.py',
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
#: rapidfuzz 参与表题/表头匹配；pdfminer.six 是 pdfplumber 的底层版面解析。
PARSE_DEPENDENCIES = ('pdfplumber', 'pdfminer.six', 'pymupdf', 'rapidfuzz',
                      'paddleocr', 'paddlepaddle', 'Pillow', 'numpy', 'opencv-python')

#: 语义产物身份保留的解析证据清单字段。这些字段记录人工映射/补录与原始解释，
#: 会改变事实资格；完整代码清单和诊断字段不在此列。
SEMANTIC_EVIDENCE_MANIFEST_KEYS = (
    'document_binding',
    'manual_cells',
    'manual_rows',
    'manual_table_identity',
    'raw_interpretation',
)

#: 明确审定的非解析本地导入。每项必须说明边界，不能整体跳过包或目录。
NON_PARSE_LOCAL_IMPORTS = {
    'src/schemas/check_receipt.py':
        '回执/诊断 schema 已拆分；解析只做兼容导出',
    'src/services/job_orchestrator.py':
        'src.services.__getattr__ 惰性属性；解析不访问 JobOrchestrator',
    'src/services/ai_model_settings.py':
        'api.config.AppConfig.load() 内惰性导入；解析只导入常量，不调用 load()',
}


def content_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _local_module_path(root: Path, dotted: str) -> str | None:
    """按 CPython 3.11 解析顺序：同名包 ``__init__.py`` 优先于 ``.py``。"""
    relative = Path(*dotted.split('.'))
    for candidate in (root / relative / '__init__.py', root / relative.with_suffix('.py')):
        if candidate.is_file():
            return candidate.relative_to(root).as_posix()
    return None


def _parent_package_inits(root: Path, relative_file: str) -> set[str]:
    """返回导入该文件时会执行的必要父包 ``__init__.py``。"""
    path = Path(relative_file)
    parts = path.parts[:-1]
    if path.name == '__init__.py':
        parts = parts[:-1]
    result = set()
    for index in range(1, len(parts) + 1):
        candidate = root / Path(*parts[:index]) / '__init__.py'
        if candidate.is_file():
            result.add(candidate.relative_to(root).as_posix())
    return result


def local_import_dependencies(relative_file: str, root: Path | None = None) -> set[str]:
    """解析模块的全部本地导入（含延迟导入、ocr_service、相对导入与包初始化）。

    只返回项目内真实存在的文件；外部包不返回。相对导入按模块所在包解析；
    目标包和必要父包的 ``__init__.py`` 都会返回，不能靠包形式逃过守卫。
    同名 ``.py`` 与包目录并存时按 CPython 解析顺序选择包的 ``__init__.py``。
    """
    root = Path(root or ROOT)
    path = root / relative_file
    if not path.is_file():
        return set()
    package = Path(relative_file).parent.parts
    result = _parent_package_inits(root, relative_file)

    def add(dotted: str) -> None:
        resolved = _local_module_path(root, dotted)
        if resolved:
            result.add(resolved)
            result.update(_parent_package_inits(root, resolved))

    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                trim = node.level - 1
                if trim > len(package):
                    continue
                base = list(package[:len(package) - trim] if trim else package)
            else:
                base = []
            if node.module:
                module = [*base, *node.module.split('.')]
                add('.'.join(module))
                for alias in node.names:
                    if alias.name != '*':
                        add('.'.join((*module, alias.name)))
            else:
                if base:
                    add('.'.join(base))
                for alias in node.names:
                    if alias.name != '*':
                        add('.'.join((*base, alias.name)))
    return result


@lru_cache(maxsize=1)
def runtime_code_manifest() -> dict:
    """冻结进程启动时的完整分析执行代码与配置。

    范围覆盖实际本地运行面：src（含 utils/security/providers/db 等）、api、
    config、rules、ocr_service、gbc_yaml；规则辅助模块的变化必须进入执行
    指纹，不能只扫解析目录。
    """
    # 在进程首次加载时冻结。运行中改磁盘文件不能把未重载的进程冒充新实现。
    paths = {
        p
        for root in _EXECUTION_CODE_ROOTS
        for p in (ROOT / root).rglob('*.py')
    }
    code = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(paths)}
    packages = {name: _package_version(name) for name in _EXECUTION_DEPENDENCIES}
    # 仅记录摘要，不把可能含服务信息的配置内容写入分析产物。
    configuration_files = {}
    for pattern in _EXECUTION_CONFIG_PATTERNS:
        for path in sorted(ROOT.glob(pattern)):
            if path.is_file():
                configuration_files[path.relative_to(ROOT).as_posix()] = hashlib.sha256(
                    path.read_bytes()).hexdigest()
    return {'version': 'gbc.input-processing.v3', 'code': code, 'packages': packages,
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


def _canonical_semantic_value(value: Any) -> Any:
    """数值规范化：0、0.0、Decimal('0.00') 在几何与金额语义上等价。"""
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, (int, float, Decimal)):
        try:
            number = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return str(value)
        if not number.is_finite():
            return str(value)
        if number == 0:
            number = Decimal(0)  # -0.0 与 0.0 在几何/金额语义上等价
        return {'__number__': format(number.normalize(), 'f')}
    if isinstance(value, dict):
        return {str(key): _canonical_semantic_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_semantic_value(item) for item in value]
    return value


def semantic_artifact_payload(artifact) -> dict:
    """语义产物投影：原文、单元格、几何/映射、主体身份与资格证据。

    排除诊断/展示字段（``reconstruction_decisions``、``label_visibility`` 汇总）、
    运行身份字段和完整代码清单。人工补录/列映射/原始解释等证据清单字段保留，
    因为它们会改变事实资格。
    """
    payload = artifact.to_dict() if hasattr(artifact, 'to_dict') else dict(artifact)
    manifest = payload.pop('processing_manifest', {}) or {}
    for key in ('parse_run_uid', 'schema_version', 'parser_name', 'parser_fingerprint',
                'config_fingerprint', 'ocr_model_fingerprint', 'source_file_hash',
                'reconstruction_decisions', 'label_visibility'):
        payload.pop(key, None)
    payload['evidence_manifest'] = {
        key: manifest[key] for key in SEMANTIC_EVIDENCE_MANIFEST_KEYS if key in manifest
    }
    return _canonical_semantic_value(payload)


def semantic_artifact_hash(artifact) -> str:
    """解析产物语义/资格内容指纹；只随内容和证据变化。"""
    return content_hash(semantic_artifact_payload(artifact))


def parse_run_identity_basis(artifact) -> dict:
    """输入身份 + 语义产物身份；缓存/上下文/回执共同绑定的运行身份。"""
    return {
        'version': PARSE_IDENTITY_VERSION,
        'source_file_hash': str(getattr(artifact, 'source_file_hash', '') or ''),
        'parser_name': str(getattr(artifact, 'parser_name', '') or ''),
        'parser_fingerprint': str(getattr(artifact, 'parser_fingerprint', '') or ''),
        'config_fingerprint': str(getattr(artifact, 'config_fingerprint', '') or ''),
        'ocr_model_fingerprint': str(getattr(artifact, 'ocr_model_fingerprint', '') or ''),
        'schema_version': str(getattr(artifact, 'schema_version', '') or ''),
        'semantic_artifact_hash': semantic_artifact_hash(artifact),
    }


def parse_run_identity(artifact) -> str:
    """可复用解析身份：输入/缓存键 + 语义产物身份。

    诊断/展示字段不参与；内容、映射或资格证据变化必须改变身份。
    """
    return content_hash(parse_run_identity_basis(artifact))[:32]


def refresh_parse_identity(artifact) -> str:
    """解析、OCR 提升、归属绑定或人工映射变化后统一重算运行身份。"""
    artifact.parse_run_uid = parse_run_identity(artifact)
    return artifact.parse_run_uid


def artifacts_share_semantic_identity(left, right) -> bool:
    """同一原件、解析实现、配置与语义产物；审计字段可以不同。"""
    return all(
        str(getattr(left, key, '') or '') == str(getattr(right, key, '') or '')
        for key in ('source_file_hash', 'parser_name', 'parser_fingerprint',
                    'config_fingerprint', 'ocr_model_fingerprint')
    ) and semantic_artifact_hash(left) == semantic_artifact_hash(right)


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
    data = {'schema_version': 'gbc.parse-cache-conflict.v2',
            'supplied_hash': artifact_content_hash(supplied), 'cached_hash': artifact_content_hash(cached),
            'supplied_semantic_hash': semantic_artifact_hash(supplied),
            'cached_semantic_hash': semantic_artifact_hash(cached),
            'supplied_identity': parse_run_identity_basis(supplied),
            'cached_identity': parse_run_identity_basis(cached),
            'supplied': supplied.to_dict(), 'cached': cached.to_dict()}
    target = root / (content_hash(data) + '.json.gz')
    if not target.exists():
        with gzip.open(target, 'wt', encoding='utf-8') as handle:
            json.dump(data, handle, ensure_ascii=False)
    return str(target)
