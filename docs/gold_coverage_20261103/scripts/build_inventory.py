# -*- coding: utf-8 -*-
"""只读盘点：108 检查点 × 33 份冻结金标 → coverage_inventory.csv / case_evidence.csv

运行（在任务目录下）：
    python3 scripts/build_inventory.py \
        --src "/Volumes/workSSD/software development/GovBudgetChecker" \
        --out "docs/gold_coverage_20261103"

不读 uploads/outputs 中的系统检查结果作为真值；只读 gold/*/annotation.json、
gold/FROZEN.csv、样本清单、两份 checklist YAML，以及 r2 的 coverage_matrix.json
（仅用于核对“系统是否给过结论”，不当作准确性证明）。
"""

import argparse
import csv
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from mapping_spec import BUDGET, FINAL, FU_DEFAULT_SUB, FREE_OVERRIDES, EFFECTIVE_CLASS  # noqa: E402

CST = timezone(timedelta(hours=8))
CHECKS = [
    "GS-S01", "GS-S02", "GS-S03", "GS-S04",
    "GS-T01", "GS-T02", "GS-T03", "GS-T04", "GS-T05", "GS-T06", "GS-T07", "GS-T08",
    "GS-X01", "GS-X02",
    "GS-N01", "GS-N02", "GS-N03", "GS-N04", "GS-N05", "GS-N06", "GS-N07", "GS-N08", "GS-N09",
    "GS-W01",
]
VERDICTS = ["pass", "defect", "not_applicable", "cannot_determine"]

BOUNDARY_MARKERS = {
    "R1舍入包络": r"\bR1\b|舍入|包络",
    "R3模板措辞": r"\bR3\b",
    "R8年度模板": r"\bR8\b",
    "D2模板豁免": r"\bD2\b",
    "R13逐行复核": r"\bR13\b",
    "R14双提取器": r"R14|pdfplumber",
    "R15方向": r"\bR15\b",
    "R16同比区间": r"\bR16\b|区间",
    "R18无三公": r"\bR18\b|无三公|无“三公”",
    "R19半角括号": r"\bR19\b",
    "R23目录范围": r"\bR23\b",
    "R26零基数": r"\bR26\b|上年数为 0|上年为 0",
    "R27标题笔误": r"\bR27\b",
    "R29截断名": r"\bR29\b|截断",
}


# ---------------------------------------------------------------------------
# 1. 读取
# ---------------------------------------------------------------------------
def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_checklist(path):
    """解析本仓库两份 checklist 的固定结构（避免引入 PyYAML 依赖）。"""
    items = []
    cur = None
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            m = re.match(r"^  - id: (\S+)\s*$", line)
            if m:
                if cur:
                    items.append(cur)
                cur = {"id": m.group(1)}
                continue
            if cur is None:
                continue
            m = re.match(r"^    (dimension|title|criterion|status|obligations|rules|decision|note):\s*(.*)$", line)
            if not m:
                continue
            key, val = m.group(1), m.group(2).strip()
            if val.startswith("[") and val.endswith("]"):
                val = [x.strip() for x in val[1:-1].split(",") if x.strip()]
            elif len(val) >= 2 and val[0] == val[-1] == '"':
                val = val[1:-1]
            cur[key] = val
    if cur:
        items.append(cur)
    return items


def load_annotations(src):
    out = {}
    for sid in sorted(os.listdir(os.path.join(src, "gold"))):
        if not re.match(r"^GS-\d{4}-\d{2}$", sid):
            continue
        path = os.path.join(src, "gold", sid, "annotation.json")
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as f:
            out[sid] = json.load(f)
    return out


def classify_finding(f):
    e = f.get("expected") or ""
    q = f.get("quote") or ""
    cat = f.get("category") or ""
    if "千分位" in e:
        return "R9-千分位"
    if "小数位" in e:
        return "R9-小数位"
    if "双小数点" in e:
        return "R9-双小数点"
    if "标点符号连用" in e:
        return "R9-标点连用"
    if "半角冒号" in e:
        return "R9-半角冒号"
    if "中文语境使用半角逗号" in e:
        return "R22"
    if "六角括号" in e:
        return "R28"
    if "口径表述" in e:
        return "R21"
    if "序号" in e:
        return "R24"
    if cat == "模板残留":
        return "模板残留-占位符" if ("占位" in e or "待选项" in e or "XX" in q) else "模板残留-指引句"
    if cat == "标点":
        return "标点其他"
    if cat == "格式":
        return "格式其他"
    return cat or "其他"


