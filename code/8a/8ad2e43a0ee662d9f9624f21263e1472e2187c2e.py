"""已有开发材料的读取缺口回归，不是独立真值或盲测验收。"""
import hashlib
import json
from pathlib import Path
import sys
import time

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
sys.path.insert(0, str(ROOT))
from src.services.pdf_parser import PDFParser
from src.services.document_profile_resolver import resolve_document_profile
from src.services.table_reading_assessment import assess_table_reading
from src.services.table_import_receipt import build_table_import_receipt
from src.engine.fact_context import build_fact_context_from_artifact


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    out = BASE / (sys.argv[1] if len(sys.argv) > 1 else "final_corpus")
    out.mkdir(exist_ok=True)
    modules = [p for directory in ["src", "api"] for p in (ROOT/directory).rglob("*.py")]
    fingerprints = {str(p.relative_to(ROOT)): sha(p) for p in modules}
    (out/"code_fingerprints.json").write_text(json.dumps(fingerprints, indent=2))
    inventory = json.loads((ROOT/"outputs/putuo_fresh_batch_20261014/inventory.json").read_text())
    results = []
    started = time.monotonic()
    for index, item in enumerate(inventory, 1):
        path = Path(item["source_pdf"])
        before = sha(path)
        artifact = PDFParser(None).build_parse_artifact(str(path))
        page_texts = ["\n".join(b.text for b in artifact.text_blocks if b.page_number == page)
                      for page in range(1, artifact.page_count+1)]
        profile = resolve_document_profile(filename=path.name, page_texts=page_texts)
        context = build_fact_context_from_artifact(artifact, org_name=profile.organization_name.value,
                    fiscal_year=profile.report_year.value, report_kind=profile.report_kind.value)
        facts = context.facts if context else []
        receipt = build_table_import_receipt(artifact, facts, org_name=profile.organization_name.value)
        reading = receipt["reading_quality"]
        results.append(dict(filename=path.name, source_pdf=str(path), sha256=before,
                    pages=artifact.page_count, reading_status=reading["status"], reading_gaps=reading["gaps"],
                    unbound_money_cells=reading["unbound_money_cells"], fact_count=len(facts),
                    reading_blocked_facts=sum(f.extra_dims.get("table_reading_status")=="incomplete" for f in facts),
                    source_unchanged=before==sha(path), full_pdf_coverage_verified=False))
        if index%25==0 or index==len(inventory):
            (out/"results.json").write_text(json.dumps(results,ensure_ascii=False,indent=2))
            print(index, len(inventory), "gaps",sum(len(x["reading_gaps"]) for x in results),flush=True)
    summary = dict(scope="known development corpus reading guard regression; no OCR; no independent truth",
                   pdfs=len(results), pages=sum(x["pages"] for x in results),
                   reports_with_gaps=sum(bool(x["reading_gaps"]) for x in results),
                   reading_gaps=sum(len(x["reading_gaps"]) for x in results),
                   unbound_money_cells=sum(len(x["unbound_money_cells"]) for x in results),
                   reading_blocked_facts=sum(x["reading_blocked_facts"] for x in results),
                   source_unchanged=all(x["source_unchanged"] for x in results),
                   source_code_drift=any(sha(ROOT/name)!=digest for name,digest in fingerprints.items()),
                   elapsed_seconds=round(time.monotonic()-started,2),full_pdf_coverage_verified=False)
    (out/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    print(json.dumps(summary,ensure_ascii=False),flush=True)
    assert not summary["source_code_drift"] and summary["source_unchanged"]


if __name__=="__main__":
    main()
