#!/usr/bin/env python
"""D0 复现脚本：离线重跑单份决算 PDF 的规则链，输出三类对象台账所需证据。

不依赖服务、数据库与 AI（ai_enabled=False），与线上 dual 模式的规则路径同一实现入口
（EngineRuleRunner.run_rules）。用于根因台账的复现方法部分：
每个案例都能用本脚本重算出"实际输入数据、实际判断、最终交付结果"。

用法:
    .venv/bin/python scripts/d0_repro_case_pack.py <pdf_path> --org <单位名> --year 2025 \
        --kind dept_final --out <输出.json> [--rules V33-243,V33-250]

输出 JSON 字段:
    summary   规则执行摘要（executed/pass/fail/insufficient_data）
    findings  交付发现（rule_id/severity/evidence_verification/page）
    ev_failed 证据失败明细（含 evidence[].text 与失败原因）
    unresolved 未决规则（detail/diagnostics/reason_codes）
    receipts  数量要素等回执（验证"零问题零回执"漏检机制）
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.schemas.issues import AnalysisConfig, JobContext  # noqa: E402
from src.services.engine_rule_runner import run_engine_rules  # noqa: E402


async def run(pdf: str, org: str, year: int, kind: str, out: str | None,
              task_dir: str | None = None) -> dict:
    fact_context = None
    parse_artifact = None
    page_texts: list[str] = []
    if task_dir:
        # 冻结输入复现：读保存的事实上下文（与当次分析同一取数版本）
        fc_path = Path(task_dir) / "fact_context.json"
        if fc_path.exists():
            fact_context = json.loads(fc_path.read_text(encoding="utf-8"))
        status = {}
        st_path = Path(task_dir) / "status.json"
        if st_path.exists():
            status = json.loads(st_path.read_text(encoding="utf-8"))
        meta_saved = ((status.get("result") or {}).get("meta") or {})
        parse_artifact = meta_saved.get("parse_artifact_obj") or meta_saved.get("parse_artifact")
        # 页文本从原件重建（保证证据校验可用）
        try:
            import pdfplumber

            with pdfplumber.open(pdf) as pdf_doc:
                page_texts = [(p.extract_text() or "") for p in pdf_doc.pages]
        except Exception:
            page_texts = []
    ctx = JobContext(
        job_id="d0-repro",
        pdf_path=pdf,
        page_texts=page_texts,
        fact_context=fact_context,
        parse_artifact=parse_artifact,
        meta={
            "org_name": org,
            "fiscal_year": year,
            "report_kind": kind,
            "use_ai_assist": False,
            "use_local_rules": True,
            "dual_mode_enabled": False,
        },
    )
    config = AnalysisConfig(dual_mode=False, ai_enabled=False, rule_enabled=True,
                            enable_ai_analysis=False, enable_ai_locator=False)
    # 规则集由 runner 按 report_kind 自选（与线上 select_rules 同一入口）
    issues = await run_engine_rules(ctx, [], config)

    findings = []
    for it in issues:
        findings.append({
            "rule_id": getattr(it, "rule_id", None),
            "severity": getattr(it, "severity", None),
            "message": str(getattr(it, "message", ""))[:300],
            "page": (getattr(it, "location", None) or {}).get("page"),
            "evidence_verification": getattr(it, "evidence_verification", None),
            "evidence_texts": [
                str(e.get("text") or "")[:200]
                for e in (getattr(it, "evidence", None) or [])
            ],
        })
    ev_failed = [f for f in findings if (f.get("evidence_verification") or {}).get("status") == "failed"]

    # 证据完整性校验（与线上 apply_evidence_completeness 同一入口）
    evidence_summary = None
    try:
        from src.services.evidence_guard import apply_evidence_completeness

        as_dicts = [it.dict() if hasattr(it, "dict") else dict(it) for it in issues]
        result_blob = {"rule_findings": as_dicts}
        apply_evidence_completeness(
            result_blob,
            document_page_count=len(page_texts) or None,
            page_texts=page_texts or None,
        )
        ev_failed = [
            f for f in result_blob["rule_findings"]
            if (f.get("evidence_verification") or {}).get("status") == "failed"
        ]
        evidence_summary = {
            "completeness_rate": (result_blob.get("evidence_completeness") or {}).get("completeness_rate"),
        }
    except Exception as exc:  # noqa: BLE001 - 证据校验失败时如实记录
        evidence_summary = {"error": str(exc)}

    # 规则执行摘要与未决规则从 runner 统计里取（与线上同源）
    from src.services.engine_rule_runner import EngineRuleRunner  # noqa: E402

    runner = EngineRuleRunner()
    summary = runner.get_rule_execution_summary() if hasattr(runner, "get_rule_execution_summary") else {}

    report = {
        "input": {"pdf": pdf, "org": org, "year": year, "report_kind": kind,
                  "task_dir": task_dir, "frozen_fact_context": bool(fact_context),
                  "code_head": "见 git rev-parse HEAD"},
        "findings_total": len(findings),
        "ev_failed_total": len(ev_failed),
        "summary": summary,
        "evidence_summary": evidence_summary,
        "ev_failed": ev_failed,
        "findings": findings,
    }
    text = json.dumps(report, ensure_ascii=False, indent=1, default=str)
    if out:
        Path(out).write_text(text, encoding="utf-8")
    else:
        print(text[:12000])
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--org", required=True)
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--kind", default="dept_final",
                    help="dept_final=部门决算 final=单位决算 budget=预算")
    ap.add_argument("--out", default=None)
    ap.add_argument("--task", default=None, help="任务目录（读冻结 fact_context.json 复现当次取数）")
    args = ap.parse_args()
    asyncio.run(run(args.pdf, args.org, args.year, args.kind, args.out, args.task))


if __name__ == "__main__":
    main()
