#!/usr/bin/env python3
"""商务委 2023 决算验收回放（2026-11-19）。

用法::

    .venv/bin/python scripts/verify_swc_acceptance_20261119.py \
        --job-dir uploads/637ceec27a9328ec21526febf81ead40 \
        --output outputs/swc_acceptance_original.json

同一命令可对“正文改错数字”的副本运行，验证单项与合计都使用正文原数。

只读：从 PostgreSQL 读取 parse_runs.artifact_snapshot（READ ONLY 事务），
从任务目录读取 fact_context.json / structured_ingest.json / 原件 PDF；
不写数据库、不改 status.json。
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.engine.fact_context import FactContext  # noqa: E402
from src.engine.pipeline import build_issues_payload  # noqa: E402
from src.engine.rules_v33 import build_document  # noqa: E402
from src.schemas.document_profile import DocumentProfile  # noqa: E402
from src.schemas.parse_artifact import ParseArtifact  # noqa: E402
from src.schemas.review_report import build_review_report  # noqa: E402


def _load_replay_module():
    spec = importlib.util.spec_from_file_location(
        "replay_recognition_remediation_20261116",
        ROOT / "scripts" / "replay_recognition_remediation_20261116.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


async def _run(job_dir: Path) -> Dict[str, Any]:
    import pymupdf

    replay = _load_replay_module()
    status = json.loads((job_dir / "status.json").read_text(encoding="utf-8"))
    ingest = json.loads((job_dir / "structured_ingest.json").read_text(encoding="utf-8"))
    database_url = os.getenv(
        "DATABASE_URL",
        "postgresql://govbudget_local:change_me_local_postgres_password@localhost:5432/fiscal_db",
    )
    artifact = await replay._load_artifact(database_url, int(ingest["parse_run_id"]))
    context = FactContext.from_dict(
        json.loads((job_dir / "fact_context.json").read_text(encoding="utf-8"))
    )
    artifact_obj = ParseArtifact.from_dict(artifact)
    pdf_path = replay._pdf_path(job_dir)
    pdf = pymupdf.open(pdf_path)
    page_texts = [page.get_text("text") for page in pdf]
    page_tables: List[List[List[Any]]] = [[] for _ in page_texts]
    for fragment in artifact_obj.physical_fragments or artifact_obj.table_fragments:
        page = int(getattr(fragment, "page_number", 0) or 0)
        rows = [[str(cell or "") for cell in row] for row in (fragment.rows or [])]
        if 1 <= page <= len(page_tables) and rows:
            page_tables[page - 1].append(rows)
    doc = build_document(str(pdf_path), page_texts, page_tables, 0)
    doc.parse_artifact = artifact_obj
    doc.fact_context = context
    if status.get("document_profile"):
        doc.document_profile = DocumentProfile.model_validate(status["document_profile"])

    payload = build_issues_payload(doc, use_ai_assist=False, report_kind="final")
    payload["issues"] = payload.get("issues") or {"all": []}
    from src.engine.check_obligations import build_obligation_ledger

    payload["check_coverage"] = build_obligation_ledger(
        None,
        report_kind="final",
        rule_execution_summary=payload.get("rule_execution_summary"),
        structured_ingest=ingest,
        fact_receipts=payload.get("fact_receipts") or [],
    )
    payload["result"] = {
        "issues": payload["issues"],
        "rule_execution_summary": payload.get("rule_execution_summary"),
        "fact_receipts": payload.get("fact_receipts") or [],
        "meta": {
            "check_receipts": payload.get("fact_receipts") or [],
            "structure_view": {"page_texts": page_texts},
        },
    }
    payload["job_id"] = status.get("job_id")
    payload["filename"] = status.get("filename")
    payload["analysis_revision"] = status.get("analysis_revision")
    payload["structured_ingest"] = ingest
    payload["document_profile"] = status.get("document_profile")
    payload["report_kind"] = "final"
    report = build_review_report(payload, resolve_pdf_bboxes=False)

    def _issues_by_rule(rule_id: str):
        return [
            item for item in payload["issues"].get("all") or []
            if str(item.get("rule_id") or item.get("rule")) == rule_id
        ]

    receipts = [
        item for item in payload.get("fact_receipts") or []
        if isinstance(item, dict)
    ]
    final_sum = next(
        (
            item for item in receipts
            if item.get("sub_check_id") == "V33-251:final:item_sum"
        ),
        None,
    )
    unresolved = {
        entry.get("rule_id"): entry
        for entry in (payload.get("rule_execution_summary") or {}).get("unresolved_rules") or []
        if isinstance(entry, dict)
    }
    return {
        "job_id": status.get("job_id"),
        "filename": status.get("filename"),
        "cmm011": _issues_by_rule("CMM-011"),
        "v33_251": _issues_by_rule("V33-251"),
        "v33_222": _issues_by_rule("V33-222"),
        "final_item_sum": final_sum,
        "unresolved_v33_222": unresolved.get("V33-222"),
        "report_issue_count": len(report.get("issues") or []),
        "report_cmm011": [
            item for item in report.get("issues") or []
            if str(item.get("rule_id") or item.get("rule")) == "CMM-011"
        ],
        "unfinished_groups": report.get("unfinished_check_groups") or [],
        "unfinished_checks": report.get("unfinished_checks") or [],
    }


def _check_original(result: Dict[str, Any]) -> List[str]:
    failures: List[str] = []
    cmm = result["cmm011"]
    if len(cmm) != 1:
        failures.append(f"CMM-011 应只出现 1 次，实际 {len(cmm)}")
    else:
        message = str(cmm[0].get("message") or "")
        for needle in ("已按第16页核对", "已纳入20项支出决算分项加总", "168959.54"):
            if needle not in message:
                failures.append(f"CMM-011 缺少“{needle}”")
        verified = (cmm[0].get("location") or {}).get("verified_by") or {}
        if verified.get("table_page") != 16:
            failures.append("CMM-011 未绑定第16页原表")
        if str(verified.get("subject_code")) != "2160250":
            failures.append("CMM-011 未绑定科目 2160250")
        if verified.get("included_count") != 20:
            failures.append("CMM-011 未记录纳入 20 项")
    final_sum = result["final_item_sum"] or {}
    evidence = final_sum.get("calculation_evidence") or {}
    if final_sum.get("status") != "pass":
        failures.append(f"V33-251 final 应为 pass，实际 {final_sum.get('status')}")
    if evidence.get("included_count") != 20:
        failures.append(f"V33-251 纳入项数应为 20，实际 {evidence.get('included_count')}")
    if str(evidence.get("subtotal_wan")) != "168959.54":
        failures.append(f"V33-251 小计应为 168959.54，实际 {evidence.get('subtotal_wan')}")
    if any("事业运行" in str(item.get("message") or "") for item in result["v33_222"]):
        failures.append("V33-222 仍把已核对的事业运行条目报成金额问题")
    unresolved = result.get("unresolved_v33_222") or {}
    detail = str(unresolved.get("detail") or "")
    if "文化体育与传媒支出" not in detail or "归口管理的行政单位离退休" not in detail:
        failures.append("第24页两处科目名称疑点未分别保留")
    page26_groups = [
        group for group in result["unfinished_groups"]
        if group.get("page") == 26 and group.get("blocks_completion")
    ]
    if page26_groups:
        failures.append("原件第26页仍出现阻塞完成的待核卡")
    return failures


def _check_wrong_number(result: Dict[str, Any]) -> List[str]:
    failures: List[str] = []
    v251 = result["v33_251"]
    if not any("168949.54" in str(item.get("message") or "") for item in v251):
        failures.append("V33-251 未按正文数字算出 168949.54")
    if not any("10.00" in str(item.get("message") or "") for item in v251):
        failures.append("V33-251 未报出 10.00 差额")
    v222 = result["v33_222"]
    if not any("10.00" in str(item.get("message") or "") for item in v222):
        failures.append("V33-222 未按正文数字报出 10.00 差额")
    cmm = result["cmm011"]
    if len(cmm) != 1:
        failures.append(f"CMM-011 应只出现 1 次，实际 {len(cmm)}")
    else:
        verified = (cmm[0].get("location") or {}).get("verified_by") or {}
        if str(verified.get("item_difference_wan")) != "-10.00":
            failures.append(
                f"单项差额应为 -10.00，实际 {verified.get('item_difference_wan')}"
            )
        if "少10.00万元" not in str(verified.get("note") or ""):
            failures.append("CMM-011 未说明正文少 10.00 万元")
    final_sum = result["final_item_sum"] or {}
    if final_sum.get("status") != "fail":
        failures.append(f"V33-251 final 应为 fail，实际 {final_sum.get('status')}")
    return failures


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-dir", required=True)
    parser.add_argument("--output")
    parser.add_argument("--expect", choices=("original", "wrong-number"), required=True)
    args = parser.parse_args()
    result = await _run(Path(args.job_dir).resolve())
    failures = (
        _check_original(result) if args.expect == "original"
        else _check_wrong_number(result)
    )
    result["expectation"] = args.expect
    result["failures"] = failures
    result["ok"] = not failures
    text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
        print(f"written {args.output}")
    else:
        print(text)
    print("PASS" if not failures else "FAIL: " + "；".join(failures))
    raise SystemExit(0 if not failures else 1)


if __name__ == "__main__":
    asyncio.run(main())
