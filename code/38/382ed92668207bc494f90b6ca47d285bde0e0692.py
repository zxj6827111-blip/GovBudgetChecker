"""只读提取 main 的原函数，复现局部预填 note 通过旧依据校验；不调用数据库。"""
import ast
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "artifacts/daily-main/src/services/review_lifecycle_service.py"
source = SOURCE.read_text(encoding="utf-8")
tree = ast.parse(source)
function = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                and n.name == "_obligation_evidence_error")
namespace = {
    "Optional": Optional, "Dict": Dict, "Any": Any,
    "OBLIGATION_DECISION_PENDING": "pending",
    "OBLIGATION_DECISION_VERIFIED_OK": "verified_ok",
    "OBLIGATION_DECISION_VERIFIED_ISSUE": "verified_issue",
    "OBLIGATION_DECISION_NOT_APPLICABLE": "not_applicable",
    "ERROR_OBLIGATION_DECISION_EVIDENCE_REQUIRED": "obligation_decision_evidence_required",
}
exec(compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), "exec"), namespace)
validator = namespace["_obligation_evidence_error"]
notes = [None, "第8页经营收入列空白，本人确认为无此项"]
results = []
for decision in ("not_applicable", "verified_ok"):
    for note in notes:
        result = validator(decision, note, None)
        results.append({"decision": decision, "note": note,
                        "evidence_reference": None, "accepted": result is None,
                        "validation_error": result})
print(json.dumps({
    "scope": "AST-extracted unchanged main pure validation function only; not API, gate or proposed R2.1 implementation",
    "source": str(SOURCE),
    "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
    "function_line": function.lineno,
    "results": results,
}, ensure_ascii=False, indent=2))
