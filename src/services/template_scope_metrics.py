"""独立标注验收统计；文件名、组织节点及人工指定写法不充当真值。"""
from __future__ import annotations


def summarize(rows):
    cohorts={}
    for cohort in sorted({r.get('cohort','directed') for r in rows}):
        subset=[r for r in rows if r.get('cohort','directed')==cohort]
        labeled=[r for r in subset if r.get('verified_independently') is True and r.get('label_reference')
            and r.get('label_version') and r.get('writing_truth') in ('unit','department')]
        automatic=[r for r in labeled if r.get('status')=='confirmed' and r.get('source') in ('template','declaration')]
        errors=sum(r['value']!=r['writing_truth'] for r in automatic)
        m=len(automatic)
        conflict_labeled=[r for r in subset if r.get('verified_independently') is True and r.get('label_reference')
            and r.get('label_version') and type(r.get('conflict_truth')) is bool]
        cohorts[cohort]={'total':len(subset),'verified_writing_labels':len(labeled),'automatic_verified_count':m,
            'automatic_errors':errors,'correct':m-errors,'accuracy':(m-errors)/m if m else None,
            'verified_conflict_labels':len(conflict_labeled),'conflict_false_positive':sum(r['status']=='conflict' and not r['conflict_truth'] for r in conflict_labeled),
            'conflict_false_negative':sum(r['status']!='conflict' and r['conflict_truth'] for r in conflict_labeled),
            'insufficient':sum(r['status']=='insufficient' for r in subset),
            'zero_error_upper_bound_95':1-.05**(1/m) if m and errors==0 and cohort=='random' else None,
            'supports_approximately_one_percent_claim':bool(cohort=='random' and m>=300 and errors==0)}
    return {'cohorts':cohorts,'independent_acceptance_granted':False,
        'note':'独立标签由业务人员提供；统计不授予能力或替代独立验收。定向反例不推断总体错误率。'}
