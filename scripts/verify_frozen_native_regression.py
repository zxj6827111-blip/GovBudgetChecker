"""对冻结原件执行可续跑开发回归；结果不是独立逐格准确率或历史发布。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    from src.services.input_processing_identity import processing_manifest
    from scripts.verify_native_pdf_core import audit

    identity = processing_manifest()
    output = args.output / identity['sha256'][:16]
    output.mkdir(parents=True, exist_ok=True)
    manifest_file = output/'processing_manifest.json'
    if manifest_file.exists() and json.loads(manifest_file.read_text()) != identity:
        raise ValueError('regression processing identity collision')
    manifest_file.write_text(json.dumps(identity, ensure_ascii=False, indent=2))
    sources = json.loads((args.baseline/'source_manifest.json').read_text())
    results = []
    for i, source in enumerate(sources, 1):
        frozen = args.baseline/'sources'/(source['sha256']+'.pdf')
        if sha(frozen) != source['sha256']:
            raise ValueError('frozen original hash mismatch')
        # 保留原文件名用于画像，不读取可被其他软件改动的桌面副本。
        named = output/'inputs'/source['year']/source['file']
        named.parent.mkdir(parents=True, exist_ok=True)
        if not named.exists():
            os.link(frozen, named)
        if sha(named) != source['sha256']:
            raise ValueError('named frozen original hash mismatch')
        case = output/'cases'/(source['year']+'-'+source['sha256'])
        receipt = case/'regression_receipt.json'
        if receipt.exists():
            result = json.loads(receipt.read_text())
            if result.get('processing_sha256') != identity['sha256']:
                raise ValueError('regression receipt processing identity mismatch')
        else:
            started = time.monotonic()
            try:
                result = audit(named, case)
                result['execution_status'] = 'completed'
            except Exception as exc:
                result = {'execution_status':'error','error_type':type(exc).__name__,
                          'error':str(exc),'elapsed_seconds':round(time.monotonic()-started,3)}
            # 运行中源代码变化即停止，不能把混合实现挂到被冻结的版本下。
            if any(sha(ROOT/path) != digest for path,digest in identity['code'].items()):
                raise ValueError('implementation changed during frozen regression')
            result.update(original=source,processing_sha256=identity['sha256'],
                          independent_truth_available=False,recognition_accuracy=None)
            case.mkdir(parents=True,exist_ok=True)
            receipt.write_text(json.dumps(result,ensure_ascii=False,indent=2))
        results.append(result)
        summary = {'schema_version':'gbc.native-regression.v1',
                   'scope':'development_regression_no_independent_accuracy_or_history_publication',
                   'processing_sha256':identity['sha256'],'expected_documents':len(sources),
                   'processed_documents':len(results),
                   'execution_errors':sum(r['execution_status']=='error'for r in results),
                   'recognition_accuracy':None,'independent_acceptance':'pending',
                   'documents':results}
        temporary = output/'summary.tmp'
        temporary.write_text(json.dumps(summary,ensure_ascii=False,indent=2))
        temporary.replace(output/'summary.json')
        print(json.dumps({'index':i,'total':len(sources),'file':source['file'],
                          'year':source['year'],'status':result['execution_status'],
                          'facts':result.get('facts'),'completed_checks':result.get('completed'),
                          'applicable_checks':result.get('applicable')},ensure_ascii=False),flush=True)
    return 1 if summary['execution_errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
