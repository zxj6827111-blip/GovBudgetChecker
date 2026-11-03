#!/usr/bin/env python3
"""第二批回归比较器拒收探针：合成记录变异，验证隐藏金额检查/未授权变化都会被拒绝。"""
from __future__ import annotations

import gzip
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

REPO = Path("/Volumes/workSSD/software development/GovBudgetChecker")
TOOL = Path(__file__).resolve().parents[1] / "scripts" / "batch2_regression_compare.py"
PYTHON = REPO / ".venv/bin/python"
SOURCE = Path("/Volumes/workSSD/software development/gbc-worktrees/"
              "pending-review-r5-execution-evidence-20261103/baseline_A")


def read_gz(path: Path) -> Dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def write_gz(path: Path, payload: Dict[str, Any]) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)


class Case:
    def __init__(self, samples=("RA-001", "RA-002")) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="batch2-reg-probe-"))
        self.base = self.root / "baseline"
        self.cur = self.root / "current"
        (self.base / "records").mkdir(parents=True)
        (self.cur / "records").mkdir(parents=True)
        for sample in samples:
            src = SOURCE / "records" / f"{sample}.json.gz"
            shutil.copy2(src, self.base / "records" / f"{sample}.json.gz")
            shutil.copy2(src, self.cur / "records" / f"{sample}.json.gz")
        meta = json.loads((SOURCE / "meta.json").read_text(encoding="utf-8"))
        meta["samples"] = [s for s in meta["samples"] if s.get("sample_id") in samples]
        meta["profile"] = {"probe": True}
        for target in (self.base, self.cur):
            (target / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        self.contract = self.root / "contract.json"
        self.out = self.root / "out"
        self.set_contract([])

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def set_contract(self, classes) -> None:
        self.contract.write_text(json.dumps({"allowed_classes": classes}, ensure_ascii=False),
                                 encoding="utf-8")

    def record_path(self, sample: str) -> Path:
        return self.cur / "records" / f"{sample}.json.gz"

    def mutate(self, sample: str, func) -> None:
        path = self.record_path(sample)
        payload = read_gz(path)
        func(payload)
        write_gz(path, payload)

    def run(self, mode="stage"):
        shutil.rmtree(self.out, ignore_errors=True)
        proc = subprocess.run(
            [str(PYTHON), "-I", str(TOOL), "--mode", mode, "--stage", "S2",
             "--baseline", str(self.base), "--current", str(self.cur),
             "--contract", str(self.contract), "--out", str(self.out)],
            capture_output=True, text=True)
        if proc.returncode not in (0, 1):
            raise AssertionError(proc.stderr[-2000:])
        return json.loads((self.out / "batch2_regression_compare.json").read_text(encoding="utf-8"))

    def verdicts(self, report):
        return {c["id"]: c["verdict"] for c in report["checks"]}


CASES = []


def case(name):
    def decorate(func):
        CASES.append((name, func))
        return func
    return decorate


def _first_unresolved(record):
    for oid, item in record.get("obligations", {}).items():
        if item.get("status") in {"insufficient_data", "parse_error", "execution_error"}:
            return oid, item
    raise AssertionError("样本没有未完成义务")


@case("clean_copy_passes")
def clean_copy(c: Case):
    report = c.run()
    assert report["overall"] == "PASS", report["checks"]


@case("unauthorized_finding_rejected")
def unauthorized_finding(c: Case):
    def add(record):
        record.setdefault("findings", []).append("f" * 64)
        record.setdefault("finding_bodies", {})["f" * 64] = {
            "rule_id": "R9", "severity": "low", "message": "未经契约的新发现", "pages": [1]}
    c.mutate("RA-001", add)
    report = c.run()
    assert report["overall"] == "FAIL" and c.verdicts(report)["C2"] == "FAIL", report["checks"]


@case("allowed_class_passes")
def allowed_class(c: Case):
    c.set_contract([{"id": "allowed_r9", "kind": "finding_added",
                     "match": {"sample_ids": ["RA-001"], "rule_prefixes": ["R9"]},
                     "basis": "探针：契约允许"}])

    def add(record):
        record.setdefault("findings", []).append("f" * 64)
        record.setdefault("finding_bodies", {})["f" * 64] = {
            "rule_id": "R9", "severity": "low", "message": "契约允许的新发现", "pages": [1]}
    c.mutate("RA-001", add)
    report = c.run()
    assert report["overall"] == "PASS", report["checks"]


@case("amount_subcheck_closed_rejected")
def amount_subcheck_closed(c: Case):
    def close(record):
        oid, _item = _first_unresolved(record)
        target = None
        for receipt in record.get("receipts", []):
            if receipt.get("obligation_id") == oid:
                target = receipt
                break
        if target is None:
            raise AssertionError("没有该义务的回执")
        target["amount_subcheck_status"] = "closed"
    c.mutate("RA-001", close)
    report = c.run()
    assert report["overall"] == "FAIL" and c.verdicts(report)["C3"] == "FAIL", report["checks"]


@case("obligation_completed_regression_rejected")
def obligation_completed(c: Case):
    def complete(record):
        oid, _item = _first_unresolved(record)
        record["obligations"][oid]["status"] = "completed"
    c.mutate("RA-001", complete)
    report = c.run()
    assert report["overall"] == "FAIL" and c.verdicts(report)["C3"] == "FAIL", report["checks"]


@case("receipt_pass_regression_rejected")
def receipt_pass(c: Case):
    def flip(record):
        oid, _item = _first_unresolved(record)
        for receipt in record.get("receipts", []):
            if receipt.get("obligation_id") == oid and receipt.get("status") != "pass":
                receipt["status"] = "pass"
                return
    c.mutate("RA-001", flip)
    report = c.run()
    assert report["overall"] == "FAIL" and c.verdicts(report)["C3"] == "FAIL", report["checks"]


def main() -> int:
    failures = []
    for name, func in CASES:
        case_obj = Case()
        try:
            func(case_obj)
            print(f"PASS {name}")
        except AssertionError as exc:
            failures.append(name)
            print(f"FAIL {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures.append(name)
            print(f"ERROR {name}: {type(exc).__name__}: {exc}")
        finally:
            case_obj.cleanup()
    print(json.dumps({"cases": len(CASES), "failures": failures}, ensure_ascii=False))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
