"""固定来源回归；金额/科目真值只读冻结原页证据，单位真值由封面原字符证明。"""
from collections import Counter
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import unicodedata

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
FROZEN = ROOT / 'outputs/shanghai_2024_new9_acceptance_20261016'


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def compact(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value or '')))


def load_comparator():
    spec = importlib.util.spec_from_file_location('frozen_source_character_comparator', FROZEN / 'compare_native_identity.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.BASE = BASE
    module.FROZEN = FROZEN
    return module


def build_cover_truth():
    """先构造并验证来源，仅后续 compare_owner 才读取被测画像或事实。"""
    import pdfplumber
    expected = {r['doc_id']: r['source_cover_owner'] for r in read(FROZEN / 'profile_identity_verification.json')['records']}
    records = []
    for doc in read(FROZEN / 'inventory.json'):
        path = FROZEN / 'documents' / doc['doc_id'] / 'source.pdf'
        assert hashlib.sha256(path.read_bytes()).hexdigest() == doc['sha256']
        owner = expected[doc['doc_id']]
        with pdfplumber.open(path) as pdf:
            chars = pdf.pages[0].chars
            projection = []
            for index, char in enumerate(chars):
                projection.extend((text, index) for text in compact(char['text']))
            text = ''.join(item[0] for item in projection)
            assert text.count(compact(owner)) == 1, (doc['doc_id'], text)
            start = text.index(compact(owner))
            native_indices = sorted(set(index for _, index in projection[start:start + len(compact(owner))]))
            proof = [dict(index=index, text=chars[index]['text'], bbox=[chars[index][k] for k in ('x0', 'top', 'x1', 'bottom')]) for index in native_indices]
            assert compact(''.join(char['text'] for char in proof)) == compact(owner)
            records.append(dict(doc_id=doc['doc_id'], file_hash=doc['sha256'], physical_page=1,
                                expected_owner=owner, source_characters=proof,
                                original_cover_text=pdf.pages[0].extract_text() or '',
                                independent_of_tested_profile_and_facts=True))
    result = dict(truth_basis='Frozen original PDF cover plus independently recorded cover owner; verified against native PDF characters',
                  reads_tested_facts=False, records=records)
    write(BASE / 'source_cover_owner_truth.json', result)
    return {r['doc_id']: r for r in records}


def owner_from_profile(profile):
    if not isinstance(profile, dict):
        return None
    field = profile.get('organization_name')
    if isinstance(field, dict):
        return field.get('value')
    return field or profile.get('organization')


def compare_owner(phase, truth):
    parser_results = {r['doc_id']: r for r in read(phase / 'parser_results.json')} if (phase / 'parser_results.json').exists() else {}
    result = []
    for doc in read(phase / 'inventory.json'):
        location = phase / 'documents' / doc['doc_id']
        facts = read(location / 'facts.json')
        expected = truth[doc['doc_id']]['expected_owner']
        profile = read(location / 'profile.json') if (location / 'profile.json').exists() else None
        public_profile = owner_from_profile(profile)
        if not profile and doc['doc_id'] in parser_results:
            public_profile = parser_results[doc['doc_id']].get('organization')
        rows = [dict(fact_uid=fact.get('fact_uid'), actual=fact.get('org_name'), expected=expected)
                for fact in facts if compact(fact.get('org_name')) != compact(expected)]
        result.append(dict(doc_id=doc['doc_id'], expected_owner=expected, profile_owner=public_profile,
                           profile_available=profile is not None or doc['doc_id'] in parser_results,
                           profile_matches_source=compact(public_profile) == compact(expected) if profile is not None or doc['doc_id'] in parser_results else None,
                           facts=len(facts), fact_owners=sorted({str(f.get('org_name')) for f in facts}),
                           owner_mismatches=rows, all_fact_owners_match_source=not rows))
    summary = dict(documents=len(result), source_cover_characters_verified=True, tested_outputs_used_as_truth=False,
                   facts=sum(r['facts'] for r in result), fact_owner_mismatches=sum(len(r['owner_mismatches']) for r in result),
                   affected_documents=sum(bool(r['owner_mismatches']) for r in result),
                   profiles_available=sum(r['profile_available'] for r in result),
                   profile_owner_mismatches=sum(r['profile_matches_source'] is False for r in result), records=result)
    write(phase / 'source_owner_comparison.json', summary)
    return summary


def intersection_over_union(left, right):
    intersection = max(0, min(left[2], right[2]) - max(left[0], right[0])) * max(0, min(left[3], right[3]) - max(left[1], right[1]))
    areas = [(b[2] - b[0]) * (b[3] - b[1]) for b in (left, right)]
    return intersection / (sum(areas) - intersection) if sum(areas) > intersection else 0


def region_comparison(phase):
    """空金额续页仍属于物理区域分母；按来源页/网格范围核验角色，不按金额选表。"""
    records = []
    artifacts = {d['doc_id']: read(phase / 'documents' / d['doc_id'] / 'artifact.json') for d in read(phase / 'inventory.json')}
    for expected in read(FROZEN / 'source_region_overlay.json')['records']:
        if expected['doc_id'] not in artifacts:
            continue
        matches = [t for t in artifacts[expected['doc_id']]['physical_fragments']
                   if t['page_number'] == expected['page'] and t.get('bbox')
                   and intersection_over_union(t['bbox'], expected['bbox']) > .85]
        expected_code = 'FIN_' + expected['source_core_code'] + '_'
        # 续页物理表允许保留 UNKNOWN 身份；其明确逻辑表成员关系承担角色，不改原始网格。
        def effective_roles(table):
            return sorted({table['table_code']} | {logical['table_code'] for logical in artifacts[expected['doc_id']]['logical_tables']
                           if table['fragment_uid'] in logical.get('fragment_uids', []) and not logical.get('identity_conflict')})
        exact = len(matches) == 1 and any(code.startswith(expected_code) for code in effective_roles(matches[0]))
        records.append(dict(doc_id=expected['doc_id'], physical_page=expected['page'], source_table_index=expected['table_index'],
                            expected_core_code=expected['source_core_code'], source_bbox=expected['bbox'],
                            source_caption=expected['source_caption'], matches=[dict(fragment_uid=t['fragment_uid'], table_code=t['table_code'],
                                                                                     bbox=t['bbox'], effective_roles=effective_roles(t), iou=intersection_over_union(t['bbox'], expected['bbox'])) for t in matches],
                            role_matches_source=exact))
    result = dict(source_regions=len(records), recognized_roles=sum(r['role_matches_source'] for r in records),
                  unresolved_regions=[r for r in records if not r['role_matches_source']],
                  tested_outputs_used_as_truth=False, records=records)
    write(phase / 'source_region_comparison.json', result)
    return result


def blank_continuation_source_proof():
    import pdfplumber
    doc_id = 'SH2024N9_04'
    oracle = read(FROZEN / 'documents' / doc_id / 'source_oracle.json')
    page = oracle['pages'][10]
    table = page['tables'][0]
    with pdfplumber.open(FROZEN / 'documents' / doc_id / 'source.pdf') as pdf:
        chars = pdf.pages[10].chars
        cells = []
        for row, boxes in enumerate(table['cell_boxes']):
            for column, box in enumerate(boxes):
                if box is None:
                    continue
                glyphs = [dict(index=index, text=c['text'], bbox=[c[k] for k in ('x0', 'top', 'x1', 'bottom')]) for index, c in enumerate(chars)
                          if str(c['text']).strip() and box[0] <= (c['x0'] + c['x1']) / 2 < box[2] and box[1] <= (c['top'] + c['bottom']) / 2 < box[3]]
                cells.append(dict(row=row, column=column, source_grid_bbox=box, original_native_characters=glyphs,
                                  source_role='amount_column_blank' if column in (2, 5) else 'source_label_or_code'))
    money = [r for r in cells if r['source_role'] == 'amount_column_blank']
    assert len(money) == 4 and all(not r['original_native_characters'] for r in money)
    code_cell = next(r for r in cells if r['row'] == 0 and r['column'] == 3)
    assert ''.join(c['text'] for c in code_cell['original_native_characters']) == '31099'
    result = dict(doc_id=doc_id, physical_page=11, source_core_role='06', source_only=True,
                  reason='原页连续基本支出表的相同六列网格；31099处于经济分类编码列，四个金额格无原生金额字符。',
                  amount_blanks=4, literal_amounts=0, code_31099_is_not_amount=True, blanks_must_not_be_filled_zero=True, cells=cells)
    write(BASE / 'source_blank_fin06_continuation.json', result)
    return result


def compare(phase, verify_pdf_characters=False, before=None):
    module = load_comparator()
    cover_truth = build_cover_truth()
    blank_continuation_source_proof()
    _, summary = module.compare_phase(phase, verify_pdf_characters=verify_pdf_characters)
    owner = compare_owner(phase, cover_truth)
    regions = region_comparison(phase)
    summary.update(source_owner_mismatches=owner['fact_owner_mismatches'], source_profile_owner_mismatches=owner['profile_owner_mismatches'],
                   source_core_regions=regions['source_regions'], recognized_core_regions=regions['recognized_roles'])
    write(phase / 'native_identity_comparison/totals.json', summary)
    if before:
        diff = module.regression_diff(before, phase)
        assert not diff['regressions'], '原先正确格出现回退'
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('phase')
    parser.add_argument('--verify-pdf-characters', action='store_true')
    parser.add_argument('--before')
    args = parser.parse_args()
    phase = Path(args.phase)
    if not phase.is_absolute():
        phase = BASE / phase
    before = Path(args.before) if args.before else None
    if before and not before.is_absolute():
        before = BASE / before
    print(json.dumps(compare(phase, args.verify_pdf_characters, before), ensure_ascii=False))
