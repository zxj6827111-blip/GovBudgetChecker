#!/usr/bin/env python3
"""原件回放：2026-11-16 识别／事实绑定整改的定向规则复跑。

用法（在本仓库根目录）::

    .venv/bin/python scripts/replay_recognition_remediation_20261116.py \
        --job-dir uploads/34f7645bc5f1cc5f09530c9d9d3cf3af \
        --job-dir uploads/d835cf2a447a40ef89ec6e9bf904b33b \
        --output outputs/recognition_remediation_playback_20261116.json

只读：从 PostgreSQL 读取 ``parse_runs.artifact_snapshot``（READ ONLY 事务），
从任务目录读取 ``fact_context.json`` 与原件 PDF；不写数据库、不改 status.json。
规则按材料文种选择；输出每条规则的显式 issue / deferred / 关键关系回执。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.engine.fact_context import FactContext  # noqa: E402
from src.schemas.document_profile import DocumentProfile  # noqa: E402
from src.engine.rules_v33 import build_document  # noqa: E402
from src.schemas.parse_artifact import ParseArtifact  # noqa: E402


def _rule_classes():
    import src.engine.rules_v33 as rules
    from src.engine.checklist_final_rules import R33251_FunctionalDetailCompleteness
    from src.engine.checklist_text_rules import CMM011_AmountUnitWan

    wanted = {
        "V33-110": rules.R33110_BudgetVsFinal_TextConsistency,
        "V33-106": rules.R33106_GeneralBudgetStruct,
        "V33-107": rules.R33107_BasicExpense_Check,
        "V33-223": rules.R33223_Narrative6_T6,
        "V33-227": rules.R33227_Narrative5_T5_NameConsistency,
        "V33-240": rules.R33240_Table2_IncomeAdvancedCheck,
        "V33-251": R33251_FunctionalDetailCompleteness,
        "CMM-011": CMM011_AmountUnitWan,
    }
    cross = next(
        value for value in vars(rules).values()
        if isinstance(value, type) and getattr(value, "code", None) == "V33-CROSS-SAN-GONG-ECON"
    )
    wanted["V33-CROSS-SAN-GONG-ECON"] = cross
    return wanted


def _pdf_path(job_dir: Path) -> Path:
    candidates = sorted(job_dir.glob("*.pdf"))
    if len(candidates) != 1:
        raise SystemExit(f"{job_dir}: 期望恰好一份 PDF，实际 {len(candidates)}")
    return candidates[0]


async def _load_artifact(database_url: str, parse_run_id: int) -> Dict[str, Any]:
    conn = await asyncpg.connect(database_url)
    try:
        async with conn.transaction(readonly=True):
            row = await conn.fetchrow(
                "SELECT artifact_snapshot FROM parse_runs WHERE id = $1", parse_run_id
            )
        if row is None or row["artifact_snapshot"] is None:
            raise SystemExit(f"parse_run {parse_run_id} 没有 artifact_snapshot")
        raw = row["artifact_snapshot"]
        return json.loads(raw) if isinstance(raw, str) else raw
    finally:
        await conn.close()


def _run_job(job_dir: Path, artifact: Dict[str, Any], status: Dict[str, Any]) -> Dict[str, Any]:
    import pymupdf

    fact_path = job_dir / "fact_context.json"
    context = FactContext.from_dict(json.loads(fact_path.read_text()))
    artifact_obj = ParseArtifact.from_dict(artifact)
    pdf = pymupdf.open(_pdf_path(job_dir))
    page_texts = [page.get_text("text") for page in pdf]
    # 兼容仍读 doc.page_tables 的旧规则：按物理页还原页面表格，
    # 不能让回放环境缺少页面表而被误判为规则执行异常。
    page_tables: List[List[List[List[Any]]]] = [[] for _ in page_texts]
    fragments = artifact_obj.physical_fragments or artifact_obj.table_fragments
    for fragment in fragments:
        page = int(getattr(fragment, "page_number", 0) or 0)
        rows = [[str(cell or "") for cell in row] for row in (fragment.rows or [])]
        if 1 <= page <= len(page_tables) and rows:
            page_tables[page - 1].append(rows)
    doc = build_document(str(_pdf_path(job_dir)), page_texts, page_tables, 0)
    doc.parse_artifact = artifact_obj
    doc.fact_context = context
    if status.get("document_profile"):
        doc.document_profile = DocumentProfile.model_validate(status["document_profile"])

    kind = str(status.get("report_kind") or status.get("doc_type") or "").lower()
    classes = _rule_classes()
    selected = ["V33-110", "V33-106", "V33-107", "V33-223", "V33-227", "V33-240",
                "V33-251", "CMM-011", "V33-CROSS-SAN-GONG-ECON"]
    outcomes: List[Dict[str, Any]] = []
    for rule_id in selected:
        cls = classes[rule_id]
        try:
            issues = cls().apply(doc)
            outcomes.append({
                "rule_id": rule_id,
                "outcome": "ok",
                "issues": [
                    {"severity": issue.severity, "message": issue.message,
                     "location": issue.location}
                    for issue in issues or []
                ],
            })
        except BaseException as exc:  # RuleDeferred/RuleNotApplicable 继承 BaseException
            outcomes.append({
                "rule_id": rule_id,
                "outcome": type(exc).__name__,
                "detail": str(getattr(exc, "detail", exc))[:2000],
                "partial_issues": [
                    {"severity": issue.severity, "message": issue.message,
                     "location": issue.location}
                    for issue in (getattr(exc, "partial_issues", None) or [])
                ],
            })
    interesting = {
        row.get("sub_check_id"): row
        for row in getattr(doc, "fact_receipts", []) or []
        if row.get("sub_check_id") and any(
            token in str(row.get("sub_check_id"))
            for token in ("2013499", "20134:total_actual", "V33-251", ":bad_unit", "income_sources")
        )
    }
    return {
        "job_id": status.get("job_id"),
        "filename": status.get("filename"),
        "report_kind": kind,
        "analysis_revision": status.get("analysis_revision"),
        "outcomes": outcomes,
        "receipts": interesting,
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-dir", action="append", required=True)
    parser.add_argument(
        "--database-url",
        default=os.getenv(
            "DATABASE_URL",
            "postgresql://govbudget_local:change_me_local_postgres_password@localhost:5432/fiscal_db",
        ),
    )
    parser.add_argument("--output")
    args = parser.parse_args()

    results = []
    for raw in args.job_dir:
        job_dir = Path(raw).resolve()
        status = json.loads((job_dir / "status.json").read_text())
        ingest_path = job_dir / "structured_ingest.json"
        ingest = json.loads(ingest_path.read_text()) if ingest_path.exists() else {}
        parse_run_id = int(ingest.get("parse_run_id") or 0)
        if not parse_run_id:
            results.append({"job_dir": str(job_dir), "error": "缺少 parse_run_id"})
            continue
        artifact = await _load_artifact(args.database_url, parse_run_id)
        results.append(_run_job(job_dir, artifact, status))

    payload = json.dumps(results, ensure_ascii=False, indent=2, default=str)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
        print(f"written {args.output}")
    else:
        print(payload)


if __name__ == "__main__":
    asyncio.run(main())
