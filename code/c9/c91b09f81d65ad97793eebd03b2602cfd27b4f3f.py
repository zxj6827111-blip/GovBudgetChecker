from pathlib import Path
import sys,json,gzip,collections,re,hashlib
ROOT=Path('/Volumes/workSSD/software development/GovBudgetChecker');OUT=ROOT/'outputs/comprehensive_review_closure_20261109';CODE=ROOT/'artifacts/comprehensive-review-closure-20261109';sys.path.insert(0,str(CODE))
from src.schemas.review_report import build_review_report,review_queue_counts
counts=collections.Counter();rules=collections.Counter();families=collections.Counter();cases=[];jobs=[]
def family(item):
 s=json.dumps(item,ensure_ascii=False)
 if 'subject_conflict' in s or '主体' in s and '不一致' in s:return 'subject_conflict'
 if '找不到同名科目' in s or 'text_binding_failed' in s:return 'subject_binding'
 if '空白' in s or '横线' in s:return 'blank_semantics'
 if '列' in s and ('绑定' in s or '身份' in s) or '缺少可唯一核对的事实' in s:return 'column_or_operand_identity'
 if '没有可用' in s or '未识别到' in s or '缺少' in s and '表' in s:return 'table_or_narrative_missing'
 if '模型' in s:return 'semantic_audit'
 return 'other'
for frozen in sorted((OUT/'frozen').glob('*/status.json.gz')):
 job=frozen.parent.name;s=json.loads(gzip.decompress(frozen.read_bytes()));result=s.get('result') or {};meta=result.get('meta') or {};rep=build_review_report(s);qc=review_queue_counts(rep,s.get('ignored_issue_ids',[]))
 unresolved=meta.get('unresolved_rules') or meta.get('insufficient_data_rules') or []
 for u in unresolved:
  if isinstance(u,dict):rules[u.get('rule_id','?')]+=1
 for item in rep['manual_review']:
  counts[item['record_type']]+=1
  if item['record_type']!='unfinished_check':continue
  fam=family(item);families[fam]+=1
  cases.append(dict(job=job,filename=s.get('filename'),job_status=s.get('status'),family=fam,**item))
 jobs.append(dict(job=job,filename=s.get('filename'),status=s.get('status'),revision=s.get('analysis_revision'),kind=s.get('report_kind'),year=s.get('report_year'),checksum=s.get('checksum'),use_ai=s.get('use_ai_assist'),queue=qc,unresolved_rules=unresolved,review_reasons=s.get('review_reasons',[]),meta_keys=list(meta)))
(OUT/'baseline_jobs.json').write_text(json.dumps(jobs,ensure_ascii=False,indent=2));(OUT/'baseline_unfinished.json').write_text(json.dumps(cases,ensure_ascii=False,indent=2))
summary=dict(jobs=len(jobs),statuses=dict(collections.Counter(j['status'] for j in jobs)),manual_types=dict(counts),unfinished_families=dict(families),unresolved_rules=dict(rules))
(OUT/'baseline_inventory_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2));print(json.dumps(summary,ensure_ascii=False))
