"""固定34份原件只读重放；只生成新证据，不覆盖旧真值/验收记录。"""

from pathlib import Path
import argparse, hashlib, json, time, sys
import pdfplumber
from src.services.pdf_parser import PDFParser
from src.services.fiscal_input_builder import FiscalInputBuilder
from src.services.document_profile_resolver import resolve_document_profile
from src.engine.fact_context import operand_rejection_reason


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("original_root")
    ap.add_argument("output")
    args = ap.parse_args()
    original = Path(args.original_root)
    dest = Path(args.output)
    dest.mkdir(exist_ok=False, parents=True)
    code_root = Path(__file__).resolve().parents[1]

    def code_hashes():
        return {
            str(p.relative_to(code_root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for d in ["src", "api", "ocr_service"]
            for p in (code_root / d).rglob("*.py")
        }

    code_before = code_hashes()
    write(dest / "code_before.json", code_before)
    summaries = []
    for name in [
        "shanghai_2024_v23_acceptance_20261015",
        "shanghai_2024_new10_acceptance_20261015",
        "shanghai_2024_new9_acceptance_20261016",
    ]:
        frozen = original / "outputs" / name
        inventory = json.loads((frozen / "inventory.json").read_text())
        batch = dest / name
        write(batch / "inventory.json", inventory)
        for entry in inventory:
            start = time.monotonic()
            source = frozen / "documents" / entry["doc_id"] / "source.pdf"
            assert hashlib.sha256(source.read_bytes()).hexdigest() == entry["sha256"]
            artifact = FiscalInputBuilder().read_native(source)
            with pdfplumber.open(source) as pdf:
                texts = [p.extract_text() or "" for p in pdf.pages]
            profile = resolve_document_profile(filename=entry["filename"], page_texts=texts)
            context = FiscalInputBuilder().facts(
                artifact,
                org_name=profile.organization_name.value,
                fiscal_year=profile.year,
                report_kind=profile.kind,
            )
            facts = context.facts if context else []
            out = batch / "documents" / entry["doc_id"]
            write(out / "artifact.json", artifact.to_dict())
            write(out / "facts.json", [f.to_dict() for f in facts])
            write(out / "fact_context.json", context.to_dict() if context else {})
            write(
                out / "fact_admission.json",
                [
                    {
                        "fact_uid": f.fact_uid,
                        "reason": operand_rejection_reason(f, profile.organization_name.value),
                    }
                    for f in facts
                ],
            )
            for n in ["source.pdf", "source_oracle.json"]:
                (out / n).symlink_to(frozen / "documents" / entry["doc_id"] / n)
            record = {
                "doc_id": entry["doc_id"],
                "batch": name,
                "source_sha256": entry["sha256"],
                "pages": artifact.page_count,
                "facts": len(facts),
                "admitted": len(context.definite_facts()) if context else 0,
                "seconds": round(time.monotonic() - start, 3),
                "source_unchanged": hashlib.sha256(source.read_bytes()).hexdigest()
                == entry["sha256"],
            }
            summaries.append(record)
            write(dest / "progress.json", summaries)
            print(json.dumps(record, ensure_ascii=False), flush=True)
    write(dest / "code_after.json", code_hashes())
    assert code_hashes() == code_before, "source changed during replay"
    write(
        dest / "summary.json",
        {
            "documents": len(summaries),
            "pages": sum(r["pages"] for r in summaries),
            "facts": sum(r["facts"] for r in summaries),
            "records": summaries,
            "independent_unseen_acceptance": False,
            "full_identity_comparison_complete": False,
        },
    )


main()
