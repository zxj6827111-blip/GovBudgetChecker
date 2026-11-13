"""只生成复核说明，不修改验收工具、指标或样本。运行目录为隔离实施工作区。"""
import collections
import csv
import gzip
import json
import re
from pathlib import Path
import sys

sys.path.insert(0, str(Path.cwd()))
from scripts.verify_intake_remediation import name_key, _real_facts

ROOT = Path.cwd()
BASE = Path('/Volumes/workSSD/software development/GovBudgetChecker/outputs/intake_remediation_20261024/baseline')
CURRENT = ROOT / 'outputs/intake_remediation_20261024/batch1_review_closure'
OUT = ROOT / 'outputs/intake_remediation_20261024/review_closure'
OUT.mkdir(exist_ok=True)


def read(folder, kind):
    with gzip.open(folder / f'{kind}.jsonl.gz', 'rt') as handle:
        return {row['file']: row for row in map(json.loads, handle)}


bi, ci = read(BASE, 'intake'), read(CURRENT, 'intake')
bp, cp = read(BASE, 'parse'), read(CURRENT, 'parse')
be, ce = read(BASE, 'e2e'), read(CURRENT, 'e2e')
verdicts = json.loads((CURRENT / 'acceptance_report.json').read_text())['verdicts']
records = []
pending_path = ROOT / 'outputs/intake_remediation_20261024/review_new_pending_cells.json'
pending_cells = {row['file']: row['cells'] for row in json.loads(pending_path.read_text())} if pending_path.exists() else {}
source_proof_path = ROOT / 'outputs/intake_remediation_20261024/review_classification_source_proof.json'
source_proofs = {row['file']: row for row in json.loads(source_proof_path.read_text())} if source_proof_path.exists() else {}


def add(kind, file, before, after, reason, evidence, status='待验收方复核'):
    records.append(dict(kind=kind, file=file, before=before, after=after,
                        explanation=reason, review_status=status, evidence=evidence))


def binding(kind, file):
    old, new = bi[file], ci[file]
    decision = new.get('binding_decision') or {}
    reasons = decision.get('reasons') or []
    explanation = '；'.join(f"{r['code']}：{'；'.join(r['unmet'])}" for r in reasons)
    if new.get('bound'):
        explanation = ('原件主体、明确本级口径与已确认父子关系共同支持组合绑定，未记录为裸部门名和本级名称等价'
                       if decision.get('match_basis') == 'combined_source_scope' else
                       '原件支持绑定；名称规则=' + ','.join(sorted({r['rule'] for r in decision.get('name_comparisons', []) if r.get('equivalent')})))
    add(kind, file, {'organization': old.get('bound'), 'level': old.get('bound_level'),
                     'filename_org': old.get('filename_org')},
        {'organization': new.get('bound'), 'level': new.get('bound_level'),
         'filename_org': new.get('filename_org')},
        explanation + '。文件名仅为候选依据；此记录提供原件页码和目录判定，不能代替验收方核对原页。',
        decision)


for file, new in ci.items():
    old = bi.get(file, {})
    originally_equal = old.get('bound') and name_key(old['bound']) == name_key(old.get('filename_org'))
    if originally_equal and not new.get('bound'):
        binding('A5b', file)
    if originally_equal and new.get('bound') and name_key(new['bound']) != name_key(old['bound']):
        binding('A5a', file)
    if not new.get('bound'):
        binding('A6', file)
    elif name_key(new['bound']) != name_key(new.get('filename_org')):
        binding('A7', file)
    if new.get('first_page_has_text') and not new.get('cover_org_name'):
        import pymupdf
        path = Path.home() / 'Desktop/决算测试上海市' / file
        with pymupdf.open(path) as pdf:
            cover = pdf[0].get_text()
        add('B1', file, old.get('cover_org_name'), new.get('cover_org_name'),
            '首页存在文字层但未形成可确认的封面主体；保留首页全文及后续原件证据。主体抽取效率尚有缺口，不能只凭文件名补齐。',
            {'first_page_text': cover, 'binding_decision': new.get('binding_decision')})
    if old.get('cover_org_name') and new.get('cover_org_name') and name_key(old['cover_org_name']) != name_key(new['cover_org_name']):
        add('B2', file, old['cover_org_name'], new['cover_org_name'],
            source_proofs[file]['explanation'] if file in source_proofs else '封面抽取发生变化，按原件证据逐例复核，不将变化等同正确。',
            {'decision': new.get('binding_decision'), 'source_proof': source_proofs.get(file)})
    if new.get('report_year') and int(new['report_year']) != int(file[:4]):
        add('B5', file, int(file[:4]), new['report_year'], '目录年份不是真值，保留原件标题/页码交验收。', new.get('binding_decision'))

