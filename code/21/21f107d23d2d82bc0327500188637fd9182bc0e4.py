"""独立固定材料复核：冻结真值按 PDF 原生字符身份定位，跨行 bbox 不充当身份。

真值只来自上一轮冻结 source_oracle / overlay / source adjudication / native audit；
被测 artifact / facts 只参与右侧观测，不得反填 expected。写入本轮 phase 子目录。
"""
from collections import Counter, defaultdict
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import unicodedata


BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
FROZEN = BASE


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def compact(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or ""))).replace("，", ",")


def digits(value):
    return compact(value).replace(",", "")


def source_truth():
    spec = importlib.util.spec_from_file_location("frozen_oracle_reader", FROZEN / "compare_source.py")
    oracle_reader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(oracle_reader)
    overlays = {r["truth_id"]: r for r in read(FROZEN / "source_truth_overlay.json")["overlays"]}
    identities = {r["truth_id"]: r for r in read(FROZEN / "identity_adjudication/source_identity_adjudications.json") if r["status"] == "source_confirmed"}
    audits = {r["truth_id"]: r for r in read(FROZEN / "native_amount_source_audit.json")["records"]}
    result = {}
    for doc in read(FROZEN / "inventory.json"):
        amounts, _ = oracle_reader.oracle_amounts(read(FROZEN / "documents" / doc["doc_id"] / "source_oracle.json"))
        for index, amount in enumerate(amounts):
            truth_id = f"{doc['doc_id']}:{index:05}"
            overlay = overlays.get(truth_id, {})
            amount["raw_text"] = overlay.get("raw_text", amount["raw_text"])
            amount["expected"].update(overlay.get("expected", {}))
            identity = identities.get(truth_id)
            if identity:
                amount["expected"].update(subject_code=identity["subject_code"], subject_name=identity["subject_name"])
            audit = audits[truth_id]
            assert audit["status"] == "native_confirmed", truth_id
            assert audit["doc_id"] == doc["doc_id"] and audit["page"] == amount["page"], truth_id
            assert digits(audit["native_amount"]) == digits(amount["raw_text"]), truth_id
            assert digits(audit["source_proof"]["text"]) == digits(amount["raw_text"]), truth_id
            amount.update(truth_id=truth_id, doc_id=doc["doc_id"], file_hash=doc["sha256"], native_source=audit["source_proof"])
            result[truth_id] = amount
    assert len(result) == len(audits)
    return result


def source_set(sources, page):
    indices = set()
    for proof in sources:
        if proof.get("physical_page") != page:
            continue
        start, end = proof.get("start_char_index"), proof.get("end_char_index")
        if isinstance(start, int) and isinstance(end, int) and 0 <= start < end:
            indices.update(range(start, end))
    return indices


def identity_candidates(candidates, truth):
    """只用原生字符来源归属选候选；不以金额相等筛选来源。"""
    page = truth["page"]
    anchor = truth["native_source"]["start_char_index"]
    return [item for item in candidates if anchor in source_set(item["sources"], page)]


def value_status(candidates, expected, exact_label):
    if not candidates:
        return "missing"
    if len(candidates) != 1:
        return "ambiguous"
    return exact_label if digits(candidates[0]["raw_text"]) == digits(expected) else "wrong_text"


