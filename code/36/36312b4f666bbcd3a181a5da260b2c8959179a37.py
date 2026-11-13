"""只读内存反例：不打开 PDF、不连接服务、不修改生产或冻结验收记录。

运行：.venv/bin/python outputs/pdf_input_technical_diagnosis_20261017/downstream_identity_probes.py
这些输入为独立构造的技术反例，不表示现有 34 份 PDF 已受到相应影响。
"""
from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.engine.fact_context import (  # noqa: E402
    FactContext,
    FactOverlay,
    apply_fact_overlay,
    remap_overlay_targets,
    resolve_operand,
)
from src.schemas.parse_artifact import FiscalFact  # noqa: E402


def main() -> None:
    base = FiscalFact(
        fact_uid="row1:total_actual", indicator="total_actual", raw_value="100.00",
        unit="万元", normalized_value=Decimal("100"), org_name="测试单位",
        org_scope="部门汇总", fiscal_year=2024, period="本年",
        fund_scope="一般公共预算", budget_stage="决算", subject_code="total",
        subject_name="合计", table_code="FIN_05_general_public_expenditure",
        instance_uid="instance_A",
    )
    first = replace(base, fund_scope="general_public_budget")
    other = replace(first, fact_uid="row2:total_actual", instance_uid="instance_B",
                    org_scope="本级")
    context = FactContext([first, other], org_name="测试单位", fiscal_year=2024,
                          file_hash="file", parse_run_uid="run1")
    resolution = resolve_operand(context, "total_actual", table_code=base.table_code,
                                 report_year=2024, budget_stages=("决算",))
    same_amount = {
        "id": "same_amount_distinct_business_identities",
        "inputs": {"facts": [first.to_dict(), other.to_dict()],
                   "query": {"indicator": "total_actual", "table_code": base.table_code,
                             "report_year": 2024, "budget_stages": ["决算"]}},
        "current_output": {"status": resolution.status,
                           "candidate_uids": list(resolution.candidate_uids),
                           "selected_fact_uid": resolution.fact.fact_uid if resolution.fact else None,
                           "selected_instance_uid": resolution.fact.instance_uid if resolution.fact else None},
        "risk": "同为一般公共预算、不同汇总范围和实例的同额候选被排序择一；金额相等不证明业务身份相同。",
        "reproduced": resolution.status == "ok" and len(resolution.candidate_uids) == 2,
    }

    changed = replace(base, indicator="project_actual", subject_name="项目支出",
                      subject_code="项目支出", instance_uid="instance_C",
                      fund_scope="政府性基金", fiscal_year=2025)
    old_context = FactContext([base], file_hash="file", parse_run_uid="run1")
    changed_context = FactContext([changed], file_hash="other_file", parse_run_uid="run2")
    token_collision = {
        "id": "identity_and_source_changes_do_not_change_context_token",
        "inputs": {"old_context": old_context.to_dict(), "changed_context": changed_context.to_dict()},
        "current_output": {"old_token": old_context.version_token,
                           "changed_token": changed_context.version_token,
                           "equal": old_context.version_token == changed_context.version_token},
        "risk": "金额和 UID 未变时，事实语义及原件运行变化不能被当前版本令牌区别。",
        "reproduced": old_context.version_token == changed_context.version_token,
    }

    reused = replace(base, indicator="project_actual", subject_code="new_code",
                     subject_name="另一科目")
    overlay = FactOverlay(fact_uid=base.fact_uid, normalized_value=Decimal("123"))
    new_context = FactContext([reused], file_hash="file", parse_run_uid="run2")
    remapped, unresolved, rewritten = remap_overlay_targets([base], new_context, [overlay])
    effective = apply_fact_overlay(new_context, remapped)
    uid_reuse = {
        "id": "reused_uid_bypasses_overlay_business_identity_comparison",
        "inputs": {"old_fact": base.to_dict(), "new_fact": reused.to_dict(),
                   "overlay": overlay.to_record()},
        "current_output": {"accepted_overlay_count": len(remapped), "unresolved": unresolved,
                           "rewritten_count": rewritten, "effective_fact": effective.facts[0].to_dict()},
        "risk": "同 UID 已代表其他指标科目时，旧校正仍可直接写入新事实。",
        "reproduced": len(remapped) == 1 and not unresolved
            and effective.facts[0].indicator == "project_actual"
            and effective.facts[0].normalized_value == Decimal("123"),
    }

    no_code1 = replace(base, subject_code=None, subject_name="甲科目")
    no_code2 = replace(base, subject_code=None, subject_name="乙科目")
    uncoded = {
        "id": "distinct_uncoded_subject_names_share_identity_key",
        "inputs": {"first_fact": no_code1.to_dict(), "second_fact": no_code2.to_dict()},
        "current_output": {"first_identity_key": list(no_code1.identity_key()),
                           "second_identity_key": list(no_code2.identity_key()),
                           "equal": no_code1.identity_key() == no_code2.identity_key()},
        "risk": "无编码科目的名称不参与业务身份，名称不同的科目可碰撞。",
        "reproduced": no_code1.identity_key() == no_code2.identity_key(),
    }
    cases = [same_amount, token_collision, uid_reuse, uncoded]
    files = ["src/engine/fact_context.py", "src/schemas/parse_artifact.py"]
    result = {
        "scope": "synthetic_read_only_in_memory_probes",
        "not_a_claim_about_existing_pdf_corpus": True,
        "service_accessed": False,
        "production_files_modified": False,
        "source_code_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in files},
        "probe_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "case_count": len(cases), "reproduced_count": sum(case["reproduced"] for case in cases),
        "cases": cases,
    }
    target = Path(__file__).with_suffix(".json")
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(target), "cases": len(cases),
                      "reproduced": result["reproduced_count"]}, ensure_ascii=False))
    assert all(case["reproduced"] for case in cases), "生产行为已变化，请检查反例结果"


if __name__ == "__main__":
    main()
