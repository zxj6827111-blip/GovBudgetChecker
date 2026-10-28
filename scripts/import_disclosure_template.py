#!/usr/bin/env python3
"""仅导入草稿并输出字面差异，不创建正式发布或年度绑定。"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.services.disclosure_template_import import compare_text, import_source


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path, help='必须不存在的新草稿目录')
    parser.add_argument('--kind', choices=('final', 'budget'), required=True)
    parser.add_argument('--type', choices=('unit', 'department'), required=True)
    parser.add_argument('--source-year', type=int, help='原件未写报告年度时留空，不以目录或下发年份补值')
    parser.add_argument('--release-reference', default='', help='同一套原件的可核实下发批次依据')
    parser.add_argument('--issuer', required=True)
    parser.add_argument('--issue-date', default='')
    parser.add_argument('--description', default='')
    parser.add_argument('--compare-text', type=Path, help='与先前提取的文本作字面比较')
    args = parser.parse_args()
    metadata = {'report_kind': args.kind, 'material_type': args.type, 'source_report_year': args.source_year,
                'release_reference':args.release_reference, 'issuer': args.issuer, 'issue_date': args.issue_date, 'description': args.description}
    result = import_source(args.source, args.output, metadata)
    if args.compare_text and result['extraction_status'] != 'failed':
        changes = compare_text(args.compare_text.read_text(encoding='utf-8'),
                               (args.output / 'extracted.txt').read_text(encoding='utf-8'))
        (args.output / 'literal-diff.json').write_text(json.dumps(changes, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['extraction_status'] != 'failed' else 1

if __name__ == '__main__':
    raise SystemExit(main())