def compare_phase(phase, *, verify_pdf_characters=False):
    phase = phase.resolve()
    assert BASE in phase.parents and phase != FROZEN
    truth = source_truth()
    inventory = read(phase / "inventory.json")
    selected = {d["doc_id"] for d in inventory}
    out = phase / "native_identity_comparison"
    out.mkdir(exist_ok=True)
    all_rows = []
    live_source_verifications = []
    for doc in inventory:
        location = phase / "documents" / doc["doc_id"]
        artifact, facts = read(location / "artifact.json"), read(location / "facts.json")
        admission = {r["fact_uid"]: r["reason"] for r in read(location / "fact_admission.json")}
        cells, refs = defaultdict(list), defaultdict(list)
        physical_ids = set()
        explicit_origins = set()
        original_grid_ids = {f"{table['fragment_uid']}:r{cell['row_idx']}c{cell['col_idx']}"
                             for table in artifact.get("source_grids", []) for cell in table["cells"]}
        for table in artifact["physical_fragments"]:
            if table["extraction_method"] == "page_text_fallback":
                continue
            for cell in table["cells"]:
                original_fragment = cell.get("origin_fragment_uid") or table["fragment_uid"]
                original_row = cell.get("origin_row_idx") if cell.get("origin_row_idx") is not None else cell["row_idx"]
                original_col = cell.get("origin_col_idx") if cell.get("origin_col_idx") is not None else cell["col_idx"]
                cid = f"{original_fragment}:r{original_row}c{original_col}"
                physical_ids.add(cid)
                if cell.get("origin_fragment_uid"):
                    explicit_origins.add(cid)
                if cell["numeric_value"] is not None:
                    cells[cell["page_number"]].append(dict(cell_id=cid, raw_text=cell["raw_text"], sources=cell.get("native_text_sources", []), bbox=cell["bbox"]))
        for fact in facts:
            for ref in fact["extra_dims"].get("evidence_refs", []):
                if ref.get("physical_page"):
                    refs[ref["physical_page"]].append(dict(fact=fact, ref=ref, raw_text=fact["raw_value"], sources=ref.get("source_provenance", {}).get("native_text_sources", [])))
        source_rows = [r for r in truth.values() if r["doc_id"] == doc["doc_id"]]
        source_verified = None
        if verify_pdf_characters:
            import pdfplumber
            source_verified = 0
            assert hashlib.sha256((location / "source.pdf").read_bytes()).hexdigest() == doc["sha256"]
            with pdfplumber.open(location / "source.pdf") as pdf:
                by_page = defaultdict(list)
                for row in source_rows:
                    by_page[row["page"]].append(row)
                for page_number, page_rows in by_page.items():
                    chars = pdf.pages[page_number - 1].chars
                    for row in page_rows:
                        proof = row["native_source"]
                        actual = chars[proof["start_char_index"]:proof["end_char_index"]]
                        assert "".join(c["text"] for c in actual) == proof["text"], row["truth_id"]
                        for recorded, current in zip(proof["characters"], actual):
                            box = (current["x0"], current["top"], current["x1"], current["bottom"])
                            assert max(abs(a - b) for a, b in zip(recorded["bbox"], box)) < .001, row["truth_id"]
                        source_verified += 1
            live_source_verifications.append(dict(doc_id=doc["doc_id"], file_hash=doc["sha256"], amounts_verified=source_verified,
                                                  character_text_exact=True, character_coordinates_max_error_lt=.001))
        rows = []
        for source in source_rows:
            page = source["page"]
            parsed = identity_candidates(cells[page], source)
            bound = identity_candidates(refs[page], source)
            reading = value_status(parsed, source["raw_text"], "read_exact")
            status = value_status(bound, source["raw_text"], "bound_exact")
            checks = {}
            reason = None
            fact = ref = None
            if len(bound) == 1:
                fact, ref = bound[0]["fact"], bound[0]["ref"]
                reason = admission.get(fact["fact_uid"])
                expected = source["expected"]
                checks = {"table_code": fact["table_code"] == source["table_code"],
                          "physical_cell_id_exists": ref.get("source_cell_id") in physical_ids,
                          "file_hash_matches": ref.get("file_hash") == doc["sha256"],
                          "explicit_origin_exists_in_untouched_source_grid": ref.get("source_cell_id") in original_grid_ids if ref.get("source_cell_id") in explicit_origins else None}
                for key in ("subject_name", "subject_code", "indicator", "budget_stage"):
                    checks[key] = None if not expected.get(key) else (compact(fact.get(key)) == compact(expected[key]))
                independent = source_set([dict(physical_page=page, **source["native_source"])], page)
                checks["native_amount_characters_exact"] = source_set(bound[0]["sources"], page) == independent
                checks["native_amount_proof_text_exact"] = digits("".join(p.get("text", "") for p in bound[0]["sources"] if p.get("physical_page") == page)) == digits(source["raw_text"])
                if status == "bound_exact" and any(value is False for value in checks.values()):
                    status = "semantic_mismatch"
                elif status == "bound_exact" and (checks.get("subject_name") is None or checks.get("indicator") is None):
                    status = "manual_review"
            rows.append(dict(truth_id=source["truth_id"], doc_id=doc["doc_id"], page=page,
                             expected=source["expected"], raw_text=source["raw_text"], table_code=source["table_code"],
                             independent_source=source["native_source"], reading_status=reading, fact_status=status,
                             checks=checks, fact_admission_rejection=reason,
                             parsed=[{k: v for k, v in r.items() if k != "sources"} for r in parsed],
                             fact_uid=fact["fact_uid"] if fact else None,
                             actual_fact={key: fact.get(key) for key in ("table_code", "subject_code", "subject_name", "indicator", "budget_stage", "raw_value")} if fact else None,
                             fact_source_cell_id=ref.get("source_cell_id") if ref else None))
        write(out / f"{doc['doc_id']}.json", rows)
        all_rows.extend(rows)
        print(doc["doc_id"], dict(reading=Counter(r["reading_status"] for r in rows), facts=Counter(r["fact_status"] for r in rows), independent_pdf_characters_verified=source_verified), flush=True)
    summary = dict(pdfs=len(inventory), source_amounts=len(all_rows), reading=dict(Counter(r["reading_status"] for r in all_rows)),
                   facts=dict(Counter(r["fact_status"] for r in all_rows)),
                   admitted_and_matched=sum(r["fact_status"] == "bound_exact" and not r["fact_admission_rejection"] for r in all_rows),
                   gate_reasons=dict(Counter(r["fact_admission_rejection"] for r in all_rows if r["fact_admission_rejection"])),
                   independent_native_source_confirmed=sum(r["doc_id"] in selected for r in truth.values()),
                   matching="PDF page and original native character index; no value search and no bbox ownership",
                   tested_outputs_used_as_truth=False)
    if (phase / "code_before.json").exists() and (phase / "code_after_parse.json").exists():
        code_before, code_after = read(phase / "code_before.json"), read(phase / "code_after_parse.json")
        summary["production_code_changed_during_parse"] = code_before != code_after
        summary["changed_code_files_during_parse"] = sorted(name for name in code_before.keys() | code_after.keys() if code_before.get(name) != code_after.get(name))
    write(out / "totals.json", summary)
    if verify_pdf_characters:
        write(out / "source_pdf_native_character_verification.json", dict(documents=live_source_verifications,
              verified_amounts=sum(row["amounts_verified"] for row in live_source_verifications), tested_outputs_used_as_truth=False))
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return all_rows, summary


