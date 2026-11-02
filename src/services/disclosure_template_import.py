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
SUPPORTED = {'.doc', '.docx', '.pdf', '.xls'}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _extract(source: Path) -> tuple[str, str]:
    if source.suffix.lower() == '.xls':
        import xlrd
        book = xlrd.open_workbook(source, on_demand=True)
        lines=[]
        cell_map=[]
        text_offset=0
        total_cells=0
        try:
            for sheet in book.sheets():
                total_cells+=sheet.nrows*sheet.ncols
                if total_cells>2_000_000:raise ValueError('模板工作簿超过安全处理上限')
                if sheet.nrows*sheet.ncols > 500_000: raise ValueError('模板表格超过安全处理上限')
                header='工作表：'+sheet.name
                lines.append(header)
                text_offset+=len(header)+1
                for row in range(sheet.nrows):
                    values=[]
                    row_offset=text_offset
                    for column in range(sheet.ncols):
                        value=sheet.cell_value(row,column)
                        if sheet.cell_type(row,column)==xlrd.XL_CELL_EMPTY: continue
                        # 仅提供模板比较视图，数字/公式缓存不参与金额事实或规则计算。
                        rendered=str(value)
                        values.append(rendered)
                        cell_map.append({'sheet':sheet.name,'row':row+1,'column':column+1,'value':value,'cell_type':sheet.cell_type(row,column),'text_start':row_offset,'text_end':row_offset+len(rendered)})
                        row_offset+=len(rendered)+1
                    if values:
                        lines.append('\t'.join(values))
                        text_offset=row_offset
        finally:book.release_resources()
        (source.parent/'spreadsheet_cells.json').write_text(json.dumps(cell_map,ensure_ascii=False,allow_nan=False),encoding='utf-8')
        return '\n'.join(lines), 'xlrd:'+xlrd.__version__+':cached-values-only'
    if source.suffix.lower() == '.pdf':
        import fitz
        with fitz.open(source) as document:
            if document.page_count > 800: raise ValueError('模板超过800页')
            pages = [page.get_text() for page in document]
        return '\f'.join(pages), 'pymupdf:' + str(fitz.VersionBind)
    converter = shutil.which('textutil')
    if not converter:
        raise RuntimeError('未找到 textutil；请保留原件并使用已验证的转换器重试')
    result = subprocess.run([converter, '-convert', 'txt', '-stdout', str(source)],
                            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=60)
    build = subprocess.run(['/usr/bin/sw_vers', '-buildVersion'], check=True, stdout=subprocess.PIPE, timeout=5).stdout.decode().strip()
    return result.stdout.decode('utf-8'), 'textutil:' + os.path.realpath(converter) + ':macOS-' + build


def import_source(source: Path, target: Path, metadata: dict) -> dict:
    """写入新的独立草稿目录。转换失败仍保留原件及错误，不能当成可发布模板。"""
    source, target = Path(source), Path(target)
    suffix = source.suffix.lower()
    if suffix not in SUPPORTED:
        raise ValueError('仅支持已验证的 DOC、DOCX、PDF、XLS 草稿导入')
    if not isinstance(metadata, dict):
        raise ValueError('来源元数据必须为对象')
    metadata = {**metadata, 'original_filename':metadata.get('original_filename') or source.name}
    with source.open('rb') as handle:
        data = handle.read(MAX_SOURCE_BYTES + 1)
    if not data or len(data) > MAX_SOURCE_BYTES:
        raise ValueError('原件为空或超过大小限制')
    # 草稿目录不可复用，防止覆盖既有原件；文件名由系统生成而非信任上传名。
    target.mkdir(parents=True, exist_ok=False)
    original = target / ('original' + suffix)
    original.write_bytes(data)
    manifest = {'status': 'draft', 'sha256': digest(data), 'bytes': len(data),
                'original': original.name, 'metadata': metadata,
                'extraction_status': 'failed', 'converter': None, 'error': None}
    try:
        text, converter = _extract(original)
        if len(text)>2_000_000:
            raise ValueError('转换文本超过安全处理上限')
        if not text.strip():
            raise ValueError('转换结果为空；不允许作为有效模板发布')
        (target / 'extracted.txt').write_text(text, encoding='utf-8')
        manifest.update(extraction_status='needs_visual_verification', converter=converter,
                        text_sha256=digest(text.encode('utf-8')))
    except Exception as exc:
        # 转换器专有解析异常也要落失败回执；原件和错误不能因空模板而消失。
        manifest['error'] = f'{type(exc).__name__}: {exc}'[:1000]
    (target / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return manifest


def _paragraphs(text: str) -> list[dict]:
    # 不删除年份、名称、数字或标点；位置仍指向原始提取文本。
    result = []
    offset = 0
    paragraph = 0
    for line in text.splitlines(keepends=True):
        content = line.rstrip('\r\n\f')
        if content.strip():
            paragraph += 1
            result.append({'page': None, 'paragraph': paragraph, 'offset': offset, 'text': content})
        offset += len(line)
    return result


def compare_text(old: str, new: str) -> list[dict]:
    """提供可追溯的字面差异；任何差异都需业务审核，不能自动声明无实质变化。"""
    left, right = _paragraphs(old), _paragraphs(new)
    # 字面比较不归一化，以免掩盖任何待审核的字符变化。
    matcher = difflib.SequenceMatcher(a=[p['text'] for p in left], b=[p['text'] for p in right], autojunk=False)
    return [{'change': tag, 'old': left[i:j], 'new': right[k:new_end],
             'business_classification': 'pending_review'}
            for tag, i, j, k, new_end in matcher.get_opcodes() if tag != 'equal']
