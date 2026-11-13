from pathlib import Path
import hashlib,json,os,sys,shutil
ROOT=Path(__file__).resolve().parents[2];OUT=Path(__file__).resolve().parent;sys.path.insert(0,str(ROOT));os.environ.update(PYTHON_DOTENV_DISABLED='1',DATABASE_URL='',GOVBUDGET_TEST_DATABASE_URL='')
from src.services.truth_grouping import canonical_truth_group
from scripts.evaluate_golden_corpus import _truth_group
from scripts.run_zc07_acceptance import validate_dataset_manifest,validate_e2e_evidence,_evaluate_exact_location,_frozen_scan_manifest,_authenticated_reference
ids=['DOC-001-income','DOC-001-expense'];groups=[_truth_group({'truth_id':x}) for x in ids];assert len(set(groups))==2
fixture=OUT/'acceptance_fixture';doc=fixture/'corpus/DOC-001';doc.mkdir(parents=True,exist_ok=True)
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
scan=OUT/'scan_guard_fixture'
for name in ['synthetic_scan.pdf','attestations.json','review.record']:shutil.copyfile(scan/name,doc/name)
golden={'sha256':sha(doc/'synthetic_scan.pdf'),'labels':[{'annotation_id':x,'truth_id':x,'label':'defect','page':1,'rule_id':'C-001'} for x in ids]}
(doc/'golden.json').write_text(json.dumps(golden));manifest=fixture/'dataset.json';manifest.write_text(json.dumps({'schema_version':'zc07.dataset.v1','documents':[{'doc_id':'DOC-001','split':'blind','document_type':'final','organization_type':'unit','source_type':'scanned'}]}))
quota=validate_dataset_manifest(manifest,fixture);assert quota['defect_groups_by_split']['blind']==2
mixed=[{'annotation_id':k,'truth_id':k,'label':k,'truth_region':{'page':1,'bbox':[1,1,2,2]}} for k in ['defect','acceptable','manual_review']];exact=_evaluate_exact_location([{'golden':{'labels':mixed},'report':{'matched':[]}}],fixture);assert exact['truth_total']==1
unlinked=_frozen_scan_manifest(golden,fixture,'DOC-001');linked=_frozen_scan_manifest({**golden,'scan_attestations_file':'attestations.json','scan_attestations_sha256':sha(doc/'attestations.json')},fixture,'DOC-001');good=json.loads((OUT/'scan_guard_probe.json').read_text())['cases']['good_only']['references_after_guard'][0]
assert unlinked is None and linked is not None
assert _authenticated_reference(good,good['file_hash'],doc/'synthetic_scan.pdf',linked)
result={'scope':'synthetic contract checks only; not business metrics','english_ids_groups':groups,'quota_defect_groups':quota['defect_groups_by_split']['blind'],'narrow_legacy_multi_evidence_group':[canonical_truth_group({'truth_id':x}) for x in ['T4a','T4b','T4income']],'exact_location_truth_total':exact['truth_total'],'template_dataset':validate_dataset_manifest(ROOT/'docs/acceptance_templates/dataset.template.json',ROOT)['status'],'template_e2e':validate_e2e_evidence(ROOT/'docs/acceptance_templates/e2e.template.json',ROOT)['status'],'golden_template_missing_scan_fields': [k for k in ['scan_attestations_file','scan_attestations_sha256'] if k not in json.loads((ROOT/'docs/acceptance_templates/golden.template.json').read_text())], 'unlinked_certificate_registry':unlinked,'linked_fixture_scan_authentication':True}
(OUT/'acceptance_contract_probe.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False))
