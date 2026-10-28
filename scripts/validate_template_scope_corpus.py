#!/usr/bin/env python3
"""冻结盲标清单并回放统一写法裁决；不读业务库、不生成业务真值或人工决定。"""
from __future__ import annotations
import argparse,asyncio,hashlib,json,os,random,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.services.template_scope_metrics import summarize


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    prepare=sub.add_parser('prepare');prepare.add_argument('--pdf-dir',type=Path,required=True)
    prepare.add_argument('--seed',type=int,required=True);prepare.add_argument('--random-count',type=int,required=True)
    prepare.add_argument('--output',type=Path,required=True)
    replay=sub.add_parser('replay');replay.add_argument('--manifest',type=Path,required=True)
    replay.add_argument('--template-export',type=Path,required=True);replay.add_argument('--template-sha256',required=True)
    replay.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():parser.error('输出已存在；请选择新文件以保留冻结证据')
    if args.command=='prepare':
        files={}
        for path in sorted(args.pdf_dir.rglob('*')):
            if path.is_file() and path.suffix.lower()=='.pdf':files.setdefault(digest(path),str(path.resolve()))
        if not 0<=args.random_count<=len(files):parser.error('随机样本数不在去重后的实际语料范围内')
        chosen=set(random.Random(args.seed).sample(sorted(files),args.random_count))
        result={'schema':'gbc.template-blind-labels.v1','seed':args.seed,'file_hash_deduplicated':True,
            'entries':[{'path':path,'sha256':sha,'cohort':'random' if sha in chosen else 'directed',
                'writing_truth':None,'style_truth':None,'conflict_truth':None,'reporting_identity_truth':None,'amount_scope_truth':None,
                'verified_independently':False,'label_reference':None,'label_version':None} for sha,path in sorted(files.items())]}
    else:
        # 即使开发者 shell 设置过真实库，本工具也只能读取批准的离线快照。
        os.environ.pop('DATABASE_URL',None)
        os.environ.update(GBC_TEMPLATE_SCOPE='true',GBC_BUDGET_TEMPLATE_SCOPE='true',GBC_TEMPLATE_EXPORT=str(args.template_export),GBC_TEMPLATE_EXPORT_SHA256=args.template_sha256)
        if digest(args.template_export)!=args.template_sha256:parser.error('批准导出包哈希不匹配')
        from src.services.document_profile_resolver import resolve_document_profile
        from src.services.material_writing import load
        import fitz
        manifest=json.loads(args.manifest.read_text());seen=set();rows=[]
        for entry in manifest['entries']:
            path=Path(entry['path']);sha=digest(path)
            if sha!=entry['sha256'] or sha in seen:parser.error('原件哈希变化或清单含重复原件')
            seen.add(sha)
            with fitz.open(path) as pdf:pages=[p.get_text() for p in pdf]
            profile=resolve_document_profile(page_texts=pages)
            resolution=asyncio.run(load('offline-validation',sha,profile,pages))
            rows.append({**entry,'status':resolution.get('status','insufficient'),'source':resolution.get('source','unresolved'),
                'value':resolution.get('value'),'resolution':resolution})
        result={'schema':'gbc.template-scope-validation.v1','manifest_sha256':digest(args.manifest),
            'template_export_sha256':args.template_sha256,'metrics':summarize(rows),'rows':rows,
            'limitations':['只回放原生文本；OCR、真实报送身份、金额及页面交互须分别验收']}
    with args.output.open('x',encoding='utf-8') as handle:json.dump(result,handle,ensure_ascii=False,indent=2)

if __name__=='__main__':main()
