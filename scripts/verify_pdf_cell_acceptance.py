#!/usr/bin/env python3
"""读取人工逐格标注与对应有效快照，输出验收差异；不生成或推测真值。"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.services.pdf_cell_acceptance import evaluate_cells,acceptance_suite


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();records=[]
    for item in json.loads(args.manifest.read_text())['items']:
        truth=json.loads(Path(item['truth']).read_text());snapshot=json.loads(Path(item['snapshot']).read_text())
        artifact=None
        if item.get('artifact'):
            from src.schemas.parse_artifact import ParseArtifact
            artifact=ParseArtifact.from_dict(json.loads(Path(item['artifact']).read_text()))
        result=evaluate_cells(truth,snapshot,artifact)
        result.update({k:truth.get(k)for k in ('report_kind','material_group','region','year')})
        records.append(result)
    output={'records':records,'suite':acceptance_suite(records)}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(output,ensure_ascii=False,indent=2))
    print(json.dumps({'accepted':output['suite']['accepted'],'materials':len(records),'output':str(args.output.resolve())}))
    return 0 if output['suite']['accepted']else 2


if __name__=='__main__':raise SystemExit(main())
