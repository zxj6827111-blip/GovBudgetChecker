"""独立负控：只改真实采集的临时副本，绝不改业务代码或交付记录。"""
import copy
import gzip
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path('/Volumes/workSSD/software development/GovBudgetChecker')
BUSINESS = Path('/Volumes/workSSD/software development/gbc-worktrees/real-painpoint-fixes-20261104')
DELIVERY = ROOT / 'outputs/real_painpoint_effect_closeout_20261105'
BASE = ROOT / 'outputs/real_painpoint_fixes_followup_20261104/regression160/base'
CANDIDATE = DELIVERY / 'candidate_d1_final'
REPEAT = DELIVERY / 'candidate_d2_final'
spec = importlib.util.spec_from_file_location('review_effect_policy', BUSINESS / 'scripts/effect_acceptance.py')
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


def clone_collection(source, target):
    target.mkdir()
    for name in ('records', 'status'):
        (target / name).mkdir()
        for item in (source / name).glob('*.gz'):
            (target / name / item.name).symlink_to(item.resolve())
    (target / 'meta.json').write_bytes((source / 'meta.json').read_bytes())
    return target


def alter(root, kind, sample, change):
    suffix = '.status.json.gz' if kind == 'status' else '.json.gz'
    path = root / kind / (sample + suffix)
    with gzip.open(path, 'rt', encoding='utf-8') as handle:
        obj = json.load(handle)
    change(obj)
    path.unlink()  # 仅删除临时副本的符号链接，避免写入源文件。
    with gzip.open(path, 'wt', encoding='utf-8') as handle:
        json.dump(obj, handle, ensure_ascii=False)


def receipt(obj, key):
    return next(r for r in obj['receipts'] if policy.receipt_key(r) == key)


def outcome(candidate):
    return policy.compare_collections(BASE, candidate)['report']['overall']


def test_clean_collection_control():
    assert outcome(CANDIDATE) == 'PASS'
    assert policy.reproducibility(CANDIDATE, REPEAT)['verdict'] == 'PASS'


def test_unknown_fact_ids_and_false_equation_are_rejected(tmp_path):
    candidate = clone_collection(CANDIDATE, tmp_path / 'candidate')
    key = ('OBL-SG-TOTAL', 'V33-244', 'three_public:决算:total_actual:sum')
    def mutate(obj):
        r = receipt(obj, key)
        r.update(operand_fact_ids=['review_nonexistent_fact'], required_fact_ids=['review_nonexistent_fact'],
                 formula='1 = 2', disclosure_sources=[], cell_observations=[],
                 explained_by=None, incomplete_reason=None, residual_difference_wan='1')
    alter(candidate, 'records', 'RA-031', mutate)
    assert outcome(candidate) == 'FAIL', '不存在的操作数及 1=2 仍获整体 PASS'


def test_na_with_only_unsupported_sentence_is_rejected(tmp_path):
    candidate = clone_collection(CANDIDATE, tmp_path / 'candidate')
    key = ('OBL-TXT-T7', 'V33-224', 'three_public:detail:overseas:7144')
    def mutate(obj):
        r = receipt(obj, key)
        r.update(disclosure_sources=[], cell_observations=[], operand_fact_ids=[], formula=None,
                 incomplete_reason=None, explained_by='无需执行（本条没有任何来源或适用性证明）')
    alter(candidate, 'records', 'RA-031', mutate)
    assert outcome(candidate) == 'FAIL', '仅一条任意说明仍被判为有依据 N/A'


def test_source_page_and_quote_must_be_grounded(tmp_path):
    candidate = clone_collection(CANDIDATE, tmp_path / 'candidate')
    key = ('OBL-TXT-T7', 'V33-224', 'three_public:detail:overseas:7144')
    def mutate(obj):
        r = receipt(obj, key)
        r['disclosure_sources'] = [{'page': 999999, 'span': '此句是审核反例，不存在于任何原件'}]
    alter(candidate, 'records', 'RA-031', mutate)
    assert outcome(candidate) == 'FAIL', '不存在的页码和引文仍被接纳'


def test_raw_formal_findings_cannot_disappear_unobserved(tmp_path):
    candidate = clone_collection(CANDIDATE, tmp_path / 'candidate')
    def mutate(obj):
        assert len(obj['review_report']['issues']) == 4
        obj['review_report']['issues'] = []
        obj['result']['issues'] = {k: [] for k in obj['result']['issues']}
    alter(candidate, 'status', 'RA-031', mutate)
    assert outcome(candidate) == 'FAIL', '原始正式问题删除后 E8 仍 PASS，规范化记录漏取问题'


def test_input_sha_change_is_rejected(tmp_path):
    candidate = clone_collection(CANDIDATE, tmp_path / 'candidate')
    alter(candidate, 'records', 'RA-031', lambda obj: obj.update(sha256='0' * 64))
    assert outcome(candidate) == 'FAIL', '同 sample_id 换了材料哈希仍 PASS'


def test_unexplained_regression_of_existing_key_is_rejected(tmp_path):
    candidate = clone_collection(CANDIDATE, tmp_path / 'candidate')
    key = ('OBL-SG-TOTAL', 'V33-244', 'three_public:年初预算:total_actual:sum')
    def mutate(obj):
        r = receipt(obj, key)
        assert r['status'] == 'pass'
        r.update(status='insufficient_data', reason_codes=['operand_missing'], next_action='human_confirm',
                 blocked_by=[{'dimension': 'cell', 'ref': 'unproven', 'reason_code': 'operand_missing'}])
    alter(candidate, 'records', 'RA-031', mutate)
    assert outcome(candidate) == 'FAIL', '已有完成关系无归因回退仍整体 PASS'


def test_reproducibility_includes_run_and_obligation_projection(tmp_path):
    second = clone_collection(REPEAT, tmp_path / 'repeat')
    def mutate(obj):
        obj['run'].update(status='error', error='review injected error')
        obj['obligations']['OBL-SG-TOTAL']['status'] = 'insufficient_data'
        obj['obligation_summary']['completed_total'] = 0
    alter(second, 'records', 'RA-031', mutate)
    assert policy.reproducibility(CANDIDATE, second)['verdict'] == 'FAIL', '运行失败和义务/汇总差异被双跑忽略'