def regression_diff(before, after):
    def load_rows(directory):
        return {r["truth_id"]: r for p in (directory / "native_identity_comparison").glob("SH*.json") for r in read(p)}
    old, new = load_rows(before), load_rows(after)
    assert old.keys() == new.keys(), "来源分母必须一致"
    regressions, changed, changed_source_ids = [], [], []
    for truth_id, was in old.items():
        now = new[truth_id]
        assert (was["expected"], was["raw_text"], was["independent_source"]) == (now["expected"], now["raw_text"], now["independent_source"]), truth_id
        reasons = []
        if was["reading_status"] == "read_exact" and now["reading_status"] != "read_exact":
            reasons.append("原先正确读取退化")
        if was["fact_status"] == "bound_exact" and now["fact_status"] != "bound_exact":
            reasons.append("原先正确金额或科目列证据绑定退化")
        if was["fact_status"] == "bound_exact" and not was["fact_admission_rejection"] and now["fact_admission_rejection"]:
            reasons.append("原先正确且准入金额被新增阻断")
        if reasons:
            regressions.append(dict(truth_id=truth_id, reasons=reasons, before=was, after=now))
        if was["fact_status"] == "bound_exact" and was["fact_source_cell_id"] != now["fact_source_cell_id"]:
            changed_source_ids.append(dict(truth_id=truth_id, before=was["fact_source_cell_id"], after=now["fact_source_cell_id"]))
        if any(was[k] != now[k] for k in ("reading_status", "fact_status", "fact_admission_rejection", "actual_fact")):
            changed.append(dict(truth_id=truth_id, before=was, after=now))
    original_grids = []
    for doc in read(after / "inventory.json"):
        name = doc["doc_id"]
        left = read(before / "documents" / name / "artifact.json").get("source_grids")
        right = read(after / "documents" / name / "artifact.json").get("source_grids")
        original_grids.append(dict(doc_id=name, unchanged=left == right, source_grid_count=len(left or [])))
    result = dict(source_denominator=len(old), reading_previously_correct=sum(r["reading_status"] == "read_exact" for r in old.values()),
                  facts_previously_correct=sum(r["fact_status"] == "bound_exact" for r in old.values()),
                  previously_admitted_and_matched=sum(r["fact_status"] == "bound_exact" and not r["fact_admission_rejection"] for r in old.values()),
                  regressions=regressions, changed_cells=len(changed), frozen_truth_unchanged=True,
                  original_source_grids=original_grids, original_source_grids_unchanged=all(row["unchanged"] for row in original_grids),
                  previously_correct_source_cell_id_changes=changed_source_ids)
    write(after / "native_identity_comparison/regression_diff.json", result)
    write(after / "native_identity_comparison/changed_cells.json", changed)
    return result