for file, new in cp.items():
    old = bp.get(file)
    if not old or 'error' in old or 'error' in new:
        continue
    delta = {key: int(new.get(key) or 0) - int(old.get(key) or 0)
             for key in ('amount_cells', 'admitted', 'pending', 'fact_count', 'identity_incomplete')}
    common = {'before_pending_reasons': old.get('pending_reasons'), 'after_pending_reasons': new.get('pending_reasons'),
              'before_rejections': old.get('rejections'), 'after_rejections': new.get('rejections'),
              'fragments_before': old.get('fragments'), 'fragments_after': new.get('fragments')}
    if delta['admitted'] < 0 or delta['pending'] > 0:
        literal_note = ''
        if delta['admitted'] == 0 and delta['amount_cells'] == delta['pending'] and delta['pending'] > 0:
            literal_note = '准入数未下降；原来未列入分母的非规范数值/分离数字现在纳入待复核，不能把这类漏计修正解释成已确认事实丢失。'
        if file in pending_cells:
            common['current_pending_cells'] = pending_cells[file]
            literal_note += ' 原格例：' + '；'.join(f"第{cell.get('page')}页 {cell.get('expected_indicator')} 原文{cell.get('raw_text')!r}，{cell.get('reason')}" for cell in pending_cells[file][:4])
        add('C3', file, {k: old.get(k) for k in delta}, {k: new.get(k) for k in delta},
            f"准入变化{delta['admitted']:+d}格，待复核变化{delta['pending']:+d}格。当前回执原因："
            + json.dumps(new.get('pending_reasons'), ensure_ascii=False)
            + '。' + literal_note + '新版本将词元、当前版本/列身份门禁落实到金额格；仍需原件核对，不承诺数量下降代表准确率提升。', common)
    lost = _real_facts(old) - _real_facts(new)
    if lost:
        counterparts = {}
        for fact in lost:
            identity = fact.rsplit('|', 1)[0]
            counterparts[fact] = [item for item in new.get('facts', []) if item.rsplit('|', 2)[0] == identity]
        add('C4', file, list(lost.elements()), counterparts,
            source_proofs[file]['explanation'] if file in source_proofs else
            '冻结比较口径下旧“可用”事实减少；逐项列出旧值及当前同身份事实。列/来源/词元未确认的值应保留未知，缺失而无依据的事实须验收方回原件核对。',
            {**common, 'source_proof': source_proofs.get(file)})
    if new.get('fact_count') and (new.get('identity_incomplete') or 0) >= .9 * new['fact_count']:
        add('C5', file, old.get('identity_incomplete'), new.get('identity_incomplete'),
            '大部分事实仍缺主体/年度/资金/阶段/实例等必需身份；不因只有口径待定而阻断。须核对当前拒绝原因及原页。', common)
    if new.get('arithmetic_conflicts', 0):
        add('C9', file, old.get('arithmetic_conflicts'), new['arithmetic_conflicts'],
            '仅允许独立结构证据支持的降级；原件算术异常本身不足以证明错列。结构证据另列，缺少证据不算通过。', common)

