"""Reparse the reused original with current native code, no DB/OCR/HTTP claims."""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.engine.fact_context import build_fact_context_from_artifact, operand_rejection_reason, resolve_operand
from src.services.pdf_parser import PDFParser

source = ROOT / "outputs/remediation_20261006_round13/original_materials/zhejiang_final.pdf"
artifact = PDFParser(None).build_parse_artifact(str(source))
context = build_fact_context_from_artifact(
    artifact, org_name="金华市福利彩票发行中心", fiscal_year=2024, report_kind="final",
)
facts = context.facts
owner_rejections = [
    {"fact_uid": fact.fact_uid, "org_name": fact.org_name,
     "source_table_title": fact.extra_dims.get("source_table_title"), "reason": reason}
    for fact in facts
    if (reason := operand_rejection_reason(fact, context.org_name)) and "主体" in reason
]
totals = [fact.to_dict() for fact in facts if fact.table_code == "FIN_02_income" and fact.subject_code == "total"]
resolution = resolve_operand(context, "total_actual", table_code="FIN_02_income", report_year=2024,
                             budget_stages=("决算",))
result = {
    "scope": "reused original native parse regression; not independent blind evidence or service replay",
    "source": str(source), "source_sha256": artifact.source_file_hash,
    "page_count": artifact.page_count, "parser_fingerprint": artifact.parser_fingerprint,
    "facts_count": len(facts), "operand_count": len(context.definite_facts()),
    "owner_rejections": owner_rejections, "income_total_facts": totals,
    "income_resolution": {"status": resolution.status, "value": str(resolution.value), "reason": resolution.reason},
    "tested_source_hashes": {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in ("src/engine/fact_context.py", "src/services/fiscal_fact_materializer.py", "src/services/pdf_parser.py")
    },
}
output = Path(__file__).with_name("native_original_probe.json")
output.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n")
assert not owner_rejections, owner_rejections
assert resolution.status == "ok" and str(resolution.value) == "746.62", result["income_resolution"]
assert any(str(fact.normalized_value) in {"0", "0.0", "0.00"} for fact in facts)
print(json.dumps({"output": str(output), "owner_rejections": len(owner_rejections),
                  "income_resolution": result["income_resolution"], "facts_count": len(facts)}, ensure_ascii=False))
