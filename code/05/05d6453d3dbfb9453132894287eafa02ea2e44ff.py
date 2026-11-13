import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace
import pymupdf

ROOT=Path(__file__).resolve().parents[1]
CAND=Path('/Volumes/workSSD/software development/gbc-worktrees/unified-remediation-20261106')
EV=CAND/'outputs/unified_review_remediation_20261106'
sys.path.insert(0,str(CAND))
from scripts import effect_acceptance_v3 as h
from src.engine.narrative_zero_evidence import narrative_zero_sources
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,sort_keys=True,indent=2))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
norm=lambda t:re.sub(r'\s+','',t)
rows=h.load_collection(EV/'final_collection')
truth={};proofs={'schema':'independent-effect-evidence-v2','review_version':'independent-r05-20261106-r2',
    'candidate_commit':'69884e9c1d7fee91e530b952b7e5b1935802e987','samples':[],'decisions':[],
    'scope':'Only eight original Shanghai office purchase-final zero relations. No global R01 or whole candidate approval.'}
verifications=[]
for sid in ['RB-2023-U04','RB-2025-U02']:
    row=rows[sid];pdf=next((EV/'kit_candidate/pdfs'/sid).glob('*.pdf'))
    assert sha(pdf)==row['sha256']
    with pymupdf.open(pdf)as doc:
        texts=[p.get_text()for p in doc];p=doc[13];tables=p.find_tables().tables
        table=next(t for t in tables if t.col_count==12);grid=table.extract()
        assert grid[-2]==['预算数','决算数']*6 and grid[-1][7]==''
        box=list(table.rows[-1].cells[7]);quote='公务用车购置支出为0万元'
        assert quote in norm(texts[20])
        cover=norm(texts[0]);assert str(row['source_binding']['year'])in cover
        assert row['source_binding']['organization']in norm(''.join(texts))
        truth[sid]={'pdf_sha256':sha(pdf),'year':row['source_binding']['year'],
            'organization':row['source_binding']['organization'],'source_statement':{'page':21,'quote':quote,'file_hash':sha(pdf)},
            'independent_blank_cell':{'page':14,'bbox':box,'indicator':'vehicle_purchase','stage':'决算','original_text':''},
            'budget_purchase_original_text':grid[-1][6],'original_grid_last_row':grid[-1],
            'truth_limit':'原格仍为空；叙事零仅证明购置决算，不证明年初预算。'}
        doc_obj=SimpleNamespace(page_texts=texts,report_kind='final',fact_context=SimpleNamespace(
            org_name=row['source_binding']['organization'],fiscal_year=row['source_binding']['year']))
        zero=narrative_zero_sources(doc_obj,table_code='FIN_07_three_public',indicator='vehicle_purchase',stage='决算')
        budget=narrative_zero_sources(doc_obj,table_code='FIN_07_three_public',indicator='vehicle_purchase',stage='年初预算')
        assert zero and budget is None and {x['page']for x in zero}=={21}
    out=ROOT/'originals'/sid;out.mkdir(exist_ok=True)
    for page in [14,21]:
        subprocess.run(['pdftoppm','-f',str(page),'-l',str(page),'-scale-to','1500','-singlefile','-png',str(pdf),str(out/f'p{page}')],check=True,capture_output=True)
    targets=[r for r in row['receipts']if r['rule_id']in {'V33-224','CMM-001'}
        and r['obligation_id']in {'OBL-TXT-T7','OBL-SG-TABLE-TEXT'}and r['sub_check_id'].startswith('three_public:detail:vehicle_purchase:')]
    assert len(targets)==4
    bundle={'binding':row['source_binding'],'facts':[],'relation_contracts':[],
            'verified_blank_observations':[],'relation_proofs':[]}
    for r in targets:
        assert r['status']=='not_applicable' and not r['operand_fact_ids']
        cells=r['cell_observations'];assert cells
        for cell in cells:
            assert cell['file_hash']==sha(pdf)and cell['indicator']=='vehicle_purchase'and cell['budget_stage']=='决算'
            assert cell['physical_page']==14 and cell['raw_text']==''
            assert max(abs(a-b)for a,b in zip(cell['bbox'],box,strict=True))<.05
            if cell not in bundle['verified_blank_observations']:bundle['verified_blank_observations'].append(cell)
        key=list(h.receipt_key(r));bundle['relation_contracts'].append({'key':key,'receipt_digest':h.digest(r),'operand_identities':{}})
        bundle['relation_proofs'].append({'key':key,'receipt_digest':h.digest(r),'conclusion':'not_applicable',
            'reason':'独立核读上海所属机构原件p14购置决算原格为空，p21同年度同单位明确购置支出0万元；不借预算零，不由其他列反推。',
            'sources':[truth[sid]['source_statement']]})
        verifications.append({'sample_id':sid,'key':key,'binding':row['source_binding'],'status':r['status'],
                               'original_source_verified':True,'budget_remains_unknown':True})
    proofs['samples'].append(bundle)
truthfile=ROOT/'receipts/r05_independent_original_truth_v2.json';save(truthfile,truth)
proofs['independent_truth_sha256']=sha(truthfile)
proofsfile=ROOT/'r05_independent_evidence_v2.json';save(proofsfile,proofs)
(ROOT/'r05_independent_evidence_v2.sha256').write_text(sha(proofsfile)+'  '+proofsfile.name+'\n')
save(ROOT/'receipts/r05_eight_independent_verifications.json',verifications)
# 只消费这八项新的精确绑定，不复用旧v1授权，也不调绿其余缺证据项。
old=CAND/'outputs/unified_remediation_20261106/final_d1'
report=h.compare_collections(old,EV/'final_collection',EV/'kit_candidate',h.load_evidence(proofsfile,sha(proofsfile)))
save(ROOT/'receipts/current_with_only_r05_evidence.json',report)
print(json.dumps({'verified_relations':len(verifications),'overall':report['overall'],'problems':len(report['problems']),
    'pending_objects':len(report['pending']),'new_evidence_sha256':sha(proofsfile)},ensure_ascii=False))