def finding_home(sid, f, cls):
    key = (sid, f.get("id"))
    if key in FREE_OVERRIDES:
        sub = FREE_OVERRIDES[key]
        if sub.startswith("final:"):
            sub = sub[6:]
        return sub, "manual_override"
    sub = FU_DEFAULT_SUB.get(cls)
    if sub and sub.startswith("final:"):
        sub = sub[6:]
    return sub, "class_default"


# ---------------------------------------------------------------------------
# 2. 子场景评估
# ---------------------------------------------------------------------------
def check_of(ann, cid):
    for c in ann.get("checks", []):
        if c.get("check_id") == cid:
            return c
    return None


def items_of(ann, cid, regex=None):
    c = check_of(ann, cid) or {}
    items = c.get("items") or []
    if regex:
        items = [it for it in items if re.search(regex, it.get("label") or "")]
    return items


def _pages_of_item(it):
    return sorted({v.get("page") for v in (it.get("values") or []) if v.get("page")})


def _first_value(it):
    vals = it.get("values") or []
    if vals:
        return vals[0].get("page"), vals[0].get("quote") or ""
    return None, ""


def eval_component(comp, ann, findings_by_sub, sid, sub_id):
    """返回 (状态, [(证据对象, 来源检查号, 证据类型), ...])。"""
    kind = comp["kind"]
    if kind == "none":
        return "unconfirmed", []
    if kind == "check":
        c = check_of(ann, comp["check"])
        if not c:
            return "unconfirmed", []
        verdict = c.get("verdict", "unconfirmed")
        note_re = comp.get("note_regex")
        its = c.get("items") or []
        if note_re:
            matched = [it for it in its
                       if re.search(note_re, (it.get("label") or "") + " " + (it.get("note") or ""))]
            if verdict == "defect":
                defects = [it for it in matched if it.get("verdict") == "defect"]
                if defects:
                    return "defect", [(x, comp["check"], "check_item") for x in defects]
                return "unconfirmed", []
            if verdict == "pass" and matched:
                return "pass", [(x, comp["check"], "check_item") for x in matched]
            return "unconfirmed", []
        if verdict == "defect":
            defects = [it for it in its if it.get("verdict") == "defect"]
            ev = defects if defects else [c]
        else:
            ev = [c]
        return verdict, [(x, comp["check"], "check_item") for x in ev]
    if kind == "note_scan":
        c = check_of(ann, comp["check"])
        if not c:
            return "unconfirmed", []
        its = c.get("items") or []
        neg = [it for it in its
               if it.get("verdict") == "defect"
               and re.search(comp["negative_regex"], (it.get("label") or "") + " " + (it.get("note") or ""))]
        if neg:
            return "defect", [(x, comp["check"], "check_item") for x in neg]
        pos = [it for it in its
               if re.search(comp["positive_regex"], (it.get("label") or "") + " " + (it.get("note") or ""))]
        if pos:
            return "pass", [(x, comp["check"], "check_item") for x in pos]
        return "unconfirmed", []
    if kind == "items":
        items = items_of(ann, comp["check"], comp.get("label_regex"))
        note_re = comp.get("note_regex")
        if note_re:
            items = [it for it in items
                     if re.search(note_re, (it.get("label") or "") + " " + (it.get("note") or ""))]
        if not items:
            c = check_of(ann, comp["check"])
            if c and c.get("verdict") == "not_applicable":
                return "not_applicable", []
            return "unconfirmed", []
        vs = [it.get("verdict") for it in items]
        if "defect" in vs:
            return "defect", [(it, comp["check"], "check_item") for it in items if it.get("verdict") == "defect"]
        if "cannot_determine" in vs:
            return "cannot_determine", [(it, comp["check"], "check_item") for it in items]
        if all(v == "not_applicable" for v in vs):
            return "not_applicable", [(it, comp["check"], "check_item") for it in items]
        if all(v == "pass" for v in vs):
            return "pass", [(it, comp["check"], "check_item") for it in items]
        return "unconfirmed", [(it, comp["check"], "check_item") for it in items]
    if kind == "fu_ref":
        fs = findings_by_sub.get(sid, {}).get(comp["ref_sub"])
        if fs:
            return "defect", [(f, "", "free_finding") for f in fs]
        return ("pass" if comp.get("exhaustive") else "unconfirmed"), []
    if kind == "fu":
        fs = findings_by_sub.get(sid, {}).get(sub_id)
        if fs:
            return "defect", [(f, "", "free_finding") for f in fs]
        return ("pass" if comp.get("exhaustive") else "unconfirmed"), []
    raise ValueError(kind)


