"""上传、重匹配与缺失关联修复共用的原件证据决策。"""
from __future__ import annotations

import re
from typing import Any, Sequence

from src.services.document_profile_resolver import detect_cover_facts, original_org_evidence
from src.services.org_name_identity import (
    _field, OrgNameIndex, identity_name, normalize_org_name, organization_name,
)


def source_scope_evidence(page_texts: Sequence[str]) -> tuple[str | None, list[dict], bool]:
    """目录/表注不能定论；明确封面与实际单位构成互相矛盾时不择一。"""
    cover = detect_cover_facts(page_texts)
    found = []
    title = normalize_org_name(cover["cover_title"])
    for scope, pattern in [("department", r"部门(?:汇总)?(?:预算|决算)|(?:预算|决算)主管部门"),
                           ("unit", r"单位(?:预算|决算)|本级|本部")]:
        if re.search(pattern, title + cover["cover_org_label"] + cover["cover_org_name"]):
            found.append({"source": "cover_label" if cover["cover_org_label"] else "cover", "page": 1,
                          "text": "\n".join(filter(None, [cover["cover_title"], cover["cover_org_label"], cover["cover_org_name"]])),
                          "scope": scope})
    for page, text in enumerate(page_texts, 1):
        if page == 1:
            continue
        compact = normalize_org_name(text)
        # 真正文须有“包括/纳入…”的范围陈述；目录里的节名不满足条件。
        for match in re.finditer(r"(?:部门决算包括|纳入[^。]{0,45}决算[^。]{0,15}(?:单位|范围)|编制范围的单位包括)[：:]?([^。]{1,700})", compact):
            clause = match.group()
            scope = None
            if re.search(r"仅(?:含|包括)?本级|只(?:含|包括)?本级|无下属", clause):
                scope = "unit"
            elif re.search(r"下属(?:行政事业)?单位决算|[、,]2[.、]|2[.、][^。]{2,80}3[.、]", clause):
                scope = "department"
            if scope:
                found.append({"source": "composition", "page": page, "text": clause, "scope": scope})
    scopes = {item["scope"] for item in found}
    return (next(iter(scopes)) if len(scopes) == 1 else None, found, len(scopes) > 1)


def decide_auto_binding(*, filename: str, page_texts: Sequence[str], directory: Sequence[Any],
                        candidates: Sequence[dict] = (), report_kind: str = "unknown") -> dict:
    evidence = original_org_evidence(page_texts, directory)
    identity_index = OrgNameIndex(directory)
    scope, scope_evidence, scope_conflict = source_scope_evidence(page_texts)
    all_evidence = [*evidence, *scope_evidence]
    suggestions = list(candidates)
    checked = [{"source": "page_scan", "page": page, "text": "已提取文字层" if str(text).strip() else "无可用文字层；未执行 OCR"}
               for page, text in enumerate(page_texts, 1)] or [
                   {"source": "filename", "page": None, "text": filename}]

    def brief(org):
        return {"id": _field(org, "id"), "name": organization_name(org), "level": _field(org, "level")}

    def defer(code, unmet, related=()):
        return {"decision": "needs_confirmation", "organization_id": None, "scope": scope,
                "evidence": all_evidence or checked,
                "reasons": [{"code": code, "evidence": all_evidence or checked,
                             "candidates": [brief(org) for org in related], "unmet": [unmet]}],
                "candidates": suggestions}

    if report_kind == "non_budget":
        return defer("non_standard_material", "原件不属于预算/决算报告")
    if not evidence:
        return defer("scan_without_text" if page_texts and not any(str(t).strip() for t in page_texts)
                     else "no_original_evidence", "缺少可核实的原件主体证据")
    if scope_conflict:
        return defer("evidence_conflict", "明确的部门汇总与单位/本级原件证据冲突")

    eligible = [org for org in directory if _field(org, "level") in {"department", "unit"}]
    matches = [org for org in eligible if any(identity_index.compare(item["text"], organization_name(org)).equivalent
                                             for item in evidence)]
    # 每一项主体证据都须检查，不能靠另一项的高分覆盖明确矛盾。
    def conflict(org):
        target = identity_name(organization_name(org))
        for item in evidence:
            value = identity_name(item["text"])
            if identity_index.compare(value, target).equivalent:
                continue
            if value and value in target:  # 抽取截断只作弱证据。
                continue
            return True
        return False

    safe = [org for org in matches if not conflict(org)]
    if matches and not safe:
        return defer("evidence_conflict", "原件主体证据与候选不一致", matches)

    departments = [org for org in safe if _field(org, "level") == "department"]
    # 当原件只写部门裸名时，本级归属只能走明确范围＋已确认父子关系的组合路径。
    if departments:
        related = [org for org in eligible if _field(org, "level") == "unit" and
                   any(identity_name(organization_name(org)) == identity_name(organization_name(dept)) + "(本级)"
                       for dept in departments)]
        if scope == "unit":
            if not related:
                return defer("directory_missing_org", "本级口径明确，但组织库没有对应本级单位", departments)
            verified = [org for org in related if any(_field(org, "parent_id") == _field(dept, "id") for dept in departments)]
            if not verified:
                return defer("organization_relation_unverified", "本级候选缺少已确认的部门归属关系", related)
            safe = verified
        elif not scope:
            # 无本级节点也不证明是部门汇总，避免用目录缺失推断口径。
            return defer("scope_undetermined", "原件部门/本级汇总范围尚无定论", [*departments, *related])
        else:
            safe = departments
    elif scope:
        safe = [org for org in safe if _field(org, "level") == scope]

    if len(safe) > 1:
        return defer("equivalent_candidates_tie", "存在多个无法区分的等价候选", safe)
    if not safe:
        return defer("directory_missing_org", "已检查完整组织目录，未找到满足主体及层级条件的唯一组织", matches)
    selected = safe[0]
    return {"decision": "bound", "organization_id": _field(selected, "id"), "scope": scope,
            "reasons": [], "evidence": all_evidence, "candidates": suggestions,
            "match_basis": "combined_source_scope" if departments and scope == "unit" else
                           ("cover_field" if any(e["source"] in {"cover", "cover_label"} for e in evidence) else "content_fallback")}
