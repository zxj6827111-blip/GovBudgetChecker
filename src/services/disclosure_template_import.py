"""模板原件草稿导入与字面比较；本模块不审核、不发布、不启用规则。"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

MAX_SOURCE_BYTES = 20 * 1024 * 1024
SUPPORTED = {'.doc', '.docx', '.pdf'}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _extract(source: Path) -> tuple[str, str]:
    if source.suffix.lower() == '.pdf':
        import fitz
        with fitz.open(source) as document:
            pages = [page.get_text() for page in document]
        return '\f'.join(pages), 'pymupdf:' + str(fitz.VersionBind)
    converter = shutil.which('textutil')
    if not converter:
        raise RuntimeError('未找到 textutil；请保留原件并使用已验证的转换器重试')
    result = subprocess.run([converter, '-convert', 'txt', '-stdout', str(source)],
                            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=60)
    return result.stdout.decode('utf-8'), 'textutil:' + os.path.realpath(converter)


def import_source(source: Path, target: Path, metadata: dict) -> dict:
    """写入新的独立草稿目录。转换失败仍保留原件及错误，不能当成可发布模板。"""
    source, target = Path(source), Path(target)
    suffix = source.suffix.lower()
    if suffix not in SUPPORTED:
        raise ValueError('仅支持已验证的 DOC、DOCX、PDF 草稿导入')
    with source.open('rb') as handle:
        data = handle.read(MAX_SOURCE_BYTES + 1)
    if not data or len(data) > MAX_SOURCE_BYTES:
        raise ValueError('原件为空或超过大小限制')
    if not isinstance(metadata, dict):
        raise ValueError('来源元数据必须为对象')
    # 草稿目录不可复用，防止覆盖既有原件；文件名由系统生成而非信任上传名。
    target.mkdir(parents=True, exist_ok=False)
    original = target / ('original' + suffix)
    original.write_bytes(data)
    manifest = {'status': 'draft', 'sha256': digest(data), 'bytes': len(data),
                'original': original.name, 'metadata': metadata,
                'extraction_status': 'failed', 'converter': None, 'error': None}
    try:
        text, converter = _extract(original)
        if not text.strip():
            raise ValueError('转换结果为空；不允许作为有效模板发布')
        (target / 'extracted.txt').write_text(text, encoding='utf-8')
        manifest.update(extraction_status='needs_visual_verification', converter=converter,
                        text_sha256=digest(text.encode('utf-8')))
    except (OSError, ValueError, UnicodeError, RuntimeError, subprocess.SubprocessError, ImportError) as exc:
        manifest['error'] = f'{type(exc).__name__}: {exc}'
    (target / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return manifest


def _paragraphs(text: str) -> list[dict]:
    # 不删除年份、名称、数字或标点；位置仍指向原始提取文本。
    result = []
    offset = 0
    page = 1
    for line in text.splitlines(keepends=True):
        content = line.rstrip('\r\n\f')
        if content.strip():
            result.append({'page': page, 'offset': offset, 'text': content})
        offset += len(line)
        page += line.count('\f')
    return result


def compare_text(old: str, new: str) -> list[dict]:
    """提供可追溯的字面差异；任何差异都需业务审核，不能自动声明无实质变化。"""
    left, right = _paragraphs(old), _paragraphs(new)
    # 字面比较不归一化，以免掩盖任何待审核的字符变化。
    matcher = difflib.SequenceMatcher(a=[p['text'] for p in left], b=[p['text'] for p in right], autojunk=False)
    return [{'change': tag, 'old': left[i:j], 'new': right[k:l],
             'business_classification': 'pending_review'}
            for tag, i, j, k, l in matcher.get_opcodes() if tag != 'equal']