cases = json.loads((ROOT / 'scripts/intake_remediation_cases.json').read_text())
for case in cases['e2e']:
    if not case.get('control') or case['file'] not in ce:
        continue
    file = case['file']
    old = {(x['rule_id'], x['message']) for x in be.get(file, {}).get('issues', [])}
    new = {(x['rule_id'], x['message']) for x in ce[file].get('issues', [])}
    if old != new:
        strip = lambda pair: (pair[0], re.sub(r'^[一二三四五六七八九十百]+、', '', pair[1]))
        old_sem, new_sem = {strip(pair) for pair in old}, {strip(pair) for pair in new}
        removed, added = sorted(old_sem - new_sem), sorted(new_sem - old_sem)
        reason = f'剔除展示序号后，原文问题未变{len(old_sem & new_sem)}条，实际消失{len(removed)}条、实际新增{len(added)}条。'
        if any(rule in {'CMM-007', 'CMM-018'} for rule, message in removed):
            reason += '跨年度比较/本部门本单位用语依赖汇总口径；原件口径尚无定论，因此转为未完成，未计为通过；需确认范围后复检。'
        if any(rule == 'V33-240' for rule, message in added + removed):
            reason += '收入表新增已明确列的纵向检查；空白未批准按零时相关关系仍不足，已知数在统一舍入/容差内不生成未知格算术问题，超阈值才保留人工线索。'
        if any(rule == 'V33-241' for rule, message in removed):
            reason += '支出表旧横向取整提示依赖项目/上缴/经营/附属补助等分项：部分原格空白不能补零；相应关系转不足，V33-005/120已明确列的提示继续保留。'
        proof_path = ROOT / 'outputs/intake_remediation_20261024/control_v241_review/proof.json'
        detail_proof = json.loads(proof_path.read_text()) if '国有企业绩效评价中心' in file and proof_path.exists() else None
        add('D控制样本', file, sorted(old - new), sorted(new - old),
            reason + '完整差异及回执均保留，仍交验收方核原页，不自动将REVIEW改为PASS。',
            {'review_reasons_before': be.get(file, {}).get('review_reasons'),
             'review_reasons_after': ce[file].get('review_reasons'), 'same_after_renumbering': sorted(old_sem & new_sem),
             'semantic_removed': removed, 'semantic_added': added, 'native_operand_proof': detail_proof})

summary = collections.Counter(r['kind'] for r in records)
payload = {'limits': ['逐例记录技术原因和原件引用，尚不代表独立业务验收通过', '所有REVIEW保持待验收方确认，不调整冻结阈值或真值'],
           'counts': dict(summary), 'records': records}
(OUT / 'review_details.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2))
with (OUT / 'review_details.csv').open('w', encoding='utf-8-sig', newline='') as handle:
    writer = csv.DictWriter(handle, fieldnames=['kind', 'file', 'before', 'after', 'explanation', 'review_status', 'evidence'])
    writer.writeheader()
    for row in records:
        writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for k, v in row.items()})
text = ['# 第一批 REVIEW 逐例说明', '', '每例的完整原件引用、目录版本、旧/新事实和问题差异保存在同目录 JSON/CSV。以下保留人工验收状态，不把技术原因说明当作业务正确性证明。', '']
for category in sorted(summary):
    text += [f'## {category}（{summary[category]}例）', '']
    for row in [r for r in records if r['kind'] == category]:
        text += [f"- `{row['file']}`：{row['explanation']} 状态：{row['review_status']}。", '']
text += ['## 比较项索引', '']
for row in verdicts:
    if row['verdict'] == 'REVIEW':
        text += [f"- {row['item']}：{row['actual']}。以上对应前缀逐例说明；汇总计数只作观察。", '']
(OUT / 'review_details.md').write_text('\n'.join(text))
print(dict(summary))