def self_test():
    # 三行金额相同且 glyph bbox 跨相邻行；来源身份仍由各自的原生字符 index 决定。
    truth = dict(page=18, native_source=dict(start_char_index=719, end_char_index=729))
    candidates = [dict(raw_text="121,914.65", bbox=[298, 500, 354, 552],
                       sources=[dict(physical_page=18, start_char_index=start, end_char_index=start + 10, text="121,914.65")])
                  for start in (682, 719, 762)]
    matched = identity_candidates(candidates, truth)
    assert matched == [candidates[1]]
    assert value_status(matched, "121,914.65", "read_exact") == "read_exact"
    assert not identity_candidates([candidates[0], candidates[2]], truth)
    assert not identity_candidates([dict(candidates[1], sources=[dict(physical_page=19, start_char_index=719, end_char_index=729)])], truth)
    truncated = dict(candidates[1], raw_text="121,914.6")
    assert value_status(identity_candidates([truncated], truth), "121,914.65", "read_exact") == "wrong_text"
    assert value_status(identity_candidates([candidates[1], truncated], truth), "121,914.65", "read_exact") == "ambiguous"
    # 即使数额相同，无原生字符来源的宽 bbox 也不得成为后备来源。
    assert not identity_candidates([dict(raw_text="121,914.65", sources=[], bbox=[0, 0, 1000, 1000])], truth)
    return dict(status="passed", cases=7, covers=["same value adjacent rows", "wrong page", "partial literal", "duplicate source rejection", "no bbox fallback"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", nargs="?")
    parser.add_argument("--verify-pdf-characters", action="store_true")
    parser.add_argument("--before")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        result = self_test()
        write(BASE / "native_identity_comparator_selftest.json", result)
        print(json.dumps(result))
        raise SystemExit(0)
    assert args.phase
    phase_path = BASE / args.phase
    compare_phase(phase_path, verify_pdf_characters=args.verify_pdf_characters)
    if args.before:
        delta = regression_diff(BASE / args.before, phase_path)
        print(json.dumps({k: v for k, v in delta.items() if k != "regressions"}, ensure_ascii=False))
        assert not delta["regressions"], "原先正确项目发生回退"
