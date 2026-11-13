"""B1 独立复核：只读实施方交付，结果仅写本 reviewer 目录。"""
from __future__ import annotations

import collections
import csv
import gzip
import hashlib
import io
import json
import subprocess
import tarfile
from decimal import Decimal
from pathlib import Path

import pymupdf

HERE = Path(__file__).resolve().parent
RUN = HERE.parent
ROOT = RUN.parents[2]
PLAN = "d90ba45956613c13900d1d972a90557a77a1b6cf"
SNAPSHOT = "48ee4e28441bd6ddbf34beee85bc6883d60db773"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def rows(name):
    with (RUN / name).open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(name, data):
    if not data:
        return
    with (HERE / name).open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(data[0]))
        writer.writeheader()
        writer.writerows(data)


def main():
    manifest = json.loads((RUN / "source_manifest.json").read_text())
    original_hashes = {r["path"]: digest(RUN / r["path"]) for r in manifest["files"]}
    hash_results = [
        {"path": r["path"], "recorded_sha256": r["sha256"],
         "actual_sha256": original_hashes[r["path"]],
         "matches": r["sha256"] == original_hashes[r["path"]]}
        for r in manifest["files"]
    ]
    write_csv("manifest_verification.csv", hash_results)

    archive = subprocess.check_output(["git", "archive", SNAPSHOT], cwd=ROOT)
    checked = 0
    mismatches = []
    with tarfile.open(fileobj=io.BytesIO(archive)) as package:
        for member in package:
            if not member.isfile():
                continue
            checked += 1
            target = RUN / "code_snapshot" / member.name
            expected = hashlib.sha256(package.extractfile(member).read()).hexdigest()
            if not target.is_file() or digest(target) != expected:
                mismatches.append(member.name)

    gold_results = []
    gold_pdf_sha = set()
    for record in manifest["gold"]["samples"]:
        path = Path(record["annotation_path"])
        annotation = json.loads(path.read_text())
        gold_pdf_sha.add(annotation["sha256"])
        gold_results.append({"sample_id": record["sample_id"],
                             "match": digest(path) == record["frozen_sha256"]})

    upload_paths = list((ROOT / "uploads").glob("*/*.pdf"))
    upload_hashes = {digest(path) for path in upload_paths}
    record_paths = list((ROOT / "outputs/fp_batch1_20261103/nongold/new/records").glob("*.json.gz"))
    record_hashes = set()
    for path in record_paths:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            record_hashes.add(json.load(stream)["sha256"])

    candidates = rows("candidates.csv")
    annotation_counts = collections.Counter()
    review_rows = []
    quote_failures = []
    generic_cd = 0
    empty_table_metadata = []
    for candidate in candidates:
        annotation = json.loads((RUN / candidate["annotation_path"]).read_text())
        with pymupdf.open(candidate["pdf_path"]) as document:
            texts = [page.get_text() for page in document]
        actual_sha = digest(candidate["pdf_path"])
        for check in annotation["checks"]:
            annotation_counts[check["verdict"]] += 1
            unexamined = check.get("reason") == "定向初标仅覆盖目标检查；本项待独立复核补充"
            generic_cd += unexamined
            problems = []
            for item in check.get("items", []):
                for value in item.get("values", []):
                    page, quote = value.get("page"), value.get("quote", "")
                    if not isinstance(page, int) or not 1 <= page <= len(texts) or quote not in texts[page - 1]:
                        problems.append({"page": page, "quote": quote})
            quote_failures.extend(problems)
            review_rows.append({"sample_id": annotation["sample_id"], "check_id": check["check_id"],
                                "submitted_verdict": check["verdict"],
                                "review_result": "RETURN_UNEXAMINED" if unexamined else "RETURN_WITH_DOCUMENT",
                                "reason": "未做检查不属于规范 cannot_determine；须完整重新初标" if unexamined else "本次已退回整份；个别数值复核见 candidate_evidence.json，不构成冻结",
                                "pdf_sha_match": actual_sha == annotation["sha256"],
                                "overlaps_old_33": actual_sha in gold_pdf_sha})
        for table in annotation["basic"]["tables"]:
            page = table["page"]
            if table["status"] == "data" and "故本表无数据" in texts[page - 1]:
                empty_table_metadata.append({"sample_id": annotation["sample_id"], "page": page,
                                             "submitted_status": "data", "expected_status": "empty_with_note"})
    write_csv("check_review.csv", review_rows)

    screening = rows("candidate_screen_log.csv")
    target_docs = collections.defaultdict(set)
    families = collections.defaultdict(set)
    for record in screening:
        if record["effective_screen_done"] == "是":
            for target in record["targets"].split(","):
                target_docs[target].add(record["sha256"])
        for family in record["family"].split("/"):
            families[family].add(record["sha256"])
    budget_results = []
    for status in rows("target_status.csv"):
        target = status["target_id"]
        budget_results.append({"target_id": target, "status": status["status"],
                               "reported_effective": int(status["effective_screened"]),
                               "log_labelled_docs": len(target_docs[target]),
                               "cap": int(status["budget_cap"]),
                               "reported_over_cap": int(status["effective_screened"]) > int(status["budget_cap"]),
                               "annotation_evidence_path": status["evidence_path"]})
    write_csv("budget_reconciliation.csv", budget_results)

    worker_results = []
    for path in sorted((RUN / "pipeline/work").glob("*/worker_result.json")):
        worker = json.loads(path.read_text())
        env = worker.get("env_seen", {})
        modules = worker.get("module_files", {})
        expected = {"AI_ASSIST_ENABLED": "false", "OCR_ENABLED": "false", "DATABASE_URL": "",
                    "PYTHON_DOTENV_DISABLED": "1", "GBC_TEMPLATE_SCOPE": "0"}
        status_path = RUN / "pipeline/status" / (path.parent.name + ".status.json.gz")
        with gzip.open(status_path, "rt", encoding="utf-8") as stream:
            status = json.load(stream)
        checksum = status.get("checksum", "")
        worker_results.append({"sample_id": path.parent.name, "ok": worker.get("ok"),
                               "expected_env_match": all(env.get(k) == v for k, v in expected.items()),
                               "dotenv_leaked": worker.get("dotenv_leaked"),
                               "imports_from_snapshot": bool(modules) and all(str(RUN / "code_snapshot") in v for v in modules.values()),
                               "checksum_length": len(checksum), "checksum_has_trailing_cr": checksum.endswith("\r")})

    plan_path = Path("/Volumes/workSSD/software development/gbc-worktrees/gold-coverage-20261103/docs/gold_coverage_20261103/next_batch_plan.md")
    plan_bytes = subprocess.check_output(["git", "show", f"{PLAN}:docs/gold_coverage_20261103/next_batch_plan.md"], cwd=ROOT)
    audit = {
        "reviewer": "Codex", "verdict": "RETURN_FOR_REANNOTATION_AND_COMPLETION",
        "b1_complete": False, "freeze_authorized": False,
        "plan_matches_fixed_commit": plan_path.read_bytes() == plan_bytes,
        "snapshot": {"commit": SNAPSHOT, "regular_files_checked": checked, "mismatches": mismatches},
        "old_gold": {"checked": len(gold_results), "matches": sum(r["match"] for r in gold_results)},
        "pools": {"upload_files": len(upload_paths), "upload_unique": len(upload_hashes),
                  "regression_records": len(record_paths), "regression_unique": len(record_hashes),
                  "intersection": len(upload_hashes & record_hashes)},
        "manifest": {"entries": len(hash_results), "mismatches": [r for r in hash_results if not r["matches"]]},
        "annotations": {"documents": len(candidates), "verdict_counts": dict(annotation_counts),
                        "unexamined_placeholder_cd": generic_cd, "quote_failures": quote_failures,
                        "incorrect_empty_table_metadata": empty_table_metadata},
        "screening": {"unique_pdf_hashes": len({r["sha256"] for r in screening}),
                      "family_label_counts": {k: len(v) for k, v in families.items()},
                      "note": "家族计数仅重算台账标签；并非确认每个目标已完成有效核对"},
        "workers": worker_results,
        "original_deliverables_unchanged_during_audit": all(digest(RUN / k) == v for k, v in original_hashes.items()),
    }
    (HERE / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in audit.items() if k not in {"workers", "manifest", "annotations"}}, ensure_ascii=False, indent=2))
    print("annotation_counts", dict(annotation_counts), "unexamined", generic_cd, "incorrect_empty_states", len(empty_table_metadata))
    print("manifest_mismatches", [r["path"] for r in hash_results if not r["matches"]])


if __name__ == "__main__":
    main()
