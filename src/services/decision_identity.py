"""人工决定的原件依据身份；模型措辞和本次运行编号不是继承依据。"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any, Mapping


def _text(value: Any) -> str:
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value or '')))


def decision_identity(payload: Mapping, issue: Mapping) -> dict | None:
    """仅为原件证据完整、检查身份明确的发现生成可继承身份。

    不按金额相同、标题相似或模型置信度配对；原件、范围、检查、操作数或
    模型结构化数值声明变化都会换身份。证据不足的历史决定仍留在历史中。
    """
    file_hash = str(payload.get('checksum') or '')
    verification = issue.get('evidence_verification') or {}
    if (not re.fullmatch(r'[a-f0-9]{64}', file_hash)
            or verification.get('status') != 'verified'
            or verification.get('source_identity_complete') is not True
            or issue.get('evidence_status') not in {'complete', 'verified'}):
        return None
    anchors = []
    evidence = issue.get('evidence') or []
    if not evidence:
        return None
    for ref in evidence:
        if not isinstance(ref, Mapping):
            return None
        page = ref.get('physical_page') or ref.get('page')
        source = ref.get('source_cell_id') or ref.get('source_block_id')
        quote = _text(ref.get('quote') or ref.get('text_snippet') or ref.get('text'))
        if (ref.get('file_hash') != file_hash or type(page) is not int or page < 1
                or not source or not quote or ref.get('source_identity_status') != 'verified'
                or ref.get('verification_status') != 'verified'
                or ref.get('location_level') not in {'exact_span', 'exact_cell'}):
            return None
        anchors.append((page, str(source), quote))
    location = issue.get('location') or {}
    rule = str(issue.get('rule_id') or issue.get('rule') or '')
    ai = str(issue.get('source') or '') == 'ai'
    check = _text(issue.get('check'))
    if ai:
        # AI-TEXT-001 等尾号只是本次返回顺序；业务检查字段须逐字一致。
        if not check:
            return None
        rule = re.sub(r'^(AI-(?:TEXT|DESC|XSEC))-\d+$', r'\1', rule)
        relations = []
    else:
        relations = sorted({str(value) for value in [
            issue.get('check_relation_id'), location.get('check_relation_id'),
            *(issue.get('check_relation_ids') or []), *(location.get('check_relation_ids') or [])
        ] if value})
        if not relations:
            return None
    result = payload.get('result') or {}
    meta = result.get('meta') or {}
    year = payload.get('report_year') or payload.get('fiscal_year')
    organization = _text(payload.get('organization_name'))
    if not organization or type(year) is not int:
        return None
    operand_fields = ('table_code', 'subject_code', 'indicator', 'budget_stage', 'value', 'role')
    operands = [tuple(_text(ref.get(key)) for key in operand_fields)
                for ref in location.get('operand_refs') or [] if isinstance(ref, Mapping)]
    claims = {key: _text(issue.get(key)) for key in ('expected', 'actual', 'difference', 'table_or_section')}
    # 没有结构化判断对象时，不能把“相同原句、相同泛化检查名”当成相同结论。
    # 这类历史记录只接受逐字相同的判断；有 expected/actual 的才允许模型换措辞。
    unstructured_claim = _text(issue.get('message') or issue.get('title')) if ai and not (
        claims['expected'] and claims['actual']) else None
    if ai and not claims['expected'] and not claims['actual'] and not unstructured_claim:
        return None
    basis = dict(source_hash=file_hash, organization=organization, year=year,
                 kind=payload.get('report_kind'), rule=rule, check=check,
                 severity=issue.get('severity'), anchors=sorted(set(anchors)), relations=relations,
                 operands=sorted(set(operands)),
                 claims=claims, unstructured_claim=unstructured_claim,
                 policy=meta.get('rule_policy_version'), registry=meta.get('rule_registry_version'))
    digest = hashlib.sha256(json.dumps(basis, ensure_ascii=False, sort_keys=True,
                                      separators=(',', ':')).encode()).hexdigest()
    return {'schema_version': 1, 'digest': digest, 'basis': basis}


def valid_decision_identity(value: Any) -> bool:
    if not isinstance(value, Mapping) or value.get('schema_version') != 1 or not isinstance(value.get('basis'), Mapping):
        return False
    digest = hashlib.sha256(json.dumps(value['basis'], ensure_ascii=False, sort_keys=True,
                                      separators=(',', ':')).encode()).hexdigest()
    return value.get('digest') == digest
