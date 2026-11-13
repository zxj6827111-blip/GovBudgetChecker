#!/usr/bin/env python3
"""金标基线：把系统输出（run1）对到金标，按协议 docs/GOLD_BASELINE_PROTOCOL_20261102.md 计算连边与状态。

阶段：
    extract   提取 F/P/R/O 四层系统条目与金标单位，自检条数（写 analysis/system_items.json、gold_units.json）
    match     按协议 §4 自动匹配（写 analysis/auto_match.json、auto_match.csv）
    metrics   合并人工复核结论（analysis/manual_decisions.csv）后计算协议 §6 的全部指标

只读 gold/、docs/GOLD_STANDARD_*、run1/ 的结果；不改金标，不改系统代码。
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "outputs/gold_baseline_20261102"
ANALYSIS = OUT / "analysis"
SAMPLE_CSV = REPO / "docs/GOLD_STANDARD_SAMPLE_20261101.csv"
GOLD_DIR = REPO / "gold"

FORMAL_SEVERITIES = {"critical", "fatal", "error", "high", "warn", "warning", "medium", "low"}
DEGRADED = "degraded_missing_evidence"

EXH_FREE = {"R9-千分位", "R9-小数位", "R9-双小数点", "R9-标点连用", "R9-半角冒号", "R22", "R28", "R21", "R24"}
BEST_LISTED = {"B-错别字", "B-多字漏字", "B-语句不通", "B-重复词"}  # R25 点名的尽力类
BEST_UNLISTED = {"B-模板残留", "B-编码名称不符", "B-其他", "B-标点其他"}  # spec 未点名，保守按尽力类，另给敏感性
ALL_FREE = EXH_FREE | BEST_LISTED | BEST_UNLISTED

N_CHECKS = [f"GS-N0{i}" for i in range(1, 10)]


def read_gz(path: Path) -> Any:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "").replace("，", ",")


# ---------------------------------------------------------------------------
# 金标
# ---------------------------------------------------------------------------


def classify_free(finding: Dict[str, Any]) -> str:
    expected = finding.get("expected") or ""
    category = finding.get("category") or ""
    quote = finding.get("quote") or ""
    if "半角逗号分千分位" in expected:
        return "R9-千分位"
    if "小数位不是两位" in expected:
        return "R9-小数位"
    if "双小数点" in expected:
        return "R9-双小数点"
    if "标点符号连用" in expected:
        return "R9-标点连用"
    if "半角冒号" in expected:
        return "R9-半角冒号"
    if "半角逗号、半角分号或句末半角句点" in expected:
        return "R22"
    if "六角括号" in expected:
        return "R28"
    if "口径表述" in expected:
        return "R21"
    if "序号" in expected or (category == "格式" and re.match(r"\s*\d+[、.]", quote)):
        return "R24"
    mapping = {"错别字": "B-错别字", "多字漏字": "B-多字漏字", "语句不通": "B-语句不通", "重复词": "B-重复词",
               "模板残留": "B-模板残留", "编码名称不符": "B-编码名称不符", "标点": "B-标点其他"}
    return mapping.get(category, "B-其他")


NUM_RE = re.compile(r"\d[\d,，]*\.?\d*")
CJK_RE = re.compile(r"[一-鿿]+")
STOP_NGRAMS = {"年初预算", "支出决算", "一般公共", "公共预算", "财政拨款", "预算为", "万元", "决算数", "预算数"}


def numbers(text: str) -> Set[str]:
    out = set()
    for token in NUM_RE.findall(text or ""):
        token = token.replace(",", "").replace("，", "").strip(".")
        if len(token) >= 3:
            out.add(token)
    return out


def ngrams(text: str, size: int = 4) -> Set[str]:
    out = set()
    for run in CJK_RE.findall(text or ""):
        for i in range(len(run) - size + 1):
            gram = run[i:i + size]
            if gram not in STOP_NGRAMS:
                out.add(gram)
    return out


def load_gold() -> Dict[str, Any]:
    rows = list(csv.DictReader(SAMPLE_CSV.open(encoding="utf-8-sig")))
    docs: Dict[str, Any] = {}
    for row in rows:
        sid = row["sample_id"]
        ann = json.loads((GOLD_DIR / sid / "annotation.json").read_text(encoding="utf-8"))
        basic = ann["basic"]
        table_pages = [t["page"] for t in basic["tables"] if isinstance(t.get("page"), int)]
        section_pages = [s["page"] for s in basic["sections"] if isinstance(s.get("page"), int)]
        table_part = (min(table_pages), min(section_pages) - 1) if table_pages and section_pages else (0, -1)
        units: List[Dict[str, Any]] = []
        exam: Dict[str, Set[int]] = defaultdict(set)
        verdicts: Dict[str, str] = {}
        for check in ann["checks"]:
            cid = check["check_id"]
            verdicts[cid] = check["verdict"]
            for idx, item in enumerate(check.get("items") or []):
                pages = {v["page"] for v in item.get("values", []) if isinstance(v.get("page"), int)}
                exam[cid] |= pages
                if item.get("verdict") != "defect":
                    continue
                text = " ".join([item.get("label") or "", item.get("note") or "", item.get("formula") or ""] +
                                [(v.get("name") or "") + " " + str(v.get("quote") or "") for v in item.get("values", [])])
                quote_text = " ".join(str(v.get("quote") or "") for v in item.get("values", []))
                units.append({
                    "unit_id": f"{sid}|C|{cid}|{idx}", "doc": sid, "kind": "CU", "check": cid, "cls": cid,
                    "label": item.get("label"), "pages": sorted(pages), "note": item.get("note") or "",
                    "values": [{"page": v.get("page"), "quote": str(v.get("quote"))} for v in item.get("values", [])],
                    "numbers": sorted(numbers(text)), "ngrams": sorted(ngrams(quote_text + " " + (item.get("label") or ""))),
                })
        for f in ann["free_findings"]:
            cls = classify_free(f)
            occs = f.get("occurrences") or [{"page": f["page"], "quote": f["quote"]}]
            pages = sorted({o["page"] for o in occs if isinstance(o.get("page"), int)} | {f["page"]})
            units.append({
                "unit_id": f"{sid}|U|{f['id']}", "doc": sid, "kind": "FU", "check": None, "cls": cls,
                "category": f["category"], "label": f.get("expected"), "pages": pages, "note": "",
                "values": [{"page": o.get("page"), "quote": str(o.get("quote"))} for o in occs],
                "numbers": [], "ngrams": [], "exhaustive": cls in EXH_FREE,
            })
        docs[sid] = {"sid": sid, "year": int(row["year"]), "scope": basic["material_scope"], "org": basic["organization"],
                     "pages": basic["pages"], "table_part": table_part, "units": units,
                     "exam": {k: sorted(v) for k, v in exam.items()}, "verdicts": verdicts,
                     "cannot_determine": [c["check_id"] for c in ann["checks"] if c["verdict"] == "cannot_determine"]}
    return docs


# ---------------------------------------------------------------------------
# 系统条目
# ---------------------------------------------------------------------------


def item_pages(item: Dict[str, Any]) -> List[int]:
    pages: Set[int] = set()
    for ev in item.get("evidence") or []:
        if isinstance(ev.get("page"), int):
            pages.add(ev["page"])
    if isinstance(item.get("page_number"), int):
        pages.add(item["page_number"])
    loc = item.get("location") or {}
    for key in ("page", "body_page"):
        if isinstance(loc.get(key), int):
            pages.add(loc[key])
    for ref in loc.get("table_refs") or []:
        if isinstance(ref, dict) and isinstance(ref.get("page"), int):
            pages.add(ref["page"])
    return sorted(pages)


def item_text(item: Dict[str, Any]) -> str:
    parts = [item.get("title") or "", item.get("message") or ""]
    for ev in item.get("evidence") or []:
        parts.append(str(ev.get("quote") or ""))
        parts.append(str(ev.get("text_snippet") or ""))
    loc = item.get("location") or {}
    parts.append(str(loc.get("actual") or ""))
    parts.append(str(loc.get("expected") or ""))
    return " ".join(parts)


def load_system(run: Path, docs: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    ledgers: Dict[str, Any] = {}
    selfcheck: Dict[str, Any] = {}
    for sid in sorted(docs):
        status = read_gz(run / "status" / f"{sid}.status.json.gz")
        todo = read_gz(run / "todo" / f"{sid}.todo.json.gz")
        report = status["review_report"]
        counts = Counter()
        for tier_src, bucket in (("issues", "issues"), ("tips", "tips")):
            for it in report.get(bucket) or []:
                sev = str(it.get("severity") or "").strip().lower()
                if str(it.get("evidence_status") or "").strip() == DEGRADED:
                    tier = "D"
                elif sev in FORMAL_SEVERITIES:
                    tier = "F"
                elif it.get("review_kind") == "review_notice" or it.get("source") == "structured_ingest":
                    tier = "N"
                elif it.get("review_kind") != "manual_review" and sev != "manual_review":
                    tier = "R"  # 提示
                else:
                    tier = "P"
                counts[tier] += 1
                items.append(_row(sid, tier, it.get("id"), it.get("rule_id") or "", sev, it, bucket))
        for rec in report.get("manual_review") or []:
            rtype = rec.get("record_type")
            if rtype == "unfinished_check":
                continue
            if rtype in {"finding_review_candidate", "ai_candidate"}:
                finding = rec.get("finding") or {}
                tier = "D" if str(finding.get("evidence_status") or "").strip() == DEGRADED else "P"
                counts[tier] += 1
                row = _row(sid, tier, finding.get("id"), rec.get("code") or finding.get("rule_id") or "",
                           str(finding.get("severity") or ""), finding, "manual_review")
                row["title"] = rec.get("display_title") or row["title"]
                items.append(row)
            else:
                counts["N"] += 1
                items.append({"id": f"{sid}|N|{rec.get('code')}", "doc": sid, "tier": "N", "rule": rec.get("code"),
                              "sev": "", "pages": [], "text": rec.get("message") or "", "title": rec.get("display_title") or "",
                              "message": (rec.get("message") or "")[:400], "quotes": [], "bucket": "manual_review"})
        ledger = status["check_coverage"]
        ledgers[sid] = {i["obligation_id"]: i for i in ledger["instances"]}
        blocking = set(todo["blocking_ids"])
        for oid in sorted(blocking):
            inst = ledgers[sid][oid]
            items.append({"id": f"{sid}|O|{oid}", "doc": sid, "tier": "O", "rule": oid, "sev": "", "pages": [],
                          "text": f"{inst.get('title')} {inst.get('detail')}", "title": inst.get("title") or "",
                          "message": (inst.get("detail") or "")[:400], "quotes": [], "bucket": "obligation",
                          "reason": inst.get("reason_label") or inst.get("reason") or "", "ob_status": inst.get("status")})
        counts["O"] = len(blocking)
        q = todo["queue_counts"]
        expect = {"F": q["queue_required_total"], "O": q["queue_obligation_total"], "P": q["queue_candidate_total"],
                  "D": q["queue_degraded_total"], "R": q["queue_tip_total"], "N": q["queue_notice_total"]}
        got = {k: counts.get(k, 0) for k in expect}
        selfcheck[sid] = {"expect": expect, "got": got, "ok": expect == got}
    return items, {"selfcheck": selfcheck, "ledgers": ledgers}


def _row(sid: str, tier: str, raw_id: Any, rule: str, sev: str, item: Dict[str, Any], bucket: str) -> Dict[str, Any]:
    loc = item.get("location") or {}
    quotes = [str(ev.get("quote") or "") for ev in item.get("evidence") or [] if ev.get("quote")]
    return {"id": f"{sid}|{tier}|{raw_id}", "doc": sid, "tier": tier, "rule": rule, "sev": sev, "raw_id": raw_id,
            "pages": item_pages(item), "text": item_text(item), "title": (item.get("title") or "")[:200],
            "message": " ".join((item.get("message") or "").split())[:600], "quotes": quotes,
            "checklist_item": loc.get("checklist_item"), "bucket": bucket, "obligation_ids": item.get("obligation_ids") or []}


# ---------------------------------------------------------------------------
# 规则 → CT → 金标目标（协议 §3.1）
# ---------------------------------------------------------------------------

NOGOLD = "NOGOLD"
NARR = "NARR"


def classify_ct(rule: str, text: str) -> str:
    """规则号 + 报文特征 → 主张类型（CT）。"""
    t = text
    if rule == "CMM-013":
        return "S02-部分" if re.search(r"标题缺少“第", t) else "S03-目录"
    if rule == "CMM-014":
        return "编号"
    if rule == "CMM-017":
        return "S04-名称"
    if rule in {"V33-001", "CMM-012"}:
        return "S04-年度"
    if rule in {"CMM-018", "CMM-019", "CMM-027", "V33-236"}:
        return "R21-口径"
    if rule in {"V33-002", "V33-003", "CMM-021"}:
        return "S01-表"
    if rule in {"V33-109", "V33-114", "V33-122", "CMM-026"}:
        return "S01-空表"
    if rule == "V33-112":
        return "占位符"
    if rule == "CMM-008":
        return "模板残留"
    if rule == "V33-234":
        if "仍保留该表格式" in t:
            return "模板残留-空表"
        if "未说明原因" in t or "仅重述结果" in t:
            return "原因"
        if "同比比例与同条金额不一致" in t:
            return "同比"
        if "条目序号" in t:
            return "编号"
        return "叙述-其他"
    if rule == "CMM-010":
        if "双小数点" in t:
            return "格式-双小数点"
        return "格式-小数位" if "小数位不统一" in t else "格式-千分位"
    if rule in {"CMM-002", "V33-113"}:
        return "标点连用" if ("连续句号" in t or "重复标点" in t) else "重复词"
    if rule == "CMM-009":
        return "错别字"
    if rule in {"CMM-011", "V33-225"}:
        return "单位"
    if rule == "V33-222":
        return "单位" if "单位口径错误" in t else "表文金额"
    if rule == "V33-227":
        return "科目名称"
    if rule == "V33-251":
        return "逐项完整"
    if rule in {"V33-249", "V33-106"}:
        return "占比"
    if rule in {"V33-102", "V33-101"}:
        return "总表-说明"
    if rule in {"V33-103", "V33-226"}:
        return "收入-说明"
    if rule in {"V33-104", "V33-220"}:
        return "支出-说明"
    if rule in {"V33-105", "V33-221", "V33-242"}:
        return "拨款-说明"
    if rule in {"V33-107", "V33-223"}:
        return "基本支出-说明"
    if rule in {"V33-108", "V33-224", "V33-245", "V33-246", "V33-SG-COMPLETION", "CMM-001"}:
        return "三公-说明"
    if rule in {"V33-TXT-FUND-DETAIL", "V33-248"}:
        return "基金-说明"
    if rule in {"CMM-007", "V33-TREND-COMPLETION-RATE"}:
        return "同比/完成率"
    if rule in {"V33-NARRATIVE-INDICATOR-REPEAT", "CMM-020"}:
        return "文内一致"
    if rule == "V33-250":
        return "绩效项目数"
    if rule in {"V33-110", "CMM-005"}:
        return "方向"
    if rule == "V33-240":
        return "表内合计-T2"
    if rule == "V33-241":
        return "表内合计-T3"
    if rule in {"V33-005", "V33-120", "V33-233"}:
        return "表内合计-T5"
    if rule in {"V33-243", "V33-117"}:
        return "基本支出"
    if rule == "V33-244":
        return "三公表"
    if rule in {"V33-214", "V33-115"}:
        return "总表平衡"
    if rule == "V33-119":
        return "拨款总表"
    if rule in {"V33-200", "V33-201"}:
        return "总表-明细"
    if rule == "V33-252":
        return "基金列"
    if rule in {"V33-202", "V33-203"}:
        return "拨款表-功能表"
    if rule == "CMM-004":
        return "D-011"
    if rule in {"CMM-015", "CMM-016"}:
        return "版面"
    raise KeyError(f"协议 §3.1 未登记的规则：{rule}")


#: CT → 可对应的金标目标 [(检查号或 free:类, 标签正则|None)]。标签正则取自金标 defect 条目的 `label`，
#: 用来排除“同检查但不是同一个错”的偶然锚点重合（协议修订 1）。
NAR_ANY = [(c, None) for c in N_CHECKS]
COMPAT: Dict[str, List[Tuple[str, Optional[str]]]] = {
    "S02-部分": [("GS-S02", None)],
    "S03-目录": [("GS-S03", None)],
    "编号": [("GS-S03", "重复"), ("free:R24", None)],
    "S04-名称": [("GS-S04", "单位|名称")],
    "S04-年度": [("GS-S04", "年度"), ("GS-S03", "第三部分"), ("free:B-错别字", None), ("free:B-多字漏字", None)],
    "R21-口径": [("free:R21", None)],
    "S01-表": [("GS-S01", None)],
    "S01-空表": [("GS-S01", "空表"), ("GS-N08", None)],
    "占位符": [("GS-S01", "空表|占位"), ("free:B-模板残留", None)],
    "模板残留": [("free:B-模板残留", None)],
    "模板残留-空表": [("GS-S01", "空表|占位"), ("free:B-模板残留", None)],
    "原因": [("GS-N09", None)],
    "同比": [("GS-N01", None), ("GS-N04", None), ("GS-N05", "总体"), ("GS-N07", "同比|增幅|增减率"), ("free:B-其他", None)],
    "同比/完成率": [("GS-N07", "同比|增幅|增减率|完成"), ("GS-N05", "总体"), ("GS-N01", None), ("GS-N04", None), ("free:B-其他", None)],
    "叙述-其他": NAR_ANY,
    "格式-千分位": [("free:R9-千分位", None)],
    "格式-双小数点": [("free:R9-双小数点", None)],
    "格式-小数位": [("free:R9-小数位", None)],
    "标点连用": [("free:R9-标点连用", None)],
    "重复词": [("free:B-重复词", None)],
    "错别字": [("free:B-错别字", None), ("free:B-多字漏字", None), ("free:B-语句不通", None)],
    "单位": [("GS-W01", None)],
    "表文金额": [("GS-N05", "总体|结构情况$|逐项科目名称、年初|完整度")],
    "科目名称": [("GS-N05", "科目名称|类名")],
    "逐项完整": [("GS-N05", "逐项年初|科目名称、年初|完整度")],
    "占比": [("GS-N05", "总体|结构"), ("GS-N03", "占比")],
    "总表-说明": [("GS-N01", None)],
    "收入-说明": [("GS-N02", None)],
    "支出-说明": [("GS-N03", None)],
    "拨款-说明": [("GS-N04", None)],
    "基本支出-说明": [("GS-N06", None)],
    "三公-说明": [("GS-N07", None)],
    "基金-说明": [("GS-N08", None)],
    "文内一致": [("GS-N05", "总体|结构|逐项"), ("GS-N07", None), ("GS-N03", None), ("GS-N01", None), ("GS-N04", None)],
    "绩效项目数": [("free:B-其他", None)],
    "方向": [("GS-N09", None)],
    "表内合计-T2": [("GS-T02", None)],
    "表内合计-T3": [("GS-T03", None)],
    "表内合计-T5": [("GS-T02", None), ("GS-T03", None), ("GS-T05", None)],
    "基本支出": [("GS-T06", None), ("GS-T08", None)],
    "三公表": [("GS-T07", None)],
    "总表平衡": [("GS-T01", None)],
    "拨款总表": [("GS-T04", None)],
    "总表-明细": [("GS-X01", None)],
    "基金列": [("GS-X02", None)],
    "拨款表-功能表": [(NOGOLD, None)],
    "D-011": [(NOGOLD, None)],
    "版面": [(NOGOLD, None)],
}


OBL_MAP: Dict[str, List[str]] = {
    "OBL-STRUCT-NINE-TABLES": ["GS-S01"], "OBL-STRUCT-EMPTY-STATEMENT": ["GS-S01"], "OBL-STRUCT-TABLE-NAMES": ["GS-S01"],
    "OBL-STRUCT-SECTIONS": ["GS-S02"], "OBL-STRUCT-TOC-ALIGN": ["GS-S03"],
    "OBL-STRUCT-ORG-NAME": ["GS-S04"], "OBL-STRUCT-COVER": ["GS-S04"], "OBL-TEXT-YEAR": ["GS-S04"],
    "OBL-INTERNAL-T1-BALANCE": ["GS-T01"], "OBL-CROSS-T1-IDENTITY": ["GS-T01"],
    "OBL-INTERNAL-T2-TOTAL": ["GS-T02"], "OBL-INTERNAL-T3-TOTAL": ["GS-T03"], "OBL-INTERNAL-DETAIL-ROW": ["GS-T03"],
    "OBL-CROSS-FISCAL-TOTAL": ["GS-T04"], "OBL-INTERNAL-TABLE-TOTAL": ["GS-T05"],
    "OBL-INTERNAL-BASIC-EXPENSE": ["GS-T06", "GS-T08"], "OBL-SG-TOTAL": ["GS-T07"], "OBL-SG-ITEMS": ["GS-T07"],
    "OBL-CROSS-T1-T2": ["GS-X01"], "OBL-CROSS-T1-T3": ["GS-X01"], "OBL-CROSS-T4-FUNDS": ["GS-X02"],
    "OBL-TXT-T1": ["GS-N01"], "OBL-TXT-T2": ["GS-N02"], "OBL-TXT-T3": ["GS-N03"], "OBL-TXT-T4": ["GS-N04"],
    "OBL-TXT-T5": ["GS-N05"], "OBL-TXT-T6": ["GS-N06"], "OBL-TXT-FINAL-DETAIL": ["GS-N05"],
    "OBL-TXT-T7": ["GS-N07"], "OBL-SG-TABLE-TEXT": ["GS-N07"], "OBL-SG-COUNTS": ["GS-N07"], "OBL-SG-DISCLOSURE": ["GS-N07"],
    "OBL-SG-COMPLETION": ["GS-N07"], "OBL-SG-YOY": ["GS-N07"], "OBL-TXT-FUND-DETAIL": ["GS-N08"],
    "OBL-TREND-DIRECTION": ["GS-N09"], "OBL-TREND-COMPARATIVE-LOGIC": ["GS-N09"],
    "OBL-TREND-ZERO-BASE": [NARR], "OBL-TREND-COMPLETION-RATE": [NARR], "OBL-NARRATIVE-AMOUNT": [NARR],
    "OBL-NARRATIVE-INDICATOR-REPEAT": [NARR], "OBL-NARRATIVE-SHARE-SUM": [NARR],
    "OBL-TEXT-AMOUNT-UNIT": ["GS-W01"],
    "OBL-TEXT-AMOUNT-FORMAT": ["free:R9-千分位", "free:R9-小数位", "free:R9-双小数点"],
    "OBL-STRUCT-PUNCTUATION": ["free:R9-标点连用", "free:R9-半角冒号", "free:R22", "free:B-重复词"],
    "OBL-STRUCT-TERMINOLOGY": ["free:R21"], "OBL-STRUCT-NUMBERING": ["free:R24"],
    "OBL-STRUCT-PLACEHOLDER": ["free:B-模板残留"], "OBL-TEXT-TYPO": ["free:B-错别字", "free:B-多字漏字"],
}
# 自动化覆盖用的检查→义务映射（O 层映射的反向；叙述类义务不计入某个单项）
CHECK_OBLIGATIONS: Dict[str, List[str]] = defaultdict(list)
for _oid, _targets in OBL_MAP.items():
    for _t in _targets:
        if _t.startswith("GS-"):
            CHECK_OBLIGATIONS[_t].append(_oid)


def expand_targets(targets: Sequence[str]) -> List[str]:
    out: List[str] = []
    for tgt in targets:
        if tgt == NARR:
            out.extend(N_CHECKS[:7])  # 协议 §3.2：叙述类义务对应 N01–N07
        else:
            out.append(tgt)
    return list(dict.fromkeys(out))


# ---------------------------------------------------------------------------
# 匹配
# ---------------------------------------------------------------------------


def quote_hit(item: Dict[str, Any], unit: Dict[str, Any]) -> List[str]:
    """FU：系统问题引用的原文串与金标出现点原文互相包含（页重合另判）。"""
    sys_strings = {norm(q) for q in item["quotes"] if len(norm(q)) >= 3}
    sys_strings |= {norm(s) for s in re.findall(r"[“\"]([^”\"]{2,40})[”\"]", item["text"])}
    sys_text = norm(item["text"])
    hits = []
    for occ in unit["values"]:
        gq = norm(occ["quote"])
        if len(gq) < 3:
            continue
        if any(s and (s in gq or gq in s) for s in sys_strings) or (len(gq) >= 4 and gq in sys_text):
            hits.append(occ["quote"][:40])
    return hits


def anchor_hit(item: Dict[str, Any], unit: Dict[str, Any]) -> List[str]:
    text = norm(item["text"]).replace(",", "")
    hits = [n for n in unit["numbers"] if n in text]
    hits += [g for g in unit["ngrams"] if g in norm(item["text"])]
    return hits


def covered_pages(doc: Dict[str, Any], item: Dict[str, Any], keys: Sequence[str]) -> Tuple[Set[int], List[str]]:
    """协议 §4.2.4：目标检查已检页 ∪ 穷尽性 FU 类的文字部分页，与 pages(I) 的交。"""
    pages = set(item["pages"])
    exam: Set[int] = set()
    used: List[str] = []
    for tgt in keys:
        if tgt.startswith("GS-"):
            got = set(doc["exam"].get(tgt, []))
            if got & pages:
                used.append(tgt)
            exam |= got
        elif tgt.startswith("free:") and tgt[5:] in EXH_FREE:
            lo, hi = doc["table_part"]
            text_pages = {p for p in range(1, doc["pages"] + 1) if not (lo <= p <= hi)}
            if text_pages & pages:
                used.append(tgt)
            exam |= text_pages
    return exam & pages, used


def compatible(unit: Dict[str, Any], compat: Sequence[Tuple[str, Optional[str]]]) -> bool:
    key = unit["check"] if unit["kind"] == "CU" else f"free:{unit['cls']}"
    for tgt, label_re in compat:
        if tgt == key and (label_re is None or re.search(label_re, unit.get("label") or "")):
            return True
    return False


def match_item(item: Dict[str, Any], doc: Dict[str, Any]) -> Dict[str, Any]:
    ct = classify_ct(item["rule"], item["text"])
    compat = COMPAT[ct]
    out: Dict[str, Any] = {"id": item["id"], "doc": item["doc"], "tier": item["tier"], "rule": item["rule"], "ct": ct,
                           "targets": [t for t, _ in compat], "links": [], "ambiguous": [], "status": "", "why": ""}
    if compat == [(NOGOLD, None)]:
        out.update(status="NOGOLD", why="协议 §3.1：无金标对应")
        return out
    pages = set(item["pages"])
    for unit in doc["units"]:
        if not compatible(unit, compat):
            continue
        if unit["kind"] == "FU" and unit["cls"] == "R21":
            # 文档级单位（整份一条）：同文档、同类即连边，页重合只作记录
            out["links"].append({"unit": unit["unit_id"], "how": "doc-level", "pages": sorted(pages & set(unit["pages"]))})
            continue
        page_hit = pages & set(unit["pages"])
        if not page_hit:
            continue
        hits = quote_hit(item, unit) if unit["kind"] == "FU" else anchor_hit(item, unit)
        if hits:
            out["links"].append({"unit": unit["unit_id"], "how": "page+anchor", "anchors": hits[:5], "pages": sorted(page_hit)})
        else:
            out["ambiguous"].append({"unit": unit["unit_id"], "pages": sorted(page_hit)})
    if out["links"]:
        out.update(status="TP", why="页重合+锚点重合的金标 defect 单位")
        return out
    if out["ambiguous"]:
        out.update(status="AMBIGUOUS", why="与金标 defect 页重合但无锚点重合")
        return out
    covered, used = covered_pages(doc, item, [t for t, _ in compat])
    if covered:
        out.update(status="FP_G", why=f"目标检查已检页覆盖 {sorted(covered)}（{','.join(used) or '穷尽类文字部分'}），无 defect 单位")
        return out
    out.update(status="UNMATCHED", why="金标未检查该处/该类为尽力类")
    return out


def match_o(item: Dict[str, Any], doc: Dict[str, Any]) -> Dict[str, Any]:
    targets = expand_targets(OBL_MAP.get(item["rule"], [NOGOLD]))
    links = []
    for unit in doc["units"]:
        key = unit["check"] if unit["kind"] == "CU" else f"free:{unit['cls']}"
        if key in targets and (unit["kind"] == "CU" or unit["cls"] in EXH_FREE or True):
            links.append({"unit": unit["unit_id"], "how": "check-level"})
    status = "NOGOLD" if targets == [NOGOLD] else ("O_HIT" if links else "O_NOISE")
    return {"id": item["id"], "doc": item["doc"], "tier": "O", "rule": item["rule"], "ct": "义务", "targets": targets,
            "links": links, "ambiguous": [], "status": status, "why": "文档级：所属检查在金标有 defect 单位" if links else ""}


# ---------------------------------------------------------------------------
# 阶段
# ---------------------------------------------------------------------------


def extract_main(args: argparse.Namespace) -> int:
    ANALYSIS.mkdir(parents=True, exist_ok=True)
    docs = load_gold()
    items, aux = load_system(OUT / args.run, docs)
    bad = {sid: v for sid, v in aux["selfcheck"].items() if not v["ok"]}
    (ANALYSIS / "system_items.json").write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    (ANALYSIS / "gold_docs.json").write_text(json.dumps(docs, ensure_ascii=False, indent=1), encoding="utf-8")
    (ANALYSIS / "selfcheck_counts.json").write_text(json.dumps(aux["selfcheck"], ensure_ascii=False, indent=1), encoding="utf-8")
    (ANALYSIS / "ledgers.json").write_text(json.dumps(aux["ledgers"], ensure_ascii=False), encoding="utf-8")
    tiers = Counter(i["tier"] for i in items)
    units = Counter((u["kind"], u["cls"] if u["kind"] == "FU" else u["check"]) for d in docs.values() for u in d["units"])
    print(json.dumps({"tiers": tiers, "selfcheck_mismatch": bad, "gold_units": sum(units.values())}, ensure_ascii=False, indent=1, default=dict))
    if bad:
        print("条数自检不一致，停止", file=sys.stderr)
        return 2
    return 0


def match_main(args: argparse.Namespace) -> int:
    docs = json.loads((ANALYSIS / "gold_docs.json").read_text(encoding="utf-8"))
    items = json.loads((ANALYSIS / "system_items.json").read_text(encoding="utf-8"))
    results = []
    for item in items:
        if item["tier"] in {"N", "D"}:
            continue
        doc = docs[item["doc"]]
        if item["tier"] == "O":
            results.append(match_o(item, doc))
        else:
            results.append(match_item(item, doc))
    (ANALYSIS / "auto_match.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    by = Counter((r["tier"], r["status"]) for r in results)
    print(json.dumps({f"{k[0]}:{k[1]}": v for k, v in sorted(by.items())}, ensure_ascii=False, indent=1))
    return 0



# ---------------------------------------------------------------------------
# metrics：合并人工复核结论，计算协议 §6 的全部指标
# ---------------------------------------------------------------------------


def unit_key(unit: Dict[str, Any]) -> str:
    return unit["check"] if unit["kind"] == "CU" else f"free:{unit['cls']}"


def finalize(items: List[Dict[str, Any]], auto: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    sys.path.insert(0, str(REPO / "scripts"))
    import gold_baseline_decisions as dec

    by_id = {i["id"]: i for i in items}
    rows: List[Dict[str, Any]] = []
    used = set()
    for r in auto:
        it = by_id[r["id"]]
        key = f"{it['doc']}|{it.get('raw_id')}"
        row = {"id": r["id"], "doc": it["doc"], "tier": it["tier"], "rule": it["rule"], "raw_id": it.get("raw_id") or it["rule"],
               "sev": it.get("sev"), "ct": r["ct"], "pages": it["pages"], "title": it["title"], "message": it["message"],
               "auto_status": r["status"], "auto_links": [l["unit"] for l in r["links"]],
               "final": "", "links": [], "cause": "", "why": "", "evidence": "", "amend": False, "decided": "auto"}
        d = dec.D.get(key) if it["tier"] in {"F", "P"} else None
        if d:
            used.add(key)
            row.update(final=d["final"], cause=d["cause"], why=d["why"], evidence=d["evidence"], amend=d["amend"], decided="manual")
            if d["final"] in {"TP", "HIT"}:
                row["links"] = list(d["links"]) if d["links"] is not None else list(row["auto_links"])
            else:
                row["links"] = list(d["links"] or [])  # 非命中条目不带连边（自动连边只是被否决的提议）
        else:
            st = r["status"]
            if it["tier"] == "F":
                if st != "TP":
                    raise SystemExit(f"F 条目缺少人工复核结论：{key} 自动状态 {st}")
                row.update(final="TP", links=list(row["auto_links"]))
            elif it["tier"] == "P":
                if st == "TP":
                    row.update(final="HIT", links=list(row["auto_links"]))
                elif st == "NOGOLD":
                    row.update(final="OUT_OF_SCOPE", why="D-011 同科目收入表/支出表金额差异：口径决议⑤不判错，无金标对应")
                else:
                    raise SystemExit(f"P 条目缺少人工复核结论：{key} 自动状态 {st}")
            elif it["tier"] == "O":
                row.update(final=st, links=list(row["auto_links"]))
            else:  # R
                row.update(final="REF_HIT" if st == "TP" else "REF_OTHER", links=list(row["auto_links"]) if st == "TP" else [])
        rows.append(row)
    unused = set(dec.D) - used
    if unused:
        raise SystemExit(f"复核结论没有对应条目：{sorted(unused)}")
    return rows


def rate(num: int, den: int) -> Optional[float]:
    return None if den == 0 else round(num / den, 4)


def metrics_main(args: argparse.Namespace) -> int:
    docs = json.loads((ANALYSIS / "gold_docs.json").read_text(encoding="utf-8"))
    items = json.loads((ANALYSIS / "system_items.json").read_text(encoding="utf-8"))
    auto = json.loads((ANALYSIS / "auto_match.json").read_text(encoding="utf-8"))
    ledgers = json.loads((ANALYSIS / "ledgers.json").read_text(encoding="utf-8"))
    rows = finalize(items, auto)
    (ANALYSIS / "final_rows.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    todo = {sid: read_gz(OUT / "run1" / "todo" / f"{sid}.todo.json.gz") for sid in docs}

    F = [r for r in rows if r["tier"] == "F"]
    P = [r for r in rows if r["tier"] == "P"]
    O = [r for r in rows if r["tier"] == "O"]
    R = [r for r in rows if r["tier"] == "R"]
    m: Dict[str, Any] = {}

    # ---- 误报率
    cnt = Counter(r["final"] for r in F)
    tp, fpg, at, af, ind = cnt["TP"], cnt["FP_GOLD"], cnt["ADJ_TRUE"], cnt["ADJ_FALSE"], cnt["INDET"]
    m["F_total"] = len(F)
    m["F_final"] = dict(cnt)
    m["FPR_headline"] = {"num": fpg + af, "den": tp + fpg + at + af, "rate": rate(fpg + af, tp + fpg + at + af)}
    m["FPR_strict_gold"] = {"num": fpg, "den": tp + fpg, "rate": rate(fpg, tp + fpg)}
    amend_fp = [r for r in F if r["final"] == "FP_GOLD" and r["amend"]]
    m["FPR_amended"] = {"num": fpg + af - len(amend_fp), "den": tp + fpg + at + af,
                        "rate": rate(fpg + af - len(amend_fp), tp + fpg + at + af), "reclassified": len(amend_fp)}
    casc = [r for r in F if r["cause"] == "CASCADE"]
    m["FPR_excl_cascade"] = {"num": fpg + af - len(casc), "den": tp + fpg + at + af - len(casc),
                             "rate": rate(fpg + af - len(casc), tp + fpg + at + af - len(casc)), "cascade_items": len(casc)}
    conflict = [r for r in F if r["final"] in {"FP_GOLD", "ADJ_FALSE"} and r["cause"] in {"D2", "R3", "R004", "STD_CONFLICT", "CASCADE", "R16", "ROUNDING", "TEMPLATE_NATIVE"}]
    m["FPR_excl_standard_conflicts"] = {"num": fpg + af - len(conflict), "den": tp + fpg + at + af - len(conflict),
                                       "rate": rate(fpg + af - len(conflict), tp + fpg + at + af - len(conflict)),
                                       "excluded": Counter(r["cause"] for r in conflict)}
    m["FP_by_cause"] = Counter((r["cause"] or "OTHER") for r in F if r["final"] in {"FP_GOLD", "ADJ_FALSE"})
    by_rule: Dict[str, Counter] = defaultdict(Counter)
    for r in F:
        by_rule[r["rule"]][r["final"]] += 1
    m["F_by_rule"] = {k: dict(v) for k, v in sorted(by_rule.items())}
    by_doc_year: Dict[str, Counter] = defaultdict(Counter)
    for r in F:
        key = f"{docs[r['doc']]['year']}|{docs[r['doc']]['scope']}"
        by_doc_year[key][r["final"]] += 1
    m["F_by_year_scope"] = {k: dict(v) for k, v in sorted(by_doc_year.items())}
    by_year: Dict[str, Counter] = defaultdict(Counter)
    for r in F:
        by_year[str(docs[r["doc"]]["year"])][r["final"]] += 1
    m["F_by_year"] = {k: dict(v) for k, v in sorted(by_year.items())}
    by_scope: Dict[str, Counter] = defaultdict(Counter)
    for r in F:
        by_scope[docs[r["doc"]]["scope"]][r["final"]] += 1
    m["F_by_scope"] = {k: dict(v) for k, v in sorted(by_scope.items())}
    # O 层界面口径
    ocnt = Counter(r["final"] for r in O)
    m["O_final"] = dict(ocnt)
    onoise, ohit = ocnt["O_NOISE"], ocnt["O_HIT"]
    m["FPR_with_O"] = {"num": fpg + af + onoise, "den": tp + fpg + at + af + ohit + onoise,
                       "rate": rate(fpg + af + onoise, tp + fpg + at + af + ohit + onoise)}

    # ---- 漏报率：金标单位
    hit_f: Dict[str, List[str]] = defaultdict(list)
    hit_p: Dict[str, List[str]] = defaultdict(list)
    hit_r: Dict[str, List[str]] = defaultdict(list)
    for r in F:
        if r["final"] == "TP":
            for u in r["links"]:
                hit_f[u].append(r["id"])
    for r in P:
        if r["final"] == "HIT":
            for u in r["links"]:
                hit_p[u].append(r["id"])
    for r in R:
        if r["final"] == "REF_HIT":
            for u in r["links"]:
                hit_r[u].append(r["id"])
    o_by_doc: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in O:
        o_by_doc[r["doc"]].append(r)
    unit_rows = []
    for sid, doc in docs.items():
        for u in doc["units"]:
            key = unit_key(u)
            o_hits = [o["id"] for o in o_by_doc[sid] if key in expand_targets(OBL_MAP.get(o["rule"], []))]
            state = ("F" if hit_f.get(u["unit_id"]) else "PEND" if (hit_p.get(u["unit_id"]) or o_hits)
                     else "REF" if hit_r.get(u["unit_id"]) else "SILENT")
            exhaustive = u["kind"] == "CU" or u.get("exhaustive")
            # 诊断：该检查/类对应的义务在账本里的状态
            obl_ids = sorted({o for o, tg in OBL_MAP.items() if key in expand_targets(tg)})
            obl_status = {o: (ledgers[sid].get(o) or {}).get("status") for o in obl_ids}
            rules_reach = sorted({ct for ct, comp in COMPAT.items() if any(t == key for t, _ in comp)})
            unit_rows.append({"unit_id": u["unit_id"], "doc": sid, "year": doc["year"], "scope": doc["scope"], "kind": u["kind"],
                              "key": key, "cls": u["cls"], "label": u.get("label"), "pages": u["pages"], "exhaustive": exhaustive,
                              "listed_best_effort": u["kind"] == "FU" and u["cls"] in BEST_LISTED,
                              "quote": " ／ ".join(v["quote"].replace("\n", " ")[:70] for v in u["values"][:3]), "n_occ": len(u["values"]),
                              "note": (u.get("note") or u.get("label") or "")[:240],
                              "state": state, "hit_F": hit_f.get(u["unit_id"], []), "hit_P": hit_p.get(u["unit_id"], []),
                              "hit_O": o_hits, "hit_R": hit_r.get(u["unit_id"], []), "obl_status": obl_status, "ct_reach": rules_reach})
    ex = [u for u in unit_rows if u["exhaustive"]]
    miss = [u for u in ex if u["state"] != "F"]
    silent = [u for u in ex if u["state"] in {"SILENT", "REF"}]
    full_silent = [u for u in ex if u["state"] == "SILENT"]
    m["gold_units"] = {"CU": sum(1 for u in unit_rows if u["kind"] == "CU"), "FU_total": sum(1 for u in unit_rows if u["kind"] == "FU"),
                       "FU_exhaustive": sum(1 for u in unit_rows if u["kind"] == "FU" and u["exhaustive"])}
    m["MR_overall"] = {"num": len(miss), "den": len(ex), "rate": rate(len(miss), len(ex))}
    m["MR_silent_literal"] = {"num": len(silent), "den": len(ex), "rate": rate(len(silent), len(ex)), "of_which_REF_only": len(silent) - len(full_silent),
                              "fully_silent": len(full_silent)}
    cu = [u for u in ex if u["kind"] == "CU"]
    fu = [u for u in ex if u["kind"] == "FU"]
    for name, grp in (("CU", cu), ("FU_exhaustive", fu)):
        mm = [u for u in grp if u["state"] != "F"]
        ss = [u for u in grp if u["state"] in {"SILENT", "REF"}]
        m[f"MR_{name}"] = {"num": len(mm), "den": len(grp), "rate": rate(len(mm), len(grp)), "silent_literal": len(ss),
                           "silent_rate": rate(len(ss), len(grp))}
    def group_rate(keyf):
        g: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for u in ex:
            g[keyf(u)].append(u)
        return {k: {"den": len(v), "miss": sum(1 for u in v if u["state"] != "F"), "silent": sum(1 for u in v if u["state"] in {"SILENT", "REF"}),
                    "pend_only": sum(1 for u in v if u["state"] == "PEND")} for k, v in sorted(g.items())}
    m["MR_by_check"] = group_rate(lambda u: u["key"])
    m["MR_by_year"] = group_rate(lambda u: str(u["year"]))
    m["MR_by_scope"] = group_rate(lambda u: u["scope"])
    best = [u for u in unit_rows if u["kind"] == "FU" and not u["exhaustive"]]
    m["best_effort"] = {"total": len(best), "hit_F": sum(1 for u in best if u["state"] == "F"),
                        "by_class": {c: {"total": sum(1 for u in best if u["cls"] == c), "hit_F": sum(1 for u in best if u["cls"] == c and u["state"] == "F")}
                                     for c in sorted({u["cls"] for u in best})}}
    unl = [u for u in best if u["cls"] in BEST_UNLISTED]
    m["MR_sensitivity_incl_unlisted_best_effort"] = {"num": len(miss) + sum(1 for u in unl if u["state"] != "F"), "den": len(ex) + len(unl),
                                                      "rate": rate(len(miss) + sum(1 for u in unl if u["state"] != "F"), len(ex) + len(unl))}
    # 出现点级召回（穷尽类 FU）
    occ_total = occ_hit = 0
    occ_detail = []
    sysq = defaultdict(list)
    for r in F:
        if r["final"] == "TP":
            sysq[r["doc"]].append(r)
    by_id = {i["id"]: i for i in items}
    for sid, doc in docs.items():
        for u in doc["units"]:
            if u["kind"] != "FU" or not u.get("exhaustive") or u["cls"] == "R21":
                continue
            for occ in u["values"]:
                occ_total += 1
                gq = norm(occ["quote"])
                hit = False
                for r in sysq[sid]:
                    if u["unit_id"] not in r["links"]:
                        continue
                    it = by_id[r["id"]]
                    if occ["page"] in it["pages"] and len(gq) >= 3 and (gq in norm(it["text"]) or any(norm(q) in gq for q in it["quotes"] if len(norm(q)) >= 3)):
                        hit = True
                        break
                occ_hit += int(hit)
    m["occurrence_recall"] = {"hit": occ_hit, "total": occ_total, "rate": rate(occ_hit, occ_total)}

    # ---- 待核实命中率
    pc = Counter(r["final"] for r in P)
    m["P_final"] = dict(pc)
    ptot = len(P)
    p_scope = ptot - pc["OUT_OF_SCOPE"]
    m["PendingHit"] = {"HIT": pc["HIT"], "of_in_scope": rate(pc["HIT"], p_scope), "in_scope_den": p_scope,
                       "of_all_P": rate(pc["HIT"], ptot), "all_P": ptot,
                       "HIT_or_true_not_in_gold": rate(pc["HIT"] + pc["TRUE_NOT_IN_GOLD"], p_scope)}
    o_total = len(O)
    o_scope = o_total - ocnt["NOGOLD"]
    m["PendingHit_P_plus_O"] = {"num": pc["HIT"] + ohit, "den": ptot + o_total, "rate": rate(pc["HIT"] + ohit, ptot + o_total),
                                "in_scope_den": p_scope + o_scope, "in_scope_rate": rate(pc["HIT"] + ohit, p_scope + o_scope)}
    m["P_by_rule"] = {k: dict(v) for k, v in sorted({rule: Counter(r["final"] for r in P if r["rule"] == rule) for rule in {r["rule"] for r in P}}.items())}
    m["O_by_obligation"] = {k: dict(v) for k, v in sorted({oid: Counter(r["final"] for r in O if r["rule"] == oid) for oid in {r["rule"] for r in O}}.items())}

    # ---- 人工工作量
    work = []
    for sid in docs:
        q = todo[sid]["queue_counts"]
        work.append({"doc": sid, "year": docs[sid]["year"], "scope": docs[sid]["scope"], "F": q["queue_required_total"], "O": q["queue_obligation_total"],
                     "P": q["queue_candidate_total"], "workload": q["queue_required_total"] + q["queue_obligation_total"] + q["queue_candidate_total"],
                     "R_tip": q["queue_tip_total"], "R_notice": q["queue_notice_total"]})
    ws = [w["workload"] for w in work]
    m["workload"] = {"mean": round(statistics.mean(ws), 2), "median": statistics.median(ws), "min": min(ws), "max": max(ws), "total": sum(ws),
                     "F_total": sum(w["F"] for w in work), "O_total": sum(w["O"] for w in work), "P_total": sum(w["P"] for w in work),
                     "mean_F": round(statistics.mean(w["F"] for w in work), 2), "mean_O": round(statistics.mean(w["O"] for w in work), 2),
                     "mean_P": round(statistics.mean(w["P"] for w in work), 2),
                     "P_excl_CMM004_total": sum(1 for r in P if r["rule"] != "CMM-004"),
                     "mean_workload_excl_CMM004": round(statistics.mean(w["workload"] - sum(1 for r in P if r["doc"] == w["doc"] and r["rule"] == "CMM-004") for w in work), 2),
                     "F_true_share": rate(tp + at, tp + fpg + at + af)}
    for key in ("year", "scope"):
        g: Dict[str, List[int]] = defaultdict(list)
        for w in work:
            g[str(w[key])].append(w["workload"])
        m[f"workload_by_{key}"] = {k: {"n": len(v), "mean": round(statistics.mean(v), 2)} for k, v in sorted(g.items())}

    # ---- 自动化覆盖
    cov_rows = []
    all_checks = [c for c in sorted({u for d in docs.values() for u in d["verdicts"]})]
    for sid, doc in docs.items():
        covered_map = todo[sid]["covered_by_finding"]
        for chk in all_checks:
            obls = CHECK_OBLIGATIONS.get(chk, [])
            states = {o: (ledgers[sid].get(o) or {}).get("status") for o in obls}
            def settled(o: str) -> bool:
                st = states[o]
                return st in {"completed", "not_applicable"} or bool(covered_map.get(o))
            has_obl = [o for o in obls if states[o] is not None]
            verdict = "none"
            if has_obl and all(settled(o) for o in has_obl):
                f_here = any(r["doc"] == sid and r["tier"] == "F" and chk in {t for t, _ in COMPAT[r["ct"]]} for r in rows)
                verdict = "defect" if f_here else "pass"
            cov_rows.append({"doc": sid, "check": chk, "gold": doc["verdicts"][chk], "system": verdict,
                             "obligations": {o: states[o] for o in has_obl}})
    withv = sum(1 for c in cov_rows if c["system"] != "none")
    m["coverage"] = {"with_verdict": withv, "total": len(cov_rows), "rate": rate(withv, len(cov_rows))}
    per_check: Dict[str, Counter] = defaultdict(Counter)
    for c in cov_rows:
        per_check[c["check"]][c["system"]] += 1
    m["coverage_by_check"] = {k: dict(v) for k, v in sorted(per_check.items())}
    m["coverage_by_unresolved_obligation"] = Counter(o for c in cov_rows if c["system"] == "none" for o, st in c["obligations"].items() if st not in {"completed", "not_applicable"})
    # 系统自认通过但金标 defect（假通过）
    false_pass = [c for c in cov_rows if c["gold"] == "defect" and c["system"] == "pass"]
    m["false_pass_checks"] = {"count": len(false_pass), "of_gold_defect_checks": sum(1 for c in cov_rows if c["gold"] == "defect"),
                              "by_check": Counter(c["check"] for c in false_pass)}
    # 金标单位级：未命中且义务全部 completed
    m["miss_diag"] = Counter()
    for u in miss:
        st = list(u["obl_status"].values())
        if not st:
            diag = "无对应义务/规则"
        elif not u["ct_reach"]:
            diag = "无对应规则(CT)"
        elif all(x in {"completed", "not_applicable"} for x in st):
            diag = "义务已完成(系统自认查完)"
        else:
            diag = "义务未完成(取数不足等)"
        u["diag"] = diag
        m["miss_diag"][diag] += 1

    m["gold_amendment_candidates"] = [{"id": r["id"], "rule": r["rule"], "pages": r["pages"], "cause": r["cause"], "why": r["why"], "evidence": r["evidence"]}
                                      for r in rows if r.get("amend") and r["tier"] in {"F", "P"}]
    m["tiers"] = Counter(r["tier"] for r in rows)
    (ANALYSIS / "metrics.json").write_text(json.dumps(m, ensure_ascii=False, indent=1, default=dict), encoding="utf-8")
    (ANALYSIS / "gold_unit_status.json").write_text(json.dumps(unit_rows, ensure_ascii=False, indent=1), encoding="utf-8")
    (ANALYSIS / "coverage_matrix.json").write_text(json.dumps(cov_rows, ensure_ascii=False, indent=1), encoding="utf-8")
    (ANALYSIS / "workload.json").write_text(json.dumps(work, ensure_ascii=False, indent=1), encoding="utf-8")
    write_csvs(rows, unit_rows, cov_rows, work)
    print(json.dumps({k: m[k] for k in ("F_final", "FPR_headline", "FPR_strict_gold", "FPR_amended", "MR_overall", "MR_silent_literal", "PendingHit", "PendingHit_P_plus_O", "workload", "coverage")},
                     ensure_ascii=False, indent=1, default=dict))
    return 0


def write_csvs(rows, unit_rows, cov_rows, work) -> None:
    with (OUT / "match_rows.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["sample_id", "tier", "system_item_id", "rule_or_obligation", "severity", "claim_type", "pages", "auto_status", "final", "decided_by",
                    "linked_gold_units", "cause", "gold_amendment_candidate", "why", "pdf_evidence", "title", "message"])
        for r in rows:
            w.writerow([r["doc"], r["tier"], r["raw_id"], r["rule"], r["sev"], r["ct"], " ".join(map(str, r["pages"])), r["auto_status"], r["final"], r["decided"],
                        ";".join(r["links"]), r["cause"], "Y" if r.get("amend") else "", r["why"], r["evidence"], r["title"], r["message"]])
    with (OUT / "gold_unit_status.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["unit_id", "sample_id", "year", "scope", "kind", "check_or_class", "label", "pages", "gold_quote(前3处)", "occurrences", "gold_note_or_expected", "in_miss_denominator", "state", "hit_F", "hit_P", "hit_O", "hit_R", "miss_diagnosis"])
        for u in unit_rows:
            w.writerow([u["unit_id"], u["doc"], u["year"], u["scope"], u["kind"], u["key"], u.get("label") or "", " ".join(map(str, u["pages"])), u["quote"], u["n_occ"], u["note"],
                        "Y" if u["exhaustive"] else "N(尽力类)", u["state"], ";".join(u["hit_F"]), ";".join(u["hit_P"]), ";".join(u["hit_O"]), ";".join(u["hit_R"]), u.get("diag", "")])
    with (OUT / "coverage_matrix.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["sample_id", "check", "gold_verdict", "system_verdict", "mapped_obligation_status"])
        for c in cov_rows:
            w.writerow([c["doc"], c["check"], c["gold"], c["system"], json.dumps(c["obligations"], ensure_ascii=False)])
    with (OUT / "workload.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["sample_id", "year", "scope", "F_required", "O_unverified", "P_pending", "workload_F+O+P", "R_tips", "R_notices"])
        for x in work:
            w.writerow([x["doc"], x["year"], x["scope"], x["F"], x["O"], x["P"], x["workload"], x["R_tip"], x["R_notice"]])


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("extract")
    p.add_argument("--run", default="run1")
    sub.add_parser("match")
    sub.add_parser("metrics")
    args = parser.parse_args(argv)
    return {"extract": extract_main, "match": match_main, "metrics": metrics_main}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
