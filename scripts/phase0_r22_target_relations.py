#!/usr/bin/env python
"""R2.2 阶段0：目标关系清单取证（离线、只读、AI/OCR 关）。

按 R2.2 计划 §3（目标关系清单种子）与 §第四部分（阶段0出口1），对 5 条目标
关系在 main 基线代码上离线复跑真实 native 入口，冻结"当前阻断位置及当前
结果"。组装链与 api/main.py 生产分析一致：

    pdfplumber 页文本/页表 → PDFParser.build_parse_artifact_from_handle
    → resolve_document_profile（封面主体/年度/文种）
    → resolve_check_subject（检查主体）
    → build_fact_context_from_artifact（统一事实）
    → build_document + fact_context + parse_artifact
    → run_rules_with_outcomes（真实规则执行函数）

冻结配置：AI 关（use_ai_assist=False）、OCR 关（不做 maybe_ocr_scanned_pages，
原生文本层材料本就无需 OCR）、缓存关（无结果复用，全部现算）。
本脚本只读输入 PDF 与 status.json，不写 uploads/、不碰数据库与运行服务。

用法：
    .venv/bin/python scripts/phase0_r22_target_relations.py \
        --output outputs/r22_phase0_20261107/target_relations_probe.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

# IR06：被测代码根可经环境变量重定向（before 集采集指向 main 检出，
# 不在 main 工作树写任何文件）；缺省为本脚本所在 worktree。
_REPO_ROOT = Path(os.environ.get(
    "R22_PROBE_REPO_ROOT", str(Path(__file__).resolve().parents[1])))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pdfplumber  # noqa: E402

from src.engine.rules_v33 import (  # noqa: E402
    R33005_TableTotalConsistency,
    R33104_Expense_vs_Text,
    R33222_Narrative5_T5,
    R33240_Table2_IncomeAdvancedCheck,
    R33CrossSanGongEcon,
    build_document,
)
from src.engine.pipeline import run_rules_with_outcomes  # noqa: E402
from src.engine.fact_context import build_fact_context_from_artifact  # noqa: E402
from src.services.check_subject import resolve_check_subject  # noqa: E402
from src.services.document_profile_resolver import resolve_document_profile  # noqa: E402
from src.services.material_writing import qualified_identity_view  # noqa: E402
from src.services.pdf_page_extract import (  # noqa: E402
    extract_tables_from_page,
    extract_visible_text_from_page,
)
from src.services.pdf_parser import PDFParser  # noqa: E402

#: 5 条目标关系 → 规则类（rules_v33 真实类名，阶段0冻结）。
TARGET_RULES = {
    "V33-005": R33005_TableTotalConsistency,
    "V33-104": R33104_Expense_vs_Text,
    "V33-222": R33222_Narrative5_T5,
    "V33-240": R33240_Table2_IncomeAdvancedCheck,
    "V33-CROSS-SAN-GONG-ECON": R33CrossSanGongEcon,
}

#: 目标 PDF（只读输入；SHA256 于运行时计算并写进输出）。
TARGET_PDFS = [
    {"label": "残联单位决算", "path": "/Volumes/workSSD/software development/GovBudgetChecker/uploads/79efdd8f0d7b3a2938de65cfb5aad101/上海市普陀区残疾人联合会2025年度单位决算.pdf",
     "job_uuid": "79efdd8f0d7b3a2938de65cfb5aad101",
     "org": {"name": "上海市普陀区残疾人联合会", "level": "unit"}},
    {"label": "知识产权局决算", "path": "/Volumes/workSSD/software development/GovBudgetChecker/uploads/b589d6bd4436edaa140a224135bf10d0/上海市知识产权局.pdf",
     "job_uuid": "b589d6bd4436edaa140a224135bf10d0",
     "org": {"name": None, "level": None}},
    {"label": "水务局决算", "path": "/Volumes/workSSD/software development/GovBudgetChecker/uploads/02e4fb64ab6bbf325117d325a2dcbfa4/上海市水务局.pdf",
     "job_uuid": "02e4fb64ab6bbf325117d325a2dcbfa4",
     "org": {"name": None, "level": None}},
    {"label": "外事办决算", "path": "/Volumes/workSSD/software development/GovBudgetChecker/uploads/f1a4c441a3bd02ec4cce465b1739f046/上海市人民政府外事办公室.pdf",
     "job_uuid": "f1a4c441a3bd02ec4cce465b1739f046",
     "org": {"name": None, "level": None}},
]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def issue_brief(issue) -> dict:
    location = getattr(issue, "location", None) or {}
    return {
        "rule": getattr(issue, "rule", None),
        "severity": getattr(issue, "severity", None),
        "message": str(getattr(issue, "message", ""))[:300],
        "page": location.get("page"),
    }


def build_offline_doc(pdf_path: Path, org: dict) -> dict:
    """按生产组装链构建 Document（不触库、不触 AI/OCR）。"""
    file_hash = sha256_file(pdf_path)
    p_texts: list[str] = []
    p_tables: list = []
    artifact_payload_error = None
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            p_texts.append(extract_visible_text_from_page(page))
            p_tables.append(extract_tables_from_page(page))
        try:
            parser = PDFParser(None)
            artifact = parser.build_parse_artifact_from_handle(pdf, file_hash=file_hash)
        except Exception as exc:  # noqa: BLE001
            artifact = None
            artifact_payload_error = f"{type(exc).__name__}: {exc}"

    if artifact is None:
        return {"file_hash": file_hash, "artifact_error": artifact_payload_error, "doc": None}

    page_texts = list(p_texts)
    page_tables = [list(tables) for tables in p_tables]
    profile_page_texts, writing_source_spans = qualified_identity_view(page_texts, artifact)
    operand_profile = resolve_document_profile(
        filename=pdf_path.name, page_texts=profile_page_texts, explicit_report_kind=None)
    identity_conflicts = {item.get("field") for item in operand_profile.conflicts}
    organization_name = org.get("name")
    if not organization_name and "organization_name" not in identity_conflicts:
        organization_name = operand_profile.organization_name.value
    report_year = operand_profile.year
    report_kind = operand_profile.kind if operand_profile.kind in {"budget", "final"} else None
    doc_type = {"budget": "dept_budget", "final": "dept_final"}.get(report_kind)

    check_subject = resolve_check_subject(
        organization_id=None,
        organization_name=organization_name,
        organization_level=org.get("level"),
        cover_name=operand_profile.organization_name.value,
        cover_conflicted="organization_name" in identity_conflicts,
        subject_level=operand_profile.subject_level.value,
        cover_text=profile_page_texts[0] if profile_page_texts else None,
    )
    check_subject_name = check_subject["name"]

    fact_context_obj = build_fact_context_from_artifact(
        artifact,
        org_name=check_subject_name,
        org_level=org.get("level"),
        fiscal_year=report_year,
        report_kind=report_kind,
        doc_type=doc_type,
        require_complete_identity=True,
    )

    filesize = pdf_path.stat().st_size
    document = build_document(
        path=str(pdf_path), page_texts=page_texts, page_tables=page_tables, filesize=filesize)
    document.fact_context = fact_context_obj
    document.parse_artifact = artifact
    document.document_profile = operand_profile
    document.writing_page_texts = profile_page_texts
    document.writing_source_spans = writing_source_spans
    return {
        "file_hash": file_hash,
        "artifact_error": None,
        "doc": document,
        "identity": {
            "check_subject_name": check_subject_name,
            "check_subject_source": check_subject.get("source"),
            "profile_org": operand_profile.organization_name.value,
            "profile_year": operand_profile.year,
            "profile_kind": operand_profile.kind,
            "report_kind": report_kind,
            "doc_type": doc_type,
            "fact_count": len(getattr(fact_context_obj, "facts", []) or []) if fact_context_obj else 0,
            "identity_conflicts": sorted(c for c in identity_conflicts if c),
        },
    }


def probe_pdf(target: dict) -> dict:
    pdf_path = Path(target["path"])
    result: dict = {
        "label": target["label"],
        "pdf_path": str(pdf_path),
        "job_uuid": target["job_uuid"],
        "sha256": sha256_file(pdf_path),
        "size_bytes": pdf_path.stat().st_size,
    }
    built = build_offline_doc(pdf_path, target["org"])
    result["file_hash"] = built["file_hash"]
    if built["doc"] is None:
        result["artifact_error"] = built["artifact_error"]
        result["rules"] = {}
        return result
    result["identity"] = built["identity"]
    doc = built["doc"]

    rules = [TARGET_RULES[code] for code in (
        "V33-005", "V33-104", "V33-222", "V33-240", "V33-CROSS-SAN-GONG-ECON")]
    doc.relation_receipts = []
    doc.fact_receipts = []
    doc.structured_consumption = []
    started = time.time()
    issues, outcomes = run_rules_with_outcomes(
        doc, use_ai_assist=False, report_kind="final", rules=rules)
    elapsed = time.time() - started

    target_codes = set(TARGET_RULES)
    result["rules"] = {}
    for outcome in outcomes:
        rule_id = outcome.rule_id
        if rule_id not in target_codes:
            continue
        rule_issues = [issue_brief(i) for i in issues if getattr(i, "rule", None) == rule_id]
        result["rules"][rule_id] = {
            "status": outcome.status,
            "detail": str(outcome.detail or ""),
            "partial_findings_count": outcome.partial_findings_count,
            "unresolved_reasons": [str(r) for r in outcome.unresolved_reasons],
            "reason_codes": list(outcome.reason_codes),
            "diagnostics": list(outcome.diagnostics),
            "issues": rule_issues,
        }
    # 关系级回执全集（IR06：fact_receipts 元素是 dict——用键读取；
    # 关系身份用真实 sub_check_id/check_relation_id，不编 relation_id；
    # 全状态保留，不截断；规则级零回执显式登记）。
    relations_by_rule: Dict[str, List[Dict[str, Any]]] = {code: [] for code in TARGET_RULES}
    for r in getattr(doc, "fact_receipts", []) or []:
        if not isinstance(r, dict):
            continue
        rid = str(r.get("rule_id") or "")
        if rid not in target_codes:
            continue
        relations_by_rule[rid].append({
            "relation_id": str(r.get("sub_check_id") or r.get("check_relation_id") or ""),
            "status": str(r.get("status") or ""),
            # R2-01/R2-05：证据性质随关系落账——恢复质量按性质分账，
            # 推导/适用性不冒计独立金额恢复。
            "evidence_nature": str(r.get("evidence_nature") or ""),
            "incomplete_reason": str(r.get("incomplete_reason") or "")[:400],
            "formula": str(r.get("formula") or "")[:300],
        })
    for rid, info in result["rules"].items():
        relations = relations_by_rule.get(rid, [])
        statuses = {r["status"] for r in relations} | {"pass", "fail", "insufficient_data", "not_applicable"}
        info["relations"] = relations
        info["receipt_counts_by_status"] = {
            status: sum(1 for r in relations if r["status"] == status)
            for status in sorted(statuses)
        }
        # 规则级零回执：规则跑完但一条关系回执都没有——"覆盖不可见"形态，
        # 显式记录而不是当作全覆盖。
        info["rule_level_zero_receipt"] = len(relations) == 0
    result["insufficient_receipts"] = [
        {"rule_id": rid, "relation_id": r["relation_id"],
         "incomplete_reason": r["incomplete_reason"]}
        for rid in TARGET_RULES for r in relations_by_rule[rid]
        if r["status"] in ("insufficient_data", "parse_error", "execution_error")
    ]
    result["elapsed_seconds"] = round(elapsed, 1)
    return result


def _git_head(repo_root: Path) -> str:
    import subprocess
    try:
        return subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=15).stdout.strip()
    except Exception:  # noqa: BLE001 — 无 git 环境时如实留空
        return ""


def _run_relations_stats(before_path: str, after_path: str, output: str) -> None:
    from src.services.remediation_statistics import (
        ATTR_CORRECTED_PREVIOUS_SKIP, ATTR_TECH_UNRESOLVED,
        aggregate_three_dimensions, derive_attribution, engine_dimension)
    before = json.loads(Path(before_path).read_text(encoding="utf-8"))
    after = json.loads(Path(after_path).read_text(encoding="utf-8"))

    def _relations(report):
        out = {}
        for target in report.get("targets", []):
            # 关系身份必须含 PDF 维度：sub_check_id 在不同原件间会重名
            # （同构表的同名子检查），跨原件合并会伪造分母。
            pdf_key = str(target.get("job_uuid") or target.get("sha256") or target.get("label"))
            for rid, info in (target.get("rules") or {}).items():
                for rel in info.get("relations") or []:
                    out[f"{pdf_key}::{rid}::{rel.get('relation_id')}"] = {
                        "rule_id": rid, "relation_id": rel.get("relation_id"),
                        "status": rel.get("status"),
                        "evidence_nature": rel.get("evidence_nature") or ""}
        return out

    before_rels, after_rels = _relations(before), _relations(after)

    def _rule_status(report):
        out = {}
        for target in report.get("targets", []):
            pdf_key = str(target.get("job_uuid") or target.get("sha256") or target.get("label"))
            for rid, info in (target.get("rules") or {}).items():
                out[f"{pdf_key}::{rid}"] = str(info.get("status") or "")
        return out

    before_rule_status, after_rule_status = _rule_status(before), _rule_status(after)

    def _to_ledger(relation):
        """回执状态 → 台账状态（engine_dimension/derive_attribution 的输入词汇）：
        pass→completed；fail→completed 且带已确认 finding；其余原样。"""
        if not relation:
            return relation
        translated = dict(relation)
        status = str(relation.get("status") or "")
        if status == "pass":
            translated["status"] = "completed"
        elif status == "fail":
            translated["status"] = "completed"
            translated["has_confirmed_findings"] = True
        return translated

    # 固定分母 = 前后并集；单侧漏采按 record_missing 归因（IR06：不冒认原件缺失）
    all_keys = sorted(set(before_rels) | set(after_rels))
    relations = []
    for key in all_keys:
        b, a = _to_ledger(before_rels.get(key)), _to_ledger(after_rels.get(key))
        # after 无记录（R2-05）：当前引擎结果=记录缺失（未知），**不得**用
        # (a or b) 把 before 的旧 pass 填进当前引擎维度；before 历史状态单列
        # 保留展示，归因走 record_missing。
        after_missing = a is None
        status = (a or {}).get("status", "") if not after_missing else "record_missing"
        attributions = derive_attribution(b or {}, a or {})
        # before 无回执的两种因果分开（IR06）：before 规则级未完成
        # （insufficient_data/parse_error/execution_error → 关系当时未被评估）
        # 且 after 完成 → 本轮恢复；before 规则级已完成（main 无回执机制，
        # 比较实际跑过）→ resolved_before_this_plan，不冒领恢复。
        if b is None and a is not None:
            before_status = before_rule_status.get(key.rsplit("::", 1)[0], "")
            after_done = str((a or {}).get("status") or "") in ("completed", "not_applicable")
            if before_status in ("pass", "fail", "not_applicable") and after_done:
                # main 规则级已完成且 after 完成 → 历史已解决（不冒领恢复）。
                attributions = ["resolved_before_this_plan"]
            elif before_status in ("pass", "not_applicable") and not after_done:
                # R3-02：父规则曾以完成态放行但该子关系在 main **无回执**
                # （比较循环实际跳过）——不能推断"此前已完成"。当前未解的
                # 准确原因=纠正旧误通过，附仍未解状态。
                attributions = [ATTR_TECH_UNRESOLVED, ATTR_CORRECTED_PREVIOUS_SKIP]
            # before 规则级 fail 且 after 未完成：main 未曾解决该子关系，
            # 保留 derive_attribution 的当前缺口归因，不强行贴历史标签。
        relations.append({
            "relation_id": key,
            "status": status,
            "engine_dimension": engine_dimension(status,
                                                 has_confirmed_findings=bool((a or {}).get("has_confirmed_findings"))),
            "attributions": attributions,
            "evidence_nature": str((after_rels.get(key) or {}).get("evidence_nature") or ""),
            "after_record_missing": after_missing,
            "before_status": str((before_rels.get(key) or {}).get("status") or ""),
        })
    report_stats = aggregate_three_dimensions(relations, manual_decisions={})
    # 关系明细回填审计字段（采集缺失标记 / before 历史状态 / 证据性质）——
    # aggregate 的 detail 只保留三维度字段，这里把来源侧字段并回，供复核。
    extras = {rel["relation_id"]: rel for rel in relations}
    for entry in report_stats.get("relations") or []:
        extra = extras.get(entry.get("relation_id")) or {}
        for field in ("evidence_nature", "after_record_missing", "before_status"):
            if field in extra:
                entry[field] = extra[field]
    # 恢复质量分账（R2-01/R2-05）：本轮恢复的关系按证据性质拆分——
    # 独立金额核对只认 real_amount_comparison；适用性判断与未标注性质
    # 的恢复单列，不冒计。
    quality = {"independent_amount_comparison": 0,
               "applicability_judgment": 0,
               "unspecified": 0}
    for rel in relations:
        if "recovered_this_round" in (rel.get("attributions") or []):
            nature = rel.get("evidence_nature") or ""
            if nature == "real_amount_comparison":
                quality["independent_amount_comparison"] += 1
            elif nature == "applicability_judgment":
                quality["applicability_judgment"] += 1
            else:
                quality["unspecified"] += 1
    report_stats["recovery_quality"] = quality
    report_stats["before_meta"] = {k: before.get(k) for k in ("generated_at", "head_commit", "repo_root")}
    report_stats["after_meta"] = {k: after.get(k) for k in ("generated_at", "head_commit", "repo_root")}
    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report_stats, ensure_ascii=False, indent=2), encoding="utf-8")
    eng, att = report_stats["engine"], report_stats["attribution"]
    print(f"固定关系分母: {report_stats['denominator']}")
    print(f"引擎: pass={eng['completed_pass']} 完成且发现问题={eng['completed_with_findings']} "
          f"不适用={eng['not_applicable']} 未知={eng['unknown']} 未执行={eng['not_executed']}")
    print(f"归因: 恢复={att['recovered_this_round']} 新发现={att['new_finding']} "
          f"仍未解={att['tech_unresolved']} resolved_before={att['resolved_before_this_plan']} "
          f"记录缺失={att['record_missing']}")
    q = report_stats["recovery_quality"]
    print(f"恢复质量分账: 独立金额核对={q['independent_amount_comparison']} "
          f"适用性判断={q['applicability_judgment']} 未标注={q['unspecified']}")
    print(f"written: {out_path}")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="command", required=True)
    p_run = sub.add_parser("run", help="采集四份原件的目标关系全集")
    p_run.add_argument("--output", required=True)
    p_run.add_argument("--repo-root", default=str(_REPO_ROOT),
                       help="被测代码根（缺省本 worktree；指向 main 检出即得 before 集）")
    p_stats = sub.add_parser("stats", help="按前后两份关系全集生成实际三维统计")
    p_stats.add_argument("--before", required=True)
    p_stats.add_argument("--after", required=True)
    p_stats.add_argument("--output", required=True)
    args = ap.parse_args()
    if args.command == "stats":
        _run_relations_stats(args.before, args.after, args.output)
        return
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    repo_root = Path(args.repo_root)

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "baseline_commit": "47581360966191d180880f7fb07dab48974948bb",
        "head_commit": _git_head(repo_root),
        "repo_root": str(repo_root),
        "branch": "codex/auto-check-remediation-r22",
        "worktree": str(_REPO_ROOT),
        "frozen_config": {
            "use_ai_assist": False,
            "ocr": "off（原生文本层材料，不做 maybe_ocr_scanned_pages）",
            "result_cache": "off（全部现算）",
            "rule_execution": "src.engine.pipeline.run_rules_with_outcomes（真实执行函数）",
        },
        "targets": [probe_pdf(t) for t in TARGET_PDFS],
    }
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    # 摘要打印
    for t in report["targets"]:
        print(f"== {t['label']} sha={t['sha256'][:8]} ==")
        if t.get("artifact_error"):
            print("  artifact_error:", t["artifact_error"])
            continue
        ident = t.get("identity", {})
        print(f"  主体={ident.get('check_subject_name')} 年度={ident.get('profile_year')}"
              f" kind={ident.get('report_kind')} 事实={ident.get('fact_count')}"
              f" 冲突={ident.get('identity_conflicts')}")
        for rid, info in t["rules"].items():
            print(f"  {rid}: {info['status']}  issues={len(info['issues'])}"
                  f"  unresolved={len(info['unresolved_reasons'])}")
            for u in info["unresolved_reasons"][:4]:
                print(f"      - {u[:150]}")
        for rid, info in t["rules"].items():
            counts = info.get("receipt_counts_by_status", {})
            print(f"  {rid}: 回执 {sum(counts.values())} {counts}"
                  f"{'  [规则级零回执]' if info.get('rule_level_zero_receipt') else ''}")
    print(f"\nwritten: {out_path}")


if __name__ == "__main__":
    main()
