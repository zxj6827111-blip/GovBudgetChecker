"""usage: queue_counts.py <code_root> <status_dir> <out.json>  — 计算每份材料的 必须处理/参考 与条目清单。"""
import gzip, json, sys, re
from pathlib import Path
code_root, status_dir, out = sys.argv[1:4]
sys.path.insert(0, code_root)
from src.schemas.review_report import review_queue_counts
from src.services.material_detail_query_service import build_coverage
from src.services.review_lifecycle_service import blocking_coverage_obligations
from src.services.review_problem_set import collect_review_problems
try:
    from src.services.review_lifecycle_service import cascaded_coverage_obligations
except ImportError:
    cascaded_coverage_obligations = None
res = {}
for path in sorted(Path(status_dir).glob('*.status.json.gz')):
    sid = path.name.split('.')[0]
    d = json.load(gzip.open(path, 'rt'))
    result = d.get('result') or {}
    if 'rule_findings' not in result:
        result = dict(result); result['rule_findings'] = (result.get('issues') or {}).get('all') or []
    report = d.get('review_report') or {}
    counts = review_queue_counts(report)
    cov = build_coverage((result.get('meta') or {}).get('obligation_coverage'))
    problems = collect_review_problems(job_uuid=sid, ai_findings=result.get('ai_findings'), rule_findings=result.get('rule_findings'),
        merged_result=result.get('merged'), structured_ingest=d.get('structured_ingest'), result=result, ignored_issue_ids=[])
    vis = {p.issue_id for p in problems}
    blocking = blocking_coverage_obligations(list(cov.items), result, visible_issue_ids=vis) if cov.available else []
    cascaded = cascaded_coverage_obligations(list(cov.items), result, visible_issue_ids=vis) if (cov.available and cascaded_coverage_obligations) else {}
    norm = lambda m: re.sub(r'^[一二三四五六七八九十百]+、', '', re.sub(r'\s+', '', m or ''))
    formal = [ (str(i.get('rule_id') or i.get('rule') or ''), str(i.get('severity')), norm(i.get('message') or i.get('title'))[:160])
               for i in (report.get('issues') or []) if str(i.get('severity') or '').lower() in ('critical','fatal','error','high','warn','warning','medium','low') and i.get('evidence_status') != 'degraded_missing_evidence']
    tips = [ (str(i.get('rule_id') or ''), str(i.get('severity')), norm(i.get('message') or i.get('title'))[:160]) for i in [*(report.get('issues') or []), *(report.get('tips') or [])] if (i.get('rule_id'), i.get('severity'), norm(i.get('message') or i.get('title'))[:160]) and str(i.get('severity') or '').lower() not in ('critical','fatal','error','high','warn','warning','medium','low')]
    res[sid] = dict(required=counts['queue_required_total'] + len(blocking), reference=counts['queue_reference_total'],
                    formal=len(formal), obligations=[i.obligation_id for i in blocking],
                    cascaded={k: [i.obligation_id for i in v] for k, v in cascaded.items()},
                    formal_items=formal, tip_items=tips, status=d.get('status'))
Path(out).write_text(json.dumps(res, ensure_ascii=False, indent=1))
print(len(res), 'docs')
