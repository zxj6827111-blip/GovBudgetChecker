"""运行态仅报告恢复与存储状态；没有独立真值就没有准确率分母。"""
from __future__ import annotations


def recognition_state(artifact,snapshot_receipt=None,fact_context=None):
    resolved=list(getattr(artifact,'resolved_cells',[])or[])
    candidates=list(getattr(artifact,'cell_candidates',[])or[])
    bound=[c for c in resolved if c.resolution_status=='source_bound']
    def anchor(page,box):return (page,tuple(round(v,6)for v in box))if box else None
    resolved_keys={anchor(c.physical_page,c.bbox)for c in resolved if c.bbox}
    unresolved={anchor(c.physical_page,c.bbox)for c in resolved if c.bbox and c.resolution_status!='source_bound'}
    unresolved.update(anchor(c.physical_page,c.bbox)for c in candidates if c.extraction_method=='image_grid'
                      and c.bbox and anchor(c.physical_page,c.bbox)not in resolved_keys)
    from src.engine.fact_context import operand_rejection_reason
    blocked=[f for f in (getattr(fact_context,'facts',[])or[])if operand_rejection_reason(f,getattr(fact_context,'org_name',None))]
    for fact in blocked:
        unresolved.update(anchor(r.get('physical_page'),r.get('bbox'))for r in fact.extra_dims.get('evidence_refs',[])if r.get('bbox'))
    recovery=((getattr(artifact,'reading_assessment',{})or{}).get('recovery')or{})
    pages=list(getattr(artifact,'page_states',[])or[])
    pending=[p.page_number for p in pages if p.ocr_status in {'pending','failed'}]
    return {'version':'gbc.recognition-state.v1','source_file_hash':artifact.source_file_hash,
        'resolved_logical_cells':len(resolved),'source_bound_cells':len(bound),
        'pending_adjudication_cells':len(unresolved),'physical_cell_candidates':len(candidates),
        'pending_fact_qualifications':len(blocked),
        'pending_processing_pages':sorted(set(pending+list(recovery.get('failed_pages')or[]))),
        'storage_consistent':(snapshot_receipt or{}).get('storage_consistent'),
        'recognition_accuracy':None,'correct_auto_recovery_coverage':None,'independent_truth_denominator':None,
        'check_coverage_meaning':'规则检查完成率'}
