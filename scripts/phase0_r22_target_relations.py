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
import sys
import time
from pathlib import Path
from types import SimpleNamespace

_REPO_ROOT = Path(__file__).resolve().parents[1]
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
            "detail": str(outcome.detail or "")[:500],
            "partial_findings_count": outcome.partial_findings_count,
            "unresolved_reasons": [str(r)[:400] for r in outcome.unresolved_reasons],
            "reason_codes": list(outcome.reason_codes),
            "diagnostics": outcome.diagnostics[:40],
            "issues": rule_issues,
        }
    # 回执级未决（关系 ID 粒度）：含规则级 deferred 时回执可能为 0——这正是
    # 计划要求保留规则级阻断的原因，两者都要记。
    receipts = []
    for r in getattr(doc, "fact_receipts", []) or []:
        rid = str(getattr(r, "rule_id", ""))
        if rid not in target_codes:
            continue
        if str(getattr(r, "status", "")) != "insufficient_data":
            continue
        receipts.append({
            "rule_id": rid,
            "relation_id": str(getattr(r, "relation_id", "") or ""),
            "incomplete_reason": str(getattr(r, "incomplete_reason", "") or "")[:400],
        })
    result["insufficient_receipts"] = receipts
    result["elapsed_seconds"] = round(elapsed, 1)
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "baseline_commit": "47581360966191d180880f7fb07dab48974948bb",
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
        print(f"  回执级insufficient: {len(t['insufficient_receipts'])}")
        for r in t["insufficient_receipts"][:8]:
            print(f"      - {r['rule_id']} {r['relation_id']}: {r['incomplete_reason'][:120]}")
    print(f"\nwritten: {out_path}")


if __name__ == "__main__":
    main()