def combine(statuses, mode="all"):
    if "defect" in statuses:
        return "defect"
    if mode == "any":
        if "pass" in statuses:
            return "pass"
        if "cannot_determine" in statuses:
            return "cannot_determine"
        if all(s == "not_applicable" for s in statuses):
            return "not_applicable"
        return "unconfirmed"
    if "cannot_determine" in statuses:
        return "cannot_determine"
    if all(s == "not_applicable" for s in statuses):
        return "not_applicable"
    if all(s in ("pass", "not_applicable") for s in statuses) and "pass" in statuses:
        return "pass"
    return "unconfirmed"


def evaluate_sub(sub, ann, findings_by_sub, sid):
    statuses = []
    units = []
    for comp in sub["components"]:
        st, ev = eval_component(comp, ann, findings_by_sub, sid, sub["sub_id"])
        statuses.append(st)
        units.extend(ev)
    return combine(statuses, sub.get("mode", "all")), units


SUB_DISPOSITION_OVERRIDE = {
    "final:B-04.1": "无金标证据（下一批补样）",
    "budget:B-04.1": "无样本（预算金标未建立）",
}


# ---------------------------------------------------------------------------
# 3. 输出
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    src = os.path.abspath(args.src)
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)

    base_commit = os.popen("git -C %s rev-parse HEAD" % json.dumps(src)).read().strip()
    branch = os.popen("git -C %s branch --show-current" % json.dumps(src)).read().strip()

    final_items = parse_checklist(os.path.join(src, "rules", "checklists", "final_checklist_v1.yaml"))
    budget_items = parse_checklist(os.path.join(src, "rules", "checklists", "budget_checklist_v1.yaml"))
    assert len(final_items) == 54, len(final_items)
    assert len(budget_items) == 54, len(budget_items)

    anns = load_annotations(src)
    assert len(anns) == 33, len(anns)

    # 自由发现分类与归属
    findings_by_sub = defaultdict(lambda: defaultdict(list))
    finding_rows = []
    cls_counter = Counter()
    for sid, ann in anns.items():
        for f in ann.get("free_findings") or []:
            cls = classify_finding(f)
            home, how = finding_home(sid, f, cls)
            eff = EFFECTIVE_CLASS.get((sid, f.get("id")), cls)
            cls_counter[eff] += 1
            if not home:
                raise SystemExit("未归属的自由发现：%s %s class=%s" % (sid, f.get("id"), cls))
            findings_by_sub[sid][home].append(f)
            finding_rows.append((sid, f, cls, home, how))

    # 23 个检查点的自由发现类之外，检查项证据
    inventory_rows = []
    case_rows = []
    case_seq = 0
    sub_index = {}

    def add_case(**kw):
        nonlocal case_seq
        case_seq += 1
        kw["case_id"] = "CE-%05d" % case_seq
        case_rows.append(kw)

    def emit_sub(rk, item, sub, ann_subset):
        sub_out = "%s:%s" % (rk, sub["sub_id"])
        sid_list = sorted(ann_subset)
        counts = Counter()
        defect_items = []
        pass_docs = []
        na_docs = []
        cd_docs = []
        unconf_docs = []
        defect_pages = set()
        pass_pages = set()
        boundary = defaultdict(set)
        for sid in sid_list:
            ann = anns[sid]
            verdict, units = evaluate_sub(sub, ann, findings_by_sub, sid)
            if verdict == "defect":
                counts["defect_docs"] += 1
                for obj, src_check, etype in units:
                    if etype == "free_finding" or obj.get("verdict") == "defect":
                        defect_items.append((sid, obj, etype, src_check))
            elif verdict == "pass":
                counts["pass_docs"] += 1
                pass_docs.append(sid)
            elif verdict == "not_applicable":
                counts["na_docs"] += 1
                na_docs.append(sid)
            elif verdict == "cannot_determine":
                counts["cd_docs"] += 1
                cd_docs.append(sid)
            else:
                counts["unconfirmed_docs"] += 1
                unconf_docs.append(sid)

            # 边界标记：扫描相关条目的 note
            texts = []
            for comp in sub["components"]:
                if comp["kind"] in ("check", "note_scan"):
                    c = check_of(ann, comp["check"]) or {}
                    for it in c.get("items") or []:
                        texts.append((it.get("label") or "") + " " + (it.get("note") or ""))
                elif comp["kind"] == "items":
                    for it in items_of(ann, comp["check"], comp.get("label_regex")):
                        texts.append((it.get("label") or "") + " " + (it.get("note") or ""))
            blob = " ".join(texts)
            for tag, pat in BOUNDARY_MARKERS.items():
                if re.search(pat, blob):
                    boundary[tag].add(sid)

        # 缺陷单位
        for sid, obj, etype, src_check in defect_items:
            if etype == "free_finding":
                pages = [obj.get("page")] if obj.get("page") else []
                quote = obj.get("quote") or ""
                occ = len(obj.get("occurrences") or []) or 1
                label = obj.get("expected") or ""
                formula = obj.get("expected") or ""
                add_case(
                    unit_key="%s|FU|%s" % (sid, obj.get("id")),
                    report_kind="final", item_key="final:" + item["id"], sub_id=sub_out,
                    evidence_type="free_finding", source_check="", source_class=classify_finding(obj),
                    sample_id=sid, year=anns[sid]["basic"]["fiscal_year"],
                    scope=anns[sid]["basic"]["material_scope"], verdict="defect",
                    item_label=label, page=pages[0] if pages else "",
                    quote=quote, expected_or_formula=formula,
                    annotation_path="gold/%s/annotation.json" % sid,
                    annotation_field="free_findings[id=%s]" % obj.get("id"),
                    occurrence_count=occ, counted_as="defect_unit", shared_evidence=bool(sub.get("shared_with")),
                    notes="同文档多处出现按 1 个缺陷单位计；occurrences=%d" % occ,
                )
            else:
                pages = _pages_of_item(obj)
                p0, q0 = _first_value(obj)
                add_case(
                    unit_key="%s|%s|%s|%s" % (sid, src_check, obj.get("label") or "", (q0 or "")[:30]),
                    report_kind="final", item_key="final:" + item["id"], sub_id=sub_out,
                    evidence_type="check_item", source_check=src_check,
                    source_class="", sample_id=sid, year=anns[sid]["basic"]["fiscal_year"],
                    scope=anns[sid]["basic"]["material_scope"], verdict="defect",
                    item_label=obj.get("label") or "", page=p0 or (pages[0] if pages else ""),
                    quote=q0, expected_or_formula=(obj.get("formula") or obj.get("note") or ""),
                    annotation_path="gold/%s/annotation.json" % sid,
                    annotation_field="checks[%s].items[label=%s]" % (src_check, obj.get("label")),
                    occurrence_count=max(1, len(obj.get("values") or [])),
                    counted_as="defect_unit", shared_evidence=bool(sub.get("shared_with")),
                    notes=(obj.get("note") or "")[:300],
                )
            defect_pages |= set(pages if etype == "free_finding" else _pages_of_item(obj))

        # 正常案例（去重文档）
        for sid in pass_docs:
            ann = anns[sid]
            page = ""
            quote = ""
            field = ""
            etype = "check_level"
            src_check = ""
            for comp in sub["components"]:
                if comp["kind"] == "check":
                    c = check_of(ann, comp["check"]) or {}
                    items = c.get("items") or []
                    src_check = comp["check"]
                    if items:
                        p, q = _first_value(items[0])
                        page, quote = p or "", q
                        field = "checks[%s].items[0]" % comp["check"]
                    else:
                        field = "checks[%s].verdict" % comp["check"]
                    break
                if comp["kind"] == "items":
                    its = items_of(ann, comp["check"], comp.get("label_regex"))
                    src_check = comp["check"]
                    if its:
                        p, q = _first_value(its[0])
                        page, quote = p or "", q
                        field = "checks[%s].items[label~=%s]" % (comp["check"], comp.get("label_regex"))
                        break
                    continue
                if comp["kind"] == "note_scan":
                    c = check_of(ann, comp["check"]) or {}
                    its = [it for it in (c.get("items") or [])
                           if re.search(comp["positive_regex"], (it.get("label") or "") + " " + (it.get("note") or ""))]
                    src_check = comp["check"]
                    if its:
                        p, q = _first_value(its[0])
                        page, quote = p or "", q
                        field = "checks[%s].items[note~%s]" % (comp["check"], comp["positive_regex"][:40])
                    break
            if not field and sub["components"][0]["kind"] in ("fu", "fu_ref"):
                etype = "free_finding_scan"
                field = "free_findings[]（穷尽扫描，无该类条目）"
            add_case(
                unit_key="%s|PASS|%s" % (sid, sub_out),
                report_kind="final", item_key="final:" + item["id"], sub_id=sub_out,
                evidence_type=etype, source_check=src_check, source_class="",
                sample_id=sid, year=ann["basic"]["fiscal_year"],
                scope=ann["basic"]["material_scope"], verdict="pass",
                item_label="（整项通过）", page=page, quote=quote,
                expected_or_formula="",
                annotation_path="gold/%s/annotation.json" % sid,
                annotation_field=field, occurrence_count=1, counted_as="pass_doc",
                shared_evidence=bool(sub.get("shared_with")),
                notes="该子场景在本份材料上判 pass；同文档多处不重复计",
            )
            if page:
                pass_pages.add(page)

        for sid in na_docs:
            ann = anns[sid]
            add_case(
                unit_key="%s|NA|%s" % (sid, sub_out),
                report_kind="final", item_key="final:" + item["id"], sub_id=sub_out,
                evidence_type="check_level", source_check="",
                source_class="", sample_id=sid, year=ann["basic"]["fiscal_year"],
                scope=ann["basic"]["material_scope"], verdict="not_applicable",
                item_label="（不适用）", page="", quote="",
                expected_or_formula="", annotation_path="gold/%s/annotation.json" % sid,
                annotation_field="checks[].verdict=not_applicable", occurrence_count=1,
                counted_as="na_doc", shared_evidence=bool(sub.get("shared_with")),
                notes="不适用不计入正常通过",
            )

        # 缺口分类
        gaps = []
        if not sid_list:
            gaps.append("无该文种/材料")
        elif counts["defect_docs"] == 0 and counts["pass_docs"] > 0:
            gaps.append("有正常案例、缺真实缺陷案例")
        elif counts["defect_docs"] > 0 and counts["pass_docs"] == 0:
            gaps.append("有缺陷案例、缺正常案例")
        elif counts["defect_docs"] == 0 and counts["pass_docs"] == 0 and counts["unconfirmed_docs"] > 0:
            gaps.append("金标未涵盖该要求")
        if counts["na_docs"] and counts["defect_docs"] == 0:
            gaps.append("仅不适用案例")
        if counts["cd_docs"]:
            gaps.append("存在无法判断")
        none_comp = any(c["kind"] == "none" for c in sub["components"])
        if none_comp:
            gaps.append("无对应金标证据")
        if sub["shared_with"]:
            gaps.append("证据与其他检查点共用")

        scope_label = "shared_with:%s（不跨项相加）" % sub["shared_with"] if sub["shared_with"] else "independent"
        inventory_rows.append({
            "report_kind": "final",
            "item_key": "final:" + item["id"],
            "item_id": item["id"],
            "dimension": item.get("dimension", ""),
            "item_title": item.get("title", ""),
            "item_status": item.get("status", ""),
            "item_decision": item.get("decision", ""),
            "system_rules": ";".join(item.get("rules", []) or []),
            "obligations": ";".join(item.get("obligations", []) or []),
            "gs_checks": ";".join(sorted({c.get("check", "") for c in sub["components"] if c.get("check")})),
            "fu_classes": ";".join(sorted({c.get("cls", "") for c in sub["components"] if c.get("cls")})),
            "sub_id": sub_out,
            "sub_requirement": sub["requirement"],
            "mapping_type": sub["mapping"],
            "mapping_confidence": sub["confidence"],
            "count_scope": scope_label,
            "docs_total": len(anns),
            "docs_applicable": len(sid_list) - counts["na_docs"],
            "pass_docs": counts["pass_docs"],
            "defect_docs": counts["defect_docs"],
            "defect_units": len(defect_items),
            "na_docs": counts["na_docs"],
            "cd_docs": counts["cd_docs"],
            "unconfirmed_docs": counts["unconfirmed_docs"],
            "boundary_verified": ";".join("%s(%d)" % (k, len(v)) for k, v in sorted(boundary.items())),
            "sample_ids_defect": ";".join(sorted({x[0] for x in defect_items})),
            "sample_ids_pass": ";".join(pass_docs),
            "sample_ids_na": ";".join(na_docs),
            "evidence_pages": ";".join(str(p) for p in sorted(defect_pages | pass_pages) if p),
            "evidence_index": _evidence_index(sub),
            "gap_types": ";".join(gaps),
            "gap_note": sub.get("note", ""),
            "disposition": _disposition(item, sub, counts, sid_list),
        })

    def _evidence_index(sub):
        parts = []
        for c in sub["components"]:
            if c["kind"] == "check":
                parts.append("checks[%s]" % c["check"])
            elif c["kind"] == "note_scan":
                parts.append("checks[%s].items[note~%s]" % (c["check"], c["positive_regex"][:40]))
            elif c["kind"] == "items":
                parts.append("checks[%s].items[label~=%s]" % (c["check"], c["label_regex"]))
            elif c["kind"] in ("fu", "fu_ref"):
                parts.append("free_findings[class=%s%s]" % (c["cls"], ",exhaustive" if c.get("exhaustive") else ""))
            elif c["kind"] == "none":
                parts.append("none:%s" % c.get("reason", ""))
        return " | ".join(parts)

    def _disposition(item, sub, counts, sid_list):
        key = "final:" + sub["sub_id"]
        if key in SUB_DISPOSITION_OVERRIDE:
            return SUB_DISPOSITION_OVERRIDE[key]
        if item.get("status") == "external":
            return "政策排除（external）"
        if item.get("decision") == "closed":
            return "政策排除（closed）"
        if item.get("decision") == "deferred":
            return "暂缓（deferred）"
        if item.get("decision") == "ai_only":
            return "AI 条件未验证（ai_only）"
        if not sid_list:
            return "无样本（预算金标未建立）"
        if counts["defect_docs"] == 0 and counts["pass_docs"] == 0:
            return "无金标证据（下一批补样）"
        if counts["defect_docs"] == 0:
            return "缺缺陷案例（下一批补样）"
        if counts["pass_docs"] == 0:
            return "缺正常/易误报案例（下一批补样）"
        return "已有双向案例（维持回归）"

    for item in final_items:
        spec = FINAL.get(item["id"])
        if not spec:
            raise SystemExit("缺 final 映射：%s" % item["id"])
        for sub in spec["subs"]:
            emit_sub("final", item, sub, sorted(anns))

    def _budget_gap(item, sub):
        if item.get("status") == "external":
            return "政策已排除（不属PDF审校）"
        if item.get("decision") == "ai_only":
            return "AI 条件未验证"
        if item.get("decision") == "deferred" and sub["sub_id"] == "B-04.2":
            return "政策暂缓（deferred）"
        return "无该文种/材料"

    def _budget_disp(item, sub):
        if item.get("status") == "external":
            return "政策排除（external）"
        if item.get("decision") == "ai_only":
            return "AI 条件未验证（ai_only）"
        if item.get("decision") == "deferred" and sub["sub_id"] == "B-04.2":
            return "暂缓（deferred）"
        return "无样本（预算金标未建立）"

    # 预算：无样本，逐子场景记 0
    for item in budget_items:
        spec = BUDGET.get(item["id"])
        if not spec:
            raise SystemExit("缺 budget 映射：%s" % item["id"])
        for sub in spec["subs"]:
            inventory_rows.append({
                "report_kind": "budget",
                "item_key": "budget:" + item["id"],
                "item_id": item["id"],
                "dimension": item.get("dimension", ""),
                "item_title": item.get("title", ""),
                "item_status": item.get("status", ""),
                "item_decision": item.get("decision", ""),
                "system_rules": ";".join(item.get("rules", []) or []),
                "obligations": ";".join(item.get("obligations", []) or []),
                "gs_checks": "",
                "fu_classes": "",
                "sub_id": "budget:" + sub["sub_id"],
                "sub_requirement": sub["requirement"],
                "mapping_type": sub["mapping"],
                "mapping_confidence": sub["confidence"],
                "count_scope": "no_sample",
                "docs_total": 0,
                "docs_applicable": 0,
                "pass_docs": 0,
                "defect_docs": 0,
                "defect_units": 0,
                "na_docs": 0,
                "cd_docs": 0,
                "unconfirmed_docs": 0,
                "boundary_verified": "",
                "sample_ids_defect": "",
                "sample_ids_pass": "",
                "sample_ids_na": "",
                "evidence_pages": "",
                "evidence_index": "预算金标未建立；无 annotation.json",
                "gap_types": _budget_gap(item, sub),
                "gap_note": sub.get("note", ""),
                "disposition": _budget_disp(item, sub),
            })

    # 写 CSV
    inv_fields = ["report_kind", "item_key", "item_id", "dimension", "item_title", "item_status",
                  "item_decision", "system_rules", "obligations", "gs_checks", "fu_classes",
                  "sub_id", "sub_requirement", "mapping_type", "mapping_confidence", "count_scope",
                  "docs_total", "docs_applicable", "pass_docs", "defect_docs", "defect_units",
                  "na_docs", "cd_docs", "unconfirmed_docs", "boundary_verified",
                  "sample_ids_defect", "sample_ids_pass", "sample_ids_na", "evidence_pages",
                  "evidence_index", "gap_types", "gap_note", "disposition"]
    with open(os.path.join(out, "coverage_inventory.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=inv_fields)
        w.writeheader()
        w.writerows(inventory_rows)

    case_fields = ["case_id", "unit_key", "report_kind", "item_key", "sub_id", "evidence_type", "source_check",
                   "source_class", "sample_id", "year", "scope", "verdict", "item_label", "page",
                   "quote", "expected_or_formula", "annotation_path", "annotation_field",
                   "occurrence_count", "counted_as", "shared_evidence", "notes"]
    with open(os.path.join(out, "case_evidence.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=case_fields)
        w.writeheader()
        w.writerows(case_rows)

    # 自检
    verdict_counter = Counter()
    cu_units = 0
    for ann in anns.values():
        for c in ann["checks"]:
            verdict_counter[c["verdict"]] += 1
            cu_units += sum(1 for it in (c.get("items") or []) if it.get("verdict") == "defect")
    exhaustive = sum(v for k, v in cls_counter.items()
                     if k in ("R9-千分位", "R9-小数位", "R9-双小数点", "R9-标点连用", "R9-半角冒号",
                              "R22", "R24", "R28", "R21"))
    checks_total = sum(len(a["checks"]) for a in anns.values())

    # 系统结论层（只统计“给过结论”，不判断对错）
    sys_cov = {}
    cov_path = os.path.join(src, "outputs", "gold_baseline_20261102", "rev2", "analysis", "coverage_matrix.json")
    if os.path.isfile(cov_path):
        with open(cov_path, encoding="utf-8") as f:
            cm = json.load(f)
        sys_cov = {
            "rows": len(cm),
            "with_system_verdict": sum(1 for r in cm if r.get("system") not in (None, "", "no_verdict", "none")),
            "system_values": dict(Counter(r.get("system") for r in cm)),
        }

    manifest = {
        "task": "金标覆盖盘点与下一批补样计划",
        "generated_at": datetime.now(CST).isoformat(timespec="seconds"),
        "worktree": {
            "branch": os.popen("git -C %s branch --show-current" % json.dumps(os.getcwd())).read().strip(),
            "head": os.popen("git -C %s rev-parse HEAD" % json.dumps(os.getcwd())).read().strip(),
            "root": os.popen("git -C %s rev-parse --show-toplevel" % json.dumps(os.getcwd())).read().strip(),
            "task_dir": os.getcwd(),
        },
        "source_project": {
            "root": src,
            "base_commit": base_commit,
            "branch_at_start": branch,
        },
        "sources": [],
        "samples": [],
        "statistics_definitions": {
            "truth_unit_check": "24 项检查中 items[].verdict=defect 的每一条（一份材料可多条）",
            "truth_unit_free": "free_findings[] 的每一条；occurrences 只用于出现点，不重复计份",
            "four_states": VERDICTS,
            "doc_count": "同一份材料同一子场景只计 1 份；同文档多处出现不重复计",
            "layers": {
                "A": "有人工案例（本盘点 pass/defect/NA/CD 均来自冻结 annotation）",
                "B": "系统给过结论（coverage_matrix.json 的 system 字段；不代表正确）",
                "C": "系统结果经核验正确（需人工/金标比对；本盘点不重算）",
            },
            "shared_evidence": "GS 检查与自由发现可同时服务多个检查点；count_scope 标明共用，不得跨项相加",
        },
        "checks": {
            "final_items": len(final_items),
            "budget_items": len(budget_items),
            "items_total": len(final_items) + len(budget_items),
            "gold_docs": len(anns),
            "check_records_expected_792": len(anns) * 24,
            "check_records_actual": checks_total,
            "verdict_counts": dict(verdict_counter),
            "cu_defect_units": cu_units,
            "free_findings_total": sum(cls_counter.values()),
            "free_findings_by_class": dict(cls_counter),
            "free_findings_exhaustive": exhaustive,
            "free_findings_best_effort": sum(cls_counter.values()) - exhaustive,
        },
        "system_verdict_scope_B_only": sys_cov,
    }

    source_specs = [
        ("AGENTS.md", "便携规则与检查口径"),
        ("rules/checklists/final_checklist_v1.yaml", "决算 54 项检查点目录"),
        ("rules/checklists/budget_checklist_v1.yaml", "预算 54 项检查点目录"),
        ("docs/GOLD_STANDARD_SPEC_20261101.md", "金标制作规范（24 项、R1–R30）"),
        ("docs/GOLD_STANDARD_SAMPLE_20261101.csv", "33 份样本清单"),
        ("gold/FROZEN.csv", "33 份冻结指纹"),
        ("gold/DISPUTES.md", "D1/D2 裁决"),
        ("gold/templates/README.md", "年度模板说明"),
        ("docs/GOLD_BASELINE_PROTOCOL_20261102.md", "基线协议（匹配与指标口径）"),
        ("docs/GOLD_BASELINE_20261102_r2_tables.md", "r2 基线完整表格"),
        ("outputs/gold_baseline_20261102/rev2/analysis/coverage_matrix.json", "系统结论层（B 层）"),
        ("outputs/gold_baseline_20261102/rev2/analysis/gold_docs.json", "金标单位索引（r2）"),
        ("outputs/fp_batch1_20261103/reviewer_round6_codex_20261103/acceptance_record.json", "第一批整改验收记录"),
    ]
    for rel, role in source_specs:
        p = os.path.join(src, rel)
        manifest["sources"].append({
            "path": p, "relative": rel, "role": role,
            "exists": os.path.isfile(p),
            "sha256": sha256_file(p) if os.path.isfile(p) else None,
            "bytes": os.path.getsize(p) if os.path.isfile(p) else None,
        })

    with open(os.path.join(src, "docs", "GOLD_STANDARD_SAMPLE_20261101.csv"), encoding="utf-8-sig") as f:
        sample_rows = list(csv.DictReader(f))
    froz = {}
    with open(os.path.join(src, "gold", "FROZEN.csv"), encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            froz[r["sample_id"]] = r
    pdf_base = os.path.expanduser("~/Desktop/预决算信息收集/决算测试上海市")
    for r in sample_rows:
        sid = r["sample_id"]
        ann_path = os.path.join(src, "gold", sid, "annotation.json")
        pdf_path = os.path.join(pdf_base, r["path"])
        ann_sha = sha256_file(ann_path)
        pdf_sha = sha256_file(pdf_path) if os.path.isfile(pdf_path) else None
        manifest["samples"].append({
            "sample_id": sid, "year": int(r["year"]), "kind": r["kind"], "department": r["department"],
            "file": r["file"], "pdf_path": pdf_path, "pdf_sha256": pdf_sha,
            "pdf_sha256_matches_list": pdf_sha == r["sha256"],
            "annotation_path": ann_path, "annotation_sha256": ann_sha,
            "frozen_sha256": froz.get(sid, {}).get("annotation_sha256"),
            "frozen_matches": ann_sha == froz.get(sid, {}).get("annotation_sha256"),
            "truth_frozen_at": froz.get(sid, {}).get("truth_frozen_at"),
            "reviewed_by": froz.get(sid, {}).get("reviewed_by"),
        })
    manifest["checks"]["pdf_sha_match"] = sum(1 for s in manifest["samples"] if s["pdf_sha256_matches_list"])
    manifest["checks"]["annotation_frozen_match"] = sum(1 for s in manifest["samples"] if s["frozen_matches"])
    check_unit_keys = {r["unit_key"] for r in case_rows if r.get("evidence_type") == "check_item" and r.get("counted_as") == "defect_unit"}
    fu_unit_keys = {r["unit_key"] for r in case_rows if r.get("evidence_type") == "free_finding" and r.get("counted_as") == "defect_unit"}
    manifest["checks"]["unique_check_defect_units_from_cases"] = len(check_unit_keys)
    manifest["checks"]["unique_free_findings_from_cases"] = len(fu_unit_keys)
    manifest["checks"]["inventory_rows"] = len(inventory_rows)
    manifest["checks"]["case_rows"] = len(case_rows)
    manifest["checks"]["final_item_keys"] = len({r["item_key"] for r in inventory_rows if r["report_kind"] == "final"})
    manifest["checks"]["budget_item_keys"] = len({r["item_key"] for r in inventory_rows if r["report_kind"] == "budget"})
    manifest["checks"]["all_item_keys"] = len({r["item_key"] for r in inventory_rows})

    with open(os.path.join(out, "source_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    # 控制台自检
    print(json.dumps(manifest["checks"], ensure_ascii=False, indent=2))
    print("inventory rows", len(inventory_rows), "case rows", len(case_rows))
    print("sub_ids", len({r["sub_id"] for r in inventory_rows}))


if __name__ == "__main__":
    main()
