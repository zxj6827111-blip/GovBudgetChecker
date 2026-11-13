"""独立复核已保存记录、原件哈希和切块几何；不启动服务或修改原记录。"""
import collections
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import io

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
os.environ['TESTING']='true';os.environ['GOVBUDGET_AUTH_ENABLED']='false'
os.environ.pop('DATABASE_URL',None)
from src.services.analysis_fact_snapshot import canonical_facts,validate_snapshot
import pymupdf as fitz
from PIL import Image
from src.services.ocr_tiles import tile_boxes
os.environ['OCR_TILE_WIDTH']='512';os.environ['OCR_TILE_HEIGHT']='256'

BASE=ROOT/'outputs/pdf_acceptance_shanghai2022_20261021'
OUT=Path(__file__).resolve().parent
manifest=json.loads((BASE/'source_manifest.json').read_text())
with (BASE/'逐份执行结果.csv').open(encoding='utf-8-sig',newline='')as f:
    execution=list(csv.DictReader(f))
by_source={r['sha256']:r for r in execution}
cases=collections.defaultdict(list)
for artifact in (BASE/'runtime_native').glob('*/artifact.json'):
    a=json.loads(artifact.read_text())
    status=json.loads((artifact.parent/'status.json').read_text())
    cases[a['source_file_hash']].append((artifact.parent,a,status))
checked=[];fin04=[];native_facts=0;exports=0
for item in manifest:
    original=Path(item['frozen_path'])
    assert hashlib.sha256(original.read_bytes()).hexdigest()==item['sha256']
    row=by_source[item['sha256']]
    if row['storage_readback_passed']!='True':continue
    eligible=[case for case in cases[item['sha256']] if case[2].get('job_id')==row['job_id']]
    assert len(eligible)==1,(item['doc_id'],len(eligible))
    directory,a,status=eligible[0]
    snapshot=validate_snapshot(json.loads((directory/'immutable_snapshot.json').read_text()))
    raw=json.loads((directory/'database_parse_facts.json').read_text())
    assert canonical_facts(raw)==canonical_facts(snapshot['raw_context']['facts'])
    assert len(raw)==int(row['raw_facts'])
    native_facts+=len(raw)
    for suffix in ['json','csv','docx','pdf']:
        assert (directory/f'report.{suffix}').stat().st_size>0
        exports+=1
    checked.append({'doc_id':item['doc_id'],'source_sha256':item['sha256'],
        'job_id':row['job_id'],'facts':len(raw),'saved_database_vs_snapshot_equal':True})
    for instance in a['logical_tables']:
        if instance['table_code']=='FIN_04_fiscal_grant_total':
            facts=[f for f in snapshot['raw_context']['facts']if f['instance_uid']==instance['instance_uid']]
            fin04.append({'doc_id':item['doc_id'],'source_sha256':item['sha256'],
                'instance_uid':instance['instance_uid'],'facts':len(facts)})
summary={'documents':len(execution),'pages':sum(int(x['pages'])for x in execution),
    'original_hashes_verified':len(manifest),'saved_database_rows_verified':native_facts,
    'native_documents_verified':len(checked),'four_format_files_present':exports,
    'unique_original_fin04_instances':len(fin04),
    'unique_original_fin04_documents':len({x['source_sha256']for x in fin04}),
    'fin04_instances_with_facts':sum(x['facts']>0 for x in fin04),
    'scope':'saved_raw_database_record_comparison_not_live_database_rerun_or_independent_accuracy',
    'details':checked,'fin04':fin04}
ocr=[]
for index,observation in enumerate(json.loads((BASE/'ocr_observation_summary.json').read_text())[:2]):
    state=json.loads(Path(observation['checkpoint']).read_text())
    records=list(state['completed'].values());parent=records[0]['context']['parent_size_px']
    boxes=state['identity']['tile_boxes']
    axes={}
    for name,points in [('x',sorted({(b[0],b[2])for b in boxes})),('y',sorted({(b[1],b[3])for b in boxes}))]:
        steps=[b[0]-a[0]for a,b in zip(points,points[1:])]
        axes[name]={'intervals':len(points),'min_stride':min(steps),
            'one_pixel_strides':sum(s==1 for s in steps),'strides_under_16':sum(s<16 for s in steps),
            'median_stride':sorted(steps)[len(steps)//2]}
    # OcrPageResult.ok 是属性，落盘协议以 error 表达成功/失败，不含 ok 字段。
    successful=[r for r in records if r['result'].get('error')is None]
    responses_without_blocks=sum(not r['result'].get('blocks')for r in successful)
    item=next(s for s in manifest if s['sha256']==observation['source_sha256'])
    with fitz.open(item['frozen_path'])as pdf:
        texts=[pdf[p].get_text().strip()for p in range(min(3,len(pdf)))];page=pdf[0]
        pix=page.get_pixmap(matrix=fitz.Matrix(300/72,300/72))
        image=Image.open(io.BytesIO(pix.tobytes('png'))).convert('RGB')
        fresh_boxes=[list(b)for b in tile_boxes(image)]
        boxes_reproduced=fresh_boxes==boxes
        page.get_pixmap(dpi=120).save(OUT/f"{item['doc_id']}-cover.png")
        if index==1:pdf[6].get_pixmap(dpi=120).save(OUT/f"{item['doc_id']}-page7.png")
    ocr.append({'doc_id':item['doc_id'],'source_sha256':item['sha256'],'parent_size':parent,
        'planned_tiles':len(boxes),'completed_responses':len(records),'axes':axes,
        'fresh_source_render_tile_plan_reproduced':boxes_reproduced,
        'uniform_grid_tile_count_at_512x256_overlap128':
            (math.ceil(max(0,parent[0]-512)/384)+1)*(math.ceil(max(0,parent[1]-256)/128)+1),
        'successful_responses_without_blocks':responses_without_blocks,
        'successful_responses':len(successful),'first_three_pages_native_text':texts,
        'original_page_operations':state['identity'].get('operations'),
        'extra_processing_forbidden':'no_ocr_requests_sent_by_this_review'})
summary['ocr_geometry_review']=ocr
(OUT/'saved_evidence_review.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
print(json.dumps({k:v for k,v in summary.items()if k not in {'details','fin04','ocr_geometry_review'}},ensure_ascii=False))
for r in ocr:print(json.dumps({k:v for k,v in r.items()if k!='first_three_pages_native_text'},ensure_ascii=False))
