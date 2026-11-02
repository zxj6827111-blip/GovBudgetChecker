from src.services.template_scope_metrics import summarize


def test_denominator_is_independently_verified_automatic_results():
    labeled={'cohort':'random','verified_independently':True,'label_reference':'business evidence','label_version':'blind-v1','writing_truth':'unit'}
    rows=[{**labeled,'status':'confirmed','source':'template','value':'unit'},
        {**labeled,'status':'conflict','source':'unresolved','value':None},
        {'cohort':'random','status':'confirmed','source':'template','value':'unit'},
        {**labeled,'status':'confirmed','source':'human','value':'unit'}]
    metric=summarize(rows)['cohorts']['random']
    assert metric['total']==4 and metric['automatic_verified_count']==1 and metric['accuracy']==1
    assert not metric['supports_approximately_one_percent_claim']


def test_random_count_and_directed_cases_do_not_manufacture_guarantees():
    row={'cohort':'directed','verified_independently':True,'label_reference':'evidence','label_version':'v1','writing_truth':'unit','status':'confirmed','source':'template','value':'unit'}
    assert summarize([row]*300)['cohorts']['directed']['zero_error_upper_bound_95'] is None
    assert not summarize([row]*300)['cohorts']['directed']['supports_approximately_one_percent_claim']
    metric=summarize([{**row,'cohort':'random'}]*100)['cohorts']['random']
    assert .029<metric['zero_error_upper_bound_95']<.030
    assert not metric['supports_approximately_one_percent_claim']


def test_pure_body_style_can_still_have_real_declaration_conflict():
    row={'cohort':'directed','verified_independently':True,'label_reference':'independent declaration evidence',
        'label_version':'v1','style_truth':'unit','conflict_truth':True,'status':'conflict'}
    result=summarize([row])['cohorts']['directed']
    assert result['verified_conflict_labels']==1 and result['conflict_false_positive']==0 and result['conflict_false_negative']==0
